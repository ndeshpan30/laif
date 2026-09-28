import datetime
from typing import Dict, Any, List, Optional
from uuid import UUID
from sqlalchemy.orm import Session

from app.models.user import UserProfile
from app.models.schedule import ScheduleItem
from app.models.telemetry import TelemetryLog
from app.models.tracker import TrackerDefinition
from app.schemas.extraction import (
    GoalInterrogationSchema,
    TelemetryDataPoint,
    UniversalExtraction,
)
from app.services.gemini_client import (
    extract_telemetry,
    generate_grill_response,
    interrogate_goal,
)
from app.services.telemetry_engine import (
    process_universal_telemetry,
)
from app.services.rag import (
    retrieve_semantic_context,
    store_episodic_constraint,
)
from app.solver.scheduler import (
    solve_weekly_schedule,
    minutes_to_ticks,
    ticks_to_minutes,
    datetime_to_tick,
    tick_to_datetime,
    generate_sleep_fixed_events,
    DEFAULT_HORIZON_TICKS,
)


def synthesize_telemetry_confirmation(data_points: List[TelemetryDataPoint]) -> str:
    """
    Safely iterates through the data_points array returned by Gemini
    to construct the confirmation string (e.g., "Logged: Water Intake (2 liters), Stress (3)").
    """
    if not data_points:
        return "No loggable data points found."

    clauses: List[str] = []
    for dp in data_points:
        entity_name = dp.entity.replace("_", " ").title()
        if dp.category == "binary_habit":
            val_bool = (
                bool(dp.value)
                if not isinstance(dp.value, str)
                else dp.value.lower() not in ("false", "0", "skipped", "none")
            )
            if not val_bool:
                clauses.append(f"{entity_name}: skipped")
            else:
                clauses.append(f"{entity_name} ✓")
        elif dp.category == "journal_note":
            clauses.append("1 note")
        else:
            val_display = (
                int(dp.value)
                if isinstance(dp.value, float) and dp.value.is_integer()
                else dp.value
            )
            if dp.unit:
                unit_clean = str(dp.unit).strip()
                if unit_clean.startswith("/"):
                    clauses.append(f"{entity_name} ({val_display}{unit_clean})")
                else:
                    clauses.append(f"{entity_name} ({val_display} {unit_clean})")
            else:
                clauses.append(f"{entity_name} ({val_display})")

    return f"Logged: {', '.join(clauses)}."


