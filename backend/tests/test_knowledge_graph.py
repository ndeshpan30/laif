import pytest
from uuid import uuid4
from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, engine, SessionLocal
from app.models.semantic import SemanticContext
from app.services.rag import generate_embedding_1536
from app.services.knowledge_graph import compute_knowledge_graph, invalidate_knowledge_graph_cache


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
    assert data["total_nodes"] == 0
    assert data["total_edges"] == 0


def test_knowledge_graph_sample_user_derivation_and_edges(client):
    user_res = client.post("/api/users", json={"email": "kg_sample@offloader.ai"})
    user_id = user_res.json()["user_id"]
    uid = uuid4()
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

    # Verify edge connections (c1 and c2 share subnetting/CIDR, c4 and c5 share migraine/screen work)
    edges = data["edges"]
    assert len(edges) > 0
    for e in edges:
        assert "source" in e
        assert "target" in e
        assert "weight" in e
        assert e["weight"] >= 0.50

    # Verify bidirectional deduplication: no edge (A, B) and (B, A) simultaneously
    pairs = set()
    for e in edges:
        p = tuple(sorted([e["source"], e["target"]]))
        assert p not in pairs, f"Duplicate edge found for pair {p}"
        pairs.add(p)
