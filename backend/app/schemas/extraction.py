from typing import List, Optional, Any, Literal
from pydantic import BaseModel, Field


# Socratic Goal Interrogation Schema
class GoalInterrogationSchema(BaseModel):
    is_ambiguous: bool = Field(..., description="True if goal lacks frequency, duration, or measurable bounds.")
    clarifying_questions: Optional[List[str]] = Field(None, description="Max 2 targeted Socratic questions if ambiguous.")
    task_name: Optional[str] = None
    target_frequency_per_week: Optional[int] = None
    session_duration_minutes: Optional[int] = None
    deadline_iso: Optional[str] = None
    priority_level: Optional[int] = Field(default=5, ge=1, le=10)
    detected_constraints: Optional[List[str]] = Field(default=[])


# UNIVERSAL_TRACKING_SPEC.md Section 2: Universal Extraction Schemas (Pydantic v2)
class TelemetryDataPoint(BaseModel):
    entity: str = Field(
        ...,
        description=(
            "The thing being tracked, in normalized snake_case "
            "(e.g., 'sleep', 'stress', 'water_intake', 'pushups', "
            "'called_mom', 'pages_read', 'random_thought'). "
            "Reuse an existing entity name whenever the user is clearly "
            "referring to something already tracked, rather than inventing "
            "a near-duplicate (e.g., always 'sleep', never 'sleep_hours' "
            "and 'hours_slept' as two different entities)."
        ),
    )
    value: Any = Field(
        ...,
        description=(
            "The magnitude or state. Numeric for metrics/volumes "
            "(5.5, 8, 20), boolean for binary habits (True/False), "
            "or a short string for journal notes/states "
            "('felt overwhelmed', 'skipped')."
        ),
    )
    unit: Optional[str] = Field(
        None,
        description="Unit of measurement if applicable (e.g., 'hours', '/10', 'cups', 'kg', 'minutes', 'pages').",
    )
    category: Literal["metric", "binary_habit", "volume", "journal_note"] = Field(
        ...,
        description=(
            "'metric': a continuous numeric scale (stress 1-10, mood, sleep hours). "
            "'binary_habit': a yes/no occurrence (did/didn't happen). "
            "'volume': a countable quantity of repeated units (cups, reps, pages, minutes). "
            "'journal_note': unstructured text with no numeric value — venting, "
            "observations, free-form thought."
        ),
    )


class UniversalExtraction(BaseModel):
    is_telemetry: bool = Field(
        ...,
        description="True if the message contains any loggable life data. False for pure scheduling requests, small talk, or questions with nothing to log.",
    )
    data_points: List[TelemetryDataPoint] = Field(
        default_factory=list,
        description="One entry per distinct trackable fact found in the message. A single sentence commonly yields multiple data points.",
    )
