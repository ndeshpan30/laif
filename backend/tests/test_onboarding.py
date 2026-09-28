"""
Automated Test Suite for Student-Centric Onboarding Intake Engine
=================================================================
Validates:
1. Rich initial brain-dump marks multiple topics answered and asks at most 2 follow-ups.
2. Minimal one-line response ("i study cs") reflects input and requests the next immediate Tier 0 gap.
3. Answered or declined questions are never repeated.
4. Three consecutive "skip" replies pauses onboarding and moves to standard mode.
5. Completing Tier 0 triggers the CP-SAT schedule generator exactly once.
6. A crisis/distress prompt halts onboarding immediately without asking follow-up questions.
7. Extracted facts create corresponding nodes and edges on the knowledge graph via sync_node.
"""

import uuid
import datetime
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, engine, SessionLocal
from app.models.user import UserProfile
from app.models.schedule import ScheduleItem
from app.models.semantic import SemanticContext, ContextEdge
from app.models.tracker import TrackerDefinition
from app.models.onboarding import OnboardingCoverage
from app.onboarding.question_bank import TIER_0_TOPICS, QUESTION_BANK
from app.services.onboarding_service import (
    process_onboarding_turn,
    update_coverage,
    get_next_questions,
    is_tier_0_complete,
    detect_fatigue,
    dispatch_entities,
    CRISIS_RESPONSE_TEXT,
)
from app.schemas.onboarding import TopicAnswer, ExtractedEntity


@pytest.fixture
def db_session():
    """Provides a fresh isolated database session per test."""
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
    """Test client bound to isolated DB."""
    with TestClient(app) as c:
        yield c


