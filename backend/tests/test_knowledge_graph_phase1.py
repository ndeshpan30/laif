import uuid
import pytest
from sqlalchemy.exc import IntegrityError
from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, engine, SessionLocal
from app.models.user import UserProfile
from app.models.semantic import (
    SemanticContext,
    ContextEdge,
    compute_content_hash,
)
from app.services.knowledge_graph import (
    CANONICAL_TYPE_REGISTRY,
    DEFAULT_TYPE_METADATA,
    get_canonical_type_info,
    get_canonical_group,
    get_canonical_glyph,
    get_canonical_style,
    order_edge_endpoints,
    create_context_edge,
    invalidate_knowledge_graph_cache,
)
from app.services.rag import generate_embedding_1536


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
# 1. Canonical Type Registry & Helpers (Design Decisions D4 & D5)
# =====================================================================

def test_canonical_type_registry_constants():
    """Verify exact mappings specified in Design Decisions D4 and D5."""
    assert CANONICAL_TYPE_REGISTRY["episodic_constraint"] == {
        "group": "CONSTRAINTS",
        "glyph": "!",
        "style": "fill var(--accent)",
    }
    assert CANONICAL_TYPE_REGISTRY["syllabus_module"] == {
        "group": "SYLLABUS",
        "glyph": "M",
        "style": "fill var(--text-ink), text var(--bg-paper)",
    }
    assert CANONICAL_TYPE_REGISTRY["life_habit"] == {
        "group": "TELEMETRY & HABITS",
        "glyph": "~",
        "style": "fill var(--ochre)",
    }
    assert CANONICAL_TYPE_REGISTRY["telemetry_entry"] == {
        "group": "TELEMETRY & HABITS",
        "glyph": "T",
        "style": "paper fill, ink outline",
    }
    assert CANONICAL_TYPE_REGISTRY["project_goal"] == {
        "group": "GOALS & PROJECTS",
        "glyph": "G",
        "style": "paper fill, double hairline",
    }
    assert DEFAULT_TYPE_METADATA == {
        "group": "OTHER",
        "glyph": "•",
        "style": "neutral ink outline",
    }


def test_canonical_type_helpers():
    """Test get_canonical_type_info, group, glyph, and style helpers with fallback."""
    # Known types (case-insensitive and trimmed)
    assert get_canonical_group("syllabus_module") == "SYLLABUS"
    assert get_canonical_glyph("syllabus_module") == "M"
    assert get_canonical_style("syllabus_module") == "fill var(--text-ink), text var(--bg-paper)"

    assert get_canonical_group("  EPISODIC_CONSTRAINT  ") == "CONSTRAINTS"
    assert get_canonical_glyph("episodic_constraint") == "!"
    assert get_canonical_style("episodic_constraint") == "fill var(--accent)"

    assert get_canonical_group("life_habit") == "TELEMETRY & HABITS"
    assert get_canonical_glyph("life_habit") == "~"

    assert get_canonical_group("telemetry_entry") == "TELEMETRY & HABITS"
    assert get_canonical_glyph("telemetry_entry") == "T"

    assert get_canonical_group("project_goal") == "GOALS & PROJECTS"
    assert get_canonical_glyph("project_goal") == "G"

    # Unknown / unmapped types -> Default fallback
    unknown_info = get_canonical_type_info("arbitrary_unknown_type")
    assert unknown_info["group"] == "OTHER"
    assert unknown_info["glyph"] == "•"
    assert unknown_info["style"] == "neutral ink outline"

    # None and empty string
    assert get_canonical_type_info(None) == DEFAULT_TYPE_METADATA
    assert get_canonical_type_info("") == DEFAULT_TYPE_METADATA
    assert get_canonical_group(None) == "OTHER"
    assert get_canonical_glyph(None) == "•"
    assert get_canonical_style(None) == "neutral ink outline"


# =====================================================================
# 2. SHA-256 Content Hash & Edge Endpoint Ordering Helpers
# =====================================================================

