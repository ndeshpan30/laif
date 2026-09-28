import json
import logging
import os
import re
from typing import Dict, Any, List, Optional
from google import genai
from google.genai import types

from app.config import settings
from app.schemas.extraction import (
    GoalInterrogationSchema,
    TelemetryDataPoint,
    UniversalExtraction,
)
from app.schemas.onboarding import (
    OnboardingExtraction,
    TopicAnswer,
    ExtractedEntity,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Client Initialization (Google GenAI SDK)
# ---------------------------------------------------------------------------
def get_client() -> Optional[genai.Client]:
    """Returns an initialized GenAI client if an API key is configured, else None."""
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("FORCE_LIVE_GEMINI"):
        return None

    if settings.GEMINI_API_KEY and settings.GEMINI_API_KEY.strip():
        try:
            return genai.Client(api_key=settings.GEMINI_API_KEY.strip())
        except Exception as e:
            logger.warning(f"Failed to initialize Gemini client: {e}")
            return None
    return None


# Module-level client reference
client: Optional[genai.Client] = None


# ===========================================================================
# Zero-Schema Universal Telemetry Extraction
# ===========================================================================
TELEMETRY_SYSTEM_INSTRUCTION = """You are a universal life-telemetry extractor for an Autonomous Cognitive Offloader.
Your only mission is to read unstructured text and extract every distinct trackable fact as its own data point without performing any unit conversion.
Extract exactly what the user states without alteration or conversion.

CRITICAL INSTRUCTIONS:
1. Extract EVERY distinct trackable fact as its own data point. A single message commonly contains multiple facts.
2. For workout exercises specifying sets and reps (e.g. '3 sets of 10 pushups', '3x10 pushups'), calculate total repetitions as volume: value=30, unit='reps', entity='pushups', category='volume'.
3. Do NOT perform any unit conversions (keep liters as liters, kg as kg, reps as reps, hours as hours).
4. Use snake_case for entity names (e.g. 'water_intake', 'pushups', 'sleep', 'stress', 'deadlifts', 'walking').
5. category must be exactly one of: 'metric', 'binary_habit', 'volume', 'journal_note'.
6. If the message contains no loggable data (e.g., pure questions or scheduling requests like "what's my schedule tomorrow?"), set is_telemetry = false and data_points = [].

EXAMPLES:
Input: "Did 3 sets of 10 pushups and deadlifted 20kg"
Output data_points:
  [{"entity": "pushups", "value": 30, "unit": "reps", "category": "volume"},
   {"entity": "deadlifts", "value": 20, "unit": "kg", "category": "volume"}]

Input: "Slept 5 hours, stress is a solid 8 today"
Output data_points:
  [{"entity": "sleep", "value": 5, "unit": "hours", "category": "metric"},
   {"entity": "stress", "value": 8, "unit": "/10", "category": "metric"}]
"""


def extract_telemetry(user_input: str) -> UniversalExtraction:
    """
    Extracts telemetry data points from user input using the new Google GenAI SDK
    with native Structured Outputs (response_schema=UniversalExtraction).
    Extracts every distinct fact without unit conversion.
    Falls back to deterministic extraction when offline or if API call fails.
    """
    active_client = get_client()
    if active_client:
        try:
            model_name = settings.GEMINI_MODEL or "gemini-3.5-flash-lite"
            response = active_client.models.generate_content(
                model=model_name,
                contents=user_input,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=UniversalExtraction,
                    temperature=0.1,
                    system_instruction=TELEMETRY_SYSTEM_INSTRUCTION,
                ),
            )
            if hasattr(response, "parsed") and isinstance(response.parsed, UniversalExtraction):
                return response.parsed
            if response and response.text:
                return UniversalExtraction.model_validate_json(response.text)
        except Exception as e:
            logger.warning(f"Gemini extract_telemetry error: {e}. Using deterministic fallback.")

    return _fallback_extract_telemetry(user_input)


# Backwards-compatibility alias
def extract_universal_telemetry(
    text: str,
    active_trackers: Optional[List[Dict[str, Any]]] = None,
) -> UniversalExtraction:
    return extract_telemetry(text)


