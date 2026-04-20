import os
import sys
import pytest
import pandas as pd
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from services.exam_scheduler import ExamScheduler

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

ROOMS_FILE = os.path.join(DATA_DIR, "rooms_test.xlsx")
EXAMS_FILE = os.path.join(DATA_DIR, "students.xlsx")
FACULTIES_FILE = os.path.join(DATA_DIR, "prepods.xlsx")


@pytest.fixture
def base_scheduler():
    """Фикстура для базовой инициализации планировщика с реальными тестовыми файлами"""
    return ExamScheduler(
        title="Test Session",
        exams_file=EXAMS_FILE,
        rooms_file=ROOMS_FILE,
        faculties_file=FACULTIES_FILE,
        start_date="2026-01-10",
        num_days=5
    )


class TestExamSchedulerUnit:
    def test_initialization(self, base_scheduler):
        """Проверяет правильность базовой инициализации объектов"""
        assert base_scheduler.title == "Test Session"
        assert base_scheduler.original_num_days == 5
        assert base_scheduler.original_start_date.strftime("%Y-%m-%d") == "2026-01-10"

    def test_prepare_data_rooms(self, base_scheduler):
        """Проверка парсинга списка комнат и их вместимости"""
        assert hasattr(base_scheduler, 'rooms')
        assert hasattr(base_scheduler, 'room_capacities')
        assert len(base_scheduler.rooms) > 0
        first_room = base_scheduler.rooms[0]
        assert base_scheduler.room_capacities[first_room] > 0

    def test_get_by_faculty_success(self, base_scheduler):
        """Проверяет логику фильтрации предметов по факультету"""
        faculties = base_scheduler.exams_df['Faculty'].unique()
        if len(faculties) > 0:
            test_faculty = faculties[0]
            subjects = base_scheduler.get_by_faculty(test_faculty)
            assert isinstance(subjects, list)

    def test_custom_dates_management(self, base_scheduler):
        """Проверка логики добавления и удаления дат из расписания"""
        current_dates = base_scheduler.get_current_dates()
        assert len(current_dates) == 5

        date_to_remove = current_dates[0]
        base_scheduler.remove_date(date_to_remove)
        new_dates = base_scheduler.get_current_dates()
        assert date_to_remove not in new_dates
        assert len(new_dates) == 5

        base_scheduler.add_custom_date('2026-12-31')
        assert '2026-12-31' in base_scheduler.get_current_dates()


class TestExamSchedulerIntegration:
    @pytest.mark.timeout(30)  # Защита от бесконечного цикла
    def test_create_schedule_execution(self, base_scheduler):
        """
        Проверяет полный цикл генерации расписания и отсутствие падений или зацикливаний
        """
        base_scheduler._load_manual_bookings = lambda: ([], set())

        base_scheduler.create_schedule()

        assert hasattr(base_scheduler, 'schedule_df')
        assert not base_scheduler.schedule_df.empty

        expected_columns = ['Date', 'Subject', 'Instructor', 'Section', 'Room', 'Time_Slot']
        for col in expected_columns:
            assert col in base_scheduler.schedule_df.columns


class TestExamSchedulerEdgeCases:
    def test_empty_files_initialization(self):
        """EDGE CASE: Инициализация с пустыми файлами."""
        scheduler = ExamScheduler(
            title="Empty Session",
            exams_file=None,
            rooms_file=None,
            faculties_file=None,
            start_date="2026-01-10",
            num_days=5
        )
        assert scheduler.exams_df.empty
        assert scheduler.rooms_df.empty
        assert scheduler.faculties_df.empty

    def test_assign_proctors_without_schedule(self, base_scheduler):
        """EDGE CASE: Попытка назначить прокторов до генерации расписания"""
        with pytest.raises(Exception):
            base_scheduler.assign_proctors()

    def test_remove_invalid_date(self, base_scheduler):
        """EDGE CASE: Попытка удалить неверную дату"""
        with pytest.raises(ValueError, match="Некорректный формат"):
            base_scheduler.remove_date("invalid-date-format")

        with pytest.raises(ValueError, match="Указанная дата не найдена"):
            base_scheduler.remove_date("1999-01-01")
