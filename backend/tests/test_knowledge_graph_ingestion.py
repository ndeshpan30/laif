import uuid
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, engine, SessionLocal
from app.models.user import UserProfile
from app.models.tracker import TrackerDefinition
from app.models.telemetry import TelemetryLog
from app.models.schedule import ScheduleItem
from app.models.semantic import SemanticContext, ContextEdge
from app.services.knowledge_graph import (
    sync_node,
    delete_synced_node,
    compute_tracker_stats,
    invalidate_knowledge_graph_cache,
)
from app.schemas.extraction import UniversalExtraction, TelemetryDataPoint
from app.services.telemetry_engine import process_universal_telemetry


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
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


# =====================================================================
# 1. sync_node for Tracker Definitions (Life Habits & Rolling Summary)
# =====================================================================

def test_sync_node_tracker_definition(db_session):
    """
    Tests syncing a tracker_definition into semantic_contexts:
    - Canonical text: 'Habit tracker: {name}, {category} in {unit}. Rolling summary: {stats}.'
    - context_type='life_habit'
    - idempotent upsert and content_hash computation
    """
    user = UserProfile(email="tracker_sync@offloader.ai")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    tracker = TrackerDefinition(
        user_id=user.user_id,
        name="water_intake",
        category="volume",
        data_type="float",
        unit="liters",
    )
    db_session.add(tracker)
    db_session.commit()
    db_session.refresh(tracker)

    # 1. Sync initial tracker without logs
    ctx = sync_node(db_session, user.user_id, "tracker_definitions", tracker.id)
    assert ctx is not None
    assert ctx.context_type == "life_habit"
    assert "Habit tracker: water_intake, volume in liters." in ctx.raw_content
    assert "Rolling summary: no entries logged yet." in ctx.raw_content
    assert ctx.content_hash is not None
    assert ctx.source_table == "tracker_definitions"
    assert str(ctx.source_id) == str(tracker.id)

    # 2. Add telemetry logs for water_intake
    db_session.add_all([
        TelemetryLog(
            user_id=user.user_id,
            entry_type="volume",
            content="drank 2 liters",
            log_metadata={"tracker_name": "water_intake", "value": 2.0},
        ),
        TelemetryLog(
            user_id=user.user_id,
            entry_type="volume",
            content="drank 3 liters",
            log_metadata={"tracker_name": "water_intake", "value": 3.0},
        ),
    ])
    db_session.commit()

    # Re-sync tracker: rolling summary should update with average
    ctx2 = sync_node(db_session, user.user_id, "tracker_definitions", tracker.id)
    assert ctx2.id == ctx.id
    assert "2 entries, avg: 2.5 liters" in ctx2.raw_content


def test_sync_node_skip_re_embedding_when_hash_matches(db_session):
    """
    Verifies that sync_node skips re-embedding if existing content_hash matches.
    """
    user = UserProfile(email="skip_embed@offloader.ai")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    tracker = TrackerDefinition(
        user_id=user.user_id,
        name="reading",
        category="metric",
        data_type="float",
        unit="pages",
    )
    db_session.add(tracker)
    db_session.commit()
    db_session.refresh(tracker)

    # First sync
    ctx1 = sync_node(db_session, user.user_id, "tracker_definitions", tracker.id)
    assert ctx1 is not None

    # Patch get_embedding to detect if called again
    with patch("app.services.knowledge_graph.get_embedding") as mock_embed:
        ctx2 = sync_node(db_session, user.user_id, "tracker_definitions", tracker.id)
        assert ctx2.id == ctx1.id
        mock_embed.assert_not_called()


# =====================================================================
# 2. sync_node for Telemetry Logs (Journal Notes & Structural Edges)
# =====================================================================