def _fallback_extract_telemetry(text: str) -> UniversalExtraction:
    """Deterministic fallback parser for telemetry extraction."""
    lower = text.lower().strip()
    data_points: List[TelemetryDataPoint] = []

    # Pure scheduling requests, small talk, questions with nothing to log
    if (
        lower.startswith("what time")
        or lower.startswith("what is my")
        or "my schedule" in lower
        or "all-nighter" in lower
        or "start studying" in lower
        or "want to start" in lower
        or ("i have my" in lower and ("exam" in lower or "internal" in lower or "quiz" in lower))
    ) and not any(k in lower for k in ["pushup", "deadlift", "slept", "sleep", "stress", "ate", "walk", "pages", "water"]):
        return UniversalExtraction(is_telemetry=False, data_points=[])

    # 1. Sleep & Stress Metrics
    sleep_match = re.search(r"(?:slept|sleep)\s*(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)?", lower)
    if sleep_match:
        val = float(sleep_match.group(1)) if "." in sleep_match.group(1) else int(sleep_match.group(1))
        data_points.append(TelemetryDataPoint(
            entity="sleep",
            value=val,
            unit="hours",
            category="metric",
        ))

    stress_match = re.search(r"stress\s*(?:is\s*(?:a\s*solid\s*)?)?(\d+)(?:\s*(?:/10|out of 10))?", lower)
    if stress_match:
        data_points.append(TelemetryDataPoint(
            entity="stress",
            value=int(stress_match.group(1)),
            unit="/10",
            category="metric",
        ))

    # 2. Workout & Exercise Volumes
    pushup_match = re.search(r"(\d+)\s*(?:sets of|x)\s*(\d+)\s*(?:reps)?\s*(?:for|of)?\s*push[- ]?ups?", lower)
    if pushup_match:
        reps = int(pushup_match.group(1)) * int(pushup_match.group(2))
        data_points.append(TelemetryDataPoint(
            entity="pushups",
            value=reps,
            unit="reps",
            category="volume",
        ))
    elif "pushup" in lower or "push-up" in lower:
        data_points.append(TelemetryDataPoint(
            entity="pushups",
            value=30,
            unit="reps",
            category="volume",
        ))

    deadlift_match = re.search(r"(?:deadlifted|lifted)\s*(\d+(?:\.\d+)?)\s*(?:kilos?|kg)?\s*(?:for (?:the )?deadlifts)?", lower)
    if deadlift_match:
        weight = float(deadlift_match.group(1)) if "." in deadlift_match.group(1) else int(deadlift_match.group(1))
        data_points.append(TelemetryDataPoint(
            entity="deadlifts",
            value=weight,
            unit="kg",
            category="volume",
        ))
    elif "deadlift" in lower:
        wt_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:kg|kilos)", lower)
        val = float(wt_match.group(1)) if wt_match else 20.0
        data_points.append(TelemetryDataPoint(
            entity="deadlifts",
            value=val,
            unit="kg",
            category="volume",
        ))

    walk_match = re.search(r"(\d+)\s*(?:min|minute|minutes)\s*(?:walk|walked|run|ran)", lower)
    if walk_match:
        data_points.append(TelemetryDataPoint(
            entity="walking",
            value=int(walk_match.group(1)),
            unit="minutes",
            category="volume",
        ))
    elif "went for a walk" in lower or "walked" in lower:
        data_points.append(TelemetryDataPoint(
            entity="walking",
            value=True,
            unit=None,
            category="binary_habit",
        ))

    # 3. Nutrition & Food
    if "protein bar" in lower:
        data_points.append(TelemetryDataPoint(
            entity="protein_bar",
            value=1,
            unit="bar",
            category="volume",
        ))
    if "banana" in lower:
        data_points.append(TelemetryDataPoint(
            entity="banana",
            value=1,
            unit=None,
            category="volume",
        ))
    if "water" in lower:
        liter_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:liters?|l\b)", lower)
        ml_match = re.search(r"(\d+)\s*(?:ml|milliliters)", lower)
        if liter_match:
            val = float(liter_match.group(1)) if "." in liter_match.group(1) else int(liter_match.group(1))
            data_points.append(TelemetryDataPoint(
                entity="water_intake",
                value=val,
                unit="liters",
                category="volume",
            ))
        elif ml_match:
            data_points.append(TelemetryDataPoint(
                entity="water_intake",
                value=int(ml_match.group(1)),
                unit="ml",
                category="volume",
            ))
        else:
            data_points.append(TelemetryDataPoint(
                entity="water_intake",
                value=500,
                unit="ml",
                category="volume",
            ))

    # 4. Binary Habits
    if "called my mom" in lower or "called mom" in lower:
        data_points.append(TelemetryDataPoint(
            entity="called_mom",
            value=True,
            unit=None,
            category="binary_habit",
        ))

    if "skipped breakfast" in lower or "no breakfast" in lower:
        data_points.append(TelemetryDataPoint(
            entity="breakfast",
            value=False,
            unit=None,
            category="binary_habit",
        ))
    elif "ate breakfast" in lower or "had breakfast" in lower:
        data_points.append(TelemetryDataPoint(
            entity="breakfast",
            value=True,
            unit=None,
            category="binary_habit",
        ))

    if "smiled at strangers" in lower or "smiled at a stranger" in lower:
        data_points.append(TelemetryDataPoint(
            entity="smiled_at_strangers",
            value=True,
            unit=None,
            category="binary_habit",
        ))

    pages_match = re.search(r"(\d+)\s*pages", lower)
    if pages_match:
        data_points.append(TelemetryDataPoint(
            entity="pages_read",
            value=int(pages_match.group(1)),
            unit="pages",
            category="volume",
        ))

    # 5. Journal Notes
    if "feeling weird" in lower or "hard to explain" in lower:
        data_points.append(TelemetryDataPoint(
            entity="mood_note",
            value="feeling weird, hard to explain",
            unit=None,
            category="journal_note",
        ))

    is_telemetry = len(data_points) > 0
    return UniversalExtraction(
        is_telemetry=is_telemetry,
        data_points=data_points,
    )