def run_conversational_turn(
    db: Session,
    user_id: UUID,
    user_message: str,
    horizon_start_iso: Optional[str] = None,
    grill_mode: bool = False,
) -> Dict[str, Any]:
    """
    Core Socratic conversational pipeline per ARCHITECTURE.md and PRD.md:
    1. Scan for episodic life constraints & store to semantic_contexts.
    2. Route according to incoming grill_mode:
       - If grill_mode is True: route to generate_grill_response.
       - If grill_mode is False: route to extract_telemetry.
    3. Safe confirmation string synthesis iterating over data_points.
    4. Socratic goal interrogation -> if ambiguous, asks max 2 questions (no schedule);
       if concrete, passes typed parameters directly to CP-SAT solver.
    5. Solver preemption handling -> if lower-priority task bumped or LAGGING, play sad trombone.
    6. Ryder Carroll BuJo migration weed check -> alerts if task migrated >= 3 times.
    """
    user = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if not user:
        # Auto-create demo user profile if not present
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

    msg_clean = user_message.strip()
    msg_lower = msg_clean.lower()

    # 1. Retrieve episodic constraints and relevant syllabus
    context = retrieve_semantic_context(db, user_id=user_id, query=msg_clean)
    guardrails = context["guardrail_text"]

    # Check for constraint pushback
    # (e.g. user asks for an all-nighter or 2 AM study session when screen work past 10 PM is forbidden)
    if ("all-nighter" in msg_lower or "2 am" in msg_lower or "11 pm" in msg_lower or "midnight" in msg_lower) and "no screen work past 10" in guardrails.lower():
        return {
            "reply": "Rejected. Your episodic guardrails prohibit screen work past 10 PM due to migraines. I will schedule this during daylight hours instead.",
            "audio_signal": "SAD_TROMBONE",
            "goal_interrogation": None,
            "universal_extraction": None,
            "solver_result": None,
            "discovered_constraints": [],
            "schedule_status": "ON_TRACK",
            "weeding_alert": None,
        }

    # Detect and store episodic constraints from conversation
    discovered_constraints: List[str] = []
    if "migraine" in msg_lower or "screen work past 10" in msg_lower:
        c_text = "Screen work past 10 PM gives me migraines"
        discovered_constraints.append(c_text)
        store_episodic_constraint(db, user_id=user_id, constraint_text=c_text)
    if "attendance" in msg_lower and "tuesday" in msg_lower:
        c_text = "85% attendance required for Tuesday 8 AM class"
        discovered_constraints.append(c_text)
        store_episodic_constraint(db, user_id=user_id, constraint_text=c_text)

    # 2. Check incoming grill_mode boolean and route accordingly:
    if grill_mode:
        context_list = [guardrails] if guardrails else []
        grill_reply = generate_grill_response(context=context_list, user_input=msg_clean)

        raw_lines = [line.strip("- *0123456789. ") for line in grill_reply.split("\n") if line.strip()]
        questions = [q for q in raw_lines if q and ("?" in q or "[GRILL" in q)]
        if not questions:
            questions = [grill_reply]

        goal_interrogation = GoalInterrogationSchema(
            is_ambiguous=True,
            clarifying_questions=questions[:2],
            task_name=f"{msg_clean[:35]} Grilling",
            priority_level=8,
            detected_constraints=discovered_constraints,
        )

        return {
            "reply": grill_reply,
            "audio_signal": "NONE",
            "goal_interrogation": goal_interrogation,
            "universal_extraction": None,
            "solver_result": None,
            "discovered_constraints": discovered_constraints,
            "schedule_status": "ON_TRACK",
            "weeding_alert": None,
        }

    # If grill_mode is False, route to extract_telemetry
    telemetry_data = extract_telemetry(msg_clean)

    # Process telemetry atomically if any loggable facts were extracted
    if telemetry_data.is_telemetry and telemetry_data.data_points:
        telemetry_res = process_universal_telemetry(
            db=db,
            user_id=user_id,
            raw_message=msg_clean,
            extraction=telemetry_data,
        )

        # Ensure response synthesizer safely iterates through data_points
        confirmation = telemetry_res.get("confirmation")
        if not confirmation or confirmation == "No loggable data points found.":
            confirmation = synthesize_telemetry_confirmation(telemetry_data.data_points)

        return {
            "reply": confirmation,
            "audio_signal": "SUCCESS_JINGLE",
            "goal_interrogation": None,
            "universal_extraction": telemetry_data,
            "solver_result": None,
            "discovered_constraints": discovered_constraints,
            "schedule_status": "ON_TRACK",
            "weeding_alert": None,
        }

    # 3. Check for Socratic Task Migration Weeding check
    weed_task = (
        db.query(ScheduleItem)
        .filter(
            ScheduleItem.user_id == user_id,
            ScheduleItem.is_completed == False,
            ScheduleItem.migration_count >= 3,
        )
        .first()
    )
    weeding_alert = None
    if weed_task and ("weed" in msg_lower or "migration" in msg_lower or "backlog" in msg_lower):
        weeding_alert = {
            "task_id": str(weed_task.id),
            "title": weed_task.title,
            "migration_count": weed_task.migration_count,
        }
        return {
            "reply": f"You've rolled '{weed_task.title}' forward {weed_task.migration_count} times over the last week. Is this actually important, or should we strike it and clear the cognitive load?",
            "audio_signal": "NONE",
            "goal_interrogation": None,
            "activity_extraction": None,
            "solver_result": None,
            "discovered_constraints": discovered_constraints,
            "schedule_status": "ON_TRACK",
            "weeding_alert": weeding_alert,
        }

    # 4. Socratic Goal Interrogation Loop (Pass 1 vs Pass 2)
    interrogation = interrogate_goal(msg_clean, contextual_guardrails=guardrails, grill_mode=grill_mode)

    # Store any constraints detected from goal interrogation
    if interrogation.detected_constraints:
        for c in interrogation.detected_constraints:
            if c not in discovered_constraints:
                discovered_constraints.append(c)
                store_episodic_constraint(db, user_id=user_id, constraint_text=c)

    # Pass 1: If ambiguous, return ONLY clarifying questions, DO NOT schedule anything
    if interrogation.is_ambiguous:
        questions = interrogation.clarifying_questions or [
            "How many units or hours do you need to cover?",
            "What is your target completion deadline?"
        ]
        clarification_text = " ".join(questions)
        return {
            "reply": clarification_text,
            "audio_signal": "NONE",
            "goal_interrogation": interrogation,
            "universal_extraction": None,
            "solver_result": None,
            "discovered_constraints": discovered_constraints,
            "schedule_status": "ON_TRACK",
            "weeding_alert": None,
        }

    # Pass 2: Parameter Emission -> Commit to Schedule and run CP-SAT Solver
    if horizon_start_iso:
        horizon_start = datetime.datetime.fromisoformat(horizon_start_iso.replace("Z", "+00:00"))
    else:
        horizon_start = datetime.datetime.now(datetime.timezone.utc).replace(minute=0, second=0, microsecond=0)

    if horizon_start.tzinfo is None:
        horizon_start = horizon_start.replace(tzinfo=datetime.timezone.utc)

    task_name = interrogation.task_name or "Scheduled Task"
    duration_mins = interrogation.session_duration_minutes or 60
    priority = interrogation.priority_level or 5
    deadline_dt = None
    if interrogation.deadline_iso:
        try:
            deadline_dt = datetime.datetime.fromisoformat(interrogation.deadline_iso.replace("Z", "+00:00"))
            if deadline_dt.tzinfo is None:
                deadline_dt = deadline_dt.replace(tzinfo=datetime.timezone.utc)
            if deadline_dt <= horizon_start:
                deadline_dt = horizon_start + datetime.timedelta(hours=2)
        except Exception:
            deadline_dt = horizon_start + datetime.timedelta(days=3)
    else:
        deadline_dt = horizon_start + datetime.timedelta(days=3)

    is_exam = "exam" in task_name.lower() or priority >= 9
    category = "exam" if is_exam else "study_session"

    new_item = ScheduleItem(
        user_id=user_id,
        title=task_name,
        category=category,
        duration_minutes=duration_mins,
        priority=priority,
        is_fixed=False,
        deadline=deadline_dt,
    )
    db.add(new_item)
    db.commit()
    db.refresh(new_item)

    # Sync goal / exam into knowledge graph (D1, D3, D6)
    try:
        from app.services.knowledge_graph import sync_node
        if category in ("exam", "lab", "project", "goal", "milestone") or is_exam:
            sync_node(db, user_id, "schedule_items", new_item.id)
    except Exception as e:
        logger.warning(f"Failed to sync goal/exam node into knowledge graph: {e}")

    # Execute CP-SAT re-solve
    total_horizon_ticks = DEFAULT_HORIZON_TICKS
    horizon_end = horizon_start + datetime.timedelta(minutes=ticks_to_minutes(total_horizon_ticks))

    sleep_events = generate_sleep_fixed_events(
        sleep_start=user.sleep_start,
        sleep_end=user.sleep_end,
        horizon_start=horizon_start,
        days=7,
    )

    items = db.query(ScheduleItem).filter(
        ScheduleItem.user_id == user_id,
        ScheduleItem.is_completed == False,
    ).all()

    fixed_events = list(sleep_events)
    flexible_tasks = []

    for item in items:
        dur_ticks = minutes_to_ticks(item.duration_minutes)
        if item.is_fixed and item.start_time and item.end_time:
            st = item.start_time if item.start_time.tzinfo else item.start_time.replace(tzinfo=horizon_start.tzinfo)
            et = item.end_time if item.end_time.tzinfo else item.end_time.replace(tzinfo=horizon_start.tzinfo)
            if et <= horizon_start or st >= horizon_end:
                continue
            s_tick = max(0, datetime_to_tick(st, horizon_start))
            e_tick = min(total_horizon_ticks, datetime_to_tick(et, horizon_start))
            if e_tick > s_tick:
                fixed_events.append({"id": str(item.id), "start": s_tick, "end": e_tick})
        else:
            dl_tick = total_horizon_ticks
            if item.deadline:
                dl = item.deadline if item.deadline.tzinfo else item.deadline.replace(tzinfo=horizon_start.tzinfo)
                dl_tick = min(total_horizon_ticks, max(0, datetime_to_tick(dl, horizon_start)))
            flexible_tasks.append({
                "id": str(item.id),
                "duration": dur_ticks,
                "priority": item.priority,
                "deadline": dl_tick,
            })

    solver_res = solve_weekly_schedule(
        fixed_events=fixed_events,
        flexible_tasks=flexible_tasks,
        total_horizon_ticks=total_horizon_ticks,
    )

    # Update item timestamps in DB based on solver output
    sched_map = {t["id"]: t["start_tick"] for t in solver_res["scheduled"]}
    bumped_titles = []

    for item in items:
        it_id = str(item.id)
        if it_id in sched_map:
            st_dt = tick_to_datetime(sched_map[it_id], horizon_start)
            item.start_time = st_dt
            item.end_time = st_dt + datetime.timedelta(minutes=item.duration_minutes)
        elif it_id in solver_res["bumped"]:
            item.start_time = None
            item.end_time = None
            item.migration_count = (item.migration_count or 0) + 1
            bumped_titles.append(item.title)

    db.commit()

    has_bumped = len(solver_res["bumped"]) > 0
    audio_signal = "SAD_TROMBONE" if (has_bumped or solver_res["schedule_status"] == "LAGGING") else "SUCCESS_JINGLE"

    if has_bumped:
        bumped_desc = ", ".join(bumped_titles)
        reply = f"Urgent item '{task_name}' placed (Priority {priority}). Pre-empted: {bumped_desc}."
    else:
        reply = f"Placed '{task_name}' ({duration_mins}m) deterministically with CP-SAT. Zero calendar conflicts."

    return {
        "reply": reply,
        "audio_signal": audio_signal,
        "goal_interrogation": interrogation,
        "universal_extraction": None,
        "solver_result": solver_res,
        "discovered_constraints": discovered_constraints,
        "schedule_status": solver_res["schedule_status"],
        "weeding_alert": None,
    }
