"""
Automated Test Suite for Artifact-First Ingestion Guardrails & Endpoints
========================================================================
Validates:
1. Timetable & Fixed Commitments (C1/C3): AI asks for official timetable PDF or photo first with text fallback.
2. Course Syllabi & Modules (B4/B1): AI asks for official syllabus copy/slide deck PDFs for [Course Name] with text fallback.
3. Academic Deadlines & Exam Circulars (D1/D2/D3): AI asks for official exam timetable/circular PDF or image first with text fallback.
4. Administrative Logistics (O2): AI offers file ingestion first for official circulars, fee notifications, or calendars.
5. System Prompt Guardrail: ONBOARDING_PHRASING_SYSTEM_INSTRUCTION contains the artifact-first ingestion guardrail.
6. Ingestion Endpoint (/api/ingest): Cleanly processes PDFs via PyMuPDF into semantic_contexts.
7. Multipart Conversation Ingestion (/api/conversation/upload): Routes document upload cleanly and returns conversational reply.
8. Effortless Text/Voice Fallback: User can freely provide plain text answers instead of uploading.
"""

import uuid
import pytest
from fastapi.testclient import TestClient
import fitz

from app.main import app
from app.database import Base, engine, SessionLocal
from app.models.user import UserProfile
from app.models.semantic import SemanticContext
from app.models.schedule import ScheduleItem
from app.models.onboarding import OnboardingCoverage
from app.onboarding.question_bank import QUESTION_BANK
from app.services.onboarding_service import (
    process_onboarding_turn,
    generate_onboarding_response,
    format_topic_prompt_question,
    update_coverage,
)
from app.services.gemini_client import ONBOARDING_PHRASING_SYSTEM_INSTRUCTION
from app.schemas.onboarding import TopicAnswer


@pytest.fixture
def db_session():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db_session):
    with TestClient(app) as c:
        yield c


def _create_user(db, email="student_guardrail@offloader.ai") -> UserProfile:
    user = UserProfile(
        user_id=uuid.uuid4(),
        email=email,
        buffer_minutes=15,
        max_study_hours_per_day=8,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _create_sample_pdf(title: str, text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), f"Syllabus for: {title}\n\n{text}")
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


# ===========================================================================
# Test 1: Timetable Artifact Priority (C1 & C3)
# ===========================================================================
def test_artifact_priority_timetable_c1_c3():
    """
    Verifies that C1 and C3 prompt questions prioritize official timetable PDFs or photos
    with an effortless text fallback.
    """
    q_c1 = format_topic_prompt_question("C1")
    assert "official timetable PDF or a photo/screenshot" in q_c1
    assert "upload it directly" in q_c1
    assert "type out the times" in q_c1

    q_c3 = format_topic_prompt_question("C3")
    assert "official batch rotation schedule PDF or a photo" in q_c3
    assert "upload it directly" in q_c3

    # Also verify Question Bank defaults
    assert "official timetable PDF" in QUESTION_BANK["C1"].prompt_question
    assert "official batch rotation schedule PDF" in QUESTION_BANK["C3"].prompt_question


# ===========================================================================
# Test 2: Course Syllabi Artifact Priority (B4 & B1) with Identified Course
# ===========================================================================
def test_artifact_priority_syllabi_b4_b1_with_identified_course():
    """
    When a course like 'Computer Networks' is identified, B4 phrasing must request
    the official syllabus copy or slide deck PDFs for that specific course.
    """
    q_b4 = format_topic_prompt_question("B4", identified_course="Computer Networks")
    assert "official syllabus copy, course handout, or slide deck PDFs for Computer Networks" in q_b4
    assert "parse the exact units and exam weightage" in q_b4

    # Generic course fallback when none specified
    q_b4_generic = format_topic_prompt_question("B4", identified_course=None)
    assert "official syllabus copy, course handout, or slide deck PDFs for your courses" in q_b4_generic

    # B1 asks for courses with optional PDF upload
    q_b1 = format_topic_prompt_question("B1")
    assert "syllabus copy or course registration PDF" in q_b1
    assert "type them out" in q_b1


# ===========================================================================
# Test 3: Academic Deadlines & Exam Circulars (D1, D2, D3)
# ===========================================================================
def test_artifact_priority_academic_deadlines_d1_d2_d3():
    """
    Verifies D1, D2, D3 prompt questions request official exam timetable or circular PDF/image.
    """
    q_d1 = format_topic_prompt_question("D1")
    assert "official exam timetable or circular PDF/image" in q_d1
    assert "lock in the exact dates and slots without errors" in q_d1
    assert "type out your next 1 to 3 imminent deadlines" in q_d1

    q_d2 = format_topic_prompt_question("D2")
    assert "official midterm timetable or circular PDF/image" in q_d2

    q_d3 = format_topic_prompt_question("D3")
    assert "official end-sem exam timetable or circular PDF/image" in q_d3


