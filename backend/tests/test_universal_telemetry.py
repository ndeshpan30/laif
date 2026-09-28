import pytest
from uuid import uuid4
from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, engine
from app.models.tracker import TrackerDefinition
from app.models.telemetry import TelemetryLog
from app.schemas.extraction import TelemetryDataPoint, UniversalExtraction
from app.services.telemetry_engine import (
    infer_data_type,
    find_existing_tracker,
    get_or_create_tracker,
    write_telemetry_log,
    format_telemetry_confirmation,
    process_universal_telemetry,
)


@pytest.fixture
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


# =====================================================================
# 1. Unit Tests: infer_data_type
# =====================================================================

def test_infer_data_type_rules():
    # binary_habit -> boolean
    assert infer_data_type("binary_habit", True) == "boolean"
    assert infer_data_type("binary_habit", False) == "boolean"
    assert infer_data_type("binary_habit", "skipped") == "boolean"

    # journal_note -> text
    assert infer_data_type("journal_note", "feeling tired") == "text"
    assert infer_data_type("journal_note", 42) == "text"

    # metric / volume numeric values -> float
    assert infer_data_type("metric", 7.5) == "float"
    assert infer_data_type("metric", 8) == "float"
    assert infer_data_type("volume", 30) == "float"
    assert infer_data_type("volume", 20.5) == "float"

    # boolean values in other categories -> boolean
    assert infer_data_type("metric", True) == "boolean"

    # string values in other categories -> text
    assert infer_data_type("metric", "overwhelmed") == "text"


# =====================================================================
# 2. Unit Tests: Response Synthesizer Formatting (Section 5)
# =====================================================================

def test_format_telemetry_confirmation_spec_examples():
    # Example 1 per Section 5.2
    dummy_user_id = uuid4()
    t_pushups = TrackerDefinition(id=uuid4(), user_id=dummy_user_id, name="pushups", category="volume", data_type="float", unit="reps")
    t_mom = TrackerDefinition(id=uuid4(), user_id=dummy_user_id, name="called_mom", category="binary_habit", data_type="boolean")
    t_pages = TrackerDefinition(id=uuid4(), user_id=dummy_user_id, name="pages_read", category="volume", data_type="float", unit="pages")

    logged_results = [
        {"tracker": t_pushups, "data_point": TelemetryDataPoint(entity="pushups", value=30, unit="reps", category="volume"), "is_new": False},
        {"tracker": t_mom, "data_point": TelemetryDataPoint(entity="called_mom", value=True, unit=None, category="binary_habit"), "is_new": False},
        {"tracker": t_pages, "data_point": TelemetryDataPoint(entity="pages_read", value=15, unit="pages", category="volume"), "is_new": False},
    ]

    conf = format_telemetry_confirmation(logged_results, note_count=0)
    assert conf == "Logged: Pushups (30 reps), Called Mom ✓, Pages Read (15 pages)."

    # Example 2 per Section 5.2 (with skipped habit and new tracker)
    t_sleep = TrackerDefinition(id=uuid4(), user_id=dummy_user_id, name="sleep", category="metric", data_type="float", unit="hours")
    t_stress = TrackerDefinition(id=uuid4(), user_id=dummy_user_id, name="stress", category="metric", data_type="float", unit="/10")
    t_bfast = TrackerDefinition(id=uuid4(), user_id=dummy_user_id, name="breakfast", category="binary_habit", data_type="boolean")
    t_smile = TrackerDefinition(id=uuid4(), user_id=dummy_user_id, name="smiled_at_strangers", category="binary_habit", data_type="boolean")

    logged_results_2 = [
        {"tracker": t_sleep, "data_point": TelemetryDataPoint(entity="sleep", value=5.5, unit="hours", category="metric"), "is_new": False},
        {"tracker": t_stress, "data_point": TelemetryDataPoint(entity="stress", value=8, unit="/10", category="metric"), "is_new": False},
        {"tracker": t_bfast, "data_point": TelemetryDataPoint(entity="breakfast", value=False, unit=None, category="binary_habit"), "is_new": False},
        {"tracker": t_smile, "data_point": TelemetryDataPoint(entity="smiled_at_strangers", value=True, unit=None, category="binary_habit"), "is_new": True},
    ]

    conf_2 = format_telemetry_confirmation(logged_results_2, note_count=0)
    assert conf_2 == "Logged: Sleep (5.5 hours), Stress (8/10), Breakfast: skipped, Smiled At Strangers ✓ (started tracking this)."


