import pytest
import pandas as pd
from unittest.mock import MagicMock, patch


@pytest.fixture
def scheduler_with_schedule(base_scheduler):
    """
    Adds a pre-built schedule_df to the scheduler so ProctorManager
    and SeatingManager can iterate over it without calling run().
    """
    base_scheduler.schedule_df = pd.DataFrame([
        {
            'Date': '2026-01-10', 'Subject': 'Math', 'Instructor': 'Prof A',
            'EduProgram': 'IT', 'Section': 'Math-101', 'Students_Count': 2,
            'Room': '101', 'Time_Slot': '08:00-11:00', 'Duration': 180,
            'pinned': False, 'proctor_needed': True, 'two_rooms_needed': False,
        },
    ])
    base_scheduler.schedule = base_scheduler.schedule_df.to_dict('records')
    return base_scheduler


# ------------------------------------------------------------------ Proctor --

class TestProctorManager:
    def test_assign_proctors_sets_proctor_column(self, scheduler_with_schedule):
        """assign_proctors() should add a Proctor column to schedule_df."""
        from services.scheduler_core.proctor_manager import ProctorManager

        # Give it a real-looking _is_instructor_available so it doesn't crash
        scheduler_with_schedule._is_instructor_available = MagicMock(return_value=True)

        pm = ProctorManager(scheduler_with_schedule)
        pm.assign_proctors()

        assert 'Proctor' in scheduler_with_schedule.schedule_df.columns

    def test_no_proctors_raises(self, scheduler_with_schedule):
        """assign_proctors() must raise ValueError when no proctors exist."""
        from services.scheduler_core.proctor_manager import ProctorManager

        scheduler_with_schedule.faculty_proctors = {}
        scheduler_with_schedule._is_instructor_available = MagicMock(return_value=True)

        pm = ProctorManager(scheduler_with_schedule)
        with pytest.raises(ValueError, match="Нет доступных прокторов"):
            pm.assign_proctors()

    def test_get_all_proctors_returns_list(self, scheduler_with_schedule):
        """get_all_proctors() should return a sorted list of unique proctor names."""
        from services.scheduler_core.proctor_manager import ProctorManager

        scheduler_with_schedule.faculty_proctors = {
            'FIT': ['Alice', 'Bob', 'Alice'],  # Alice duplicated
        }
        pm = ProctorManager(scheduler_with_schedule)
        result = pm.get_all_proctors()

        assert sorted(result) == result
        assert 'Alice' in result
        assert result.count('Alice') == 1  # de-duped


# ------------------------------------------------------------------ Seating --

class TestSeatingManager:
    def test_assign_seats_populates_dict(self, scheduler_with_schedule):
        """assign_seats() should fill seat_assignments for each scheduled student."""
        from services.scheduler_core.seating_manager import SeatingManager

        # section_students_map must know the students in Math-101
        scheduler_with_schedule.section_students_map = {
            'Math-101': ['STU1', 'STU2'],
        }
        scheduler_with_schedule.get_students_for_section = lambda sec: \
            scheduler_with_schedule.section_students_map.get(sec, [])

        sm = SeatingManager(scheduler_with_schedule)
        sm.assign_seats()

        assert len(scheduler_with_schedule.seat_assignments) == 2
        for key in scheduler_with_schedule.seat_assignments:
            parts = key.split('|')
            assert len(parts) == 4, f"Unexpected key format: {key}"

    def test_assign_seats_empty_schedule(self, base_scheduler):
        """assign_seats() should silently skip when schedule_df is empty."""
        from services.scheduler_core.seating_manager import SeatingManager

        sm = SeatingManager(base_scheduler)
        sm.assign_seats()  # schedule_df is empty -> should log warning and return

        assert base_scheduler.seat_assignments == {}
