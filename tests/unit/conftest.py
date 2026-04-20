import pytest
import pandas as pd
from datetime import datetime
from unittest.mock import MagicMock, patch
import sys

# ============================================================
# CRITICAL: Mock 'create_db' BEFORE any app imports so that
# SQLAlchemy never tries to connect to PostgreSQL.
# ============================================================
sys.modules['create_db'] = MagicMock()

from services.exam_scheduler import ExamScheduler


@pytest.fixture
def mock_db_session():
    """Returns the MagicMock session already in sys.modules['create_db']."""
    mock_session = MagicMock()
    sys.modules['create_db'].Session.return_value = mock_session
    mock_query = MagicMock()
    mock_session.query.return_value = mock_query
    mock_query.all.return_value = []
    mock_query.filter_by.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.order_by.return_value = mock_query
    return mock_session


@pytest.fixture
def base_scheduler(mock_db_session):
    """
    Creates a fully-initialised ExamScheduler with tiny in-memory
    DataFrames that satisfy every column _prepare_data() touches.
    """
    # ---- exams (студенты) ----
    # Columns required by _prepare_data:
    #   fake_id, fake_name, Subject, Instructor, EduProgram,
    #   YearsOfStudy, Section, Faculty
    exams_data = [
        {
            'fake_id': 'STU1', 'fake_name': 'Aibek A',
            'Subject': 'Math', 'Instructor': 'Prof A',
            'EduProgram': 'IT', 'YearsOfStudy': 1,
            'Section': 'Math-101', 'Faculty': 'FIT',
        },
        {
            'fake_id': 'STU2', 'fake_name': 'Beka B',
            'Subject': 'Math', 'Instructor': 'Prof A',
            'EduProgram': 'IT', 'YearsOfStudy': 1,
            'Section': 'Math-101', 'Faculty': 'FIT',
        },
        {
            'fake_id': 'STU3', 'fake_name': 'Cara C',
            'Subject': 'Physics', 'Instructor': 'Prof B',
            'EduProgram': 'IT', 'YearsOfStudy': 1,
            'Section': 'Phy-201', 'Faculty': 'FIT',
        },
    ]
    exams_df = pd.DataFrame(exams_data)

    # ---- rooms ----
    # Columns required: Аудитория, Вместительность аудитории, (Type optional)
    rooms_data = [
        {'Аудитория': '101', 'Вместительность аудитории': 50, 'Type': 'regular'},
        {'Аудитория': '102', 'Вместительность аудитории': 50, 'Type': 'regular'},
        {'Аудитория': '107', 'Вместительность аудитории': 150, 'Type': 'regular'},
        {'Аудитория': '201', 'Вместительность аудитории': 30, 'Type': 'it_lab'},
    ]
    rooms_df = pd.DataFrame(rooms_data)

    # ---- faculties ----
    # Columns required: Subject, Faculty, Instructor
    faculties_data = [
        {'Subject': 'Math',    'Faculty': 'FIT', 'Instructor': 'Prof A'},
        {'Subject': 'Physics', 'Faculty': 'FIT', 'Instructor': 'Prof B'},
    ]
    faculties_df = pd.DataFrame(faculties_data)

    # pd.read_excel is called multiple times in __init__ (once per file arg).
    # Map each dummy filename to the matching DataFrame so order/count don't matter.
    excel_map = {
        'dummy_exams.xlsx':    exams_df,
        'dummy_rooms.xlsx':    rooms_df,
        'dummy_faculties.xlsx': faculties_df,
    }

    def fake_read_excel(path, *args, **kwargs):
        return excel_map.get(path, pd.DataFrame())

    with patch('services.exam_scheduler.pd.read_excel', side_effect=fake_read_excel):
        scheduler = ExamScheduler(
            exams_file   ='dummy_exams.xlsx',
            rooms_file   ='dummy_rooms.xlsx',
            faculties_file='dummy_faculties.xlsx',
            start_date   ='2026-01-10',
            num_days     =3,
            title        ='Test Session',
        )

    # Runtime state that strategies / constraint_engine read/write
    scheduler.schedule = []
    scheduler.student_exams = {}
    scheduler.exams_per_day_count = {
        d.strftime('%Y-%m-%d'): 0 for d in scheduler.custom_dates
    }
    scheduler.exclusions_by_date = {}

    # PlannerOrchestrator requires this attribute; _prepare_data computes it locally but
    # never stores it on self, so we derive it here from the scheduler's own settings.
    total_mins = (scheduler.work_day_end - scheduler.work_day_start).total_seconds() / 60
    scheduler.num_blocks_in_day = int(total_mins / scheduler.time_step)

    return scheduler
