import os
import sys
import pytest
import pandas as pd
from datetime import datetime
import json

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from services.exam_scheduler import ExamScheduler

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

ROOMS_FILE = os.path.join(DATA_DIR, "rooms_test.xlsx")
EXAMS_FILE = os.path.join(DATA_DIR, "students.xlsx")
FACULTIES_FILE = os.path.join(DATA_DIR, "prepods.xlsx")

@pytest.fixture
def base_scheduler():
    scheduler = ExamScheduler(
        title="Coverage Session",
        exams_file=EXAMS_FILE,
        rooms_file=ROOMS_FILE,
        faculties_file=FACULTIES_FILE,
        start_date="2026-01-10",
        num_days=5
    )
    scheduler._load_manual_bookings = lambda: ([], set())
    return scheduler

class TestExamSchedulerCoverage:

    def test_restore_default_dates(self, base_scheduler):
        """Проверяет метод restore_default_dates."""
        base_scheduler.add_custom_date('2030-01-01')
        assert len(base_scheduler.custom_dates) == 6
        base_scheduler.restore_default_dates()
        assert len(base_scheduler.custom_dates) == 5

    def test_create_schedule_already_exists(self, base_scheduler):
        """Ветка if not self.schedule_df.empty."""
        base_scheduler.schedule_df = pd.DataFrame({"dummy": [1]})
        base_scheduler.create_schedule()
        assert not base_scheduler.schedule_df.empty # Не должен перезаписать

    def test_create_schedule_not_available_rooms(self, base_scheduler):
        """Ветка когда не хватает комнат (set room_capacities to empty)."""
        base_scheduler.room_capacities = {}
        base_scheduler.rooms = []
        base_scheduler.create_schedule()
        assert len(base_scheduler.failed_sections) > 0

    def test_get_student_sections_no_exams_df(self, base_scheduler):
        """Ветка 'if self.exams_df is None or self.exams_df.empty:' в get_student_sections."""
        base_scheduler.exams_df = pd.DataFrame()
        result = base_scheduler.get_student_sections(123)
        assert result.empty

    def test_get_student_sections_no_schedule(self, base_scheduler):
        """Ветка 'if self.schedule_df is None' в get_student_sections."""
        base_scheduler.schedule_df = pd.DataFrame()
        result = base_scheduler.get_student_sections(123)
        assert result.empty
        
    def test_get_student_sections_student_not_found(self, base_scheduler):
        """Ветка 'if len(student_sections) == 0' - студент не найден."""
        base_scheduler.create_schedule()
        result = base_scheduler.get_student_sections(99999999) # Несуществующий ID
        assert result.empty

    def test_get_student_sections_success(self, base_scheduler):
        """Успешный поиск студента."""
        base_scheduler.create_schedule()
        if not base_scheduler.exams_df.empty:
            first_student_id = base_scheduler.exams_df.iloc[0]['fake_id']
            result = base_scheduler.get_student_sections(first_student_id)
            assert not result.empty

    def test_assign_proctors_no_available_proctors(self, base_scheduler):
        """Ветка 'if not available_proctors:' в assign_proctors."""
        base_scheduler.create_schedule()
        base_scheduler.faculty_proctors = {} # Очищаем пул прокторов
        with pytest.raises(ValueError, match="Нет доступных прокторов для назначения."):
            base_scheduler.assign_proctors()

    def test_assign_proctors_107_room_logic(self, base_scheduler):
        """Проверяет назначение 4х прокторов, если комната 107."""
        # Мокаем расписание, чтобы там была комната 107
        base_scheduler.schedule_df = pd.DataFrame([{
            'Section': 'TestSection1',
            'Subject': 'TestSubject',
            'Date': '2026-01-10',
            'Time_Slot': '08:00 - 11:00',
            'Room': '107', # Целевая логика
            'proctor_needed': True,
            'two_rooms_needed': False
        }])
        # Мокаем прокторов (В коде 107 комната и IT предметы часто требуют ШЦТ прокторов)
        # Судя по логу ошибки: "Нет доступных прокторов из ШЦТ..."
        base_scheduler.faculty_proctors = {
            'Факультет ИТ': ['Прок1', 'Прок2', 'Прок3', 'Прок4', 'Прок5'],
            'Школа цифровых технологий': ['SCT1', 'SCT2', 'SCT3', 'SCT4', 'SCT5']
        }
        base_scheduler.subject_faculty_map = {'TestSubject': set(['Школа цифровых технологий'])}
        
        base_scheduler.assign_proctors()
        
        assigned_list = base_scheduler.schedule_df.iloc[0]['Proctor'].split(', ')
        assert len(assigned_list) == 4