# ===========================================================================
# Test 4: Administrative Logistics & Fee Circulars (O2)
# ===========================================================================
def test_artifact_priority_paperwork_o2():
    """
    Verifies O2 offers file ingestion first for official circulars and fee receipts.
    """
    q_o2 = format_topic_prompt_question("O2")
    assert "official circulars, fee notifications, or academic calendar PDFs/photos" in q_o2
    assert "upload them directly" in q_o2
    assert "type out the dates" in q_o2


# ===========================================================================
# Test 5: Phrasing Engine System Prompt Guardrail
# ===========================================================================
def test_phrasing_engine_system_instruction_guardrail():
    """
    Verifies that ONBOARDING_PHRASING_SYSTEM_INSTRUCTION contains the exact guardrail rules.
    """
    prompt = ONBOARDING_PHRASING_SYSTEM_INSTRUCTION
    assert "ARTIFACT-FIRST INGESTION GUARDRAIL" in prompt
    assert "Whenever inquiring about courses, class hours, lab schedules, or exam dates" in prompt
    assert "always frame the question to request the official document" in prompt
    assert "casual text fallback" in prompt
    assert "CASUAL FALLBACK GUARANTEE" in prompt


# ===========================================================================
# Test 6: Ingestion Endpoint POST /api/ingest
# ===========================================================================
def test_api_ingest_pdf_route_pymupdf(db_session, client):
    """
    Verifies POST /api/ingest cleanly accepts PDF files and extracts modules via PyMuPDF.
    """
    user = _create_user(db_session, "ingest_endpoint@offloader.ai")
    pdf_content = (
        "Unit 1: Introduction to Computer Networks and Layering.\n"
        "Unit 2: Physical and Data Link Layer protocols, Framing, CRC.\n"
        "Unit 3: Network Layer Subnetting, CIDR, and OSPF Routing.\n"
    )
    pdf_bytes = _create_sample_pdf("Computer Networks", pdf_content)

    res = client.post(
        "/api/ingest",
        data={"user_id": str(user.user_id), "subject": "Computer Networks"},
        files={"file": ("networks_syllabus.pdf", pdf_bytes, "application/pdf")},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["filename"] == "networks_syllabus.pdf"
    assert data["modules_ingested"] >= 1
    assert "Ingested 'networks_syllabus.pdf'" in data["reply"]

    # Verify rows in semantic_contexts
    contexts = (
        db_session.query(SemanticContext)
        .filter(SemanticContext.user_id == user.user_id, SemanticContext.context_type == "syllabus_module")
        .all()
    )
    assert len(contexts) >= 1
    assert any("Subnetting" in c.raw_content for c in contexts)


# ===========================================================================
# Test 7: Multipart Conversation Document Ingestion POST /api/conversation/upload
# ===========================================================================
def test_api_conversation_upload_multipart(db_session, client):
    """
    Verifies POST /api/conversation/upload processes uploaded documents and
    returns an updated conversational response.
    """
    user = _create_user(db_session, "convo_upload@offloader.ai")
    pdf_bytes = _create_sample_pdf("Timetable", "Monday to Friday 9:00 AM to 4:00 PM Core Engineering Classes")

    res = client.post(
        "/api/conversation/upload",
        data={"user_id": str(user.user_id), "message": "Here is my timetable"},
        files={"file": ("timetable.pdf", pdf_bytes, "application/pdf")},
    )
    assert res.status_code == 200
    data = res.json()
    assert "Ingested 'timetable.pdf'" in data["reply"]
    assert "reply" in data


# ===========================================================================
# Test 8: End-to-End Onboarding Phrasing with Artifact Ingestion Guardrail
# ===========================================================================
def test_onboarding_turn_delivers_artifact_first_question_with_course_context(db_session):
    """
    When student dumps course details, the follow-up asks for syllabus artifacts for that course.
    """
    user = _create_user(db_session, "e2e_guardrail@offloader.ai")

    # Step 1: User mentions they are taking Computer Networks
    res1 = process_onboarding_turn(
        db=db_session,
        user_id=user.user_id,
        user_message="I'm a 3rd sem CSE student and taking Computer Networks",
    )
    reply1 = res1["reply"]
    # The reflection reflects what was logged
    assert "Got it" in reply1 or "Computer Networks" in reply1

    # Step 2: Let's test generating response explicitly asking for B4 with identified course
    reply_b4 = generate_onboarding_response(
        facts=["Enrolled in Computer Networks"],
        next_topics=["B4"],
        identified_course="Computer Networks",
    )
    assert "official syllabus copy, course handout, or slide deck PDFs for Computer Networks" in reply_b4
    assert "(You can say 'skip' if you'd rather add this later)" in reply_b4
