"""
Student-Centric 'Describe Your Life' Onboarding Intake Engine.
Per ONBOARDING_KNOWLEDGE_BASE.md and Design Decisions.

Neuro-symbolic separation:
- Gemini performs structured extraction and phrasing.
- Deterministic Python services manage coverage state, store routing,
  knowledge graph sync_node hooks, and CP-SAT schedule generation.
"""
import datetime
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.onboarding import OnboardingCoverage
from app.models.schedule import ScheduleItem
from app.models.semantic import SemanticContext
from app.models.tracker import TrackerDefinition
from app.models.user import UserProfile
from app.onboarding.question_bank import (
    QUESTION_BANK,
    TIER_0_TOPICS,
    TIER_1_TOPICS,
    TIER_2_TOPICS,
    get_question_by_id,
)
from app.schemas.onboarding import (
    ExtractedEntity,
    OnboardingExtraction,
    TopicAnswer,
)
from app.services.gemini_client import (
    extract_onboarding,
    generate_onboarding_phrasing,
)
from app.services.knowledge_graph import recompute_edges_for, sync_node
from app.services.rag import store_episodic_constraint

logger = logging.getLogger(__name__)

CRISIS_RESPONSE_TEXT = (
    "I hear how much pain you're carrying right now, and I want you to know that you don't have to carry this alone. "
    "Please pause our planning and connect with someone who can support you right now:\n\n"
    "• Tele-MANAS: Call 14416 or 1800-891-4416 (24/7 Toll-Free, Confidential, India)\n"
    "• Vandrevala Foundation: +91 9999 666 555 (24/7 Mental Health Helpline)\n"
    "• AASRA: +91 98204 66726 (24/7 Crisis Helpline)\n"
    "• US / International: Dial 988 or text HOME to 741741\n\n"
    "Your life, wellbeing, and safety matter far more than any schedule or academic deadline. "
    "When you feel ready and safe, you can tell me to continue."
)

OFFICIAL_OPENING_MESSAGE = (
    "Hey, I'm here to take the 'what should I be doing right now?' load off your head. "
    "To do that I need to know your life. Not a form, just talk. Tell me about a normal week: "
    "what you're studying, what your days look like, what you do for fun, who and what takes your time, "
    "what's stressing you out. Messy is fine. Write as much or as little as you want, and I'll ask about anything I'm missing. "
    "You can say 'skip' to anything."
)


# ===========================================================================
# 1. Coverage State Machine
# ===========================================================================

