import datetime
from sqlalchemy import Column, String, Integer, Boolean, DateTime, ForeignKey, func, Index
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.base_types import UniversalUUID, default_uuid


class ScheduleItem(Base):
    __tablename__ = "schedule_items"

    id = Column(UniversalUUID, primary_key=True, default=default_uuid)
    user_id = Column(UniversalUUID, ForeignKey("user_profiles.user_id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    category = Column(String(50), nullable=False)  # 'exam' | 'class' | 'lab' | 'habit' | 'study_session'
    start_time = Column(DateTime(timezone=True), nullable=True)
    end_time = Column(DateTime(timezone=True), nullable=True)
    duration_minutes = Column(Integer, nullable=False)
    priority = Column(Integer, nullable=False, default=5)  # 10 = immovable exam, 1 = discretionary
    is_fixed = Column(Boolean, default=False)
    deadline = Column(DateTime(timezone=True), nullable=True)
    is_completed = Column(Boolean, default=False)
    migration_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.datetime.now(datetime.timezone.utc))

    user = relationship("UserProfile", back_populates="schedule_items")


Index("idx_schedule_user_dates", ScheduleItem.user_id, ScheduleItem.start_time, ScheduleItem.end_time)