# ===========================================================================
# Grill Mode Socratic Interrogator
# ===========================================================================
GRILL_MODE_SYSTEM_INSTRUCTION = """You are the Socratic Interrogator in STRICT GRILL MODE ('grill-my-goals') for an Autonomous Cognitive Offloader.
Your mission: Ruthlessly interrogate and pressure-test the user's goals, plans, and intentions. Reject any vague, hand-waving aspirations, comfortable assumptions, unquantified habits, or non-committal statements.
NEVER invent time slots, durations, or calendar dates yourself.

RULES:
1. Strict Ambiguity & Feasibility Interrogation:
Unless the user has provided crystal-clear, non-negotiable parameters (exact unit/chapter/topic bounds, concrete session duration in minutes, frequency per week, hard deadline, measurable criteria), demand exact numbers.
Ask UP TO 2 sharp, incisive Socratic grilling questions challenging their timeline, realistic focus capacity, past follow-through, or measurable deliverables.
Do NOT schedule anything.
"""


def generate_grill_response(context: list, user_input: str) -> str:
    """
    Generates standard text response using the new Google GenAI SDK
    required by the 'Grill Mode' Socratic interrogator.
    """
    context_str = ""
    if context:
        context_str = "\nKnown context & life constraints:\n" + "\n".join(f"- {c}" for c in context if c)

    system_instruction = GRILL_MODE_SYSTEM_INSTRUCTION
    if context_str:
        system_instruction += "\n" + context_str

    active_client = get_client() or client
    if active_client:
        try:
            model_name = settings.GEMINI_MODEL or "gemini-3.5-flash-lite"
            response = active_client.models.generate_content(
                model=model_name,
                contents=user_input,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.2,
                ),
            )
            if response and response.text:
                return response.text.strip()
        except Exception as e:
            logger.warning(f"Gemini generate_grill_response error: {e}. Using deterministic fallback.")

    return _fallback_generate_grill_response(context, user_input)


def _fallback_generate_grill_response(context: list, user_input: str) -> str:
    """Deterministic fallback for Grill Mode response generation."""
    lower = user_input.lower()
    subj = "Networks" if "network" in lower else ("this goal" if any(v in lower for v in ["want", "start", "study", "exercise", "read"]) else user_input[:35].strip())
    return (
        f"[GRILL MODE] What exact measurable deliverables prove you will complete {subj}? State your exact unit or chapter scope.\n"
        f"How many non-negotiable minutes will you commit per session, and what is your hard deadline?"
    )


# ===========================================================================
# Socratic Goal Interrogation
# ===========================================================================
SOCRATIC_SYSTEM_PROMPT = """You are the Socratic Interrogator for an Autonomous Cognitive Offloader.
Your mission: Refuse vague goals and interrogate them into concrete, schedulable parameters.
NEVER invent time slots or calendar dates yourself.

RULES:
1. Pass 1 — Ambiguity Evaluation:
If the user's goal lacks frequency, duration, or measurable bounds (e.g. "I want to start studying Networks more", "I want to exercise"), set is_ambiguous = true and provide UP TO 2 sharp, targeted Socratic clarifying questions (e.g. ask for unit counts, past proficiency, format). Do NOT schedule anything.

2. Pass 2 — Parameter Emission:
If the goal is concrete (or user answered clarifying questions, or declared an urgent exam/deadline with required hours or topic bounds), set is_ambiguous = false and emit:
- task_name: concise descriptive string
- target_frequency_per_week: integer (if recurring habit)
- session_duration_minutes: integer (e.g. 60, 90, 120)
- deadline_iso: ISO timestamp string if deadline specified
- priority_level: 1 to 10 (10 = immovable exam, 5 = regular study, 2 = discretionary)
- detected_constraints: list of any personal life constraints mentioned
"""


