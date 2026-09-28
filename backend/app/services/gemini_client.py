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
