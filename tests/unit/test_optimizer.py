import pytest
from unittest.mock import MagicMock

from services.scheduler_core.optimizer import SimulatedAnnealingOptimizer


@pytest.fixture
def conflicted_scheduler(base_scheduler):
    """
    Returns a scheduler whose schedule already contains a student conflict:
    STU1 has two overlapping exams on the same day.
    """
    from services.scheduler_core.planner_orchestrator import PlannerOrchestrator

    orch = PlannerOrchestrator(base_scheduler)
    base_scheduler.constraint_engine = orch.constraint_engine
    base_scheduler.grid_manager = orch.grid_manager
    base_scheduler.room_availability_grid = orch.grid_manager.grid

    exam_a = {
        'Date': '2026-01-10', 'Subject': 'Math', 'Instructor': 'Prof A',
        'EduProgram': 'IT', 'Section': 'Math-101', 'Students_Count': 2,
        'Room': '101', 'Time_Slot': '08:00-11:00', 'Duration': 180,
        'pinned': False, 'two_rooms_needed': False,
    }
    exam_b = {
        'Date': '2026-01-10', 'Subject': 'Physics', 'Instructor': 'Prof B',
        'EduProgram': 'IT', 'Section': 'Phy-201', 'Students_Count': 1,
        'Room': '102', 'Time_Slot': '09:00-12:00', 'Duration': 180,
        'pinned': False, 'two_rooms_needed': False,
    }
    base_scheduler.schedule = [exam_a, exam_b]
    # STU1 has both exams on the same day at overlapping times -> conflict
    base_scheduler.student_exams = {
        'STU1': [exam_a, exam_b],
        'STU2': [exam_a],
        'STU3': [exam_b],
    }
    return base_scheduler


def test_optimizer_initialization(base_scheduler):
    """SimulatedAnnealingOptimizer should attach to scheduler without errors."""
    optimizer = SimulatedAnnealingOptimizer(base_scheduler)
    assert optimizer.scheduler is base_scheduler


def test_calculate_total_conflicts(conflicted_scheduler):
    """
    calculate_total_conflicts should detect overlapping exams for the same student.
    """
    optimizer = SimulatedAnnealingOptimizer(conflicted_scheduler)
    conflicts = optimizer.calculate_total_conflicts(conflicted_scheduler.student_exams)

    # STU1 has 2 exams with overlap -> penalty > 0
    assert isinstance(conflicts, (int, float))
    assert conflicts > 0


def test_simulated_annealing_optimization(conflicted_scheduler):
    """
    Running optimize() should return updated student_exams and conflicting-student list.
    It must not crash and must not drop any exams.
    """
    optimizer = SimulatedAnnealingOptimizer(conflicted_scheduler)
    initial_cost = optimizer.calculate_total_conflicts(conflicted_scheduler.student_exams)

    new_student_exams, conflicting = optimizer.optimize(conflicted_scheduler.student_exams)

    final_cost = optimizer.calculate_total_conflicts(new_student_exams)
    assert final_cost <= initial_cost
    assert isinstance(new_student_exams, dict)
    # No exams were accidentally dropped
    assert len(conflicted_scheduler.schedule) == 2
