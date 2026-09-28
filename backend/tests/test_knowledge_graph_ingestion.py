"""
Knowledge Graph Ingestion Engine & Backfill Migration Test Suite
================================================================
Comprehensive test suite validating:
- Mock embeddings (fixed-dimension 1536 vectors, zero external API calls).
- Test 1: Logging a habit creates one life_habit node; 20 numeric entries trigger zero new nodes.
- Test 2: Logging a journal note creates a telemetry_entry node with a structural edge to its tracker.
- Test 3: Deleting a goal deletes both its node and connected edges.
- Test 4: sync_node idempotency (unaltered text produces zero database writes).
- Test 5: Graph failures are isolated and do not interrupt primary telemetry/chat transactions.
- Test 6: Multi-tenant safety (User A cannot query or link to User B nodes).
- Backfill Migration Utility: full iteration, idempotency, and summary reporting.
"""

import uuid
import pytest
from typing import List
from unittest.mock import patch, MagicMock
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
    compute_knowledge_graph,
    create_context_edge,
    invalidate_knowledge_graph_cache,
)
from app.schemas.extraction import UniversalExtraction, TelemetryDataPoint
from app.services.telemetry_engine import process_universal_telemetry
from scripts.backfill_knowledge_graph import backfill_knowledge_graph


# =====================================================================
# Fixtures & Mock Embedding Configuration
# =====================================================================

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
def client():
    """Provides an isolated FastAPI test client with clean schema."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def mock_embedding_engine(monkeypatch):
    """
    Enforces mock embeddings across all tests in this suite.
    Generates deterministic 1536-dimensional float vectors locally.
    Guarantees that no external API calls are ever made.
    """
    def _mock_fixed_dim_embedding(text: str) -> List[float]:
        dim = 1536
        tokens = text.lower().split()
        vec = [0.0] * dim
        for idx, token in enumerate(tokens):
            bucket = (hash(token) + idx) % dim
            vec[bucket] += 1.0
        norm = sum(x * x for x in vec) ** 0.5
        if norm > 0:
            vec = [round(x / norm, 6) for x in vec]
        else:
            vec[0] = 1.0
        return vec

    monkeypatch.setattr("app.services.knowledge_graph.get_embedding", _mock_fixed_dim_embedding)
    monkeypatch.setattr("app.services.rag.generate_embedding_1536", _mock_fixed_dim_embedding)


# =====================================================================
# Test 1: Habit Logging & Numeric Entries Zero Nodes
# =====================================================================

def test_1_habit_logging_creates_one_life_habit_node_numeric_entries_zero_nodes(db_session):
    """
    Test 1:
    Logging a habit creates exactly one 'life_habit' node.
    Logging 20 numeric entries updates the rolling summary but triggers ZERO new nodes.
    """
    user = UserProfile(email="test1_habit@offloader.ai")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    # 1. Register a habit tracker
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

    # Sync habit node
    ctx = sync_node(db_session, user.user_id, "tracker_definitions", tracker.id)
    assert ctx is not None
    assert ctx.context_type == "life_habit"
    assert "water_intake" in ctx.raw_content
    assert "Rolling summary: no entries logged yet." in ctx.raw_content

    # Exactly 1 semantic context node exists
    initial_node_count = db_session.query(SemanticContext).filter(SemanticContext.user_id == user.user_id).count()
    assert initial_node_count == 1

    # 2. Log 20 numeric entries for water_intake
    for i in range(1, 21):
        extraction = UniversalExtraction(
            is_telemetry=True,
            data_points=[
                TelemetryDataPoint(
                    entity="water_intake",
                    value=float(i * 0.25),
                    unit="liters",
                    category="volume",
                )
            ],
        )
        res = process_universal_telemetry(
            db_session,
            user_id=user.user_id,
            raw_message=f"drank {i * 0.25} liters of water",
            extraction=extraction,
        )
        assert res["logged_count"] == 1

    # Verify 20 logs in telemetry_logs
    total_logs = db_session.query(TelemetryLog).filter(TelemetryLog.user_id == user.user_id).count()
    assert total_logs == 20

    # 3. Assert total nodes in semantic_contexts is STILL EXACTLY ONE (0 new nodes triggered)
    final_nodes = db_session.query(SemanticContext).filter(SemanticContext.user_id == user.user_id).all()
    assert len(final_nodes) == 1
    assert final_nodes[0].id == ctx.id
    assert final_nodes[0].context_type == "life_habit"

    # Zero telemetry_entry nodes created
    telemetry_entry_nodes = db_session.query(SemanticContext).filter(
        SemanticContext.user_id == user.user_id,
        SemanticContext.context_type == "telemetry_entry",
    ).count()
    assert telemetry_entry_nodes == 0

    # Rolling summary was updated on the existing node to reflect 20 entries
    assert "20 entries, avg:" in final_nodes[0].raw_content


# =====================================================================
# Test 2: Journal Note Creates Telemetry Entry Node + Structural Edge
# =====================================================================

def test_2_journal_note_creates_telemetry_entry_with_structural_edge(db_session):
    """
    Test 2:
    Logging a journal note creates a 'telemetry_entry' node with a structural
    context edge connecting it directly to its parent tracker definition.
    """
    user = UserProfile(email="test2_journal@offloader.ai")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    # 1. Create parent tracker definition for journal notes
    parent_tracker = TrackerDefinition(
        user_id=user.user_id,
        name="daily_reflection",
        category="journal_note",
        data_type="text",
    )
    db_session.add(parent_tracker)
    db_session.commit()
    db_session.refresh(parent_tracker)

    tracker_ctx = sync_node(db_session, user.user_id, "tracker_definitions", parent_tracker.id)
    assert tracker_ctx is not None
    assert tracker_ctx.context_type == "life_habit"

    # 2. Log a journal note
    note_content = "Shipped the knowledge graph ingestion engine ahead of schedule."
    note_log = TelemetryLog(
        user_id=user.user_id,
        entry_type="journal_note",
        content=note_content,
        log_metadata={
            "tracker_name": "daily_reflection",
            "category": "journal_note",
            "value": note_content,
        },
    )
    db_session.add(note_log)
    db_session.commit()
    db_session.refresh(note_log)

    # 3. Synchronize journal note node
    note_ctx = sync_node(db_session, user.user_id, "telemetry_logs", note_log.id)
    assert note_ctx is not None
    assert note_ctx.context_type == "telemetry_entry"
    assert note_content in note_ctx.raw_content
    assert note_ctx.source_table == "telemetry_logs"
    assert note_ctx.source_id == note_log.id

    # 4. Verify structural edge to tracker definition
    edges = db_session.query(ContextEdge).filter(
        ContextEdge.user_id == user.user_id,
        ContextEdge.kind == "structural",
    ).all()
    assert len(edges) == 1

    edge = edges[0]
    assert edge.kind == "structural"
    assert edge.weight == 1.0
    assert edge.cross_domain is False

    # Endpoints connect note node and tracker node in canonical ordering
    endpoints = {str(edge.source_id), str(edge.target_id)}
    assert str(note_ctx.id) in endpoints
    assert str(tracker_ctx.id) in endpoints


# =====================================================================
# Test 3: Deleting a Goal Deletes Both Its Node and Connected Edges
# =====================================================================

def test_3_deleting_goal_deletes_both_node_and_connected_edges(db_session, client):
    """
    Test 3:
    Deleting a goal schedule item cascades and drops both its semantic_contexts
    record and all connected context_edges.
    """
    user_res = client.post("/api/users", json={"email": "test3_goal_del@offloader.ai"})
    user_id = uuid.UUID(user_res.json()["user_id"])

    # 1. Create a goal item via API
    item_res = client.post("/api/schedule/items", json={
        "user_id": str(user_id),
        "title": "Graduate with Distinction",
        "category": "goal",
        "duration_minutes": 120,
        "priority": 10,
    })
    assert item_res.status_code == 200
    item_id = uuid.UUID(item_res.json()["id"])

    # Verify goal node exists in semantic_contexts
    goal_ctx = db_session.query(SemanticContext).filter(
        SemanticContext.user_id == user_id,
        SemanticContext.source_table == "schedule_items",
        SemanticContext.source_id == item_id,
    ).first()
    assert goal_ctx is not None
    assert goal_ctx.context_type == "project_goal"

    # 2. Create another context node and connect an edge to the goal
    other_ctx = SemanticContext(
        user_id=user_id,
        context_type="life_habit",
        raw_content="Deep study habit 4 hours daily",
        embedding=[0.05] * 1536,
    )
    db_session.add(other_ctx)
    db_session.commit()
    db_session.refresh(other_ctx)

    edge = create_context_edge(
        db=db_session,
        user_id=user_id,
        source_id=goal_ctx.id,
        target_id=other_ctx.id,
        kind="semantic",
        weight=0.88,
    )
    assert edge.id is not None
    assert db_session.query(ContextEdge).filter(ContextEdge.id == edge.id).count() == 1

    # 3. Delete the goal item via API (triggers after_delete cascade hook)
    del_res = client.delete(f"/api/schedule/items/{item_id}")
    assert del_res.status_code == 200

    # 4. Verify goal node is deleted
    goal_ctx_after = db_session.query(SemanticContext).filter(
        SemanticContext.user_id == user_id,
        SemanticContext.source_table == "schedule_items",
        SemanticContext.source_id == item_id,
    ).first()
    assert goal_ctx_after is None

    # 5. Verify connected edge is deleted
    edge_after = db_session.query(ContextEdge).filter(ContextEdge.id == edge.id).first()
    assert edge_after is None

    # Other node remains intact
    other_ctx_after = db_session.query(SemanticContext).filter(SemanticContext.id == other_ctx.id).first()
    assert other_ctx_after is not None


# =====================================================================
# Test 4: sync_node Idempotency (Unaltered Text -> Zero DB Writes)
# =====================================================================

def test_4_sync_node_idempotency_zero_database_writes(db_session):
    """
    Test 4:
    sync_node is strictly idempotent.
    When invoked on an existing node with unaltered content text:
    - SHA-256 content_hash matches
    - Embedding generation is skipped
    - Zero database writes (no db.commit / no UPDATE statements emitted)
    """
    user = UserProfile(email="test4_idempotent@offloader.ai")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    tracker = TrackerDefinition(
        user_id=user.user_id,
        name="meditation",
        category="metric",
        data_type="float",
        unit="minutes",
    )
    db_session.add(tracker)
    db_session.commit()
    db_session.refresh(tracker)

    # First sync: writes node to DB
    ctx1 = sync_node(db_session, user.user_id, "tracker_definitions", tracker.id)
    assert ctx1 is not None
    original_hash = ctx1.content_hash
    assert original_hash is not None

    # Second sync: spy on db_session.commit and get_embedding
    with patch.object(db_session, "commit") as mock_commit, \
         patch("app.services.knowledge_graph.get_embedding") as mock_embed:

        ctx2 = sync_node(db_session, user.user_id, "tracker_definitions", tracker.id)

        # Content was unaltered: zero re-embedding, zero DB commits
        mock_embed.assert_not_called()
        mock_commit.assert_not_called()

        assert ctx2.id == ctx1.id
        assert ctx2.content_hash == original_hash

    # Node count in DB remains exactly 1
    total_nodes = db_session.query(SemanticContext).filter(SemanticContext.user_id == user.user_id).count()
    assert total_nodes == 1


# =====================================================================
# Test 5: Graph Failures Isolated from Primary Transactions
# =====================================================================

def test_5_graph_failures_isolated_from_primary_transactions(db_session, client):
    """
    Test 5:
    Graph engine failures (e.g. embedding failures, DB errors during sync)
    are strictly isolated and DO NOT interrupt primary telemetry or chat transactions.
    """
    user_res = client.post("/api/users", json={"email": "test5_isolation@offloader.ai"})
    user_id = uuid.UUID(user_res.json()["user_id"])

    # 1. Direct sync_node returns None safely upon exception
    tracker = TrackerDefinition(
        user_id=user_id,
        name="cycling",
        category="volume",
        data_type="float",
        unit="miles",
    )
    db_session.add(tracker)
    db_session.commit()
    db_session.refresh(tracker)

    with patch("app.services.knowledge_graph.get_embedding", side_effect=RuntimeError("Vector DB timeout")):
        result = sync_node(db_session, user_id, "tracker_definitions", tracker.id)
        # Must catch exception and return None without raising
        assert result is None

    # 2. Universal telemetry transaction succeeds despite graph synchronization failure
    extraction = UniversalExtraction(
        is_telemetry=True,
        data_points=[
            TelemetryDataPoint(entity="bench_press", value=205, unit="lbs", category="metric"),
            TelemetryDataPoint(entity="workout_notes", value="Great chest workout", unit="", category="journal_note"),
        ],
    )

    with patch("app.services.knowledge_graph.sync_node", side_effect=RuntimeError("Graph engine down")):
        # Primary telemetry process must succeed without raising RuntimeError
        res = process_universal_telemetry(
            db_session,
            user_id=user_id,
            raw_message="benched 205 lbs",
            extraction=extraction,
        )
        assert res["logged_count"] == 2
        assert "bench_press" in res["trackers"]

    # Telemetry logs are persisted safely in the database
    bench_log = db_session.query(TelemetryLog).filter(
        TelemetryLog.user_id == user_id,
        TelemetryLog.entry_type == "metric",
    ).first()
    assert bench_log is not None

    # 3. Chat conversational endpoint returns 200 OK despite graph failure
    with patch("app.services.knowledge_graph.sync_node", side_effect=RuntimeError("Graph down")):
        chat_res = client.post("/api/conversation", json={
            "user_id": str(user_id),
            "message": "did 30 pushups",
        })
        assert chat_res.status_code == 200
        assert "Logged" in chat_res.json()["reply"]


# =====================================================================
# Test 6: Multi-Tenant Safety (Query & Link Isolation)
# =====================================================================

def test_6_multi_tenant_safety_cannot_query_or_link_other_user_nodes(db_session, client):
    """
    Test 6:
    Enforces strict multi-tenant safety:
    - User A cannot query User B's nodes or edges.
    - User A cannot create edges connecting to User B's nodes.
    - User A cannot trigger sync_node for User B's entities.
    """
    # 1. Setup User A (Alice) and User B (Bob)
    res_a = client.post("/api/users", json={"email": "alice_tenant@offloader.ai"})
    user_a = uuid.UUID(res_a.json()["user_id"])

    res_b = client.post("/api/users", json={"email": "bob_tenant@offloader.ai"})
    user_b = uuid.UUID(res_b.json()["user_id"])

    # Create Alice's nodes
    alice_tracker = TrackerDefinition(user_id=user_a, name="yoga", category="metric", data_type="float")
    alice_item = ScheduleItem(user_id=user_a, title="Alice Project", category="project", duration_minutes=60)
    db_session.add_all([alice_tracker, alice_item])
    db_session.commit()

    ctx_a1 = sync_node(db_session, user_a, "tracker_definitions", alice_tracker.id)
    ctx_a2 = sync_node(db_session, user_a, "schedule_items", alice_item.id)
    assert ctx_a1 is not None and ctx_a2 is not None

    # Create Bob's nodes
    bob_tracker = TrackerDefinition(user_id=user_b, name="boxing", category="metric", data_type="float")
    bob_item = ScheduleItem(user_id=user_b, title="Bob Exam", category="exam", duration_minutes=90)
    db_session.add_all([bob_tracker, bob_item])
    db_session.commit()

    ctx_b1 = sync_node(db_session, user_b, "tracker_definitions", bob_tracker.id)
    ctx_b2 = sync_node(db_session, user_b, "schedule_items", bob_item.id)
    assert ctx_b1 is not None and ctx_b2 is not None

    # 2. Query Isolation: Alice queries knowledge graph
    graph_a = compute_knowledge_graph(db_session, user_a)
    alice_node_ids = {n["id"] for n in graph_a["nodes"]}

    assert str(ctx_a1.id) in alice_node_ids
    assert str(ctx_a2.id) in alice_node_ids
    # Bob's nodes MUST NOT appear in Alice's graph
    assert str(ctx_b1.id) not in alice_node_ids
    assert str(ctx_b2.id) not in alice_node_ids

    # Query via API endpoint
    api_res_a = client.get(f"/api/knowledge-graph?user_id={user_a}")
    assert api_res_a.status_code == 200
    api_nodes_a = {n["id"] for n in api_res_a.json()["nodes"]}
    assert str(ctx_b1.id) not in api_nodes_a

    # 3. Link Isolation: Alice attempts to create edge linking to Bob's node
    with pytest.raises(ValueError, match="Multi-tenant violation"):
        create_context_edge(
            db=db_session,
            user_id=user_a,
            source_id=ctx_a1.id,
            target_id=ctx_b1.id,  # Cross-tenant link attempt
            kind="semantic",
        )

    # Bob attempts to create edge linking to Alice's node
    with pytest.raises(ValueError, match="Multi-tenant violation"):
        create_context_edge(
            db=db_session,
            user_id=user_b,
            source_id=ctx_b1.id,
            target_id=ctx_a1.id,
            kind="semantic",
        )

    # Alice attempts to link two of Bob's nodes under Alice's account
    with pytest.raises(ValueError, match="Multi-tenant violation"):
        create_context_edge(
            db=db_session,
            user_id=user_a,
            source_id=ctx_b1.id,
            target_id=ctx_b2.id,
            kind="semantic",
        )

    # Assert zero edges created
    assert db_session.query(ContextEdge).count() == 0

    # 4. Ingestion Isolation: Alice cannot sync Bob's tracker
    stolen_sync = sync_node(db_session, user_a, "tracker_definitions", bob_tracker.id)
    assert stolen_sync is None

    # Alice cannot sync Bob's schedule item
    stolen_item_sync = sync_node(db_session, user_a, "schedule_items", bob_item.id)
    assert stolen_item_sync is None


# =====================================================================
# Backfill Migration Utility Tests (Idempotency & Summary Reporting)
# =====================================================================

def test_backfill_migration_utility_idempotency_and_summary_report(db_session):
    """
    Tests scripts/backfill_knowledge_graph.py:
    - Iterates over active users
    - Syncs tracker_definitions, journal notes, qualifying schedule_items
    - Skips non-qualifying schedule items and non-journal telemetry
    - Strictly idempotent: multiple consecutive runs produce identical node and edge counts
    - Clean summary report breaking down counts by node group and type
    """
    # 1. Create 2 test users
    u1 = UserProfile(email="user1_backfill@offloader.ai")
    u2 = UserProfile(email="user2_backfill@offloader.ai")
    db_session.add_all([u1, u2])
    db_session.commit()
    db_session.refresh(u1)
    db_session.refresh(u2)

    # User 1 entities:
    # - 2 trackers
    # - 1 journal note + 2 numeric logs (numeric must NOT generate nodes)
    # - 1 exam + 1 goal + 1 class (class must NOT generate nodes)
    trk1_u1 = TrackerDefinition(user_id=u1.user_id, name="hydration", category="volume", data_type="float", unit="l")
    trk2_u1 = TrackerDefinition(user_id=u1.user_id, name="reflection", category="journal_note", data_type="text")
    db_session.add_all([trk1_u1, trk2_u1])
    db_session.commit()

    log_journal_u1 = TelemetryLog(
        user_id=u1.user_id,
        entry_type="journal_note",
        content="Reflection entry 1",
        log_metadata={"tracker_name": "reflection", "category": "journal_note", "value": "Reflection entry 1"},
    )
    log_num1_u1 = TelemetryLog(
        user_id=u1.user_id,
        entry_type="metric",
        content="drank 1l",
        log_metadata={"tracker_name": "hydration", "category": "metric", "value": 1.0},
    )
    log_num2_u1 = TelemetryLog(
        user_id=u1.user_id,
        entry_type="metric",
        content="drank 2l",
        log_metadata={"tracker_name": "hydration", "category": "metric", "value": 2.0},
    )
    db_session.add_all([log_journal_u1, log_num1_u1, log_num2_u1])

    item_exam_u1 = ScheduleItem(user_id=u1.user_id, title="OS Midterm", category="exam", duration_minutes=120)
    item_goal_u1 = ScheduleItem(user_id=u1.user_id, title="Submit Paper", category="goal", duration_minutes=90)
    item_class_u1 = ScheduleItem(user_id=u1.user_id, title="Physics Lecture", category="class", duration_minutes=60)
    db_session.add_all([item_exam_u1, item_goal_u1, item_class_u1])
    db_session.commit()

    # User 2 entities:
    # - 1 tracker
    # - 1 qualifying project
    trk1_u2 = TrackerDefinition(user_id=u2.user_id, name="running", category="metric", data_type="float", unit="km")
    item_proj_u2 = ScheduleItem(user_id=u2.user_id, title="Compiler Project", category="project", duration_minutes=180)
    db_session.add_all([trk1_u2, item_proj_u2])
    db_session.commit()

    # 2. Run Backfill: Pass 1
    summary1 = backfill_knowledge_graph(db=db_session)
    assert summary1["status"] == "success"
    assert summary1["users_processed"] == 2

    # Expected node breakdown:
    # User 1: 2 trackers (life_habit) + 1 journal note (telemetry_entry) + 2 schedule items (project_goal) = 5 nodes
    # User 2: 1 tracker (life_habit) + 1 schedule item (project_goal) = 2 nodes
    # Total nodes = 7
    total_nodes_pass1 = summary1["total_nodes"]
    assert total_nodes_pass1 == 7

    # Edges: 1 structural edge connecting reflection note to reflection tracker
    total_edges_pass1 = summary1["total_edges"]
    assert total_edges_pass1 == 1

    # Check breakdown by group
    groups = summary1["nodes_by_group"]
    assert groups["TELEMETRY & HABITS"] == 4  # 3 life_habit + 1 telemetry_entry
    assert groups["GOALS & PROJECTS"] == 3    # 3 project_goal

    # Check breakdown by type
    types = summary1["nodes_by_type"]
    assert types["life_habit"] == 3
    assert types["telemetry_entry"] == 1
    assert types["project_goal"] == 3

    # Check scanned statistics
    scanned = summary1["scanned"]
    assert scanned["trackers"] == 3
    assert scanned["journal_notes"] == 1
    assert scanned["schedule_items"] == 3  # OS Midterm, Submit Paper, Compiler Project (Physics class excluded)

    # 3. Run Backfill: Pass 2 (STRICT IDEMPOTENCY VERIFICATION)
    summary2 = backfill_knowledge_graph(db=db_session)
    assert summary2["status"] == "success"
    assert summary2["users_processed"] == 2
    assert summary2["total_nodes"] == total_nodes_pass1
    assert summary2["total_edges"] == total_edges_pass1
    assert summary2["nodes_by_group"] == summary1["nodes_by_group"]
    assert summary2["nodes_by_type"] == summary1["nodes_by_type"]
    assert summary2["edges_by_kind"] == summary1["edges_by_kind"]

    # 4. Run Backfill: Pass 3 for a specific user
    summary3 = backfill_knowledge_graph(db=db_session, user_id=u1.user_id)
    assert summary3["users_processed"] == 1
    assert summary3["total_nodes"] == 5
    assert summary3["total_edges"] == 1
