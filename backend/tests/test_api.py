import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.database import Base, engine, SessionLocal


@pytest.fixture
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


def test_health_endpoint(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "CP-SAT" in data["solver"]
    assert "15-minute" in data["discretization"]


def test_solve_endpoint_exam_bumps_discretionary(client):
    """
    Tests the CP-SAT solver endpoint directly via HTTP POST.
    Proves priority-10 exam bumps priority-2 discretionary task.
    """
    payload = {
        "fixed_events": [],
        "flexible_tasks": [
            {
                "id": "gaming_session",
                "duration": 4,  # 1 hour
                "priority": 2,  # discretionary
                "deadline": 4,
            },
            {
                "id": "urgent_exam_prep",
                "duration": 4,  # 1 hour
                "priority": 10, # immovable exam
                "deadline": 4,
            }
        ],
        "total_horizon_ticks": 4
    }

    response = client.post("/api/schedule/solve", json=payload)
    assert response.status_code == 200
    data = response.json()

    scheduled_ids = [item["id"] for item in data["scheduled"]]
    bumped_ids = data["bumped"]

    assert "urgent_exam_prep" in scheduled_ids
    assert "gaming_session" in bumped_ids
    assert data["schedule_status"] == "LAGGING"


def test_user_creation_and_schedule_flow(client):
    """
    End-to-end user creation, tracker setup, and schedule resolution flow.
    """
    # 1. Create User
    user_res = client.post("/api/users", json={
        "email": "hackathon_demo@offloader.ai",
        "sleep_start": "23:00:00",
        "sleep_end": "07:00:00",
        "buffer_minutes": 15,
        "max_study_hours_per_day": 8,
    })
    assert user_res.status_code == 200
    user_id = user_res.json()["user_id"]

    # 2. Create Dynamic Tracker (EAV)
    tracker_res = client.post("/api/trackers", json={
        "user_id": user_id,
        "name": "pushups",
        "category": "volume",
        "data_type": "integer",
        "unit": "reps",
        "val_min": 0,
        "val_max": 200,
    })
    assert tracker_res.status_code == 200
    assert tracker_res.json()["name"] == "pushups"

    # 3. Create BuJo Telemetry Log
    log_res = client.post("/api/telemetry/logs", json={
        "user_id": user_id,
        "entry_type": "metric",
        "content": "did 3x10 pushups",
        "metadata": {"name": "pushups", "value": 30},
        "logged_date": "2026-09-26",
    })
    assert log_res.status_code == 200

    # 4. Create Schedule Items: 1 priority-2 discretionary task and 1 priority-10 exam
    # Both competing for the exact same 1-hour slot between 08:00 and 09:00 AM
    item1_res = client.post("/api/schedule/items", json={
        "user_id": user_id,
        "title": "Casual Video Games",
        "category": "habit",
        "duration_minutes": 60,
        "priority": 2,
        "is_fixed": False,
        "deadline": "2026-09-27T09:00:00Z",
    })
    assert item1_res.status_code == 200
    item1_id = item1_res.json()["id"]

    item2_res = client.post("/api/schedule/items", json={
        "user_id": user_id,
        "title": "Networks Exam Prep",
        "category": "study_session",
        "duration_minutes": 60,
        "priority": 10,
        "is_fixed": False,
        "deadline": "2026-09-27T09:00:00Z",
    })
    assert item2_res.status_code == 200
    item2_id = item2_res.json()["id"]

    # 5. Resolve user schedule with CP-SAT at 08:00 AM (post-sleep window)
    resolve_res = client.post(
        f"/api/schedule/resolve-user/{user_id}",
        params={"horizon_start_iso": "2026-09-27T08:00:00Z"}
    )
    assert resolve_res.status_code == 200
    res_data = resolve_res.json()
    assert res_data["status"] in ("OPTIMAL", "FEASIBLE")

    scheduled_ids = [t["id"] for t in res_data["scheduled"]]
    bumped_ids = res_data["bumped"]

    # Preemption assertion: Priority-10 exam prep wins the slot, Priority-2 gaming is bumped
    assert item2_id in scheduled_ids, "Priority-10 exam must be scheduled"
    assert item1_id in bumped_ids, "Priority-2 discretionary task must be bumped"
    assert res_data["schedule_status"] == "LAGGING"

    # 6. Verify database state reflects solver result and migration count
    items_after = client.get(f"/api/schedule/items?user_id={user_id}").json()
    items_map = {it["id"]: it for it in items_after}

    assert items_map[item2_id]["start_time"] is not None
    assert items_map[item1_id]["start_time"] is None
    assert items_map[item1_id]["migration_count"] == 1


def test_resolve_user_past_fixed_events_do_not_cause_infeasible(client):
    """
    Ensures that past uncompleted fixed items in the database do not collapse
    into [0, 1] or cause model INFEASIBLE.
    """
    user_res = client.post("/api/users", json={
        "email": "past_events_test@offloader.ai",
        "sleep_start": "23:00:00",
        "sleep_end": "07:00:00",
        "buffer_minutes": 15,
        "max_study_hours_per_day": 8,
    })
    user_id = user_res.json()["user_id"]

    # 1. Past fixed class 1: Ended 2 days ago
    client.post("/api/schedule/items", json={
        "user_id": user_id,
        "title": "Past Class 1",
        "category": "class",
        "duration_minutes": 60,
        "priority": 5,
        "is_fixed": True,
        "start_time": "2026-09-25T10:00:00Z",
        "end_time": "2026-09-25T11:00:00Z",
    })

    # 2. Past fixed class 2: Ended 1 day ago
    client.post("/api/schedule/items", json={
        "user_id": user_id,
        "title": "Past Class 2",
        "category": "class",
        "duration_minutes": 60,
        "priority": 5,
        "is_fixed": True,
        "start_time": "2026-09-26T10:00:00Z",
        "end_time": "2026-09-26T11:00:00Z",
    })

    # 3. Future flexible task
    fut = client.post("/api/schedule/items", json={
        "user_id": user_id,
        "title": "Upcoming Study Session",
        "category": "study_session",
        "duration_minutes": 60,
        "priority": 8,
        "is_fixed": False,
        "deadline": "2026-09-27T12:00:00Z",
    }).json()

    # Resolve schedule starting today at 08:00
    resolve_res = client.post(
        f"/api/schedule/resolve-user/{user_id}",
        params={"horizon_start_iso": "2026-09-27T08:00:00Z"}
    )
    assert resolve_res.status_code == 200
    data = resolve_res.json()
    assert data["status"] in ("OPTIMAL", "FEASIBLE")
    scheduled_ids = [t["id"] for t in data["scheduled"]]
    assert fut["id"] in scheduled_ids


def test_resolve_user_past_deadline_bumped_with_lagging(client):
    """
    Ensures flexible tasks whose deadline is in the past are bumped
    and trigger schedule_status = 'LAGGING'.
    """
    user_res = client.post("/api/users", json={
        "email": "past_deadline_test@offloader.ai",
        "sleep_start": "23:00:00",
        "sleep_end": "07:00:00",
        "buffer_minutes": 15,
        "max_study_hours_per_day": 8,
    })
    user_id = user_res.json()["user_id"]

    # Task whose deadline was yesterday
    task = client.post("/api/schedule/items", json={
        "user_id": user_id,
        "title": "Expired Assignment",
        "category": "study_session",
        "duration_minutes": 60,
        "priority": 7,
        "is_fixed": False,
        "deadline": "2026-09-26T12:00:00Z",
    }).json()

    resolve_res = client.post(
        f"/api/schedule/resolve-user/{user_id}",
        params={"horizon_start_iso": "2026-09-27T08:00:00Z"}
    )
    assert resolve_res.status_code == 200
    data = resolve_res.json()
    assert task["id"] in data["bumped"]
    assert data["schedule_status"] == "LAGGING"


def test_conversation_grill_mode_toggle(client):
    """
    Verifies that when grill_mode is enabled:
    1. Casual telemetry is bypassed.
    2. Strict Socratic goal interrogation ('grill-my-goals') is invoked.
    3. Clarifying Socratic questions are returned to pressure-test the user.
    """
    user_res = client.post("/api/users", json={"email": "grill_mode_test@offloader.ai"})
    user_id = user_res.json()["user_id"]

    # 1. Grill Mode ON with vague intention
    res_grill = client.post(
        "/api/conversation",
        json={
            "user_id": user_id,
            "message": "I want to start studying Networks more.",
            "grill_mode": True,
        }
    )
    assert res_grill.status_code == 200
    data_grill = res_grill.json()
    assert data_grill["goal_interrogation"] is not None
    assert data_grill["goal_interrogation"]["is_ambiguous"] is True
    assert len(data_grill["goal_interrogation"]["clarifying_questions"]) > 0
    assert data_grill["audio_signal"] == "NONE"

    # 2. Grill Mode ON with habit/workout phrase -> forces interrogation instead of casual telemetry log
    res_grill_workout = client.post(
        "/api/conversation",
        json={
            "user_id": user_id,
            "message": "Did 3 sets of 10 pushups",
            "grill_mode": True,
        }
    )
    assert res_grill_workout.status_code == 200
    data_workout = res_grill_workout.json()
    assert data_workout["universal_extraction"] is None
    assert data_workout["goal_interrogation"] is not None

    # 3. Grill Mode OFF with habit/workout phrase -> extracts and logs universal telemetry normally
    res_normal = client.post(
        "/api/conversation",
        json={
            "user_id": user_id,
            "message": "Did 3 sets of 10 pushups",
            "grill_mode": False,
        }
    )
    assert res_normal.status_code == 200
    data_normal = res_normal.json()
    assert "Logged: Pushups (30 reps)" in data_normal["reply"]
    assert data_normal["audio_signal"] == "SUCCESS_JINGLE"


def test_chat_request_schema_without_user_id(client):
    """
    Verifies that incoming requests conforming to ChatRequest {message, grill_mode}
    without an explicit user_id are accepted and use the default user UUID.
    """
    res = client.post(
        "/api/conversation",
        json={
            "message": "I want to get better at distributed systems",
            "grill_mode": True,
        }
    )
    assert res.status_code == 200
    data = res.json()
    assert data["goal_interrogation"] is not None
    assert data["goal_interrogation"]["is_ambiguous"] is True


