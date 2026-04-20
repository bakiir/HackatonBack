import pytest
import pandas as pd
from unittest.mock import MagicMock, patch

from services.scheduler_core.planner_orchestrator import PlannerOrchestrator


def test_planner_orchestrator_initialization(base_scheduler):
    """
    Test that PlannerOrchestrator correctly sets up ConstraintEngine and GridManager.
    """
    orchestrator = PlannerOrchestrator(base_scheduler)

    assert orchestrator.constraint_engine is not None
    assert orchestrator.grid_manager is not None
    # Grid should have one key per date
    assert len(orchestrator.grid_manager.grid) == 3  # num_days=3


def test_full_generation_pipeline(base_scheduler):
    """
    Test the full generation pipeline (Генерация).
    Verifies that run() places exams and populates scheduler.schedule.
    """
    # Patch optimize_schedule to avoid running the heavy SA loop
    base_scheduler.optimize_schedule = MagicMock(return_value=(base_scheduler.student_exams, []))

    orchestrator = PlannerOrchestrator(base_scheduler)
    orchestrator.run()

    sections_scheduled = [e['Section'] for e in base_scheduler.schedule]
    assert 'Math-101' in sections_scheduled
    assert 'Phy-201' in sections_scheduled
    assert isinstance(base_scheduler.failed_sections, list)


def test_generation_with_manual_bookings(base_scheduler, mock_db_session):
    """
    Test that _load_manual_bookings reads ClassroomSlot and pins those exams.
    """
    import json
    from datetime import datetime

    base_scheduler.optimize_schedule = MagicMock(return_value=(base_scheduler.student_exams, []))

    # Build a mock slot that matches our dummy data
    mock_slot = MagicMock()
    mock_slot.is_booked = True
    mock_slot.classroom_number = '101'
    mock_slot.start_time = datetime(2026, 1, 10, 8, 0)
    mock_slot.end_time   = datetime(2026, 1, 10, 11, 0)
    mock_slot.booked_groups_info = json.dumps({'subject': 'Math', 'sections': ['Math-101']})

    # Mock Session.query(...).filter_by(...).all() to return our slot
    mock_db_session.query.return_value.filter_by.return_value.all.return_value = [mock_slot]

    orchestrator = PlannerOrchestrator(base_scheduler)
    orchestrator.run()

    pinned = [e for e in base_scheduler.schedule if e.get('pinned')]
    assert len(pinned) > 0
    assert any(e['Section'] == 'Math-101' for e in pinned)