def test_format_telemetry_confirmation_journal_notes_counted():
    dummy_user_id = uuid4()
    t_pushups = TrackerDefinition(id=uuid4(), user_id=dummy_user_id, name="pushups", category="volume", data_type="float", unit="reps")

    logged_results = [
        {"tracker": t_pushups, "data_point": TelemetryDataPoint(entity="pushups", value=30, unit="reps", category="volume"), "is_new": False},
    ]

    # 1 note counted
    conf_1 = format_telemetry_confirmation(logged_results, note_count=1)
    assert conf_1 == "Logged: Pushups (30 reps), and 1 note."

    # 2 notes counted
    conf_2 = format_telemetry_confirmation(logged_results, note_count=2)
    assert conf_2 == "Logged: Pushups (30 reps), and 2 notes."

    # Only notes
    conf_only_notes = format_telemetry_confirmation([], note_count=1)
    assert conf_only_notes == "Logged: 1 note."

    conf_multiple_notes = format_telemetry_confirmation([], note_count=3)
    assert conf_multiple_notes == "Logged: 3 notes."


# =====================================================================
# 3. Integration Tests: JIT Auto-Registration & De-duplication
# =====================================================================

def test_jit_auto_registration_and_fuzzy_dedup(client):
    user_res = client.post("/api/users", json={"email": "jit_test@offloader.ai"})
    user_id = user_res.json()["user_id"]

    # Turn 1: Log never-before-seen entities
    msg1 = "Slept 6 hours, read 25 pages, smiled at strangers"
    res1 = client.post("/api/conversation", json={"user_id": user_id, "message": msg1})
    assert res1.status_code == 200
    d1 = res1.json()

    assert "(started tracking this)" in d1["reply"]
    assert "Sleep (6 hours)" in d1["reply"]
    assert "Pages Read (25 pages)" in d1["reply"]
    assert "Smiled At Strangers ✓" in d1["reply"]

    # Verify trackers exist in database
    trackers = client.get(f"/api/trackers?user_id={user_id}").json()
    tracker_map = {t["name"]: t for t in trackers}
    assert "sleep" in tracker_map
    assert tracker_map["sleep"]["category"] == "metric"
    assert tracker_map["sleep"]["data_type"] == "float"
    assert "pages_read" in tracker_map
    assert "smiled_at_strangers" in tracker_map
    assert tracker_map["smiled_at_strangers"]["category"] == "binary_habit"
    assert tracker_map["smiled_at_strangers"]["data_type"] == "boolean"

    # Turn 2: Subsequent log should NOT include '(started tracking this)'
    msg2 = "Slept 7 hours"
    res2 = client.post("/api/conversation", json={"user_id": user_id, "message": msg2})
    assert res2.status_code == 200
    d2 = res2.json()
    assert "(started tracking this)" not in d2["reply"]
    assert "Logged: Sleep (7 hours)." in d2["reply"]

    # Verify no duplicate tracker created
    trackers2 = client.get(f"/api/trackers?user_id={user_id}").json()
    assert len([t for t in trackers2 if t["name"] == "sleep"]) == 1


def test_atomic_transaction_rollback(client):
    from app.database import SessionLocal
    from unittest.mock import patch

    user_res = client.post("/api/users", json={"email": "atomic_test@offloader.ai"})
    user_id = user_res.json()["user_id"]

    db = SessionLocal()
    extraction = UniversalExtraction(
        is_telemetry=True,
        data_points=[
            TelemetryDataPoint(entity="water", value=500, unit="ml", category="volume"),
            TelemetryDataPoint(entity="steps", value=8000, unit="steps", category="volume"),
        ],
    )

    # Simulate an error during second write to verify atomicity
    with patch("app.services.telemetry_engine.write_telemetry_log") as mock_write:
        mock_write.side_effect = [True, RuntimeError("Simulated DB connection failure")]
        with pytest.raises(RuntimeError):
            process_universal_telemetry(db, user_id=user_id, raw_message="dummy", extraction=extraction)

    # Verify nothing was committed (neither trackers nor logs)
    trackers = db.query(TrackerDefinition).filter(TrackerDefinition.user_id == user_id).all()
    logs = db.query(TelemetryLog).filter(TelemetryLog.user_id == user_id).all()
    assert len(trackers) == 0, "Failed transaction must roll back created trackers"
    assert len(logs) == 0, "Failed transaction must roll back created logs"
    db.close()
