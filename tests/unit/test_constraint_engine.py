import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from services.scheduler_core.constraint_engine import ConstraintEngine

@pytest.fixture
def empty_constraint_engine(base_scheduler):
    return ConstraintEngine(base_scheduler)

def test_is_student_available(empty_constraint_engine, base_scheduler):
    # Dummy setup
    student_id = "STU1"
    day_str = "2026-01-10"
    
    # 1. No exams assigned yet
    assert empty_constraint_engine.is_student_available(student_id, day_str) is True
    
    # Assign an exam
    base_scheduler.student_exams[student_id] = [
        {'Date': day_str, 'Time_Slot': '08:00-11:00'}
    ]
    
    # 2. Strict Check (fails if any exam is present)
    assert empty_constraint_engine.is_student_available(student_id, day_str, strict=True) is False
    
    # 3. Non- strict check (allows up to 2 exams)
    assert empty_constraint_engine.is_student_available(student_id, day_str, time_slot="12:00-15:00", strict=False) is True
    
    # 4. Non-strict with overlapping time
    assert empty_constraint_engine.is_student_available(student_id, day_str, time_slot="09:00-12:00", strict=False) is False
    
    # 5. Non-strict with 2 exams already
    base_scheduler.student_exams[student_id].append({'Date': day_str, 'Time_Slot': '14:00-17:00'})
    assert empty_constraint_engine.is_student_available(student_id, day_str, time_slot="18:00-20:00", strict=False) is False

def test_is_instructor_available_basic(empty_constraint_engine, base_scheduler):
    day_str = "2026-01-10"
    group_info = {'has_exam': True, 'proctor_needed': False, 'two_rooms_needed': False} # type regular
    
    # Empty schedule
    assert empty_constraint_engine.is_instructor_available("Prof A", day_str, "08:00-11:00", group_info) is True
    
    # Overlapping instructor schedule
    base_scheduler.schedule.append({
        'Date': day_str,
        'Time_Slot': '09:00-12:00',
        'Instructor': "Prof A",
        'two_rooms_needed': False, # regular type
        'proctor_needed': False,
        'has_exam': True
    })
    
    assert empty_constraint_engine.is_instructor_available("Prof A", day_str, "08:00-11:00", group_info) is False
    # Non-overlapping
    assert empty_constraint_engine.is_instructor_available("Prof A", day_str, "13:00-16:00", group_info) is True

def test_is_room_excluded(empty_constraint_engine, base_scheduler):
    class MockExclusion:
        def __init__(self, room, start, end):
            self.room_number = room
            self.start_time = start
            self.end_time = end

    # Setting up an exclusion on 2026-01-10 between 09:00 and 12:00
    day_date = datetime(2026, 1, 10).date()
    start_dt = datetime.combine(day_date, datetime.strptime("09:00", "%H:%M").time())
    end_dt = datetime.combine(day_date, datetime.strptime("12:00", "%H:%M").time())
    
    base_scheduler.exclusions_by_date = {
        day_date: [MockExclusion("101", start_dt, end_dt)]
    }

    # Time slot overlapping the exclusion
    test_start = datetime.combine(day_date, datetime.strptime("08:00", "%H:%M").time())
    test_end = datetime.combine(day_date, datetime.strptime("11:00", "%H:%M").time())
    
    assert empty_constraint_engine.is_room_excluded("101", day_date, test_start, test_end) is True
    assert empty_constraint_engine.is_room_excluded("102", day_date, test_start, test_end) is False

def test_find_suitable_rooms(empty_constraint_engine, base_scheduler):
    available_rooms = ["101", "102", "107", "201"]
    
    # 1. Regular exam in regular room
    group_info_regular = {'has_exam': True, 'proctor_needed': False, 'two_rooms_needed': False, 'classroom_type': 'regular'}
    room_str, rooms_to_book = empty_constraint_engine.find_suitable_rooms(available_rooms, 40, group_info_regular)
    assert room_str in ["101", "102"]  # Excludes 107 for non-written
    
    # 2. IT Lab exam
    group_info_it = {'has_exam': True, 'proctor_needed': False, 'two_rooms_needed': False, 'classroom_type': 'it_lab'}
    room_str, rooms_to_book = empty_constraint_engine.find_suitable_rooms(available_rooms, 20, group_info_it)
    assert room_str == "201"
    
    # 3. Two rooms needed (Written)
    group_info_written = {'has_exam': True, 'proctor_needed': True, 'two_rooms_needed': True, 'classroom_type': 'regular'}
    room_str, rooms_to_book = empty_constraint_engine.find_suitable_rooms(available_rooms, 90, group_info_written)
    
    # Should pick 107 ideally as it has 150 capacity, or combine 101+102
    assert "107" in room_str or ("101" in rooms_to_book and "102" in rooms_to_book)