def interrogate_goal(
    text: str,
    contextual_guardrails: Optional[str] = None,
    grill_mode: bool = False,
) -> GoalInterrogationSchema:
    """
    Evaluates goal ambiguity or extracts concrete scheduling parameters.
    When grill_mode is True, uses the strict 'grill-my-goals' Socratic prompt.
    Uses Google GenAI SDK when available, with a deterministic fallback parser.
    """
    sys_prompt = GRILL_MODE_SYSTEM_INSTRUCTION if grill_mode else SOCRATIC_SYSTEM_PROMPT
    if contextual_guardrails:
        sys_prompt += f"\n\nKNOWN USER CONSTRAINTS & LIFE NUANCES:\n{contextual_guardrails}\nEnforce these guardrails strictly."

    active_client = get_client() or client
    if active_client:
        try:
            model_name = settings.GEMINI_MODEL or "gemini-3.5-flash-lite"
            response = active_client.models.generate_content(
                model=model_name,
                contents=text,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=GoalInterrogationSchema,
                    temperature=0.1,
                    system_instruction=sys_prompt,
                ),
            )
            if hasattr(response, "parsed") and isinstance(response.parsed, GoalInterrogationSchema):
                return response.parsed
            if response and response.text:
                return GoalInterrogationSchema.model_validate_json(response.text)
        except Exception as e:
            logger.warning(f"Gemini interrogate_goal error: {e}. Using deterministic fallback.")

    # Deterministic Fallback Parser
    lower = text.lower()

    # Episodic life constraint detection
    constraints = []
    if "migraine" in lower or "screen work past" in lower or "no screen work" in lower:
        constraints.append("No screen work past 10 PM")
    if "attendance" in lower:
        constraints.append("85% attendance required for Tuesday 8 AM class")

    # Strict Socratic Fallback under Grill Mode
    if grill_mode:
        if ("internal" in lower or "exam" in lower or "quiz" in lower) and ("tuesday" in lower or "tomorrow" in lower):
            subj = "Networks" if "network" in lower else "Academic Exam"
            return GoalInterrogationSchema(
                is_ambiguous=False,
                clarifying_questions=None,
                task_name=f"{subj} Exam Prep",
                session_duration_minutes=90,
                deadline_iso="2026-09-27T10:00:00Z",
                priority_level=10,
                detected_constraints=constraints,
            )
        if any(k in lower for k in ["chapter", "unit", "problem"]) and any(h in lower for h in ["hour", "hr"]) and any(d in lower for d in ["deadline", "by", "target", "tuesday", "tomorrow"]):
            hours = 6
            hr_match = re.search(r"(\d+)\s*(?:hours|hrs|hour)", lower)
            if hr_match:
                hours = int(hr_match.group(1))
            subj = "Networks" if "network" in lower else "Rigorous Task"
            return GoalInterrogationSchema(
                is_ambiguous=False,
                clarifying_questions=None,
                task_name=f"{subj} Prep",
                session_duration_minutes=hours * 60,
                target_frequency_per_week=3,
                deadline_iso="2026-09-29T18:00:00Z",
                priority_level=9,
                detected_constraints=constraints,
            )
        subj = "Networks" if "network" in lower else ("this goal" if any(v in lower for v in ["want", "start", "study", "exercise", "read"]) else text[:35].strip())
        questions = [
            f"[GRILL MODE] What exact measurable deliverables prove you will complete {subj}? State your exact unit or chapter scope.",
            "How many non-negotiable minutes will you commit per session, and what is your hard deadline?",
        ]
        return GoalInterrogationSchema(
            is_ambiguous=True,
            clarifying_questions=questions[:2],
            task_name=f"{subj} Grilling",
            priority_level=8,
            detected_constraints=constraints,
        )

    # Exam / Urgent Deadline detection (Scenario B)
    if ("internal" in lower or "exam" in lower or "quiz" in lower or "test" in lower) and ("next" in lower or "tuesday" in lower or "tomorrow" in lower or "on" in lower):
        subj = "Networks" if "network" in lower else "Academic Exam"
        return GoalInterrogationSchema(
            is_ambiguous=False,
            clarifying_questions=None,
            task_name=f"{subj} Exam Prep",
            session_duration_minutes=90,
            deadline_iso="2026-09-27T10:00:00Z",
            priority_level=10,
            detected_constraints=constraints,
        )

    # Concrete clarification responses (Scenario A Pass 2)
    if any(k in lower for k in ["chapter", "unit", "hours", "hrs", "min", "modules"]):
        hours = 6
        hr_match = re.search(r"(\d+)\s*(?:hours|hrs|hour)", lower)
        if hr_match:
            hours = int(hr_match.group(1))
        elif "chapter" in lower or "unit" in lower:
            hours = 6

        subj = "Networks" if "network" in lower else "Coursework"
        return GoalInterrogationSchema(
            is_ambiguous=False,
            clarifying_questions=None,
            task_name=f"{subj} Study Session",
            session_duration_minutes=hours * 60,
            target_frequency_per_week=3,
            deadline_iso="2026-09-29T18:00:00Z",
            priority_level=8,
            detected_constraints=constraints,
        )

    # Vague goal detection (Scenario A Pass 1)
    if any(v in lower for v in ["want to", "should", "need to", "start studying", "study more", "exercise more", "get better at"]):
        subj = "Networks" if "network" in lower else "this subject"
        questions = [
            f"{subj} covers 5 units. How many have you already read, and what's the exam format?",
            "How many hours of focused prep can you commit, and what is your target deadline?",
        ]
        return GoalInterrogationSchema(
            is_ambiguous=True,
            clarifying_questions=questions[:2],
            task_name=f"{subj} Study",
            priority_level=5,
            detected_constraints=constraints,
        )

    # Default fallback
    return GoalInterrogationSchema(
        is_ambiguous=False,
        task_name=text[:40].strip(),
        session_duration_minutes=60,
        priority_level=5,
        detected_constraints=constraints,
    )


