import datetime
from sqlalchemy import Column, String, Float, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.base_types import UniversalUUID, default_uuid


class TrackerDefinition(Base):
    __tablename__ = "tracker_definitions"

    id = Column(UniversalUUID, primary_key=True, default=default_uuid)
    user_id = Column(UniversalUUID, ForeignKey("user_profiles.user_id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    category = Column(String(50), nullable=False)  # 'metric' | 'binary_habit' | 'volume' | 'journal_note'
    data_type = Column(String(20), nullable=False)  # 'float' | 'boolean' | 'text'
    val_min = Column(Float, nullable=True)
    val_max = Column(Float, nullable=True)
    unit = Column(String(30), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.datetime.now(datetime.timezone.utc))

    user = relationship("UserProfile", back_populates="tracker_definitions")
