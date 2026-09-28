import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.database import Base, engine


@pytest.fixture
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


def test_demo_day_scenario_1_vague_goal(client):
    """
    Demo-Day Scenario 1 (PRD Section 10 & 6.A):
    Verbal/textual interrogation of a vague goal into precise scheduled constraints.
    Pass 1: Ambiguous input produces up to 2 Socratic questions; no slot scheduled.
    Pass 2: Concrete input emitted directly to solver.
    """
    user_res = client.post("/api/users", json={"email": "demo1@offloader.ai"})
    user_id = user_res.json()["user_id"]

    # Turn 1: Vague intention
    res1 = client.post("/api/conversation", json={
        "user_id": user_id,
        "message": "I want to start studying Networks more."
    })
    assert res1.status_code == 200
    d1 = res1.json()
    assert d1["goal_interrogation"]["is_ambiguous"] is True
    assert len(d1["goal_interrogation"]["clarifying_questions"]) <= 2
    assert d1["audio_signal"] == "NONE"

    # Confirm 0 schedule items in database
    items1 = client.get(f"/api/schedule/items?user_id={user_id}").json()
    assert len(items1) == 0

    # Turn 2: User clarifies with concrete bounds
    res2 = client.post("/api/conversation", json={
        "user_id": user_id,
        "message": "3 chapters. I know chapter 1 well, haven't touched 2 or 3. 6 hours needed.",
        "horizon_start_iso": "2026-09-27T08:00:00Z"
    })
    assert res2.status_code == 200
    d2 = res2.json()
    assert d2["goal_interrogation"]["is_ambiguous"] is False
    assert d2["solver_result"]["status"] in ("OPTIMAL", "FEASIBLE")

    items2 = client.get(f"/api/schedule/items?user_id={user_id}").json()
    assert len(items2) == 1
    assert items2[0]["start_time"] is not None


def test_demo_day_scenario_2_sudden_exam_preemption(client):
    """
    Demo-Day Scenario 2 (PRD Section 10 & 6.B):
    Ingestion of urgent exam deadline triggering CP-SAT to instantly
    reorganize schedule, bump lower-priority task, and play sad trombone warning sound.
    """
    user_res = client.post("/api/users", json={"email": "demo2@offloader.ai"})
    user_id = user_res.json()["user_id"]

    # Initial state: lower-priority discretionary task (Priority 2, 90 mins)
    client.post("/api/schedule/items", json={
        "user_id": user_id,
        "title": "Casual Video Games",
        "category": "habit",
        "duration_minutes": 90,
        "priority": 2,
        "is_fixed": False,
        "deadline": "2026-09-27T10:00:00Z",
    })

    # Sudden exam announcement
    res = client.post("/api/conversation", json={
        "user_id": user_id,
        "message": "I have my Networks internal next Tuesday!",
        "horizon_start_iso": "2026-09-27T08:00:00Z"
    })
    assert res.status_code == 200
    data = res.json()

    assert data["audio_signal"] == "SAD_TROMBONE"
    assert data["schedule_status"] == "LAGGING"
    assert "pre-empted" in data["reply"].lower()

    # Verify solver outcomes in DB
    items = client.get(f"/api/schedule/items?user_id={user_id}").json()
    exam_item = next(it for it in items if "exam" in it["title"].lower())
    game_item = next(it for it in items if "games" in it["title"].lower())

    assert exam_item["start_time"] is not None, "Exam must be placed"
    assert game_item["start_time"] is None, "Gaming task must be bumped"
    assert game_item["migration_count"] == 1, "Migration count incremented"


def test_demo_day_scenario_3_casual_bujo_workout_logging(client):
    """
    Demo-Day Scenario 3 (PRD Section 10 & 6.C):
    Casual workout logging via text/speech with zero forms:
    extracts sets x reps + weight + nutrition, updates streak, and triggers dopamine jingle.
    """
    user_res = client.post("/api/users", json={"email": "demo3@offloader.ai"})
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

    # Verify telemetry logs in DB
    logs = client.get(f"/api/telemetry/logs?user_id={user_id}").json()
    assert len(logs) >= 2  # pushups, deadlifts, protein bar
    logged_trackers = [l["metadata"].get("tracker_name") for l in logs]
    assert "pushups" in logged_trackers


def test_web_ui_endpoints(client):
    """
    Verifies that FastAPI serves the interactive web UI and OpenAPI docs.
    """
    # 1. /app serves HTML UI
    app_res = client.get("/app")
    assert app_res.status_code == 200
    assert "text/html" in app_res.headers["content-type"]
    assert "Autonomous Cognitive Offloader" in app_res.text
    assert "SoundManager" in app_res.text
    assert "playSuccessJingle" in app_res.text
    assert "playSadTrombone" in app_res.text

    # 2. Browser request to / with text/html serves UI
    root_html_res = client.get("/", headers={"Accept": "text/html"})
    assert root_html_res.status_code == 200
    assert "text/html" in root_html_res.headers["content-type"]

    # 3. JSON client to / gets API metadata
    root_json_res = client.get("/", headers={"Accept": "application/json"})
    assert root_json_res.status_code == 200
    assert root_json_res.json()["status"] == "online"
