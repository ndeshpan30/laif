import datetime
from sqlalchemy import Column, String, Text, Date, DateTime, ForeignKey, func, Index
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.base_types import UniversalUUID, UniversalJSON, default_uuid


class TelemetryLog(Base):
    __tablename__ = "telemetry_logs"

    id = Column(UniversalUUID, primary_key=True, default=default_uuid)
    user_id = Column(UniversalUUID, ForeignKey("user_profiles.user_id", ondelete="CASCADE"), nullable=False, index=True)
    entry_type = Column(String(20), nullable=False)  # 'task' | 'event' | 'note' | 'metric' | 'memory'
    content = Column(Text, nullable=True)
    log_metadata = Column("metadata", UniversalJSON, default=dict)
    logged_date = Column(Date, nullable=False, server_default=func.current_date(), default=datetime.date.today)
    logged_at = Column(DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.datetime.now(datetime.timezone.utc))

    user = relationship("UserProfile", back_populates="telemetry_logs")


Index("idx_telemetry_user_date", TelemetryLog.user_id, TelemetryLog.logged_date)