def test_compute_content_hash():
    """Verify compute_content_hash produces 64-char SHA-256 hex digest."""
    text1 = "Discrete Mathematics Module 1: Propositional Logic"
    hash1 = compute_content_hash(text1)
    assert isinstance(hash1, str)
    assert len(hash1) == 64
    # Deterministic
    assert compute_content_hash(text1) == hash1
    # Different text yields different hash
    assert compute_content_hash("Another content") != hash1


def test_order_edge_endpoints():
    """Verify order_edge_endpoints enforces source_id < target_id and blocks self-loops."""
    id1 = uuid.UUID("11111111-1111-1111-1111-111111111111")
    id2 = uuid.UUID("22222222-2222-2222-2222-222222222222")

    # Correct order
    src, tgt = order_edge_endpoints(id1, id2)
    assert src == id1 and tgt == id2

    # Reverse order swapped
    src, tgt = order_edge_endpoints(id2, id1)
    assert src == id1 and tgt == id2

    # Self-referential edge raises ValueError
    with pytest.raises(ValueError, match="Self-referential edge not allowed"):
        order_edge_endpoints(id1, id1)


# =====================================================================
# 3. Database Model & Schema Constraints (Design Decisions D1 & D2)
# =====================================================================

def test_semantic_context_provenance_and_idempotent_upsert(db_session):
    """
    Tests provenance columns (source_table, source_id, content_hash)
    and UNIQUE(user_id, source_table, source_id) idempotent upsert constraint.
    """
    user = UserProfile(email="provenance_test@offloader.ai")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    item_source_id = uuid.uuid4()
    content = "Distributed Systems Exam - Ch 4 Paxos consensus"
    c_hash = compute_content_hash(content)

    ctx1 = SemanticContext(
        user_id=user.user_id,
        context_type="syllabus_module",
        subject="Distributed Systems",
        raw_content=content,
        source_table="schedule_items",
        source_id=item_source_id,
        content_hash=c_hash,
    )
    db_session.add(ctx1)
    db_session.commit()
    db_session.refresh(ctx1)

    assert ctx1.id is not None
    assert ctx1.source_table == "schedule_items"
    assert ctx1.source_id == item_source_id
    assert ctx1.content_hash == c_hash

    # Duplicate (user_id, source_table, source_id) must raise IntegrityError
    ctx_dup = SemanticContext(
        user_id=user.user_id,
        context_type="syllabus_module",
        subject="Distributed Systems",
        raw_content="Different text, but duplicate provenance",
        source_table="schedule_items",
        source_id=item_source_id,
        content_hash=compute_content_hash("Different text"),
    )
    db_session.add(ctx_dup)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Different source_id for same user succeeds
    ctx2 = SemanticContext(
        user_id=user.user_id,
        context_type="syllabus_module",
        raw_content="Ch 5 Raft consensus",
        source_table="schedule_items",
        source_id=uuid.uuid4(),
    )
    db_session.add(ctx2)
    db_session.commit()
    assert ctx2.id is not None