def test_sync_node_journal_note_and_structural_edge(db_session):
    """
    Tests that telemetry_logs:
    - only category == 'journal_note' creates telemetry_entry nodes
    - non-journal notes (numeric/boolean) return None and do NOT generate new nodes
    - structural edge is created to the parent tracker definition
    """
    user = UserProfile(email="note_sync@offloader.ai")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    # Create parent tracker definition for journal notes
    parent_tracker = TrackerDefinition(
        user_id=user.user_id,
        name="daily_reflection",
        category="journal_note",
        data_type="text",
    )
    db_session.add(parent_tracker)
    db_session.commit()
    db_session.refresh(parent_tracker)

    # 1. Create a numeric telemetry log -> sync_node should return None
    numeric_log = TelemetryLog(
        user_id=user.user_id,
        entry_type="metric",
        content="did 50 pushups",
        log_metadata={"tracker_name": "pushups", "category": "metric", "value": 50},
    )
    db_session.add(numeric_log)
    db_session.commit()
    db_session.refresh(numeric_log)

    res_num = sync_node(db_session, user.user_id, "telemetry_logs", numeric_log.id)
    assert res_num is None

    # 2. Create a journal note telemetry log -> sync_node should create telemetry_entry node
    note_log = TelemetryLog(
        user_id=user.user_id,
        entry_type="journal_note",
        content="Felt energized after morning deep work session.",
        log_metadata={
            "tracker_name": "daily_reflection",
            "category": "journal_note",
            "value": "Felt energized after morning deep work session.",
        },
    )
    db_session.add(note_log)
    db_session.commit()
    db_session.refresh(note_log)

    note_ctx = sync_node(db_session, user.user_id, "telemetry_logs", note_log.id)
    assert note_ctx is not None
    assert note_ctx.context_type == "telemetry_entry"
    assert "Felt energized after morning deep work session." in note_ctx.raw_content
    assert note_ctx.source_table == "telemetry_logs"

    # 3. Verify structural edge connects journal note node to parent tracker node
    tracker_ctx = (
        db_session.query(SemanticContext)
        .filter(
            SemanticContext.user_id == user.user_id,
            SemanticContext.source_table == "tracker_definitions",
            SemanticContext.source_id == parent_tracker.id,
        )
        .first()
    )
    assert tracker_ctx is not None
    assert tracker_ctx.context_type == "life_habit"

    edges = (
        db_session.query(ContextEdge)
        .filter(
            ContextEdge.user_id == user.user_id,
            ContextEdge.kind == "structural",
        )
        .all()
    )
    assert len(edges) == 1
    edge = edges[0]
    endpoints = {str(edge.source_id), str(edge.target_id)}
    assert str(note_ctx.id) in endpoints
    assert str(tracker_ctx.id) in endpoints


# =====================================================================
# 3. sync_node for Schedule Items (Goals & Exams)
# =====================================================================

def test_sync_node_schedule_items(db_session):
    """
    Tests schedule_items synchronization:
    - categories exam/lab/project/goal/milestone are indexed as project_goal
    - discretionary tasks (study_session, class, habit) are skipped
    """
    user = UserProfile(email="goal_sync@offloader.ai")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    # 1. Exam item -> indexed
    exam = ScheduleItem(
        user_id=user.user_id,
        title="Algorithms Midterm Exam",
        category="exam",
        duration_minutes=120,
        priority=10,
    )
    db_session.add(exam)

    # 2. Regular class -> skipped
    class_item = ScheduleItem(
        user_id=user.user_id,
        title="Physics Lecture",
        category="class",
        duration_minutes=60,
        priority=5,
    )
    db_session.add(class_item)

    # 3. Project milestone -> indexed
    milestone = ScheduleItem(
        user_id=user.user_id,
        title="Deliver MVP Demo",
        category="milestone",
        duration_minutes=90,
        priority=8,
    )
    db_session.add(milestone)
    db_session.commit()

    # Exam
    ctx_exam = sync_node(db_session, user.user_id, "schedule_items", exam.id)
    assert ctx_exam is not None
    assert ctx_exam.context_type == "project_goal"
    assert "Algorithms Midterm Exam" in ctx_exam.raw_content

    # Class (discretionary)
    ctx_class = sync_node(db_session, user.user_id, "schedule_items", class_item.id)
    assert ctx_class is None

    # Milestone
    ctx_milestone = sync_node(db_session, user.user_id, "schedule_items", milestone.id)
    assert ctx_milestone is not None
    assert ctx_milestone.context_type == "project_goal"


# =====================================================================
# 4. Failure Isolation (Design Decision D6)
# =====================================================================

def test_sync_node_failure_isolation(db_session):
    """
    Verifies that errors in embedding generation or DB are logged
    without raising exceptions, returning None.
    """
    user = UserProfile(email="failure_iso@offloader.ai")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    tracker = TrackerDefinition(
        user_id=user.user_id,
        name="pushups",
        category="metric",
        data_type="float",
    )
    db_session.add(tracker)
    db_session.commit()
    db_session.refresh(tracker)

    # Force get_embedding to throw an unexpected exception
    with patch("app.services.knowledge_graph.get_embedding", side_effect=RuntimeError("Embedding service down")):
        ctx = sync_node(db_session, user.user_id, "tracker_definitions", tracker.id)
        # Must return None safely without raising RuntimeError
        assert ctx is None