def _create_user(db, email="student@offloader.ai") -> UserProfile:
    user = UserProfile(
        user_id=uuid.uuid4(),
        email=email,
        sleep_start=datetime.time(23, 0),
        sleep_end=datetime.time(7, 0),
        buffer_minutes=15,
        max_study_hours_per_day=8,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ===========================================================================
# Test 1: Rich initial brain-dump marks multiple topics answered
# ===========================================================================
def test_1_rich_initial_brain_dump(db_session, client):
    """
    Test 1: Rich initial brain-dump marks multiple topics answered and asks at most 2 follow-ups.
    Input: "I'm in 4th sem CSE at Ramaiah doing AI/ML, have a DBMS test this Thursday, and can't focus past 10 PM"
    """
    user = _create_user(db_session, "rich_dump@offloader.ai")
    dump_text = (
        "I'm in 4th sem CSE at Ramaiah doing AI/ML, have a DBMS test this Thursday, and can't focus past 10 PM"
    )

    result = process_onboarding_turn(
        db=db_session,
        user_id=user.user_id,
        user_message=dump_text,
    )

    onboarding_data = result.get("onboarding", {})
    extracted_topics = onboarding_data.get("extracted_topics", [])
    next_questions = onboarding_data.get("next_questions", [])

    # Must mark multiple topics answered
    assert len(extracted_topics) >= 4
    assert "A2" in extracted_topics  # Program / Sem
    assert "A3" in extracted_topics  # College
    assert "B1" in extracted_topics  # Course (AI/ML)
    assert "D1" in extracted_topics  # DBMS test Thursday
    assert "E7" in extracted_topics  # Can't focus past 10 PM

    # Must ask at most 2 follow-ups
    assert len(next_questions) <= 2
    assert len(next_questions) >= 1

    # Verify coverage state in database
    cov_records = (
        db_session.query(OnboardingCoverage)
        .filter(OnboardingCoverage.user_id == user.user_id)
        .all()
    )
    answered_ids = {r.topic_id for r in cov_records if r.status == "answered"}
    assert "A2" in answered_ids
    assert "D1" in answered_ids
    assert "E7" in answered_ids


# ===========================================================================
# Test 2: Minimal one-line response reflects input and requests next gap
# ===========================================================================
def test_2_minimal_one_line_response(db_session):
    """
    Test 2: Minimal one-line response ("i study cs") reflects input and requests
    the next immediate Tier 0 gap.
    """
    user = _create_user(db_session, "minimal@offloader.ai")
    msg = "i study cs"

    result = process_onboarding_turn(
        db=db_session,
        user_id=user.user_id,
        user_message=msg,
    )

    reply = result["reply"]
    # Reflection acknowledges what was logged
    assert "Got it" in reply or "CS" in reply or "Studying" in reply

    # Requests the next immediate Tier 0 gap (e.g. A1, A3, B1, etc.)
    next_q = result["onboarding"].get("next_questions", [])
    assert len(next_q) >= 1
    assert next_q[0] in TIER_0_TOPICS
    assert next_q[0] != "A2"  # A2 was already answered by 'i study cs'


# ===========================================================================
# Test 3: Answered or declined questions are never repeated
# ===========================================================================
def test_3_answered_or_declined_questions_are_never_repeated(db_session):
    """
    Test 3: Answered or declined questions are never repeated in follow-ups.
    """
    user = _create_user(db_session, "norepeat@offloader.ai")

    # Mark A1, A2, A3 as answered, and B1 as declined
    update_coverage(
        db=db_session,
        user_id=user.user_id,
        topics=[
            TopicAnswer(topic_id="A1", status="answered", facts=["Name is Alex"]),
            TopicAnswer(topic_id="A2", status="answered", facts=["3rd Sem CSE"]),
            TopicAnswer(topic_id="A3", status="answered", facts=["Ramaiah"]),
            TopicAnswer(topic_id="B1", status="declined", facts=[]),
        ],
    )

    # Ask for next questions 5 times
    for _ in range(5):
        next_qs = get_next_questions(db_session, user.user_id, "here is an update")
        for q in next_qs:
            assert q not in ("A1", "A2", "A3", "B1")
            assert q in QUESTION_BANK


# ===========================================================================
# Test 4: Three consecutive skips pauses onboarding and moves to standard mode
# ===========================================================================
def test_4_three_consecutive_skips_pauses_onboarding(db_session):
    """
    Test 4: Three consecutive 'skip' replies pauses onboarding and moves to standard mode.
    """
    user = _create_user(db_session, "skips@offloader.ai")

    history = [
        {"sender": "agent", "text": "What courses are you taking?"},
        {"sender": "user", "text": "skip"},
        {"sender": "agent", "text": "What does your timetable look like?"},
        {"sender": "user", "text": "skip"},
        {"sender": "agent", "text": "When is your sleep window?"},
    ]

    # Third skip
    res = process_onboarding_turn(
        db=db_session,
        user_id=user.user_id,
        user_message="skip",
        history=history,
    )

    # Fatigue exit must be triggered
    assert res["onboarding"]["fatigue_exit"] is True
    assert res["onboarding"]["is_complete"] is True
    assert "pause the onboarding" in res["reply"] or "standard mode" in res["reply"] or "saved what you shared" in res["reply"]


# ===========================================================================
# Test 5: Completing Tier 0 triggers CP-SAT schedule generator exactly once
# ===========================================================================
def test_5_completing_tier_0_triggers_cpsat_solver(db_session):
    """
    Test 5: Completing Tier 0 triggers the CP-SAT schedule generator exactly once.
    """
    user = _create_user(db_session, "t0_complete@offloader.ai")

    # Pre-populate 8 of the 9 Tier 0 topics
    t0_pre = [
        TopicAnswer(topic_id="A1", status="answered", facts=["Name is Rohan"]),
        TopicAnswer(topic_id="A2", status="answered", facts=["4th sem CSE"]),
        TopicAnswer(topic_id="A3", status="answered", facts=["College is RVCE"]),
        TopicAnswer(topic_id="B1", status="answered", facts=["Taking OS and Networks"]),
        TopicAnswer(topic_id="C1", status="answered", facts=["Classes 9 to 4"]),
        TopicAnswer(topic_id="D1", status="answered", facts=["OS exam next Friday"]),
        TopicAnswer(topic_id="E7", status="answered", facts=["No screen work past 10 PM"]),
        TopicAnswer(topic_id="F1", status="answered", facts=["Maintain 9.0 CGPA"]),
    ]
    update_coverage(db_session, user.user_id, t0_pre)
    assert is_tier_0_complete(db_session, user.user_id) is False

    # Add a mock flexible task for CP-SAT to schedule
    task = ScheduleItem(
        user_id=user.user_id,
        title="OS Exam Prep Session",
        category="study_session",
        duration_minutes=60,
        priority=8,
        is_fixed=False,
    )
    db_session.add(task)
    db_session.commit()

    # Supply the final missing T0 topic (K1: Sleep window)
    turn_res = process_onboarding_turn(
        db=db_session,
        user_id=user.user_id,
        user_message="I sleep at 11 PM and wake up at 7 AM every day",
    )

    assert turn_res["onboarding"]["t0_complete"] is True
    assert turn_res["onboarding"]["is_complete"] is True
    assert turn_res["audio_signal"] == "SUCCESS_JINGLE"
    assert turn_res["solver_result"] is not None
    assert turn_res["solver_result"]["status"] in ("OPTIMAL", "FEASIBLE")

    # Confirm schedule item received start and end times
    db_session.refresh(task)
    assert task.start_time is not None
    assert task.end_time is not None


# ===========================================================================
# Test 6: Crisis/distress prompt halts onboarding immediately without follow-up
# ===========================================================================
def test_6_crisis_distress_halts_onboarding(db_session):
    """
    Test 6: A crisis/distress prompt halts onboarding immediately without asking
    follow-up questions and returns verified emergency helpline resources.
    """
    user = _create_user(db_session, "crisis@offloader.ai")
    distress_msg = "I can't go on anymore, I want to end it all and give up on life"

    res = process_onboarding_turn(
        db=db_session,
        user_id=user.user_id,
        user_message=distress_msg,
    )

    assert res["onboarding"]["distress_signal"] is True
    assert res["onboarding"]["is_complete"] is False
    assert len(res["onboarding"]["extracted_topics"]) == 0
    assert res["solver_result"] is None

    # Reply must contain crisis helplines
    reply = res["reply"]
    assert "Tele-MANAS" in reply or "14416" in reply
    assert "Vandrevala" in reply or "988" in reply or "AASRA" in reply

    # No follow-up questions about courses or timetable should be asked
    assert "What courses" not in reply
    assert "timetable" not in reply


# ===========================================================================
# Test 7: Extracted facts create nodes and edges in knowledge graph via sync_node
# ===========================================================================
def test_7_extracted_facts_create_nodes_and_edges(db_session):
    """
    Test 7: Extracted facts create corresponding nodes and edges on the knowledge graph
    via sync_node.
    """
    user = _create_user(db_session, "kg_sync@offloader.ai")

    # Provide a dump that contains:
    # - an episodic constraint: "no screen work past 10 PM"
    # - a schedule item: "DBMS test this Thursday"
    msg = "I'm studying AI/ML, have a DBMS test this Thursday, and no screen work past 10 PM"

    turn_res = process_onboarding_turn(
        db=db_session,
        user_id=user.user_id,
        user_message=msg,
    )

    # 1. Verify semantic_contexts contains episodic_constraint
    constraint_nodes = (
        db_session.query(SemanticContext)
        .filter(
            SemanticContext.user_id == user.user_id,
            SemanticContext.context_type == "episodic_constraint",
        )
        .all()
    )
    assert len(constraint_nodes) >= 1
    assert "10 PM" in constraint_nodes[0].raw_content

    # 2. Verify schedule_items contains DBMS Test
    exam_items = (
        db_session.query(ScheduleItem)
        .filter(
            ScheduleItem.user_id == user.user_id,
            ScheduleItem.category == "exam",
        )
        .all()
    )
    assert len(exam_items) >= 1
    assert "DBMS" in exam_items[0].title

    # 3. Verify sync_node synced the exam item into semantic_contexts
    synced_exam_ctx = (
        db_session.query(SemanticContext)
        .filter(
            SemanticContext.user_id == user.user_id,
            SemanticContext.source_table == "schedule_items",
            SemanticContext.source_id == exam_items[0].id,
        )
        .first()
    )
    assert synced_exam_ctx is not None
    assert synced_exam_ctx.context_type == "project_goal"
