from typing import Optional, Dict, Any, List
from uuid import UUID
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.conversation import run_conversational_turn
from app.schemas.extraction import GoalInterrogationSchema, UniversalExtraction

router = APIRouter(prefix="/api/conversation", tags=["Conversational Agent"])


DEFAULT_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


class ConversationRequest(BaseModel):
    user_id: Optional[UUID] = Field(default_factory=lambda: DEFAULT_USER_ID)
    message: str = Field(..., min_length=1)
    horizon_start_iso: Optional[str] = None
    grill_mode: bool = False


# Alias ChatRequest per requirements
ChatRequest = ConversationRequest


class ConversationResponse(BaseModel):
    reply: str
    audio_signal: str  # "SUCCESS_JINGLE" | "SAD_TROMBONE" | "NONE"
    schedule_status: str  # "ON_TRACK" | "LAGGING"
    goal_interrogation: Optional[GoalInterrogationSchema] = None
    universal_extraction: Optional[UniversalExtraction] = None
    solver_result: Optional[Dict[str, Any]] = None
    discovered_constraints: List[str] = []
    weeding_alert: Optional[Dict[str, Any]] = None


@router.post("", response_model=ConversationResponse)
def handle_conversation(
    req: ConversationRequest,
    db: Session = Depends(get_db),
):
    """
    Main conversational endpoint:
    - Socratic goal interrogation (Pass 1 ambiguous vs Pass 2 concrete)
    - Zero-form BuJo telemetry extraction (workouts, meals, habits)
    - Deterministic CP-SAT re-solve & preemption audio feedback
    - Episodic life constraint capture
    """
    user_id = req.user_id or DEFAULT_USER_ID
    result = run_conversational_turn(
        db=db,
        user_id=user_id,
        user_message=req.message,
        horizon_start_iso=req.horizon_start_iso,
        grill_mode=req.grill_mode,
    )
    return result
