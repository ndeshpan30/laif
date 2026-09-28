from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.tracker import TrackerDefinition
from app.schemas.tracker import TrackerDefinitionCreate, TrackerDefinitionResponse

router = APIRouter(prefix="/api/trackers", tags=["Trackers"])


@router.get("", response_model=List[TrackerDefinitionResponse])
def get_trackers(user_id: UUID, db: Session = Depends(get_db)):
    return db.query(TrackerDefinition).filter(TrackerDefinition.user_id == user_id).all()


@router.post("", response_model=TrackerDefinitionResponse)
def create_tracker(tracker_in: TrackerDefinitionCreate, db: Session = Depends(get_db)):
    tracker = TrackerDefinition(
        user_id=tracker_in.user_id,
        name=tracker_in.name,
        category=tracker_in.category,
        data_type=tracker_in.data_type,
        val_min=tracker_in.val_min,
        val_max=tracker_in.val_max,
        unit=tracker_in.unit,
    )
    db.add(tracker)
    db.commit()
    db.refresh(tracker)
    try:
        from app.services.knowledge_graph import sync_node
        sync_node(db, tracker.user_id, "tracker_definitions", tracker.id)
    except Exception:
        pass
    return tracker


@router.delete("/{tracker_id}")
def delete_tracker(
    tracker_id: UUID,
    db: Session = Depends(get_db),
):
    tracker = db.query(TrackerDefinition).filter(TrackerDefinition.id == tracker_id).first()
    if not tracker:
        raise HTTPException(status_code=404, detail="Tracker not found")

    user_id = tracker.user_id
    try:
        from app.services.knowledge_graph import delete_synced_node
        delete_synced_node(db, user_id, "tracker_definitions", tracker_id)
    except Exception:
        pass

    db.delete(tracker)
    db.commit()
    return {"status": "deleted", "id": str(tracker_id)}