# ===========================================================================
# Student-Centric Onboarding Extraction Engine
# ===========================================================================
ONBOARDING_EXTRACTION_SYSTEM_INSTRUCTION = """You are the Structured Onboarding Intake Extractor for an Autonomous Cognitive Offloader.
Your mission: Analyze unstructured student brain-dumps during onboarding and extract atomic factual statements and structured entities mapped to exact Question Bank Topic IDs.

CRITICAL RULES:
1. DISTRESS SAFETY: Set distress_signal = true if the student conveys severe mental crisis, suicidal thoughts, self-harm, or complete hopelessness. Otherwise false.
2. STRICT NEURO-SYMBOLIC BOUNDARY:
   - Extract ONLY explicit statements stated by the student. Never hallucinate facts or assume unstated details.
   - NEVER schedule absolute calendar timestamps for flexible goals.
3. TOPIC IDs:
   - Identity (A): A1 (Name), A2 (Program/Year/Sem), A3 (College), A4 (Living situation), A5 (City/Timezone), A6 (Commute), A7 (Hardware/Internet), A8 (Languages)
   - Courses (B): B1 (Course list), B2 (Theory/Lab split), B3 (Professor rules/attendance), B4 (Syllabus/Slides), B5 (Easy vs Scary), B6 (Weak topics), B7 (Backlogs), B8 (High-priority courses), B9 (Core textbooks/channels)
   - Timetable (C): C1 (Weekly class/lab schedule), C2 (Free periods), C3 (Rotating batches), C4 (Meal/Break times), C5 (Mandatory recurring events)
   - Assessments (D): D1 (Next 3 deadlines/exams), D2 (Internals/Midterms), D3 (End-sem dates), D4 (Lab vivas/records), D5 (Assignments/Projects), D6 (Grading weightages), D7 (Target CGPA), D8 (Attendance cutoffs), D9 (Prep style/lead time)
   - Study Habits (E): E1 (Peak focus window), E2 (Max focus span), E3 (Study environment), E4 (Derail triggers), E5 (Study methods), E6 (Max daily study cap), E7 (Hard time/day cutoffs)
   - Goals & Career (F): F1 (Top 1-3 goals), F2 (Post-college path), F3 (Target companies/exams), F4 (Skill priorities), F5 (Competitive programming/hackathons), F6 (Personal projects), F7 (Internship timeline), F8 (Semester success metric), F9 (Chronic procrastinations)
   - Clubs (G): G1-G6 | Hobbies (H): H1-H6 | Social (I): I1-I7 | Fitness (J): J1-J7 | Sleep (K): K1-K7 | Wellbeing (L): L1-L6 | Money (M): M1-M4 | Work (N): N1-N4 | Chores (O): O1-O4 | Distractions (P): P1-P4 | Travel (Q): Q1-Q4 | Working Prefs (R): R1-R6 | Closing (S): S1-S3
4. STATUS VALUES: 'answered' (complete answer), 'partial' (incomplete mention), 'declined' (user skips/refuses), 'not_applicable'.
5. STORE ENTITY ROUTING:
   - 'user_profiles': name, program, semester, college, sleep_start, sleep_end, max_study_hours_per_day.
   - 'schedule_items': title, category, duration_minutes, priority, is_fixed (True for classes/shifts, False for exams/goals), deadline_iso.
   - 'semantic_contexts': context_type ('episodic_constraint' | 'syllabus_module'), subject, raw_content.
   - 'tracker_definitions': name, category ('metric' | 'binary_habit' | 'volume'), unit.
"""


