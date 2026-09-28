import uuid
import datetime
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import (
    UserProfile,
    SemanticContext,
    ScheduleItem,
    TrackerDefinition,
    TelemetryLog,
)


@pytest.fixture
def db_session():
    # Use in-memory SQLite for fast, isolated database testing
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_database_schema_creation_and_models(db_session):
    # 1. Create UserProfile
    user = UserProfile(
        email="student@university.edu",
        sleep_start=datetime.time(23, 0),
        sleep_end=datetime.time(7, 0),
        buffer_minutes=15,
        max_study_hours_per_day=8,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    assert user.user_id is not None
    assert user.email == "student@university.edu"

    # 2. Create SemanticContext (Syllabus Module)
    semantic_item = SemanticContext(
        user_id=user.user_id,
        context_type="syllabus_module",
        subject="Computer Networks",
        raw_content="Module 3: Subnetting, CIDR, and Routing Protocols.",
        context_metadata={"module_number": 3, "prep_hours_needed": 6},
    )
    db_session.add(semantic_item)

    # 3. Create ScheduleItem (Exam and habit)
    exam_item = ScheduleItem(
        user_id=user.user_id,
        title="Computer Networks Final Exam",
        category="exam",
        duration_minutes=120,
        priority=10,
        is_fixed=True,
        deadline=datetime.datetime(2026, 9, 29, 9, 0, tzinfo=datetime.timezone.utc),
    )
    study_item = ScheduleItem(
        user_id=user.user_id,
        title="Subnetting Practice",
        category="study_session",
        duration_minutes=90,
        priority=8,
        is_fixed=False,
    )
    db_session.add(exam_item)
    db_session.add(study_item)

    # 4. Create TrackerDefinition (EAV)
    tracker = TrackerDefinition(
        user_id=user.user_id,
        name="pushups",
        category="volume",
        data_type="integer",
        unit="reps",
        val_min=0,
        val_max=500,
    )
    db_session.add(tracker)

    # 5. Create TelemetryLog (Rapid Log BuJo Entry)
    telemetry = TelemetryLog(
        user_id=user.user_id,
        entry_type="metric",
        content="did 3x10 pushups",
        log_metadata={"tracker_name": "pushups", "sets": 3, "reps": 10, "value": 30},
        logged_date=datetime.date.today(),
    )
    db_session.add(telemetry)

    db_session.commit()

    # Query and verify
    assert len(user.semantic_contexts) == 1
    assert user.semantic_contexts[0].context_type == "syllabus_module"

    assert len(user.schedule_items) == 2
    priorities = [item.priority for item in user.schedule_items]
    assert 10 in priorities
    assert 8 in priorities

    assert len(user.tracker_definitions) == 1
    assert user.tracker_definitions[0].name == "pushups"

    assert len(user.telemetry_logs) == 1
    assert user.telemetry_logs[0].entry_type == "metric"
    assert user.telemetry_logs[0].log_metadata["value"] == 30


def test_cascade_delete_removes_all_dependent_records(db_session):
    """
    Verifies that deleting a user cascades to all related entities
    per ARCHITECTURE.md Section 5 schema.
    """
    user = UserProfile(email="cascade_test@offloader.ai")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    uid = user.user_id

    # Add child items across all 4 dependent tables
    db_session.add(SemanticContext(user_id=uid, context_type="episodic_constraint", raw_content="No work past 10 PM"))
    db_session.add(ScheduleItem(user_id=uid, title="Gym", category="habit", duration_minutes=60, priority=5))
    db_session.add(TrackerDefinition(user_id=uid, name="water", category="volume", data_type="integer"))
    db_session.add(TelemetryLog(user_id=uid, entry_type="note", content="feeling refreshed"))
    db_session.commit()

    # Delete User
    db_session.delete(user)
    db_session.commit()

    assert db_session.query(SemanticContext).filter(SemanticContext.user_id == uid).count() == 0
    assert db_session.query(ScheduleItem).filter(ScheduleItem.user_id == uid).count() == 0
    assert db_session.query(TrackerDefinition).filter(TrackerDefinition.user_id == uid).count() == 0
    assert db_session.query(TelemetryLog).filter(TelemetryLog.user_id == uid).count() == 0

