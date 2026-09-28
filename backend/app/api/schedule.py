from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.database import get_db
from app.models.schedule import ScheduleItem
from app.models.user import UserProfile
from app.schemas.schedule import (
    ScheduleItemCreate,
    ScheduleItemResponse,
    ScheduleItemUpdate,
    SolverResult,
    ScheduledTaskResult,
)
from app.solver.scheduler import (
    solve_weekly_schedule,
    minutes_to_ticks,
    ticks_to_minutes,
    datetime_to_tick,
    tick_to_datetime,
    generate_sleep_fixed_events,
    DEFAULT_HORIZON_TICKS,
)

router = APIRouter(prefix="/api/schedule", tags=["Schedule & Solver"])


class DirectSolveRequest(BaseModel):
    fixed_events: List[Dict[str, Any]] = Field(default_factory=list)
    flexible_tasks: List[Dict[str, Any]] = Field(default_factory=list)
    total_horizon_ticks: int = DEFAULT_HORIZON_TICKS


@router.post("/solve", response_model=SolverResult)
def run_solver(request: DirectSolveRequest):
    """
    Executes CP-SAT scheduler directly on provided fixed events and flexible tasks.
    Enforces sub-50ms deterministic schedule optimization with zero overlap.
    """
    result = solve_weekly_schedule(
        fixed_events=request.fixed_events,
        flexible_tasks=request.flexible_tasks,
        total_horizon_ticks=request.total_horizon_ticks,
    )
    return result