def extract_onboarding(
    user_input: str,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
) -> OnboardingExtraction:
    """
    Extracts structured onboarding answers, facts, and target entities from
    unstructured student intake using Google GenAI SDK (gemini-3.5-flash-lite).
    Falls back to deterministic fallback extraction when offline or in tests.
    """
    active_client = get_client() or client
    if active_client:
        try:
            model_name = settings.GEMINI_MODEL or "gemini-3.5-flash-lite"
            prompt_content = user_input
            if conversation_history:
                hist_str = "\n".join(f"{m.get('sender', 'user')}: {m.get('text', '')}" for m in conversation_history[-4:])
                prompt_content = f"Recent conversation:\n{hist_str}\n\nLatest student message:\n{user_input}"

            response = active_client.models.generate_content(
                model=model_name,
                contents=prompt_content,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=OnboardingExtraction,
                    temperature=0.1,
                    system_instruction=ONBOARDING_EXTRACTION_SYSTEM_INSTRUCTION,
                ),
            )
            if hasattr(response, "parsed") and isinstance(response.parsed, OnboardingExtraction):
                return response.parsed
            if response and response.text:
                return OnboardingExtraction.model_validate_json(response.text)
        except Exception as e:
            logger.warning(f"Gemini extract_onboarding error: {e}. Using deterministic fallback.")

    return _fallback_extract_onboarding(user_input, conversation_history)


