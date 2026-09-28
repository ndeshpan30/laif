import datetime
from uuid import UUID
from pydantic import BaseModel, EmailStr, Field, ConfigDict


class UserProfileBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    email: EmailStr
    sleep_start: datetime.time = datetime.time(23, 0, 0)
    sleep_end: datetime.time = datetime.time(7, 0, 0)
    buffer_minutes: int = Field(default=15, ge=0)
    max_study_hours_per_day: int = Field(default=8, ge=1, le=24)


class UserProfileCreate(UserProfileBase):
    pass


class UserProfileResponse(UserProfileBase):
    user_id: UUID
    created_at: datetime.datetime