def test_context_edge_canonical_ordering_constraint(db_session):
    """
    Tests ContextEdge ck_canonical_edge_order CHECK constraint (source_id < target_id)
    and uq_context_edge uniqueness constraint.
    """
    user = UserProfile(email="edge_constraints@offloader.ai")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    # Create two semantic contexts with known ordered IDs
    id_low = uuid.UUID("10000000-0000-0000-0000-000000000001")
    id_high = uuid.UUID("20000000-0000-0000-0000-000000000002")

    ctx_low = SemanticContext(id=id_low, user_id=user.user_id, context_type="syllabus_module", raw_content="Low Node")
    ctx_high = SemanticContext(id=id_high, user_id=user.user_id, context_type="project_goal", raw_content="High Node")
    db_session.add_all([ctx_low, ctx_high])
    db_session.commit()

    # 1. Valid insertion with source_id < target_id
    valid_edge = ContextEdge(
        user_id=user.user_id,
        source_id=id_low,
        target_id=id_high,
        kind="semantic",
        weight=0.88,
        cross_domain=True,
    )
    db_session.add(valid_edge)
    db_session.commit()
    db_session.refresh(valid_edge)
    assert valid_edge.id is not None
    assert valid_edge.weight == 0.88
    assert valid_edge.cross_domain is True

    # 2. Invalid insertion violating ck_canonical_edge_order (source_id >= target_id)
    inverted_edge = ContextEdge(
        user_id=user.user_id,
        source_id=id_high,
        target_id=id_low,
        kind="semantic",
        weight=0.5,
    )
    db_session.add(inverted_edge)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # 3. Invalid self-referential edge (source_id == target_id)
    self_loop = ContextEdge(
        user_id=user.user_id,
        source_id=id_low,
        target_id=id_low,
        kind="semantic",
    )
    db_session.add(self_loop)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # 4. Duplicate edge with same (user_id, source_id, target_id, kind) raises IntegrityError
    dup_edge = ContextEdge(
        user_id=user.user_id,
        source_id=id_low,
        target_id=id_high,
        kind="semantic",
        weight=0.95,
    )
    db_session.add(dup_edge)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # 5. Same endpoints with different kind ('structural') is allowed
    structural_edge = ContextEdge(
        user_id=user.user_id,
        source_id=id_low,
        target_id=id_high,
        kind="structural",
        weight=1.0,
        cross_domain=False,
    )
    db_session.add(structural_edge)
    db_session.commit()
    assert structural_edge.id is not None


def test_create_context_edge_helper(db_session):
    """
    Tests create_context_edge automatically sorts endpoints to satisfy
    ck_canonical_edge_order regardless of invocation parameter order.
    """
    user = UserProfile(email="edge_helper@offloader.ai")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    id_a = uuid.UUID("30000000-0000-0000-0000-000000000003")
    id_b = uuid.UUID("40000000-0000-0000-0000-000000000004")

    c_a = SemanticContext(id=id_a, user_id=user.user_id, context_type="life_habit", raw_content="Morning run")
    c_b = SemanticContext(id=id_b, user_id=user.user_id, context_type="telemetry_entry", raw_content="Logged 5km run")
    db_session.add_all([c_a, c_b])
    db_session.commit()

    # Pass in inverted order: source_id=id_b (greater), target_id=id_a (lesser)
    edge = create_context_edge(
        db=db_session,
        user_id=user.user_id,
        source_id=id_b,
        target_id=id_a,
        kind="structural",
        weight=1.0,
        cross_domain=False,
    )
    assert str(edge.source_id) == str(id_a)
    assert str(edge.target_id) == str(id_b)


def test_cascade_delete_context_edges(db_session):
    """Verifies that deleting a user or a semantic context cascades to ContextEdge."""
    user = UserProfile(email="cascade_edge@offloader.ai")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    id_1 = uuid.UUID("50000000-0000-0000-0000-000000000001")
    id_2 = uuid.UUID("50000000-0000-0000-0000-000000000002")

    c1 = SemanticContext(id=id_1, user_id=user.user_id, context_type="syllabus_module", raw_content="Node 1")
    c2 = SemanticContext(id=id_2, user_id=user.user_id, context_type="syllabus_module", raw_content="Node 2")
    db_session.add_all([c1, c2])
    db_session.commit()

    edge = create_context_edge(db_session, user.user_id, id_1, id_2, kind="semantic")
    assert db_session.query(ContextEdge).filter(ContextEdge.id == edge.id).count() == 1

    # Deleting c1 should cascade to the edge
    db_session.delete(c1)
    db_session.commit()
    assert db_session.query(ContextEdge).filter(ContextEdge.id == edge.id).count() == 0


# =====================================================================
# 4. API Integration & Enriched Knowledge Graph Response
# =====================================================================

