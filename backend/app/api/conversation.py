import re
from typing import Optional, Dict, Any, List
from uuid import UUID
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.onboarding import OnboardingCoverage
from app.services.conversation import run_conversational_turn
from app.services.onboarding_service import (
    process_onboarding_turn,
    is_tier_0_complete,
    OFFICIAL_OPENING_MESSAGE,
)
from app.services.rag import ingest_syllabus_pdf
from app.schemas.extraction import GoalInterrogationSchema, UniversalExtraction

router = APIRouter(prefix="/api/conversation", tags=["Conversational Agent"])


DEFAULT_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


class ConversationRequest(BaseModel):
    user_id: Optional[UUID] = Field(default_factory=lambda: DEFAULT_USER_ID)
    message: str = Field(..., min_length=1)
    horizon_start_iso: Optional[str] = None
    grill_mode: bool = False
    is_onboarding: Optional[bool] = None
    history: Optional[List[Dict[str, Any]]] = None


# Alias ChatRequest per requirements
ChatRequest = ConversationRequest


class ConversationResponse(BaseModel):
    reply: str
    audio_signal: str = "NONE"  # "SUCCESS_JINGLE" | "SAD_TROMBONE" | "NONE"
    schedule_status: str = "ON_TRACK"  # "ON_TRACK" | "LAGGING"
    goal_interrogation: Optional[GoalInterrogationSchema] = None
    universal_extraction: Optional[UniversalExtraction] = None
    solver_result: Optional[Dict[str, Any]] = None
    discovered_constraints: List[str] = []
    weeding_alert: Optional[Dict[str, Any]] = None
    onboarding: Optional[Dict[str, Any]] = None


@router.get("/status")
def get_conversation_status(
    user_id: Optional[UUID] = Query(None, description="Target user UUID"),
    db: Session = Depends(get_db),
):
    """
    Returns the onboarding intake status for the given user:
    - is_brand_new: True if no onboarding coverage entries exist yet.
    - is_onboarding: True if Tier 0 is not yet complete.
    - t0_complete: True if all Tier 0 topics are retired.
    - opening_message: The official conversational opening greeting.
    """
    target_user_id = user_id or DEFAULT_USER_ID
    t0_done = is_tier_0_complete(db, target_user_id)
    cov_count = db.query(OnboardingCoverage).filter(OnboardingCoverage.user_id == target_user_id).count()
    is_brand_new = (cov_count == 0)

    return {
        "user_id": str(target_user_id),
        "is_brand_new": is_brand_new,
        "is_onboarding": not t0_done,
        "t0_complete": t0_done,
        "opening_message": OFFICIAL_OPENING_MESSAGE,
    }


@router.post("", response_model=ConversationResponse)
def handle_conversation(
    req: ConversationRequest,
    db: Session = Depends(get_db),
):
    """
    Main conversational endpoint:
    - Student-Centric 'Describe Your Life' Onboarding Intake when Tier 0 is incomplete
    - Socratic goal interrogation (Pass 1 ambiguous vs Pass 2 concrete)
    - Zero-form BuJo telemetry extraction (workouts, meals, habits)
    - Deterministic CP-SAT re-solve & preemption audio feedback
    - Episodic life constraint capture
    """
    user_id = req.user_id or DEFAULT_USER_ID

    # Determine whether to route through Onboarding Intake Engine
    should_onboard = False
    if req.is_onboarding is True:
        should_onboard = True
    elif req.is_onboarding is False:
        should_onboard = False
    elif not req.grill_mode:
        t0_done = is_tier_0_complete(db, user_id)
        if not t0_done:
            cov_count = db.query(OnboardingCoverage).filter(OnboardingCoverage.user_id == user_id).count()
            msg_l = req.message.lower().strip()
            is_intake_dump = (
                bool(re.search(r"\b(\d+(?:st|nd|rd|th)?\s*sem(?:ester)?)\b", msg_l))
                or bool(re.search(r"\b(cse|ece|b\.?tech|computer science)\b", msg_l))
                or any(
                    w in msg_l
                    for w in [
                        "i study cs", "studying cs", "at ramaiah", "my college",
                        "in hostel", "my name is", "call me ", "kill myself", "end my life"
                    ]
                )
            )
            if cov_count > 0 or is_intake_dump:
                should_onboard = True

    if should_onboard:
        result = process_onboarding_turn(
            db=db,
            user_id=user_id,
            user_message=req.message,
            history=req.history,
            horizon_start_iso=req.horizon_start_iso,
        )
        return result

    result = run_conversational_turn(
        db=db,
        user_id=user_id,
        user_message=req.message,
        horizon_start_iso=req.horizon_start_iso,
        grill_mode=req.grill_mode,
    )
    return result


@router.post("/upload")
async def handle_conversation_upload(
    user_id: Optional[UUID] = Form(None),
    message: Optional[str] = Form(None),
    subject: Optional[str] = Form(None),
    file: UploadFile = File(...),
    horizon_start_iso: Optional[str] = Form(None),
    grill_mode: bool = Form(False),
    db: Session = Depends(get_db),
):
    """
    Accepts official primary artifacts (PDFs, timetable images) directly in the conversation flow,
    ingests them into the semantic RAG pipeline (PyMuPDF), and generates an updated conversational response.
    """
    target_user_id = user_id or DEFAULT_USER_ID
    allowed_exts = (".pdf", ".png", ".jpg", ".jpeg", ".webp")
    filename_lower = (file.filename or "").lower()
    if not any(filename_lower.endswith(ext) for ext in allowed_exts):
        raise HTTPException(
            status_code=400,
            detail="Only PDF and image files (.pdf, .png, .jpg, .jpeg, .webp) are supported",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file uploaded")

    records = ingest_syllabus_pdf(
        db=db,
        user_id=target_user_id,
        pdf_bytes=content,
        subject=subject,
        filename=file.filename,
    )

    clean_msg = (message or "").strip()
    if not clean_msg:
        clean_msg = f"I uploaded my official document: {file.filename}"

    # Route through onboarding if active or conversation
    t0_done = is_tier_0_complete(db, target_user_id)
    cov_count = db.query(OnboardingCoverage).filter(OnboardingCoverage.user_id == target_user_id).count()

    if not t0_done and cov_count > 0:
        result = process_onboarding_turn(
            db=db,
            user_id=target_user_id,
            user_message=clean_msg,
            horizon_start_iso=horizon_start_iso,
        )
        result["reply"] = f"Ingested '{file.filename}' ({len(records)} modules extracted). {result['reply']}"
        return result

    result = run_conversational_turn(
        db=db,
        user_id=target_user_id,
        user_message=clean_msg,
        horizon_start_iso=horizon_start_iso,
        grill_mode=grill_mode,
    )
    result["reply"] = f"Ingested '{file.filename}' ({len(records)} modules extracted). {result['reply']}"
    return result

