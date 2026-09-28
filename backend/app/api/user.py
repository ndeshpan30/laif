from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import UserProfile
from app.schemas.user import UserProfileCreate, UserProfileResponse

router = APIRouter(prefix="/api/users", tags=["User Profiles"])


@router.post("", response_model=UserProfileResponse)
def create_or_get_user(user_in: UserProfileCreate, db: Session = Depends(get_db)):
    existing = db.query(UserProfile).filter(UserProfile.email == user_in.email).first()
    if existing:
        return existing

    user = UserProfile(
        email=user_in.email,
        sleep_start=user_in.sleep_start,
        sleep_end=user_in.sleep_end,
        buffer_minutes=user_in.buffer_minutes,
        max_study_hours_per_day=user_in.max_study_hours_per_day,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.get("/{user_id}", response_model=UserProfileResponse)
def get_user(user_id: UUID, db: Session = Depends(get_db)):
    user = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user
