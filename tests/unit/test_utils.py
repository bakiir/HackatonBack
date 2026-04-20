import pytest
from services.scheduler_core.utils import get_exam_type, check_overlap, group_consecutive_slots

def test_get_exam_type():
    # Test written exam (needs 2 rooms and proctor)
    written_group = {
        'two_rooms_needed': True,
        'proctor_needed': True,
        'has_exam': True
    }
    assert get_exam_type(written_group) == "written"
    
    # Test defense exam
    defense_group = {
        'two_rooms_needed': False,
        'proctor_needed': False,
        'has_exam': True
    }
    assert get_exam_type(defense_group) == "defense"
    
    # Test summative exam
    summative_group = {
        'two_rooms_needed': False,
        'proctor_needed': False,
        'has_exam': False
    }
    assert get_exam_type(summative_group) == "summative"
    
    # Test unknown
    unknown_group = {'has_exam': True, 'proctor_needed': True, 'two_rooms_needed': False}
    assert get_exam_type(unknown_group) == "unknown"

def test_check_overlap():
    assert check_overlap("08:00-11:00", "09:00-12:00") is True
    assert check_overlap("08:00-09:30", "08:30-10:00") is True
    
    assert check_overlap("08:00-09:30", "09:30-11:00") is False
    assert check_overlap("08:00-10:00", "11:00-13:00") is False
    
    # Invalid formats
    assert check_overlap("invalid", "09:00-12:00") is False
    assert check_overlap("08:00-11:00", None) is False

def test_group_consecutive_slots():
    records = [
        {
            'Date': '2026-01-10', 'Subject': 'Math', 'Instructor': 'Prof A',
            'Room': '101', 'EduProgram': 'IT', 'Section': 'A',
            'Students_Count': '2', 'pinned': False, 'two_rooms_needed': False,
            'Time_Slot': '08:00-08:30'
        },
        {
            'Date': '2026-01-10', 'Subject': 'Math', 'Instructor': 'Prof A',
            'Room': '101', 'EduProgram': 'IT', 'Section': 'A',
            'Students_Count': '2', 'pinned': False, 'two_rooms_needed': False,
            'Time_Slot': '08:30-09:00'
        },
        {
            'Date': '2026-01-10', 'Subject': 'Physics', 'Instructor': 'Prof B',
            'Room': '102', 'EduProgram': 'IT', 'Section': 'B',
            'Students_Count': '1', 'pinned': False, 'two_rooms_needed': False,
            'Time_Slot': '09:00-10:00'
        },
    ]
    grouped = group_consecutive_slots(records)

    assert len(grouped) == 2

    # Section A: two consecutive slots should merge into one wide slot
    a_record = [r for r in grouped if r['Section'] == 'A'][0]
    assert a_record['Time_Slot'] == '08:00-09:00'

    # Section B remains unchanged
    b_record = [r for r in grouped if r['Section'] == 'B'][0]
    assert b_record['Time_Slot'] == '09:00-10:00'