@router.post("/resolve-user/{user_id}", response_model=SolverResult)
def resolve_user_schedule(
    user_id: UUID,
    horizon_start_iso: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    Fetches user's active schedule items and master profile from the database,
    discretizes them into 15-minute ticks (defending sleep windows and fixed classes),
    runs CP-SAT, and returns the mathematically optimized schedule.
    """
    user = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if horizon_start_iso:
        horizon_start = datetime.fromisoformat(horizon_start_iso)
    else:
        horizon_start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

    if horizon_start.tzinfo is None:
        horizon_start = horizon_start.replace(tzinfo=timezone.utc)

    total_horizon_ticks = DEFAULT_HORIZON_TICKS
    horizon_end = horizon_start + timedelta(minutes=ticks_to_minutes(total_horizon_ticks))

    # 1. Generate sleep window fixed events from user_profiles
    sleep_events = generate_sleep_fixed_events(
        sleep_start=user.sleep_start,
        sleep_end=user.sleep_end,
        horizon_start=horizon_start,
        days=7,
    )

    # 2. Query user's schedule items
    items = db.query(ScheduleItem).filter(
        ScheduleItem.user_id == user_id,
        ScheduleItem.is_completed == False,
    ).all()

    fixed_events = list(sleep_events)
    flexible_tasks = []

    for item in items:
        dur_ticks = minutes_to_ticks(item.duration_minutes)

        if item.is_fixed and item.start_time and item.end_time:
            st = item.start_time
            if st.tzinfo is None:
                st = st.replace(tzinfo=horizon_start.tzinfo)
            et = item.end_time
            if et.tzinfo is None:
                et = et.replace(tzinfo=horizon_start.tzinfo)

            # Skip fixed items that ended before horizon_start or start after horizon_end
            if et <= horizon_start or st >= horizon_end:
                continue

            start_tick = max(0, datetime_to_tick(st, horizon_start))
            end_tick = min(total_horizon_ticks, datetime_to_tick(et, horizon_start))
            if end_tick > start_tick:
                fixed_events.append({
                    "id": str(item.id),
                    "start": start_tick,
                    "end": end_tick,
                })
        else:
            # Flexible task
            deadline_tick = total_horizon_ticks
            if item.deadline:
                dl = item.deadline
                if dl.tzinfo is None:
                    dl = dl.replace(tzinfo=horizon_start.tzinfo)
                # Compute raw deadline tick relative to horizon_start
                dl_tick = datetime_to_tick(dl, horizon_start)
                # If deadline is in the past or before duration can fit,
                # keep actual dl_tick clamped to min 0 (which causes solver to bump it)
                deadline_tick = min(total_horizon_ticks, max(0, dl_tick))

            flexible_tasks.append({
                "id": str(item.id),
                "duration": dur_ticks,
                "priority": item.priority,
                "deadline": deadline_tick,
            })

    # 3. Solve with CP-SAT
    solver_out = solve_weekly_schedule(
        fixed_events=fixed_events,
        flexible_tasks=flexible_tasks,
        total_horizon_ticks=total_horizon_ticks,
    )

    # 4. Map scheduled ticks back to datetimes
    scheduled_items = []
    scheduled_map = {t["id"]: t["start_tick"] for t in solver_out["scheduled"]}

    for item in items:
        item_id_str = str(item.id)
        if item_id_str in scheduled_map:
            st_tick = scheduled_map[item_id_str]
            st_dt = tick_to_datetime(st_tick, horizon_start)
            end_dt = st_dt + timedelta(minutes=item.duration_minutes)
            item.start_time = st_dt
            item.end_time = end_dt
            scheduled_items.append(
                ScheduledTaskResult(
                    id=item_id_str,
                    start_tick=st_tick,
                    duration_ticks=minutes_to_ticks(item.duration_minutes),
                    start_time=st_dt,
                    end_time=end_dt,
                )
            )
        elif item_id_str in solver_out["bumped"]:
            item.start_time = None
            item.end_time = None
            item.migration_count = (item.migration_count or 0) + 1

    db.commit()

    return SolverResult(
        scheduled=scheduled_items,
        bumped=solver_out["bumped"],
        status=solver_out["status"],
        schedule_status=solver_out["schedule_status"],
    )


@router.get("/items", response_model=List[ScheduleItemResponse])
def get_schedule_items(
    user_id: UUID,
    db: Session = Depends(get_db),
):
    return db.query(ScheduleItem).filter(ScheduleItem.user_id == user_id).all()


@router.post("/items", response_model=ScheduleItemResponse)
def create_schedule_item(
    item_in: ScheduleItemCreate,
    db: Session = Depends(get_db),
):
    # Neuro-symbolic boundary: schedule items created without hardcoded times
    # will be positioned deterministically by CP-SAT during solve
    item = ScheduleItem(
        user_id=item_in.user_id,
        title=item_in.title,
        category=item_in.category,
        duration_minutes=item_in.duration_minutes,
        priority=item_in.priority,
        is_fixed=item_in.is_fixed,
        deadline=item_in.deadline,
        start_time=item_in.start_time if item_in.is_fixed else None,
        end_time=item_in.end_time if item_in.is_fixed else None,
        is_completed=item_in.is_completed,
        migration_count=item_in.migration_count,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    try:
        from app.services.knowledge_graph import sync_node
        if item.category.lower() in ("exam", "lab", "project", "goal", "milestone") or item.priority >= 9:
            sync_node(db, item.user_id, "schedule_items", item.id)
    except Exception:
        pass
    return item


@router.patch("/items/{item_id}", response_model=ScheduleItemResponse)
def update_schedule_item(
    item_id: UUID,
    update_in: ScheduleItemUpdate,
    db: Session = Depends(get_db),
):
    item = db.query(ScheduleItem).filter(ScheduleItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Schedule item not found")

    update_data = update_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(item, field, value)

    db.commit()
    db.refresh(item)
    try:
        from app.services.knowledge_graph import sync_node
        if item.category.lower() in ("exam", "lab", "project", "goal", "milestone") or item.priority >= 9:
            sync_node(db, item.user_id, "schedule_items", item.id)
    except Exception:
        pass
    return item


@router.delete("/items/{item_id}")
def delete_schedule_item(
    item_id: UUID,
    db: Session = Depends(get_db),
):
    item = db.query(ScheduleItem).filter(ScheduleItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Schedule item not found")

    user_id = item.user_id
    try:
        from app.services.knowledge_graph import delete_synced_node
        delete_synced_node(db, user_id, "schedule_items", item_id)
    except Exception:
        pass

    db.delete(item)
    db.commit()
    return {"status": "deleted", "id": str(item_id)}