def update_coverage(
    db: Session,
    user_id: UUID,
    topics: List[TopicAnswer],
    message_id: Optional[UUID] = None,
) -> List[OnboardingCoverage]:
    """
    Updates coverage status for topics extracted in this turn:
    - 'answered', 'declined', 'not_applicable' permanently retire the topic.
    - 'skipped' increments asked_count. After 2 skips, marked 'skipped' and deprioritized.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    updated_records: List[OnboardingCoverage] = []

    for topic in topics:
        t_id = topic.topic_id.upper().strip()

        # Handle explicit generic skip
        if t_id == "SKIP":
            # Find the most recently asked pending topic
            recent_asked = (
                db.query(OnboardingCoverage)
                .filter(
                    OnboardingCoverage.user_id == user_id,
                    OnboardingCoverage.status.in_(["unknown", "partial"]),
                )
                .order_by(OnboardingCoverage.last_asked_at.desc())
                .first()
            )
            if recent_asked:
                recent_asked.asked_count += 1
                if recent_asked.asked_count >= 2:
                    recent_asked.status = "skipped"
                db.add(recent_asked)
                updated_records.append(recent_asked)
            continue

        if t_id not in QUESTION_BANK:
            continue

        cov = (
            db.query(OnboardingCoverage)
            .filter(
                OnboardingCoverage.user_id == user_id,
                OnboardingCoverage.topic_id == t_id,
            )
            .first()
        )

        if cov:
            # If already permanently retired, do not regress to partial
            if cov.status in ("answered", "declined", "not_applicable"):
                continue

            if topic.status in ("answered", "declined", "not_applicable"):
                cov.status = topic.status
                cov.answered_via = message_id
            elif topic.status == "partial":
                cov.status = "partial"
            elif topic.status == "skipped":
                cov.asked_count += 1
                if cov.asked_count >= 2:
                    cov.status = "skipped"
        else:
            cov = OnboardingCoverage(
                user_id=user_id,
                topic_id=t_id,
                status=topic.status,
                asked_count=1 if topic.status == "partial" else 0,
                last_asked_at=now,
                answered_via=message_id if topic.status in ("answered", "declined", "not_applicable") else None,
            )

        db.add(cov)
        updated_records.append(cov)

    db.commit()
    return updated_records


def is_tier_0_complete(db: Session, user_id: UUID) -> bool:
    """
    Returns True if all 9 Tier 0 foundational topics are answered, declined, or not_applicable.
    """
    records = (
        db.query(OnboardingCoverage)
        .filter(
            OnboardingCoverage.user_id == user_id,
            OnboardingCoverage.topic_id.in_(TIER_0_TOPICS),
            OnboardingCoverage.status.in_(["answered", "declined", "not_applicable"]),
        )
        .all()
    )
    retired_set = {r.topic_id for r in records}
    return all(t_id in retired_set for t_id in TIER_0_TOPICS)


def detect_fatigue(db: Session, user_id: UUID, recent_messages: List[Dict[str, Any]]) -> bool:
    """
    Detects user fatigue during onboarding:
    Returns True if the user sent 3 consecutive unhelpful or 'skip' messages in a row.
    """
    if not recent_messages or len(recent_messages) < 3:
        return False

    user_msgs = [m.get("text", "").lower().strip() for m in recent_messages if m.get("sender") == "user"]
    if len(user_msgs) < 3:
        return False

    last_3 = user_msgs[-3:]
    skip_keywords = {"skip", "pass", "next", "leave it", "idk", "stop", "no", "later"}

    consecutive_skips = 0
    for m in last_3:
        if m in skip_keywords or len(m) <= 4 or "skip" in m:
            consecutive_skips += 1

    return consecutive_skips >= 3


def get_next_questions(db: Session, user_id: UUID, user_message: str = "") -> List[str]:
    """
    Determines the next 1–2 questions to ask:
    1. Evaluates unretired Tier 0 topics first.
    2. Respects dependency rules (B1 before B4/B6; C1 before C2).
    3. Escalates pain topics if recent message mentions exams or sleep.
    4. Never repeats answered or permanently declined questions.
    5. Returns at most 2 topics (preferably 1).
    """
    records = {
        r.topic_id: r
        for r in db.query(OnboardingCoverage).filter(OnboardingCoverage.user_id == user_id).all()
    }

    def is_retired(t_id: str) -> bool:
        rec = records.get(t_id)
        return rec is not None and rec.status in ("answered", "declined", "not_applicable")

    def is_deprioritized(t_id: str) -> bool:
        rec = records.get(t_id)
        return rec is not None and (rec.status == "skipped" or rec.asked_count >= 2)

    msg_lower = user_message.lower()

    # Find candidate Tier 0 topics
    t0_candidates: List[str] = []
    for t_id in TIER_0_TOPICS:
        if is_retired(t_id):
            continue
        if is_deprioritized(t_id):
            continue

        # Check dependencies
        q_topic = QUESTION_BANK[t_id]
        if any(not is_retired(dep) for dep in q_topic.dependencies):
            continue

        t0_candidates.append(t_id)

    # Pain escalation priority reordering
    escalated_candidates: List[str] = []
    if any(k in msg_lower for k in ["exam", "test", "quiz", "internal", "deadline", "midterm"]):
        if "D1" in t0_candidates:
            escalated_candidates.append("D1")
            t0_candidates.remove("D1")
    if any(k in msg_lower for k in ["sleep", "tired", "wake", "bedtime", "insomnia"]):
        if "K1" in t0_candidates:
            escalated_candidates.append("K1")
            t0_candidates.remove("K1")
    if any(k in msg_lower for k in ["class", "lab", "timetable", "timing"]):
        if "C1" in t0_candidates:
            escalated_candidates.append("C1")
            t0_candidates.remove("C1")
    if any(k in msg_lower for k in ["course", "subject", "syllabus"]):
        if "B1" in t0_candidates:
            escalated_candidates.append("B1")
            t0_candidates.remove("B1")

    prioritized_t0 = escalated_candidates + t0_candidates

    # If Tier 0 topics remain, pick up to 2 (preferably 1)
    if prioritized_t0:
        chosen = prioritized_t0[:1]
        _record_asked_topics(db, user_id, chosen, records)
        return chosen

    # If Tier 0 is complete, look at Tier 1 topics
    t1_candidates: List[str] = []
    for t_id in TIER_1_TOPICS:
        if is_retired(t_id) or is_deprioritized(t_id):
            continue
        q_topic = QUESTION_BANK[t_id]
        if any(not is_retired(dep) for dep in q_topic.dependencies):
            continue
        t1_candidates.append(t_id)

    if t1_candidates:
        chosen = t1_candidates[:1]
        _record_asked_topics(db, user_id, chosen, records)
        return chosen

    return []


def _record_asked_topics(
    db: Session,
    user_id: UUID,
    topic_ids: List[str],
    existing_records: Dict[str, OnboardingCoverage],
):
    """Marks topics as asked in onboarding_coverage."""
    now = datetime.datetime.now(datetime.timezone.utc)
    for t_id in topic_ids:
        rec = existing_records.get(t_id)
        if rec:
            rec.asked_count += 1
            rec.last_asked_at = now
            db.add(rec)
        else:
            new_rec = OnboardingCoverage(
                user_id=user_id,
                topic_id=t_id,
                status="unknown",
                asked_count=1,
                last_asked_at=now,
            )
            db.add(new_rec)
    db.commit()


# ===========================================================================
# 2. Deterministic Store Dispatcher & Knowledge Graph Sync
# ===========================================================================

def dispatch_entities(
    db: Session,
    user_id: UUID,
    entities: List[ExtractedEntity],
) -> Dict[str, Any]:
    """
    Dispatches extracted entities to their canonical relational and semantic stores,
    and calls sync_node to guarantee every entity appears on The Web.
    """
    counts = {
        "user_profiles": 0,
        "schedule_items": 0,
        "tracker_definitions": 0,
        "semantic_contexts": 0,
    }

    user = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if not user:
        user = UserProfile(
            user_id=user_id,
            email=f"user_{str(user_id)[:8]}@offloader.ai",
            sleep_start=datetime.time(23, 0),
            sleep_end=datetime.time(7, 0),
            buffer_minutes=15,
            max_study_hours_per_day=8,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    for ent in entities:
        target = ent.target_store
        payload = ent.payload or {}

        try:
            # 1. User Profiles
            if target == "user_profiles":
                if "name" in payload and payload["name"]:
                    user.name = str(payload["name"]).strip()
                if "program" in payload and payload["program"]:
                    user.program = str(payload["program"]).strip()
                if "semester" in payload and payload["semester"]:
                    user.semester = str(payload["semester"]).strip()
                if "college" in payload and payload["college"]:
                    user.college = str(payload["college"]).strip()
                if "sleep_start" in payload and payload["sleep_start"]:
                    try:
                        s_str = str(payload["sleep_start"]).strip()
                        parts = s_str.split(":")
                        user.sleep_start = datetime.time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
                    except Exception:
                        pass
                if "sleep_end" in payload and payload["sleep_end"]:
                    try:
                        e_str = str(payload["sleep_end"]).strip()
                        parts = e_str.split(":")
                        user.sleep_end = datetime.time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
                    except Exception:
                        pass
                if "max_study_hours_per_day" in payload:
                    try:
                        user.max_study_hours_per_day = int(payload["max_study_hours_per_day"])
                    except Exception:
                        pass
                db.add(user)
                db.commit()
                counts["user_profiles"] += 1

            # 2. Schedule Items (Fixed classes/shifts vs discretionary goals/exams)
            elif target == "schedule_items":
                title = str(payload.get("title", "Scheduled Event")).strip()
                category = str(payload.get("category", "study_session")).strip()
                duration = int(payload.get("duration_minutes", 60))
                priority = int(payload.get("priority", 5))
                is_fixed = bool(payload.get("is_fixed", False))

                deadline_dt = None
                if payload.get("deadline_iso"):
                    try:
                        deadline_dt = datetime.datetime.fromisoformat(str(payload["deadline_iso"]).replace("Z", "+00:00"))
                    except Exception:
                        pass

                # Check for duplicate item title for user
                existing_item = (
                    db.query(ScheduleItem)
                    .filter(
                        ScheduleItem.user_id == user_id,
                        ScheduleItem.title == title,
                    )
                    .first()
                )
                if not existing_item:
                    item = ScheduleItem(
                        user_id=user_id,
                        title=title,
                        category=category,
                        duration_minutes=duration,
                        priority=priority,
                        is_fixed=is_fixed,
                        deadline=deadline_dt,
                    )
                    db.add(item)
                    db.commit()
                    db.refresh(item)
                    counts["schedule_items"] += 1

                    # Knowledge graph sync hook
                    try:
                        sync_node(db, user_id, "schedule_items", item.id)
                    except Exception as ex:
                        logger.warning(f"Error syncing schedule_items node to knowledge graph: {ex}")

            # 3. Semantic Contexts (Episodic constraints, syllabus modules)
            elif target == "semantic_contexts":
                context_type = str(payload.get("context_type", "episodic_constraint")).strip()
                raw_content = str(payload.get("raw_content", "")).strip()
                subject = payload.get("subject")

                if raw_content:
                    if context_type == "episodic_constraint":
                        rec = store_episodic_constraint(db, user_id=user_id, constraint_text=raw_content)
                        counts["semantic_contexts"] += 1
                        try:
                            recompute_edges_for(db, rec.id)
                        except Exception as ex:
                            logger.warning(f"Error recomputing edges for episodic constraint: {ex}")
                    else:
                        existing_ctx = (
                            db.query(SemanticContext)
                            .filter(
                                SemanticContext.user_id == user_id,
                                SemanticContext.raw_content == raw_content,
                            )
                            .first()
                        )
                        if not existing_ctx:
                            from app.services.rag import generate_embedding_1536
                            emb = generate_embedding_1536(raw_content)
                            ctx = SemanticContext(
                                user_id=user_id,
                                context_type=context_type,
                                subject=subject,
                                raw_content=raw_content,
                                embedding=emb,
                                source_table="conversation",
                            )
                            db.add(ctx)
                            db.commit()
                            db.refresh(ctx)
                            counts["semantic_contexts"] += 1
                            try:
                                recompute_edges_for(db, ctx.id)
                            except Exception as ex:
                                logger.warning(f"Error recomputing edges for semantic_contexts: {ex}")

            # 4. Tracker Definitions (EAV JIT Registration)
            elif target == "tracker_definitions":
                name = str(payload.get("name", "habit")).strip().lower().replace(" ", "_")
                category = str(payload.get("category", "metric")).strip()
                unit = payload.get("unit")
                data_type = "float" if category == "volume" or unit else ("boolean" if category == "binary_habit" else "text")

                existing_tracker = (
                    db.query(TrackerDefinition)
                    .filter(
                        TrackerDefinition.user_id == user_id,
                        TrackerDefinition.name == name,
                    )
                    .first()
                )
                if not existing_tracker:
                    tracker = TrackerDefinition(
                        user_id=user_id,
                        name=name,
                        category=category,
                        data_type=data_type,
                        unit=unit,
                    )
                    db.add(tracker)
                    db.commit()
                    db.refresh(tracker)
                    counts["tracker_definitions"] += 1

                    try:
                        sync_node(db, user_id, "tracker_definitions", tracker.id)
                    except Exception as ex:
                        logger.warning(f"Error syncing tracker_definitions node: {ex}")

        except Exception as e:
            logger.error(f"Error dispatching entity to {target}: {e}", exc_info=True)
            db.rollback()

    return counts


# ===========================================================================
# 3. Conversational Phrasing Generator
# ===========================================================================

def format_topic_prompt_question(topic_id: str, identified_course: Optional[str] = None) -> str:
    """
    Returns the artifact-first prompt question for a topic:
    - Timetable & Fixed Commitments (C1/C3): Official timetable PDF or photo first with text fallback.
    - Course Syllabi & Modules (B4/B1): Official syllabus copy / slide deck PDFs for [Course Name] with text fallback.
    - Academic Deadlines & Exam Circulars (D1/D2/D3): Official exam timetable / circular PDF/image with text fallback.
    - Administrative Logistics (O2): Official circulars, notifications, calendar PDFs/photos first with text fallback.
    """
    t_upper = topic_id.upper()
    if t_upper in ("C1", "C3"):
        if t_upper == "C3":
            return "Do you have your official batch rotation schedule PDF or a photo of it? You can upload it directly, or just type out how your lab batches rotate."
        return "Do you have your official timetable PDF or a photo/screenshot of it? You can upload it directly, or just type out the times if that's easier."

    if t_upper in ("B4", "B1"):
        course_name = identified_course or "your courses"
        if t_upper == "B1":
            return "What subjects or courses are you taking this semester? If you have your syllabus copy or course registration PDF, you can upload it directly, or just type them out."
        return f"Do you have the official syllabus copy, course handout, or slide deck PDFs for {course_name}? Uploading the PDF lets me parse the exact units and exam weightage directly, or you can just list key topics."

    if t_upper in ("D1", "D2", "D3"):
        if t_upper == "D2":
            return "If your department released an official midterm timetable or circular PDF/image, upload it here so I can lock in the exact dates and slots without errors, or type out when your internals are scheduled."
        if t_upper == "D3":
            return "If your department released the official end-sem exam timetable or circular PDF/image, upload it here so I can lock in the exact dates and slots without errors, or type out when your finals begin."
        return "If your department released an official exam timetable or circular PDF/image, upload it here so I can lock in the exact dates and slots without errors, or type out your next 1 to 3 imminent deadlines."

    if t_upper == "O2":
        return "Do you have official circulars, fee notifications, or academic calendar PDFs/photos for upcoming deadlines? You can upload them directly, or just type out the dates."

    q_topic = QUESTION_BANK.get(t_upper)
    return q_topic.prompt_question if q_topic else "What else takes your time during the week?"


def generate_onboarding_response(
    facts: List[str],
    next_topics: List[str],
    distress_signal: bool = False,
    is_t0_complete: bool = False,
    fatigue_exit: bool = False,
    identified_course: Optional[str] = None,
) -> str:
    """
    Generates student-centric conversational phrasing:
    Sentence 1: Brief reflection acknowledging what was logged.
    Sentence 2: Natural delivery of next 1–2 target questions with an explicit skip option,
                enforcing the Artifact-First Ingestion Guardrail.
    """
    if distress_signal:
        return CRISIS_RESPONSE_TEXT

    if fatigue_exit:
        reflection = f"Got it: {', '.join(facts[:3])}." if facts else "Understood."
        return (
            f"{reflection} Let's pause the onboarding intake here. "
            "I've saved what you shared and your workspace is fully active. "
            "Feel free to talk normally, plan a study session, or ask about your schedule anytime."
        )

    if is_t0_complete:
        reflection = f"Got it: {', '.join(facts[:3])}." if facts else "All set."
        return (
            f"{reflection}\n\n"
            "We have everything needed for your foundation! "
            "I've scheduled your first conflict-free week and mapped your core context to The Web. "
            "Moving forward, you can adjust tasks, rapid-log daily habits, or trigger Grill Mode anytime."
        )

    # Attempt live Gemini phrasing if available
    llm_phrase = generate_onboarding_phrasing(facts, next_topics, identified_course)
    if llm_phrase:
        return llm_phrase

    # Sentence 1: Reflection
    if facts:
        # Deduplicate and trim facts
        unique_facts = list(dict.fromkeys(facts))[:3]
        sentence_1 = f"Got it: {', '.join(unique_facts)}."
    else:
        sentence_1 = "Got that."

    # Sentence 2: Next questions with artifact-first prioritization
    if not next_topics:
        sentence_2 = "Tell me more about what else you're juggling this semester, or say 'skip' to begin."
    elif len(next_topics) == 1:
        q_text = format_topic_prompt_question(next_topics[0], identified_course)
        sentence_2 = f"{q_text} (You can say 'skip' if you'd rather add this later)."
    else:
        q1_text = format_topic_prompt_question(next_topics[0], identified_course)
        q2_text = format_topic_prompt_question(next_topics[1], identified_course)
        sentence_2 = f"{q1_text} Also, {q2_text} (Feel free to answer one, both, or say 'skip')."

    return f"{sentence_1} {sentence_2}"


# ===========================================================================
# 4. CP-SAT Schedule Solver Trigger on T0 Completion
# ===========================================================================

def trigger_initial_cpsat_schedule(
    db: Session,
    user_id: UUID,
    horizon_start_iso: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Executes the initial CP-SAT weekly conflict-free schedule generation upon Tier 0 completion.
    """
    from app.services.conversation import run_conversational_turn
    from app.solver.scheduler import (
        DEFAULT_HORIZON_TICKS,
        datetime_to_tick,
        generate_sleep_fixed_events,
        minutes_to_ticks,
        solve_weekly_schedule,
        tick_to_datetime,
        ticks_to_minutes,
    )

    user = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if not user:
        return None

    if horizon_start_iso:
        horizon_start = datetime.datetime.fromisoformat(horizon_start_iso.replace("Z", "+00:00"))
    else:
        horizon_start = datetime.datetime.now(datetime.timezone.utc).replace(minute=0, second=0, microsecond=0)

    if horizon_start.tzinfo is None:
        horizon_start = horizon_start.replace(tzinfo=datetime.timezone.utc)

    total_horizon_ticks = DEFAULT_HORIZON_TICKS
    horizon_end = horizon_start + datetime.timedelta(minutes=ticks_to_minutes(total_horizon_ticks))

    sleep_events = generate_sleep_fixed_events(
        sleep_start=user.sleep_start,
        sleep_end=user.sleep_end,
        horizon_start=horizon_start,
        days=7,
    )

    items = (
        db.query(ScheduleItem)
        .filter(
            ScheduleItem.user_id == user_id,
            ScheduleItem.is_completed == False,
        )
        .all()
    )

    fixed_events: List[Dict[str, Any]] = list(sleep_events)
    tasks_to_solve: List[Dict[str, Any]] = []

    for item in items:
        if item.is_fixed and item.start_time and item.end_time:
            s_tick = datetime_to_tick(item.start_time, horizon_start)
            e_tick = datetime_to_tick(item.end_time, horizon_start)
            if e_tick > 0 and s_tick < total_horizon_ticks:
                fixed_events.append({"start": max(0, s_tick), "end": min(total_horizon_ticks, e_tick), "title": item.title})
        elif not item.is_fixed:
            deadline_tick = total_horizon_ticks
            if item.deadline:
                deadline_tick = max(1, min(total_horizon_ticks, datetime_to_tick(item.deadline, horizon_start)))

            dur_ticks = minutes_to_ticks(item.duration_minutes)
            tasks_to_solve.append({
                "id": str(item.id),
                "title": item.title,
                "duration": dur_ticks,
                "duration_ticks": dur_ticks,
                "priority": item.priority,
                "deadline": deadline_tick,
                "deadline_tick": deadline_tick,
            })

    solver_res = solve_weekly_schedule(
        fixed_events=fixed_events,
        flexible_tasks=tasks_to_solve,
        total_horizon_ticks=total_horizon_ticks,
    )

    # Persist scheduled times
    if solver_res.get("status") in ("OPTIMAL", "FEASIBLE"):
        scheduled_list = solver_res.get("scheduled", []) or solver_res.get("scheduled_tasks", [])
        for scheduled_task in scheduled_list:
            t_id = scheduled_task.get("id") or scheduled_task.get("task_id")
            if not t_id:
                continue
            db_item = db.query(ScheduleItem).filter(ScheduleItem.id == UUID(str(t_id))).first()
            if db_item:
                start_tick = scheduled_task["start_tick"]
                dur_ticks = scheduled_task.get("duration_ticks", minutes_to_ticks(db_item.duration_minutes))
                end_tick = scheduled_task.get("end_tick", start_tick + dur_ticks)
                db_item.start_time = tick_to_datetime(start_tick, horizon_start)
                db_item.end_time = tick_to_datetime(end_tick, horizon_start)
                db.add(db_item)
        db.commit()

    return solver_res