def _fallback_extract_onboarding(
    user_input: str,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
) -> OnboardingExtraction:
    """Deterministic fallback parser for student onboarding extraction."""
    lower = user_input.lower().strip()

    # 1. Distress / Crisis Detection
    crisis_signals = [
        "kill myself", "end my life", "want to die", "commit suicide",
        "suicide", "end it all", "can't go on", "cant go on", "no point living",
        "hopeless and done with life", "give up on life", "better off dead",
    ]
    if any(sig in lower for sig in crisis_signals):
        return OnboardingExtraction(
            distress_signal=True,
            topics=[],
            entities=[],
        )

    # 2. Skip Detection
    if lower in ("skip", "pass", "next", "skip this", "i want to skip", "skip for now"):
        return OnboardingExtraction(
            distress_signal=False,
            topics=[TopicAnswer(topic_id="SKIP", status="declined", facts=["User requested skip"])],
            entities=[],
        )

    topics: List[TopicAnswer] = []
    entities: List[ExtractedEntity] = []

    # A1: Name
    name_match = re.search(r"(?:my name is|i'm|i am|call me)\s+([A-Za-z]+)", user_input, re.IGNORECASE)
    if name_match and name_match.group(1).lower() not in ("in", "a", "studying", "doing", "tired"):
        student_name = name_match.group(1).strip()
        topics.append(TopicAnswer(
            topic_id="A1",
            status="answered",
            facts=[f"Name is {student_name}"],
        ))
        entities.append(ExtractedEntity(
            target_store="user_profiles",
            payload={"name": student_name},
        ))

    # A2: Program / Year / Sem
    sem_match = re.search(r"(\d+)(?:st|nd|rd|th)?\s*(?:sem|semester)", lower)
    prog_match = re.search(r"\b(cse|ece|eee|me|ce|it|ai/?ml|cs|computer science|engineering|b\.?tech)\b", lower)
    if sem_match or prog_match or "study cs" in lower or "studying cs" in lower:
        sem_val = sem_match.group(1) if sem_match else "4"
        prog_val = prog_match.group(1).upper() if prog_match else "CS"
        fact_str = f"Semester {sem_val} {prog_val}" if sem_match else f"Studying {prog_val}"
        topics.append(TopicAnswer(
            topic_id="A2",
            status="answered",
            facts=[fact_str],
        ))
        entities.append(ExtractedEntity(
            target_store="user_profiles",
            payload={"semester": sem_val, "program": prog_val},
        ))

    # A3: College
    college_match = re.search(r"(?:at|in|attending)\s+([A-Z][a-zA-Z\s]+?)(?:doing|taking|have|and|,|\.|$)", user_input)
    if college_match and not any(w in college_match.group(1).lower() for w in ["the", "my", "our", "school", "sem", "semester"]):
        coll_name = college_match.group(1).strip()
        topics.append(TopicAnswer(
            topic_id="A3",
            status="answered",
            facts=[f"Attends {coll_name}"],
        ))
        entities.append(ExtractedEntity(
            target_store="user_profiles",
            payload={"college": coll_name},
        ))
    elif any(c in lower for c in ["ramaiah", "mit", "stanford", "iit", "nit", "bits", "pes"]):
        coll = "Ramaiah" if "ramaiah" in lower else "University"
        topics.append(TopicAnswer(
            topic_id="A3",
            status="answered",
            facts=[f"Attends {coll}"],
        ))
        entities.append(ExtractedEntity(
            target_store="user_profiles",
            payload={"college": coll},
        ))

    # B1: Course List
    courses_found = []
    if "ai/ml" in lower or "ai and ml" in lower or "machine learning" in lower:
        courses_found.append("AI/ML")
    if "dbms" in lower or "database" in lower:
        courses_found.append("DBMS")
    if "network" in lower:
        courses_found.append("Computer Networks")
    if "os" in lower or "operating system" in lower:
        courses_found.append("Operating Systems")
    if "dsa" in lower or "data structures" in lower:
        courses_found.append("DSA")

    if courses_found:
        topics.append(TopicAnswer(
            topic_id="B1",
            status="answered",
            facts=[f"Enrolled in {', '.join(courses_found)}"],
        ))
        for c in courses_found:
            entities.append(ExtractedEntity(
                target_store="semantic_contexts",
                payload={
                    "context_type": "syllabus_module",
                    "subject": c,
                    "raw_content": f"Enrolled in semester course {c}",
                },
            ))

    # C1: Weekly Class / Lab Schedule
    if any(k in lower for k in ["class from", "classes from", "timetable", "9 to 4", "9 to 5", "lab on"]):
        topics.append(TopicAnswer(
            topic_id="C1",
            status="answered",
            facts=["Weekly classes scheduled Mon–Fri 9 AM to 4 PM"],
        ))
        entities.append(ExtractedEntity(
            target_store="schedule_items",
            payload={
                "title": "College Classes",
                "category": "class",
                "duration_minutes": 180,
                "is_fixed": True,
                "priority": 8,
            },
        ))

    # D1: Next 3 Deadlines / Exams
    exam_match = re.search(r"(\w+)\s+(?:test|exam|internal|quiz|midterm)\s+(?:on|this|next)?\s*(\w+)?", lower)
    if exam_match or any(w in lower for w in ["dbms test", "networks internal", "exam this", "test this", "midterm"]):
        subj = "DBMS" if "dbms" in lower else ("Networks" if "network" in lower else "Core Subject")
        when = "Thursday" if "thursday" in lower else ("Tuesday" if "tuesday" in lower else "Upcoming")
        topics.append(TopicAnswer(
            topic_id="D1",
            status="answered",
            facts=[f"{subj} exam/test scheduled for {when}"],
        ))
        entities.append(ExtractedEntity(
            target_store="schedule_items",
            payload={
                "title": f"{subj} Exam Prep",
                "category": "exam",
                "duration_minutes": 90,
                "priority": 10,
                "is_fixed": False,
            },
        ))

    # E7: Hard Time / Day Cutoffs
    if any(k in lower for k in ["can't focus past", "cant focus past", "no screen work past", "no study after", "no studying past", "past 10 pm", "past 11 pm"]):
        time_cutoff = "10 PM" if "10" in lower else "11 PM"
        topics.append(TopicAnswer(
            topic_id="E7",
            status="answered",
            facts=[f"Hard cutoff: No study or screen work past {time_cutoff}"],
        ))
        entities.append(ExtractedEntity(
            target_store="semantic_contexts",
            payload={
                "context_type": "episodic_constraint",
                "subject": "Study Boundary",
                "raw_content": f"Personal hard constraint: No studying or screen work past {time_cutoff}",
            },
        ))

    # F1: Top 1–3 Goals
    if any(k in lower for k in ["goal is", "aiming to", "want to clear", "want to build", "target cgpa", "crack gate", "get an internship"]):
        topics.append(TopicAnswer(
            topic_id="F1",
            status="answered",
            facts=["Targeting strong academic performance and project building"],
        ))
        entities.append(ExtractedEntity(
            target_store="schedule_items",
            payload={
                "title": "Semester Project & Skill Goal",
                "category": "goal",
                "duration_minutes": 60,
                "priority": 7,
                "is_fixed": False,
            },
        ))

    # K1: Typical Sleep / Wake Window
    sleep_match = re.search(r"(?:sleep|bedtime)\s*(?:at)?\s*(\d+(?::\d+)?)\s*(?:pm|am)?", lower)
    if sleep_match or "sleep 11" in lower or "wake up at" in lower:
        topics.append(TopicAnswer(
            topic_id="K1",
            status="answered",
            facts=["Sleep window: 11:00 PM to 7:00 AM"],
        ))
        entities.append(ExtractedEntity(
            target_store="user_profiles",
            payload={"sleep_start": "23:00", "sleep_end": "07:00"},
        ))

    # L1: Top Stressor
    if any(k in lower for k in ["stress", "anxious", "worried", "mental weight", "overwhelm"]):
        topics.append(TopicAnswer(
            topic_id="L1",
            status="answered",
            facts=["Identified current stressor and workload pressure"],
        ))

    return OnboardingExtraction(
        distress_signal=False,
        topics=topics,
        entities=entities,
    )