# =====================================================================
# 5. Write Path Integration & Deletion Cascades
# =====================================================================

def test_telemetry_engine_jit_and_journal_note_write_path(client):
    """
    Tests that process_universal_telemetry:
    - JIT-registers tracker and triggers sync_node (creating life_habit node)
    - Journal note triggers sync_node (creating telemetry_entry node + structural edge)
    - Numeric telemetry only updates rolling summary and does not create telemetry log node
    """
    user_res = client.post("/api/users", json={"email": "write_path@offloader.ai"})
    user_id = user_res.json()["user_id"]

    db = SessionLocal()

    # Process extraction with 1 numeric metric and 1 journal note
    extraction = UniversalExtraction(
        is_telemetry=True,
        data_points=[
            TelemetryDataPoint(entity="bench_press", value=185, unit="lbs", category="metric"),
            TelemetryDataPoint(entity="workout_notes", value="Hit new personal record on bench press!", unit="", category="journal_note"),
        ],
    )

    res = process_universal_telemetry(db, user_id=user_id, raw_message="benched 185, hit new PR!", extraction=extraction)
    assert res["logged_count"] == 2

    # Query semantic_contexts
    contexts = db.query(SemanticContext).filter(SemanticContext.user_id == user_id).all()
    ctx_types = {c.context_type for c in contexts}

    # Must contain life_habit (from bench_press tracker) and telemetry_entry (from journal note)
    assert "life_habit" in ctx_types
    assert "telemetry_entry" in ctx_types

    # Verify bench_press tracker has rolling summary
    bench_ctx = next(c for c in contexts if c.source_table == "tracker_definitions" and "bench_press" in c.raw_content)
    assert "185" in bench_ctx.raw_content

    # Verify structural edge was created between workout_notes note and its tracker
    edges = db.query(ContextEdge).filter(ContextEdge.user_id == user_id, ContextEdge.kind == "structural").all()
    assert len(edges) >= 1
    db.close()


def test_deletion_cascade_tracker_and_schedule_item(client):
    """
    Tests that deleting a tracker or schedule item via API (or delete_synced_node)
    drops its corresponding semantic_contexts record and attached context_edges.
    """
    user_res = client.post("/api/users", json={"email": "delete_cascade@offloader.ai"})
    user_id = user_res.json()["user_id"]
    db = SessionLocal()

    # 1. Create and sync tracker via API
    trk_res = client.post("/api/trackers", json={
        "user_id": user_id,
        "name": "pullups",
        "category": "metric",
        "data_type": "float",
        "unit": "reps",
    })
    tracker_id = trk_res.json()["id"]

    # Verify node exists
    trk_ctx = db.query(SemanticContext).filter(
        SemanticContext.user_id == user_id,
        SemanticContext.source_table == "tracker_definitions",
        SemanticContext.source_id == tracker_id,
    ).first()
    assert trk_ctx is not None

    # Delete tracker via API
    del_res = client.delete(f"/api/trackers/{tracker_id}")
    assert del_res.status_code == 200

    # Verify semantic_context is dropped
    trk_ctx_after = db.query(SemanticContext).filter(
        SemanticContext.user_id == user_id,
        SemanticContext.source_table == "tracker_definitions",
        SemanticContext.source_id == tracker_id,
    ).first()
    assert trk_ctx_after is None

    # 2. Create and sync schedule item via API
    item_res = client.post("/api/schedule/items", json={
        "user_id": user_id,
        "title": "Compiler Design Final Exam",
        "category": "exam",
        "duration_minutes": 180,
        "priority": 10,
    })
    item_id = item_res.json()["id"]

    item_ctx = db.query(SemanticContext).filter(
        SemanticContext.user_id == user_id,
        SemanticContext.source_table == "schedule_items",
        SemanticContext.source_id == item_id,
    ).first()
    assert item_ctx is not None

    # Delete schedule item via API
    del_item_res = client.delete(f"/api/schedule/items/{item_id}")
    assert del_item_res.status_code == 200

    # Verify semantic_context is dropped
    item_ctx_after = db.query(SemanticContext).filter(
        SemanticContext.user_id == user_id,
        SemanticContext.source_table == "schedule_items",
        SemanticContext.source_id == item_id,
    ).first()
    assert item_ctx_after is None
    db.close()
