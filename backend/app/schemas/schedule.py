import datetime
from uuid import UUID
from typing import Optional, List, Literal
from pydantic import BaseModel, Field, ConfigDict


ScheduleCategory = Literal["exam", "class", "lab", "habit", "study_session", "goal", "project", "milestone"]


class ScheduleItemBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    title: str = Field(..., max_length=255)
    category: ScheduleCategory
    duration_minutes: int = Field(..., gt=0)
    priority: int = Field(default=5, ge=1, le=10)
    is_fixed: bool = False
    deadline: Optional[datetime.datetime] = None
    start_time: Optional[datetime.datetime] = None
    end_time: Optional[datetime.datetime] = None
    is_completed: bool = False
    migration_count: int = 0


class ScheduleItemCreate(ScheduleItemBase):
    user_id: UUID


class ScheduleItemUpdate(BaseModel):
    title: Optional[str] = None
    category: Optional[ScheduleCategory] = None
    duration_minutes: Optional[int] = Field(None, gt=0)
    priority: Optional[int] = Field(None, ge=1, le=10)
    is_fixed: Optional[bool] = None
    deadline: Optional[datetime.datetime] = None
    start_time: Optional[datetime.datetime] = None
    end_time: Optional[datetime.datetime] = None
    is_completed: Optional[bool] = None
    migration_count: Optional[int] = None


class ScheduleItemResponse(ScheduleItemBase):
    id: UUID
    user_id: UUID
    created_at: datetime.datetime


# Solver I/O schemas
class ScheduledTaskResult(BaseModel):
    id: str
    start_tick: int
    duration_ticks: int
    start_time: Optional[datetime.datetime] = None
    end_time: Optional[datetime.datetime] = None


class SolverResult(BaseModel):
    scheduled: List[ScheduledTaskResult]
    bumped: List[str]
    status: str
    schedule_status: str = "ON_TRACK"  # "ON_TRACK" or "LAGGING"
