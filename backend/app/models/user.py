import datetime
from sqlalchemy import Column, String, Integer, Time, DateTime, func
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.base_types import UniversalUUID, default_uuid


class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id = Column(UniversalUUID, primary_key=True, default=default_uuid)
    email = Column(String(255), unique=True, nullable=False, index=True)
    sleep_start = Column(Time, nullable=False, default=datetime.time(23, 0, 0))
    sleep_end = Column(Time, nullable=False, default=datetime.time(7, 0, 0))
    buffer_minutes = Column(Integer, nullable=False, default=15)
    max_study_hours_per_day = Column(Integer, nullable=False, default=8)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.datetime.now(datetime.timezone.utc))

    semantic_contexts = relationship("SemanticContext", back_populates="user", cascade="all, delete-orphan")
    schedule_items = relationship("ScheduleItem", back_populates="user", cascade="all, delete-orphan")
    tracker_definitions = relationship("TrackerDefinition", back_populates="user", cascade="all, delete-orphan")
    telemetry_logs = relationship("TelemetryLog", back_populates="user", cascade="all, delete-orphan")
    context_edges = relationship("ContextEdge", back_populates="user", cascade="all, delete-orphan")