def test_knowledge_graph_api_returns_canonical_node_attributes(client):
    """
    Verifies that /api/knowledge-graph returns nodes enriched with
    canonical group, glyph, style, and provenance columns.
    """
    user_res = client.post("/api/users", json={"email": "kg_canonical@offloader.ai"})
    user_id = user_res.json()["user_id"]

    db = SessionLocal()
    c_syllabus = SemanticContext(
        id=uuid.uuid4(),
        user_id=user_id,
        context_type="syllabus_module",
        subject="AI",
        raw_content="Graph Neural Networks and Message Passing algorithms.",
        embedding=generate_embedding_1536("Graph Neural Networks and Message Passing algorithms."),
        source_table="upload",
        source_id=uuid.uuid4(),
        content_hash=compute_content_hash("Graph Neural Networks and Message Passing algorithms."),
    )
    c_constraint = SemanticContext(
        id=uuid.uuid4(),
        user_id=user_id,
        context_type="episodic_constraint",
        subject="Health",
        raw_content="No heavy cognitive tasks after 10 PM.",
        embedding=generate_embedding_1536("No heavy cognitive tasks after 10 PM."),
        source_table="conversation",
    )
    c_habit = SemanticContext(
        id=uuid.uuid4(),
        user_id=user_id,
        context_type="life_habit",
        raw_content="Morning meditation 15 mins daily.",
        embedding=generate_embedding_1536("Morning meditation 15 mins daily."),
        source_table="tracker_definitions",
    )
    c_telemetry = SemanticContext(
        id=uuid.uuid4(),
        user_id=user_id,
        context_type="telemetry_entry",
        raw_content="Meditation session completed 15 mins.",
        embedding=generate_embedding_1536("Meditation session completed 15 mins."),
        source_table="telemetry_logs",
    )
    c_goal = SemanticContext(
        id=uuid.uuid4(),
        user_id=user_id,
        context_type="project_goal",
        raw_content="Publish GNN paper by December.",
        embedding=generate_embedding_1536("Publish GNN paper by December."),
    )
    c_unknown = SemanticContext(
        id=uuid.uuid4(),
        user_id=user_id,
        context_type="custom_unregistered_concept",
        raw_content="Random user note on miscellaneous thoughts.",
        embedding=generate_embedding_1536("Random user note on miscellaneous thoughts."),
    )

    db.add_all([c_syllabus, c_constraint, c_habit, c_telemetry, c_goal, c_unknown])
    db.commit()
    db.close()

    res = client.get(f"/api/knowledge-graph?user_id={user_id}&threshold=0.30")
    assert res.status_code == 200
    data = res.json()

    assert data["total_nodes"] == 6
    nodes_by_type = {n["type"]: n for n in data["nodes"]}

    # Verify canonical attributes for each type
    s_node = nodes_by_type["syllabus_module"]
    assert s_node["group"] == "SYLLABUS"
    assert s_node["glyph"] == "M"
    assert s_node["style"] == "fill var(--text-ink), text var(--bg-paper)"
    assert s_node["source_table"] == "upload"
    assert s_node["source_id"] is not None
    assert s_node["content_hash"] is not None

    e_node = nodes_by_type["episodic_constraint"]
    assert e_node["group"] == "CONSTRAINTS"
    assert e_node["glyph"] == "!"
    assert e_node["style"] == "fill var(--accent)"
    assert e_node["source_table"] == "conversation"

    h_node = nodes_by_type["life_habit"]
    assert h_node["group"] == "TELEMETRY & HABITS"
    assert h_node["glyph"] == "~"
    assert h_node["style"] == "fill var(--ochre)"

    t_node = nodes_by_type["telemetry_entry"]
    assert t_node["group"] == "TELEMETRY & HABITS"
    assert t_node["glyph"] == "T"
    assert t_node["style"] == "paper fill, ink outline"

    g_node = nodes_by_type["project_goal"]
    assert g_node["group"] == "GOALS & PROJECTS"
    assert g_node["glyph"] == "G"
    assert g_node["style"] == "paper fill, double hairline"

    u_node = nodes_by_type["custom_unregistered_concept"]
    assert u_node["group"] == "OTHER"
    assert u_node["glyph"] == "•"
    assert u_node["style"] == "neutral ink outline"
