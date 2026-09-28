import io
import datetime
import pytest
from fastapi.testclient import TestClient
import fitz  # PyMuPDF

from app.main import app
from app.database import Base, engine, SessionLocal
from app.models.user import UserProfile
from app.models.schedule import ScheduleItem
from app.models.telemetry import TelemetryLog
from app.models.tracker import TrackerDefinition
from app.models.semantic import SemanticContext


@pytest.fixture
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


def create_sample_pdf(title: str, text: str) -> bytes:
    """Creates an in-memory PDF using PyMuPDF for testing."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), f"Syllabus for: {title}\n\n{text}")
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def test_socratic_vague_goal_interrogation(client):
    """
    ARCHITECTURE.md 4.1 & PRD Scenario A (Pass 1):
    Vague goal 'I want to start studying Networks more' must return
    at most 2 sharp clarifying questions and NEVER write a schedule item.
    """
    user_res = client.post("/api/users", json={"email": "socratic_test@offloader.ai"})
    user_id = user_res.json()["user_id"]

    res = client.post("/api/conversation", json={
        "user_id": user_id,
        "message": "I want to start studying Networks more."
    })
    assert res.status_code == 200
    data = res.json()

    assert data["goal_interrogation"] is not None
    assert data["goal_interrogation"]["is_ambiguous"] is True
    questions = data["goal_interrogation"]["clarifying_questions"]
    assert len(questions) <= 2
    assert "Networks" in data["reply"] or "units" in data["reply"]
    assert data["audio_signal"] == "NONE"

    # Verify no schedule items were created during Pass 1
    items = client.get(f"/api/schedule/items?user_id={user_id}").json()
    assert len(items) == 0


def test_socratic_clarification_to_solver_emission(client):
    """
    ARCHITECTURE.md 4.1 & PRD Scenario A (Pass 2):
    Complete clarified input emits validated JSON directly to CP-SAT solver.
    """
    user_res = client.post("/api/users", json={"email": "socratic_pass2@offloader.ai"})
    user_id = user_res.json()["user_id"]

    res = client.post("/api/conversation", json={
        "user_id": user_id,
        "message": "3 chapters. I know chapter 1 well, haven't touched 2 or 3. 6 hours needed.",
        "horizon_start_iso": "2026-09-27T08:00:00Z"
    })
    assert res.status_code == 200
    data = res.json()

    assert data["goal_interrogation"] is not None
    assert data["goal_interrogation"]["is_ambiguous"] is False
    assert data["solver_result"] is not None
    assert data["solver_result"]["status"] in ("OPTIMAL", "FEASIBLE")

    # Verify item was placed into schedule
    items = client.get(f"/api/schedule/items?user_id={user_id}").json()
    assert len(items) == 1
    assert items[0]["start_time"] is not None


def test_sudden_exam_arrival_preemption_and_sad_trombone(client):
    """
    PRD Scenario B:
    Sudden exam declaration marks it priority 10, pre-empts lower-priority
    discretionary task, and triggers SAD_TROMBONE sound signal.
    """
    user_res = client.post("/api/users", json={"email": "exam_preempt@offloader.ai"})
    user_id = user_res.json()["user_id"]

    # 1. Create a priority-2 discretionary task competing for the same slot
    client.post("/api/schedule/items", json={
        "user_id": user_id,
        "title": "Casual Video Games",
        "category": "habit",
        "duration_minutes": 90,
        "priority": 2,
        "is_fixed": False,
        "deadline": "2026-09-27T10:00:00Z",
    })

    # 2. User announces sudden exam competing for slot at 08:00
    res = client.post("/api/conversation", json={
        "user_id": user_id,
        "message": "I have my Networks internal next Tuesday!",
        "horizon_start_iso": "2026-09-27T08:00:00Z"
    })
    assert res.status_code == 200
    data = res.json()

    assert data["audio_signal"] == "SAD_TROMBONE"
    assert data["schedule_status"] == "LAGGING"
    assert "pre-empted" in data["reply"].lower() or "video games" in data["reply"].lower()

    # Verify database state: gaming task bumped, exam task placed
    items = client.get(f"/api/schedule/items?user_id={user_id}").json()
    items_by_title = {it["title"]: it for it in items}

    assert items_by_title["Networks Exam Prep"]["start_time"] is not None
    assert items_by_title["Casual Video Games"]["start_time"] is None
    assert items_by_title["Casual Video Games"]["migration_count"] == 1


def test_conversational_bujo_telemetry_and_dopamine_jingle(client):
    """
    ARCHITECTURE.md 4.3 & PRD Scenario C:
    Casual sentence 'did 3 sets of 10 pushups and deadlifted 20kg. Having a protein bar now'
    silently logs telemetry, updates streak, and returns SUCCESS_JINGLE.
    """
    user_res = client.post("/api/users", json={"email": "bujo_telemetry@offloader.ai"})
    user_id = user_res.json()["user_id"]

    res = client.post("/api/conversation", json={
        "user_id": user_id,
        "message": "Just did 3 sets of 10 pushups and deadlifted 20kg. Having a protein bar now."
    })
    assert res.status_code == 200
    data = res.json()

    assert data["audio_signal"] == "SUCCESS_JINGLE"
    assert "Logged" in data["reply"]
    assert "pushups" in data["reply"].lower()

    # Verify telemetry logs in database
    logs_res = client.get(f"/api/telemetry/logs?user_id={user_id}")
    assert logs_res.status_code == 200
    logs = logs_res.json()
    assert len(logs) >= 2  # pushups, deadlifts, protein bar

    # Verify tracker definition was dynamically registered
    trackers = client.get(f"/api/trackers?user_id={user_id}").json()
    tracker_names = [t["name"] for t in trackers]
    assert "pushups" in tracker_names


def test_syllabus_pdf_upload_and_rag_retrieval(client):
    """
    ARCHITECTURE.md 4.4 A & Phase 3:
    PyMuPDF extraction of syllabus PDF, chunking, and semantic retrieval.
    """
    user_res = client.post("/api/users", json={"email": "syllabus_rag@offloader.ai"})
    user_id = user_res.json()["user_id"]

    pdf_content = (
        "Unit 1: Physical Layer and Transmission Media.\n"
        "Unit 2: Data Link Layer, Error Correction and Flow Control.\n"
        "Unit 3: Subnetting, Variable Length Subnet Masking (VLSM), CIDR, and Routing Protocols.\n"
        "Unit 4: Transport Layer TCP/UDP.\n"
        "Unit 5: Application Layer protocols DNS and HTTP."
    )
    pdf_bytes = create_sample_pdf("Computer Networks", pdf_content)

    upload_res = client.post(
        "/api/upload-syllabus",
        data={"user_id": str(user_id), "subject": "Computer Networks"},
        files={"file": ("syllabus.pdf", pdf_bytes, "application/pdf")},
    )
    assert upload_res.status_code == 200
    up_data = upload_res.json()
    assert up_data["status"] == "success"
    assert up_data["modules_ingested"] >= 1

    # Query semantic memory for Subnetting
    query_res = client.get(
        "/api/semantic/query",
        params={"user_id": str(user_id), "query": "I am terrified of Subnetting"}
    )
    assert query_res.status_code == 200
    q_data = query_res.json()
    assert len(q_data["top_syllabus_modules"]) >= 1
    assert "Subnetting" in q_data["top_syllabus_modules"][0]["content"]


def test_episodic_constraint_guardrail_rejection(client):
    """
    ARCHITECTURE.md 4.4 B & Phase 3:
    Episodic personal constraint ('No screen work past 10 PM') remembered
    and injected to push back when user attempts conflicting schedule.
    """
    user_res = client.post("/api/users", json={"email": "guardrail_test@offloader.ai"})
    user_id = user_res.json()["user_id"]

    # 1. Register episodic constraint
    c_res = client.post("/api/semantic/constraints", json={
        "user_id": user_id,
        "constraint_text": "No screen work past 10 PM gives me migraines",
    })
    assert c_res.status_code == 200

    # 2. User requests conflicting all-nighter
    chat_res = client.post("/api/conversation", json={
        "user_id": user_id,
        "message": "Let's plan an all-nighter for tomorrow at midnight!"
    })
    assert chat_res.status_code == 200
    c_data = chat_res.json()

    assert "Rejected" in c_data["reply"] or "prohibit" in c_data["reply"]
    assert "migraine" in c_data["reply"].lower() or "10 pm" in c_data["reply"].lower()
    assert c_data["audio_signal"] == "SAD_TROMBONE"


def test_socratic_migration_weeding_check(client):
    """
    PRD Scenario D & ARCHITECTURE.md 4.3:
    If a task has rolled forward >= 3 times, alert user Ryder Carroll style.
    """
    user_res = client.post("/api/users", json={"email": "weed_test@offloader.ai"})
    user_id = user_res.json()["user_id"]

    # Create task with migration_count = 4
    db = SessionLocal()
    task = ScheduleItem(
        user_id=user_id,
        title="clean out email inbox",
        category="habit",
        duration_minutes=30,
        priority=2,
        is_fixed=False,
        migration_count=4,
    )
    db.add(task)
    db.commit()
    db.close()

    res = client.post("/api/conversation", json={
        "user_id": user_id,
        "message": "Check my backlog and weed tasks"
    })
    assert res.status_code == 200
    data = res.json()

    assert "clean out email inbox" in data["reply"]
    assert "4 times" in data["reply"]
    assert "strike it" in data["reply"]
    assert data["weeding_alert"] is not None


def test_encrypted_and_corrupt_pdf_upload_rejected(client):
    """
    Validates Edge Case:
    Encrypted / password-protected or corrupt PDFs uploaded to /api/upload-syllabus
    are rejected with clear 400 Bad Request and descriptive message.
    """
    user_res = client.post("/api/users", json={"email": "pdf_edge_test@offloader.ai"})
    user_id = user_res.json()["user_id"]

    # 1. Create password-protected PDF
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Classified Syllabus Content")
    enc_bytes = doc.tobytes(
        encryption=fitz.PDF_ENCRYPT_AES_256,
        user_pw="password123",
        owner_pw="master123",
    )
    doc.close()

    res_enc = client.post(
        "/api/upload-syllabus",
        data={"user_id": str(user_id)},
        files={"file": ("encrypted.pdf", enc_bytes, "application/pdf")},
    )
    assert res_enc.status_code == 400
    assert "encrypted" in res_enc.json()["detail"].lower() or "password" in res_enc.json()["detail"].lower()

    # 2. Corrupted file content
    res_corrupt = client.post(
        "/api/upload-syllabus",
        data={"user_id": str(user_id)},
        files={"file": ("corrupt.pdf", b"%PDF-1.4 corrupt data", "application/pdf")},
    )
    assert res_corrupt.status_code == 400


def test_multi_user_episodic_isolation(client):
    """
    Validates Data Isolation:
    User A's episodic guardrails and schedule items do NOT bleed into User B.
    """
    u1_res = client.post("/api/users", json={"email": "alice_iso@offloader.ai"})
    u1_id = u1_res.json()["user_id"]

    u2_res = client.post("/api/users", json={"email": "bob_iso@offloader.ai"})
    u2_id = u2_res.json()["user_id"]

    # Alice has episodic constraint against late screen work
    client.post("/api/semantic/constraints", json={
        "user_id": u1_id,
        "constraint_text": "No screen work past 10 PM gives me migraines",
    })

    # Alice asking for all-nighter is rejected
    alice_res = client.post("/api/conversation", json={
        "user_id": u1_id,
        "message": "Let's plan an all-nighter for midnight",
    })
    assert alice_res.status_code == 200
    assert "Rejected" in alice_res.json()["reply"]

    # Bob asking for all-nighter is NOT rejected by Alice's constraint
    bob_res = client.post("/api/conversation", json={
        "user_id": u2_id,
        "message": "Let's plan an all-nighter for midnight",
    })
    assert bob_res.status_code == 200
    assert "Rejected" not in bob_res.json()["reply"]