# ===========================================================================
# 5. Master Onboarding Orchestration Pipeline
# ===========================================================================

def process_onboarding_turn(
    db: Session,
    user_id: UUID,
    user_message: str,
    history: Optional[List[Dict[str, Any]]] = None,
    horizon_start_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Main entry point for processing a conversational turn during onboarding:
    1. Extracts answers, facts, distress signals, and entities via extract_onboarding.
    2. Crisis bypass: If distress_signal is True, aborts interview immediately.
    3. Dispatches extracted entities to stores with sync_node knowledge graph hooks.
    4. Updates coverage state in onboarding_coverage.
    5. Checks for user fatigue (3 consecutive skips).
    6. Checks for Tier 0 completion:
       - If complete, triggers CP-SAT weekly solver.
       - Delivers final confirmation and graduates conversation to standard mode.
    7. Otherwise, selects next 1–2 questions and returns natural phrasing.
    """
    # 1. Structured Gemini Extraction
    extraction = extract_onboarding(user_message, history)

    # 2. Safety & Crisis Bypass
    if extraction.distress_signal:
        return {
            "reply": CRISIS_RESPONSE_TEXT,
            "audio_signal": "NONE",
            "schedule_status": "ON_TRACK",
            "onboarding": {
                "is_active": True,
                "is_complete": False,
                "distress_signal": True,
                "extracted_topics": [],
                "extracted_facts": [],
                "t0_complete": False,
            },
            "goal_interrogation": None,
            "universal_extraction": None,
            "solver_result": None,
            "discovered_constraints": [],
            "weeding_alert": None,
        }

    # 3. Deterministic Store Routing & sync_node Hooks
    dispatch_counts = dispatch_entities(db, user_id, extraction.entities)

    # 4. Coverage State Machine Update
    updated_cov = update_coverage(db, user_id, extraction.topics)

    # Collect extracted facts
    all_facts: List[str] = []
    extracted_topic_ids: List[str] = []
    for t in extraction.topics:
        all_facts.extend(t.facts)
        extracted_topic_ids.append(t.topic_id)

    # 5. Check Fatigue
    recent_history = list(history or [])
    recent_history.append({"sender": "user", "text": user_message})
    is_fatigued = detect_fatigue(db, user_id, recent_history)

    if is_fatigued:
        reply_text = generate_onboarding_response(
            facts=all_facts,
            next_topics=[],
            fatigue_exit=True,
        )
        return {
            "reply": reply_text,
            "audio_signal": "SUCCESS_JINGLE",
            "schedule_status": "ON_TRACK",
            "onboarding": {
                "is_active": False,
                "is_complete": True,
                "fatigue_exit": True,
                "extracted_topics": extracted_topic_ids,
                "extracted_facts": all_facts,
                "t0_complete": False,
            },
            "goal_interrogation": None,
            "universal_extraction": None,
            "solver_result": None,
            "discovered_constraints": [],
            "weeding_alert": None,
        }

    # 6. Check Tier 0 Completion
    t0_done = is_tier_0_complete(db, user_id)
    solver_result = None

    if t0_done:
        # Trigger CP-SAT solver
        try:
            solver_result = trigger_initial_cpsat_schedule(db, user_id, horizon_start_iso)
        except Exception as e:
            logger.warning(f"Initial CP-SAT schedule generation error: {e}")

        reply_text = generate_onboarding_response(
            facts=all_facts,
            next_topics=[],
            is_t0_complete=True,
        )

        return {
            "reply": reply_text,
            "audio_signal": "SUCCESS_JINGLE",
            "schedule_status": "ON_TRACK",
            "onboarding": {
                "is_active": False,
                "is_complete": True,
                "t0_complete": True,
                "extracted_topics": extracted_topic_ids,
                "extracted_facts": all_facts,
            },
            "goal_interrogation": None,
            "universal_extraction": None,
            "solver_result": solver_result,
            "discovered_constraints": [],
            "weeding_alert": None,
        }

    # Identify course name if mentioned (for B4/B1 contextual phrasing)
    identified_course = None
    for ent in extraction.entities:
        if ent.target_store == "semantic_contexts" and ent.payload.get("subject"):
            identified_course = ent.payload.get("subject")
            break
        if ent.target_store == "schedule_items" and ent.payload.get("title"):
            title = ent.payload.get("title", "")
            if any(k in title.lower() for k in ["exam", "test", "quiz", "prep"]):
                identified_course = title.replace("Exam Prep", "").replace("Exam", "").replace("Test", "").strip()
                break

    if not identified_course:
        for f in all_facts:
            if "Enrolled in" in f:
                identified_course = f.replace("Enrolled in", "").strip().split(",")[0].strip()
                break
            if any(k in f.lower() for k in ["exam", "test"]):
                identified_course = f.split()[0]
                break

    # 7. Next Questions & Phrasing
    next_q_topics = get_next_questions(db, user_id, user_message)
    reply_text = generate_onboarding_response(
        facts=all_facts,
        next_topics=next_q_topics,
        is_t0_complete=False,
        identified_course=identified_course,
    )

    return {
        "reply": reply_text,
        "audio_signal": "NONE",
        "schedule_status": "ON_TRACK",
        "onboarding": {
            "is_active": True,
            "is_complete": False,
            "t0_complete": False,
            "extracted_topics": extracted_topic_ids,
            "extracted_facts": all_facts,
            "next_questions": next_q_topics,
            "dispatched_entities": dispatch_counts,
        },
        "goal_interrogation": None,
        "universal_extraction": None,
        "solver_result": None,
        "discovered_constraints": [],
        "weeding_alert": None,
    }
