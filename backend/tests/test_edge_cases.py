import pytest
from app.solver.scheduler import (
    solve_weekly_schedule,
    minutes_to_ticks,
    ticks_to_minutes,
    datetime_to_tick,
    tick_to_datetime,
)
from datetime import datetime, timezone


def test_empty_schedule_inputs():
    """Empty tasks and fixed events should solve cleanly with zero error."""
    res = solve_weekly_schedule([], [])
    assert res["scheduled"] == []
    assert res["bumped"] == []
    assert res["status"] in ("OPTIMAL", "FEASIBLE")
    assert res["schedule_status"] == "ON_TRACK"


def test_zero_horizon_bumps_all():
    """A zero horizon should bump any flexible task requiring duration > 0."""
    tasks = [{"id": "study", "duration": 4, "priority": 10, "deadline": 0}]
    res = solve_weekly_schedule([], tasks, total_horizon_ticks=0)
    assert res["scheduled"] == []
    assert res["bumped"] == ["study"]
    assert res["schedule_status"] == "LAGGING"


def test_task_duration_exceeding_deadline():
    """Task whose duration exceeds its deadline must be bumped."""
    tasks = [{"id": "long_task", "duration": 10, "priority": 10, "deadline": 5}]
    res = solve_weekly_schedule([], tasks, total_horizon_ticks=100)
    assert "long_task" in res["bumped"]


def test_multiple_competing_priorities():
    """
    Given 8 ticks available:
    Task P10 (dur 4)
    Task P8 (dur 4)
    Task P5 (dur 4)
    Task P2 (dur 4)
    Only 2 tasks can fit (4 + 4 = 8).
    Solver must choose P10 and P8, bumping P5 and P2.
    """
    tasks = [
        {"id": "p2", "duration": 4, "priority": 2, "deadline": 8},
        {"id": "p10", "duration": 4, "priority": 10, "deadline": 8},
        {"id": "p5", "duration": 4, "priority": 5, "deadline": 8},
        {"id": "p8", "duration": 4, "priority": 8, "deadline": 8},
    ]
    res = solve_weekly_schedule([], tasks, total_horizon_ticks=8)
    scheduled_ids = [t["id"] for t in res["scheduled"]]
    assert "p10" in scheduled_ids
    assert "p8" in scheduled_ids
    assert "p5" in res["bumped"]
    assert "p2" in res["bumped"]


def test_invalid_durations_gracefully_bumped():
    """Negative and zero duration tasks must be safely bumped without crashing CP-SAT."""
    tasks = [
        {"id": "neg_task", "duration": -5, "priority": 10, "deadline": 10},
        {"id": "zero_task", "duration": 0, "priority": 8, "deadline": 10},
    ]
    res = solve_weekly_schedule([], tasks, total_horizon_ticks=20)
    assert res["status"] in ("OPTIMAL", "FEASIBLE")
    assert "neg_task" in res["bumped"]
    assert "zero_task" in res["bumped"]
    assert res["scheduled"] == []


def test_task_duration_exceeding_total_horizon():
    """Task whose duration exceeds total_horizon_ticks must be safely bumped without CP-SAT domain collision."""
    tasks = [{"id": "massive_task", "duration": 1000, "priority": 10, "deadline": 1000}]
    res = solve_weekly_schedule([], tasks, total_horizon_ticks=672)
    assert res["status"] in ("OPTIMAL", "FEASIBLE")
    assert "massive_task" in res["bumped"]
    assert res["scheduled"] == []


def test_duplicate_identical_fixed_events_merged():
    """Identical duplicate fixed intervals must be merged without triggering model INFEASIBLE."""
    fixed = [
        {"id": "dup_1", "start": 10, "end": 20},
        {"id": "dup_2", "start": 10, "end": 20},
    ]
    flexible = [{"id": "task", "duration": 5, "priority": 5, "deadline": 30}]
    res = solve_weekly_schedule(fixed, flexible, total_horizon_ticks=30)
    assert res["status"] in ("OPTIMAL", "FEASIBLE")
    assert len(res["scheduled"]) == 1
    # Scheduled either before 10 or after 20
    s_tick = res["scheduled"][0]["start_tick"]
    assert s_tick + 5 <= 10 or s_tick >= 20

