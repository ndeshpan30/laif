from typing import List, Optional
from uuid import UUID
import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.telemetry import TelemetryLog
from app.schemas.telemetry import TelemetryLogCreate, TelemetryLogResponse

router = APIRouter(prefix="/api/telemetry", tags=["BuJo Telemetry"])


@router.get("/logs", response_model=List[TelemetryLogResponse])
def get_telemetry_logs(
    user_id: UUID,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    return (
        db.query(TelemetryLog)
        .filter(TelemetryLog.user_id == user_id)
        .order_by(TelemetryLog.logged_at.desc())
        .limit(limit)
        .all()
    )


@router.post("/logs", response_model=TelemetryLogResponse)
def create_telemetry_log(
    log_in: TelemetryLogCreate,
    db: Session = Depends(get_db),
):
    entry = TelemetryLog(
        user_id=log_in.user_id,
        entry_type=log_in.entry_type,
        content=log_in.content,
        log_metadata=log_in.metadata_dict,
        logged_date=log_in.logged_date,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


@router.get("/analytics/{user_id}")
def get_lifestyle_analytics(
    user_id: UUID,
    days: int = 30,
    db: Session = Depends(get_db),
):
    """
    Returns rolling aggregations from telemetry_logs for correlation analysis.
    Per ARCHITECTURE.md Section 4.6.
    """
    since_date = datetime.date.today() - datetime.timedelta(days=days)
    logs = (
        db.query(TelemetryLog)
        .filter(
            TelemetryLog.user_id == user_id,
            TelemetryLog.logged_date >= since_date,
        )
        .order_by(TelemetryLog.logged_date.desc())
        .all()
    )

    daily_data = {}
    for log in logs:
        d_str = log.logged_date.isoformat()
        if d_str not in daily_data:
            daily_data[d_str] = {"date": d_str, "metrics": {}}

        if log.entry_type == "metric" and isinstance(log.log_metadata, dict):
            name = log.log_metadata.get("tracker_name") or log.log_metadata.get("name")
            val = log.log_metadata.get("value")
            if name and val is not None:
                try:
                    daily_data[d_str]["metrics"][name] = float(val)
                except (ValueError, TypeError):
                    daily_data[d_str]["metrics"][name] = val

    return {
        "user_id": user_id,
        "days_analyzed": days,
        "daily_records": list(daily_data.values()),
    }
