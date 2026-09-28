import pytest
from datetime import datetime, timezone, timedelta
from app.solver.scheduler import (
    solve_weekly_schedule,
    minutes_to_ticks,
    ticks_to_minutes,
    datetime_to_tick,
    tick_to_datetime,
    generate_sleep_fixed_events,
    DEFAULT_HORIZON_TICKS,
)


def test_priority_10_exam_bumps_priority_2_task():
    """
    CORE UNIT TEST PER SPECIFICATION:
    Proves that a priority-10 exam prep task bumps a priority-2 discretionary task
    when capacity is constrained.
    """
    # 4 ticks available (1 hour)
    total_horizon_ticks = 4

    discretionary_task = {
        "id": "task_discretionary_gaming",
        "duration": 4,  # 1 hour
        "priority": 2,  # Low priority discretionary
        "deadline": 4,
    }

    urgent_exam_task = {
        "id": "task_urgent_exam_prep",
        "duration": 4,  # 1 hour
        "priority": 10, # Immovable / urgent exam
        "deadline": 4,
    }

    # Solve with both tasks competing for the exact same 4-tick window
    result = solve_weekly_schedule(
        fixed_events=[],
        flexible_tasks=[discretionary_task, urgent_exam_task],
        total_horizon_ticks=total_horizon_ticks,
    )

    scheduled_ids = [t["id"] for t in result["scheduled"]]
    bumped_ids = result["bumped"]

    # Assertions
    assert "task_urgent_exam_prep" in scheduled_ids, "Priority-10 exam must be scheduled"
    assert "task_discretionary_gaming" in bumped_ids, "Priority-2 task must be bumped"
    assert "task_discretionary_gaming" not in scheduled_ids
    assert result["schedule_status"] == "LAGGING", "Bumped task triggers LAGGING status for audio feedback"


def test_dynamic_preemption_on_exam_arrival():
    """
    Scenario B from PRD.md:
    Initially only the discretionary task exists and gets scheduled.
    When a sudden priority-10 exam arrives in the same window,
    re-solving causes the discretionary task to be bumped.
    """
    horizon = 8  # 2 hours total

    # Step 1: Initial state — user has a 2-hour gaming/discretionary block
    initial_tasks = [
        {"id": "gaming_session", "duration": 8, "priority": 2, "deadline": 8}
    ]
    initial_res = solve_weekly_schedule([], initial_tasks, total_horizon_ticks=horizon)
    assert len(initial_res["scheduled"]) == 1
    assert initial_res["scheduled"][0]["id"] == "gaming_session"
    assert initial_res["bumped"] == []
    assert initial_res["schedule_status"] == "ON_TRACK"

    # Step 2: Sudden exam announced! Priority 10, needs 8 ticks (2 hours)
    urgent_exam = {"id": "networks_internal_exam", "duration": 8, "priority": 10, "deadline": 8}
    updated_tasks = [initial_tasks[0], urgent_exam]

    updated_res = solve_weekly_schedule([], updated_tasks, total_horizon_ticks=horizon)
    scheduled_ids = [t["id"] for t in updated_res["scheduled"]]

    assert "networks_internal_exam" in scheduled_ids
    assert "gaming_session" in updated_res["bumped"]
    assert updated_res["schedule_status"] == "LAGGING"


def test_solver_sub_50ms_solve_time():
    """
    Validates Non-Functional Requirement:
    Solve time must be sub-50ms (<0.05s) even with fixed events and multiple flexible tasks.
    """
    fixed_events = [
        {"id": "class_networks", "start": 36, "end": 44},   # 2 hours class
        {"id": "lab_os", "start": 50, "end": 58},          # 2 hours lab
        {"id": "sleep_night1", "start": 92, "end": 124},   # 8 hours sleep
    ]
    flexible_tasks = [
        {"id": "study_cn_ch1", "duration": 8, "priority": 10, "deadline": 100},
        {"id": "gym_workout", "duration": 4, "priority": 6, "deadline": 200},
        {"id": "read_fiction", "duration": 4, "priority": 1, "deadline": 150},
        {"id": "clean_desk", "duration": 2, "priority": 3, "deadline": 80},
    ]

    result = solve_weekly_schedule(fixed_events, flexible_tasks, total_horizon_ticks=DEFAULT_HORIZON_TICKS)
    assert result["status"] in ("OPTIMAL", "FEASIBLE")
    assert result["wall_time"] < 0.05, f"Solve time was {result['wall_time']}s, must be < 0.05s"


def test_zero_calendar_collision_guarantee():
    """
    Validates structural zero-overlap guarantee enforced by AddNoOverlap.
    """
    fixed_events = [
        {"id": "fixed_1", "start": 10, "end": 20},
        {"id": "fixed_2", "start": 30, "end": 40},
    ]
    flexible_tasks = [
        {"id": "task_a", "duration": 6, "priority": 8, "deadline": 50},
        {"id": "task_b", "duration": 8, "priority": 7, "deadline": 50},
        {"id": "task_c", "duration": 10, "priority": 5, "deadline": 50},
    ]

    result = solve_weekly_schedule(fixed_events, flexible_tasks, total_horizon_ticks=100)

    # Collect all scheduled and fixed intervals
    all_intervals = []
    for f in fixed_events:
        all_intervals.append((f["start"], f["end"], f["id"]))
    for s in result["scheduled"]:
        start = s["start_tick"]
        end = start + s["duration_ticks"]
        all_intervals.append((start, end, s["id"]))

    # Sort by start tick
    all_intervals.sort(key=lambda x: x[0])

    # Check pairwise overlap
    for i in range(len(all_intervals) - 1):
        curr_start, curr_end, curr_id = all_intervals[i]
        next_start, next_end, next_id = all_intervals[i + 1]
        assert curr_end <= next_start, f"Collision detected between {curr_id} ({curr_start}-{curr_end}) and {next_id} ({next_start}-{next_end})"


