import uuid
import pytest
from uuid import uuid4
from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, engine, SessionLocal
from app.models.user import UserProfile
from app.models.semantic import SemanticContext, ContextEdge
from app.services.rag import generate_embedding_1536
from app.services.knowledge_graph import (
    compute_knowledge_graph,
    recompute_edges_for,
    create_context_edge,
    invalidate_knowledge_graph_cache,
)


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


def test_knowledge_graph_empty_for_new_user(client):
    user_res = client.post("/api/users", json={"email": "kg_empty@offloader.ai"})
    user_id = user_res.json()["user_id"]

    res = client.get(f"/api/knowledge-graph?user_id={user_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["nodes"] == []
    assert data["edges"] == []
    assert data["types_present"] == []
    assert data["truncated"] is False
    assert data["total_nodes"] == 0
    assert data["total_edges"] == 0


def test_knowledge_graph_sample_user_derivation_and_edges(client):
    user_res = client.post("/api/users", json={"email": "kg_sample@offloader.ai"})
    user_id = user_res.json()["user_id"]
    db = SessionLocal()

    # 1. Create sample syllabus modules and episodic constraints
    c1 = SemanticContext(
        id=uuid4(),
        user_id=user_id,
        context_type="syllabus_module",
        subject="Computer Networks",
        raw_content="Subnetting, CIDR, VLSM basics and IPv4 packet header structure.",
        embedding=generate_embedding_1536("Subnetting, CIDR, VLSM basics and IPv4 packet header structure."),
        context_metadata={"topic": "Subnetting & CIDR", "module_number": 2},
    )
    c2 = SemanticContext(
        id=uuid4(),
        user_id=user_id,
        context_type="syllabus_module",
        subject="Computer Networks",
        raw_content="IPv4 address space allocation, classless inter-domain routing CIDR and subnet masks.",
        embedding=generate_embedding_1536("IPv4 address space allocation, classless inter-domain routing CIDR and subnet masks."),
        context_metadata={"topic": "CIDR & Subnetting", "module_number": 2},
    )
    c3 = SemanticContext(
        id=uuid4(),
        user_id=user_id,
        context_type="syllabus_module",
        subject="Computer Networks",
        raw_content="Routing Information Protocol RIP, OSPF link state routing and Dijkstra algorithms.",
        embedding=generate_embedding_1536("Routing Information Protocol RIP, OSPF link state routing and Dijkstra algorithms."),
        context_metadata={"topic": "Routing Protocols", "module_number": 3},
    )
    c4 = SemanticContext(
        id=uuid4(),
        user_id=user_id,
        context_type="episodic_constraint",
        subject="Personal Constraint",
        raw_content="No screen work past 10 PM to prevent late night migraines.",
        embedding=generate_embedding_1536("No screen work past 10 PM to prevent late night migraines."),
        context_metadata={"source": "conversation", "type": "episodic_constraint"},
    )
    c5 = SemanticContext(
        id=uuid4(),
        user_id=user_id,
        context_type="episodic_constraint",
        subject="Personal Constraint",
        raw_content="Screen work after 10 PM triggers severe migraine episodes.",
        embedding=generate_embedding_1536("Screen work after 10 PM triggers severe migraine episodes."),
        context_metadata={"source": "conversation", "type": "episodic_constraint"},
    )

    c1_id = str(c1.id)
    c4_id = str(c4.id)

    db.add_all([c1, c2, c3, c4, c5])
    db.commit()
    db.close()

    # Call endpoint with threshold 0.50 to observe semantic edges
    res = client.get(f"/api/knowledge-graph?user_id={user_id}&threshold=0.50&top_k=3")
    assert res.status_code == 200
    data = res.json()

    assert data["total_nodes"] == 5
    assert len(data["nodes"]) == 5

    # Check node structure
    node_map = {n["id"]: n for n in data["nodes"]}
    assert c1_id in node_map
    n1 = node_map[c1_id]
    assert n1["label"] == "Subnetting & CIDR"
    assert n1["type"] == "syllabus_module"
    assert n1["subject"] == "Computer Networks"
    assert "Subnetting" in n1["snippet"]
    assert isinstance(n1["degree"], int)

    # Check constraint node
    n4 = node_map[c4_id]
    assert n4["type"] == "episodic_constraint"
    assert "No screen work past 10 PM" in n4["label"]

    # Verify edge connections
    edges = data["edges"]
    assert len(edges) > 0
    for e in edges:
        assert "source" in e
        assert "target" in e
        assert "weight" in e
        assert "kind" in e
        assert "cross_domain" in e
        assert e["weight"] >= 0.50

    # Verify bidirectional deduplication: no edge (A, B) and (B, A) simultaneously
    pairs = set()
    for e in edges:
        p = tuple(sorted([e["source"], e["target"]]))
        assert p not in pairs, f"Duplicate edge found for pair {p}"
        pairs.add(p)


# =====================================================================
# Phase 2 Dynamic Edge Engine Tests: recompute_edges_for
# =====================================================================

def make_test_vector(base_val: float, seed: int = 0) -> list[float]:
    """Creates a deterministic 1536 unit vector with controllable cosine similarity."""
    dim = 1536
    vec = [0.0] * dim
    vec[0] = base_val
    vec[1 + (seed % 100)] = (1.0 - base_val * base_val) ** 0.5 if base_val < 1.0 else 0.0
    norm = sum(x * x for x in vec) ** 0.5
    return [round(x / norm, 6) for x in vec] if norm > 0 else vec


def test_recompute_edges_top_k_and_cross_domain_guarantee(db_session):
    """
    Validates recompute_edges_for logic:
    - Top-6 neighbors with cosine similarity >= 0.72.
    - Cross-Domain Guarantee: up to 2 additional neighbors with different UI group and sim >= 0.60.
    - Enforces canonical edge storage: source_id < target_id and unique (user_id, source_id, target_id, kind).
    - Neighbors with sim < 0.60 are rejected.
    """
    user = UserProfile(email="recompute_test@offloader.ai")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    target_emb = [1.0] + [0.0] * 1535

    # 1. Target node in group 'TELEMETRY & HABITS' (context_type='life_habit')
    target_node = SemanticContext(
        id=uuid4(),
        user_id=user.user_id,
        context_type="life_habit",
        raw_content="Target habit tracker node",
        embedding=target_emb,
    )
    db_session.add(target_node)

    # 2. Add 8 neighbors in the SAME UI group ('TELEMETRY & HABITS')
    # 7 neighbors have similarity >= 0.72 (0.95, 0.90, 0.85, 0.80, 0.78, 0.75, 0.73)
    # 1 neighbor has similarity 0.65 (< 0.72, and same group -> must NOT be selected)
    same_group_sims = [0.95, 0.90, 0.85, 0.80, 0.78, 0.75, 0.73, 0.65]
    same_group_nodes = []
    for idx, sim in enumerate(same_group_sims):
        node = SemanticContext(
            id=uuid4(),
            user_id=user.user_id,
            context_type="life_habit",
            raw_content=f"Same group neighbor {idx} with sim {sim}",
            embedding=make_test_vector(sim, seed=idx + 1),
        )
        same_group_nodes.append(node)
        db_session.add(node)

    # 3. Add 3 neighbors in a DIFFERENT UI group ('SYLLABUS', context_type='syllabus_module')
    # sim 0.68 (>= 0.60 -> cross-domain candidate 1)
    # sim 0.64 (>= 0.60 -> cross-domain candidate 2)
    # sim 0.55 (< 0.60 -> must be rejected)
    diff_group_sims = [0.68, 0.64, 0.55]
    diff_group_nodes = []
    for idx, sim in enumerate(diff_group_sims):
        node = SemanticContext(
            id=uuid4(),
            user_id=user.user_id,
            context_type="syllabus_module",
            raw_content=f"Cross domain neighbor {idx} with sim {sim}",
            embedding=make_test_vector(sim, seed=idx + 50),
        )
        diff_group_nodes.append(node)
        db_session.add(node)

    db_session.commit()

    # Execute recompute_edges_for
    new_edges = recompute_edges_for(db_session, target_node.id)

    # Top-6 (from the 7 same-group candidates >= 0.72) + 2 cross-domain (0.68, 0.64) = 8 edges
    assert len(new_edges) == 8

    # Verify cross_domain flags:
    cross_edges = [e for e in new_edges if e.cross_domain is True]
    same_edges = [e for e in new_edges if e.cross_domain is False]
    assert len(cross_edges) == 2
    assert len(same_edges) == 6

    # Verify canonical edge ordering source_id < target_id
    for e in new_edges:
        assert str(e.source_id) < str(e.target_id)
        assert e.kind == "semantic"

    # Verify the 0.55 similarity node was NOT connected
    diff_55_id = diff_group_nodes[2].id
    connected_ids = {
        e.target_id if e.source_id == target_node.id else e.source_id
        for e in new_edges
    }
    assert diff_55_id not in connected_ids

    # Verify the 0.65 same-group node was NOT connected (below 0.72 top-K and same group)
    same_65_id = same_group_nodes[7].id
    assert same_65_id not in connected_ids


def test_recompute_edges_purges_stale_semantic_edges_and_preserves_structural(db_session):
    """
    Verifies that recompute_edges_for purges stale semantic edges for this node
    while strictly preserving structural edges (e.g., journal note -> tracker links).
    """
    user = UserProfile(email="purge_stale@offloader.ai")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    node_a = SemanticContext(
        id=uuid4(),
        user_id=user.user_id,
        context_type="life_habit",
        raw_content="Node A habit",
        embedding=[1.0] + [0.0] * 1535,
    )
    node_b = SemanticContext(
        id=uuid4(),
        user_id=user.user_id,
        context_type="life_habit",
        raw_content="Node B habit",
        embedding=[0.4] + [0.0] * 1535,  # Low similarity, will not qualify anew
    )
    node_c = SemanticContext(
        id=uuid4(),
        user_id=user.user_id,
        context_type="telemetry_entry",
        raw_content="Node C structural note",
        embedding=[0.2] + [0.0] * 1535,
    )
    db_session.add_all([node_a, node_b, node_c])
    db_session.commit()

    # Pre-populate an old semantic edge between Node A and Node B
    old_semantic_edge = create_context_edge(
        db_session, user.user_id, node_a.id, node_b.id, kind="semantic", weight=0.50
    )
    # Pre-populate a structural edge between Node A and Node C
    structural_edge = create_context_edge(
        db_session, user.user_id, node_a.id, node_c.id, kind="structural", weight=1.0
    )

    assert db_session.query(ContextEdge).filter(ContextEdge.id == old_semantic_edge.id).count() == 1
    assert db_session.query(ContextEdge).filter(ContextEdge.id == structural_edge.id).count() == 1

    # Recompute edges for Node A (Node B similarity is 0.40, which is below 0.72)
    recompute_edges_for(db_session, node_a.id)

    # Old semantic edge MUST be purged
    assert db_session.query(ContextEdge).filter(ContextEdge.id == old_semantic_edge.id).first() is None

    # Structural edge MUST be preserved intact
    persisted_struct = db_session.query(ContextEdge).filter(ContextEdge.id == structural_edge.id).first()
    assert persisted_struct is not None
    assert persisted_struct.kind == "structural"


# =====================================================================
# Upgraded GET /api/knowledge-graph Endpoint Tests
# =====================================================================

def test_knowledge_graph_api_drops_minimum_5_nodes_check(client):
    """
    Validates:
    - Drop all 'minimum 5 nodes' checks:
      - If N >= 1 (e.g. N = 1 isolated node), returns existing node with degree 0, empty edges.
      - If N == 0, returns empty lists.
    """
    user_res = client.post("/api/users", json={"email": "drop_min5@offloader.ai"})
    user_id = user_res.json()["user_id"]

    # 1. N = 0: returns empty lists
    res_0 = client.get(f"/api/knowledge-graph?user_id={user_id}")
    assert res_0.status_code == 200
    d0 = res_0.json()
    assert d0["nodes"] == []
    assert d0["edges"] == []
    assert d0["types_present"] == []
    assert d0["truncated"] is False

    # 2. N = 1: Single isolated node
    node_id = uuid4()
    db = SessionLocal()
    single_node = SemanticContext(
        id=node_id,
        user_id=uuid.UUID(user_id),
        context_type="project_goal",
        subject="AI Thesis",
        raw_content="Complete Master's thesis on Autonomous Cognitive Architectures.",
        embedding=[0.5] * 1536,
    )
    db.add(single_node)
    db.commit()
    db.close()

    res_1 = client.get(f"/api/knowledge-graph?user_id={user_id}")
    assert res_1.status_code == 200
    d1 = res_1.json()

    # Returns 1 node with degree 0
    assert len(d1["nodes"]) == 1
    n = d1["nodes"][0]
    assert n["id"] == str(node_id)
    assert n["type"] == "project_goal"
    assert n["group"] == "GOALS & PROJECTS"
    assert n["degree"] == 0
    assert n["subject"] == "AI Thesis"

    # Edges are empty for single isolated node
    assert d1["edges"] == []
    assert len(d1["types_present"]) == 1
    assert d1["types_present"][0]["type"] == "project_goal"
    assert d1["types_present"][0]["count"] == 1
    assert d1["types_present"][0]["glyph"] == "G"
    assert d1["truncated"] is False


def test_knowledge_graph_api_filtering_types_and_subject(client):
    """
    Validates query parameter filters:
    - types (comma-separated): filters nodes and isolates edges to filtered nodes.
    - subject: filters nodes by subject.
    - min_similarity: filters edge weights.
    """
    user_res = client.post("/api/users", json={"email": "kg_filters@offloader.ai"})
    user_id = user_res.json()["user_id"]
    db = SessionLocal()

    # Node 1: Syllabus module (Computer Networks)
    n1 = SemanticContext(
        id=uuid4(),
        user_id=uuid.UUID(user_id),
        context_type="syllabus_module",
        subject="Computer Networks",
        raw_content="TCP UDP protocols and reliable byte stream transport.",
        embedding=generate_embedding_1536("TCP UDP protocols and reliable byte stream transport."),
    )
    # Node 2: Syllabus module (Computer Networks)
    n2 = SemanticContext(
        id=uuid4(),
        user_id=uuid.UUID(user_id),
        context_type="syllabus_module",
        subject="Computer Networks",
        raw_content="TCP congestion control window sliding and transport layer.",
        embedding=generate_embedding_1536("TCP congestion control window sliding and transport layer."),
    )
    # Node 3: Life Habit (Fitness)
    n3 = SemanticContext(
        id=uuid4(),
        user_id=uuid.UUID(user_id),
        context_type="life_habit",
        subject="Fitness",
        raw_content="Morning 10km run session.",
        embedding=generate_embedding_1536("Morning 10km run session."),
    )
    # Node 4: Project Goal (Algorithms)
    n4 = SemanticContext(
        id=uuid4(),
        user_id=uuid.UUID(user_id),
        context_type="project_goal",
        subject="Algorithms",
        raw_content="Implement Graph Neural Network message passing.",
        embedding=generate_embedding_1536("Implement Graph Neural Network message passing."),
    )
    db.add_all([n1, n2, n3, n4])
    db.commit()
    db.close()

    # 1. Filter by types=syllabus_module,project_goal
    res_types = client.get(f"/api/knowledge-graph?user_id={user_id}&types=syllabus_module,project_goal")
    assert res_types.status_code == 200
    data_types = res_types.json()
    types_in_nodes = {node["type"] for node in data_types["nodes"]}
    assert "syllabus_module" in types_in_nodes
    assert "project_goal" in types_in_nodes
    assert "life_habit" not in types_in_nodes
    assert len(data_types["nodes"]) == 3

    # 2. Filter by subject=Networks
    res_subject = client.get(f"/api/knowledge-graph?user_id={user_id}&subject=Networks")
    assert res_subject.status_code == 200
    data_subj = res_subject.json()
    assert len(data_subj["nodes"]) == 2
    for node in data_subj["nodes"]:
        assert "Computer Networks" in node["subject"]

    # 3. min_similarity filter: high threshold removes lower edges
    res_sim = client.get(f"/api/knowledge-graph?user_id={user_id}&min_similarity=0.99")
    assert res_sim.status_code == 200
    data_sim = res_sim.json()
    for e in data_sim["edges"]:
        assert e["weight"] >= 0.99


def test_knowledge_graph_api_response_schema_and_truncation(client):
    """
    Validates exact JSON response schema and truncation flag:
    Schema:
    {
      "nodes": [{ "id": "...", "label": "...", "type": "...", "group": "...", "subject": "...", "snippet": "...", "degree": 0, "source_table": "...", "source_id": "...", "created_at": "..." }],
      "edges": [{ "source": "...", "target": "...", "weight": 0.85, "kind": "semantic", "cross_domain": true }],
      "types_present": [{ "type": "...", "group": "...", "label": "...", "glyph": "...", "count": 1 }],
      "truncated": false
    }
    """
    user_res = client.post("/api/users", json={"email": "schema_test@offloader.ai"})
    user_id = user_res.json()["user_id"]
    db = SessionLocal()

    nodes = []
    for i in range(4):
        node = SemanticContext(
            id=uuid4(),
            user_id=uuid.UUID(user_id),
            context_type="syllabus_module",
            subject="Algorithms",
            raw_content=f"Algorithm complexity analysis chunk {i} sorting and trees.",
            embedding=generate_embedding_1536(f"Algorithm complexity analysis chunk {i} sorting and trees."),
        )
        nodes.append(node)
        db.add(node)
    db.commit()
    db.close()

    # Query with limit=2 to trigger truncated=True
    res = client.get(f"/api/knowledge-graph?user_id={user_id}&limit=2")
    assert res.status_code == 200
    data = res.json()

    # Top-level keys
    assert "nodes" in data
    assert "edges" in data
    assert "types_present" in data
    assert "truncated" in data
    assert data["truncated"] is True
    assert len(data["nodes"]) == 2

    # Verify node structure
    node0 = data["nodes"][0]
    expected_node_fields = ["id", "label", "type", "group", "subject", "snippet", "degree", "source_table", "source_id", "created_at"]
    for field in expected_node_fields:
        assert field in node0, f"Field '{field}' missing from node response schema"

    # Verify types_present structure
    assert len(data["types_present"]) >= 1
    tp0 = data["types_present"][0]
    expected_tp_fields = ["type", "group", "label", "glyph", "count"]
    for field in expected_tp_fields:
        assert field in tp0, f"Field '{field}' missing from types_present response schema"
    assert tp0["type"] == "syllabus_module"
    assert tp0["group"] == "SYLLABUS"
    assert tp0["glyph"] == "M"

    # Verify edge structure if edges exist
    if data["edges"]:
        e0 = data["edges"][0]
        expected_edge_fields = ["source", "target", "weight", "kind", "cross_domain"]
        for field in expected_edge_fields:
            assert field in e0, f"Field '{field}' missing from edge response schema"
        assert isinstance(e0["cross_domain"], bool)