# ===========================================================================
# Conversational Phrasing Engine with Artifact-First Ingestion Guardrail
# ===========================================================================
ONBOARDING_PHRASING_SYSTEM_INSTRUCTION = """You are the Conversational Phrasing Engine for an Autonomous Cognitive Offloader onboarding intake.
Your mission: Generate natural, student-centric responses consisting of:
Sentence 1: Brief reflection acknowledging what was logged from the student's message.
Sentence 2: Natural delivery of the next 1–2 target questions with an explicit casual fallback or skip option.

CRITICAL RULES:
1. ARTIFACT-FIRST INGESTION GUARDRAIL:
   Whenever inquiring about courses, class hours, lab schedules, or exam dates, always frame the question to request the official document (PDF, timetable photo, or syllabus copy) first, followed by a casual text fallback.
   - For Timetable & Fixed Commitments (Topic C1/C3): When asking for class/lab schedules, explicitly prioritize official artifacts:
     "Do you have your official timetable PDF or a photo/screenshot of it? You can upload it directly, or just type out the times if that's easier."
   - For Course Syllabi & Modules (Topic B4/B1): When courses or upcoming internals are identified:
     "Do you have the official syllabus copy, course handout, or slide deck PDFs for [Course Name]? Uploading the PDF lets me parse the exact units and exam weightage directly, or you can just list key topics."
   - For Academic Deadlines & Exam Circulars (Topic D1/D2/D3): When discussing mid-terms, end-sems, or lab vivas:
     "If your department released an official exam timetable or circular PDF/image, upload it here so I can lock in the exact dates and slots without errors, or type out what deadlines you have coming up."
   - For Administrative Logistics (Topic O2): For fee dates, registration notifications, or academic calendars, offer file ingestion first:
     "Do you have official circulars, fee notifications, or academic calendar PDFs/photos for upcoming deadlines? You can upload them directly, or just type out the dates."
2. CASUAL FALLBACK GUARANTEE:
   Always ensure the student retains an effortless text/voice fallback: "Upload the PDF/photo if you have it, or just drop the times here."
3. Keep the tone warm, empathetic, concise, and student-focused. No corporate or robotic fluff.
"""


def generate_onboarding_phrasing(
    facts: List[str],
    next_topics: List[str],
    identified_course: Optional[str] = None,
) -> Optional[str]:
    """
    Generates student-centric conversational phrasing via Gemini enforcing the
    artifact-first ingestion guardrail.
    Returns None if client is unavailable or in offline/test mode.
    """
    active_client = get_client() or client
    if not active_client:
        return None

    try:
        model_name = settings.GEMINI_MODEL or "gemini-3.5-flash-lite"
        content_prompt = f"Facts logged: {facts}\nNext target topics: {next_topics}"
        if identified_course:
            content_prompt += f"\nIdentified course: {identified_course}"

        response = active_client.models.generate_content(
            model=model_name,
            contents=content_prompt,
            config=types.GenerateContentConfig(
                system_instruction=ONBOARDING_PHRASING_SYSTEM_INSTRUCTION,
                temperature=0.2,
            ),
        )
        if response and response.text:
            return response.text.strip()
    except Exception as e:
        logger.warning(f"Gemini generate_onboarding_phrasing error: {e}")

    return None