def test_15_minute_tick_discretization():
    """
    Tests 15-minute tick discretization math and conversions.
    """
    assert minutes_to_ticks(0) == 0
    assert minutes_to_ticks(15) == 1
    assert minutes_to_ticks(30) == 2
    assert minutes_to_ticks(45) == 3
    assert minutes_to_ticks(60) == 4
    assert minutes_to_ticks(90) == 6
    assert minutes_to_ticks(120) == 8
    # Rounding up for partial ticks
    assert minutes_to_ticks(10) == 1
    assert minutes_to_ticks(20) == 2

    # Reverse conversion
    assert ticks_to_minutes(1) == 15
    assert ticks_to_minutes(4) == 60
    assert ticks_to_minutes(8) == 120

    # Datetime conversions
    horizon_start = datetime(2026, 9, 26, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 9, 26, 1, 0, tzinfo=timezone.utc)  # 1 hour = 4 ticks
    tick = datetime_to_tick(t1, horizon_start)
    assert tick == 4
    recovered_dt = tick_to_datetime(tick, horizon_start)
    assert recovered_dt == t1


def test_fixed_sleep_window_preservation():
    """
    Tests that sleep intervals are defended both across midnight into Day 0
    and across subsequent days, ensuring no task can be placed inside sleep hours.
    """
    from datetime import time
    horizon_start = datetime(2026, 9, 26, 0, 0, tzinfo=timezone.utc)
    # User sleeps 23:00 to 07:00
    sleep_events = generate_sleep_fixed_events(
        sleep_start=time(23, 0),
        sleep_end=time(7, 0),
        horizon_start=horizon_start,
        days=2,
    )
    # Must capture Day -1 overnight sleep [0, 28] and Day 0 sleep [92, 124]
    assert len(sleep_events) >= 2
    assert sleep_events[0]["start"] == 0
    assert sleep_events[0]["end"] == 28
    assert sleep_events[1]["start"] == 92
    assert sleep_events[1]["end"] == 124

    # 1. A task attempted at 2:00 AM (tick 8) must be bumped by the ongoing sleep window
    early_morning_task = {
        "id": "early_morning_study",
        "duration": 4,
        "priority": 5,
        "earliest_start": 8,
        "deadline": 24,
    }

    # 2. A task with earliest_start in evening sleep window that has deadline before sleep ends
    late_night_task = {
        "id": "late_night_study",
        "duration": 4,
        "priority": 5,
        "earliest_start": 94,
        "deadline": 110,
    }
    res = solve_weekly_schedule(sleep_events, [early_morning_task, late_night_task], total_horizon_ticks=200)
    assert "early_morning_study" in res["bumped"], "Task cannot violate ongoing overnight sleep window"
    assert "late_night_study" in res["bumped"], "Task cannot violate fixed evening sleep window"


def test_overlapping_fixed_events_merged_and_solve_succeeds():
    """
    Edge case: User inputs two overlapping fixed classes or commitments.
    CP-SAT would be INFEASIBLE if not merged. Merging preserves busy times
    and schedules flexible tasks smoothly around them.
    """
    overlapping_fixed = [
        {"id": "class_a", "start": 40, "end": 48},  # 10:00 to 12:00
        {"id": "class_b", "start": 44, "end": 52},  # 11:00 to 13:00
    ]
    flexible_tasks = [
        {"id": "homework", "duration": 4, "priority": 8, "deadline": 60}
    ]

    res = solve_weekly_schedule(overlapping_fixed, flexible_tasks, total_horizon_ticks=60)
    assert res["status"] in ("OPTIMAL", "FEASIBLE")
    assert len(res["scheduled"]) == 1
    sched = res["scheduled"][0]
    # Homework must NOT overlap merged interval [40, 52]
    assert sched["start_tick"] + sched["duration_ticks"] <= 40 or sched["start_tick"] >= 52


def test_fixed_event_overlapping_sleep_window():
    """
    Real-world scenario: User has a mandatory 6:30 AM exam that overlaps
    their default 7:00 AM sleep end. Merging prevents false INFEASIBLE
    and allows optimal scheduling for other tasks.
    """
    from datetime import time
    horizon_start = datetime(2026, 9, 26, 0, 0, tzinfo=timezone.utc)
    sleep_events = generate_sleep_fixed_events(
        sleep_start=time(23, 0),
        sleep_end=time(7, 0),
        horizon_start=horizon_start,
        days=1,
    )
    # Early morning fixed exam from 06:00 (tick 24) to 08:00 (tick 32)
    # Overlaps sleep window [0, 28] between ticks 24 and 28
    fixed_exam = {"id": "early_exam", "start": 24, "end": 32}
    all_fixed = sleep_events + [fixed_exam]

    study_task = {"id": "post_exam_nap_or_study", "duration": 4, "priority": 9, "deadline": 40}

    res = solve_weekly_schedule(all_fixed, [study_task], total_horizon_ticks=50)
    assert res["status"] in ("OPTIMAL", "FEASIBLE")
    assert len(res["scheduled"]) == 1
    # Must be scheduled after tick 32 (post-exam)
    assert res["scheduled"][0]["start_tick"] >= 32

