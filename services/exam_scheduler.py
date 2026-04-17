import json
import math
import random
import re
import statistics
import traceback
from collections import defaultdict, Counter
from itertools import combinations


import pandas as pd
from datetime import datetime, timedelta
import logging
from io import StringIO
from .scheduler_core.seating_manager import SeatingManager
from .scheduler_core.proctor_manager import ProctorManager
from .scheduler_core.optimizer import SimulatedAnnealingOptimizer
from .scheduler_core.utils import group_consecutive_slots, normalize_room

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')




class ExamScheduler:
    def __init__(
            self,
            exams_file=None,
            rooms_file=None,
            faculties_file=None,
            start_date=None,
            num_days=14,
            title="Сезон беp имени",
            schedule_data=None,
            session_data=None,
            time_step=30,  # Шаг временных слотов в минутах
            work_day_start="08:00",  # Начало рабочего дня
            work_day_end="19:30",  # Конец рабочего дня
            buffer_time=30  # Буферное время после каждого экзамена в минутах
    ):
        logging.info("Инициализация планировщика экзаменов.")
        self.schedule_data = schedule_data
        self.title = title
        self.schedule_df = pd.DataFrame()
        self.time_step = time_step
        self.buffer_time = buffer_time
        self.work_day_start = datetime.strptime(work_day_start, "%H:%M")
        self.work_day_end = datetime.strptime(work_day_end, "%H:%M")
        self.seat_assignments = {}
        self.failed_sections = []
        self.room_107_bookings = []
        self.debug_sections = {"HRM 2301-24-Ch", "BUS 2203-51-Ch", "BUS 2203-50-Ch"}

        # Инициализация сервисов декомпозиции
        self.seating_manager = SeatingManager(self)
        self.proctor_manager = ProctorManager(self)
        self.optimizer = SimulatedAnnealingOptimizer(self)

        # Установка дат
        if start_date:
            self.start_date = datetime.strptime(start_date, '%Y-%m-%d')
        else:
            self.start_date = datetime.now()
        self.custom_dates = [
            self.start_date + timedelta(days=x)
            for x in range(num_days)
        ]

        self.exam_groups = pd.read_excel(exams_file) if exams_file else pd.DataFrame()

        if 'proctor_needed' not in self.exam_groups.columns:
            self.exam_groups['proctor_needed'] = False

        # Добавляем поле has_exam, если его нет
        if 'has_exam' not in self.exam_groups.columns:
            self.exam_groups['has_exam'] = True
        # Если переданы данные сессии, загружаем их
        if session_data:
            self._load_from_session(session_data)
        else:
            # Загрузка данных из файлов (если они предоставлены)
            if exams_file:
                self.exams_df = pd.read_excel(exams_file)
            else:
                self.exams_df = pd.DataFrame()  # Пустой DataFrame, если файл не предоставлен

            if rooms_file:
                self.rooms_df = pd.read_excel(rooms_file)
            else:
                self.rooms_df = pd.DataFrame()  # Пустой DataFrame, если файл не предоставлен

            if faculties_file:
                self.faculties_df = pd.read_excel(faculties_file)
            else:
                self.faculties_df = pd.DataFrame()  # Пустой DataFrame, если файл не предоставлен

            # Инициализация дат
            self.original_start_date = datetime.strptime(start_date, '%Y-%m-%d') if start_date else datetime.now()
            self.original_num_days = num_days
            self.custom_dates = self._generate_initial_dates()  # Инициализация списка дат

            # Подготовка данных
            self._prepare_data()

    def update_exam_status(self, section_id, has_exam):
        """
        Обновляет статус экзамена для указанного потока.

        :param section_id: ID потока (Section)
        :param has_exam: Булево значение (True/False)
        """
        if section_id not in self.exam_groups['Section'].values:
            raise ValueError(f"Поток с ID {section_id} не найден")

        # Обновляем статус экзамена
        self.exam_groups.loc[self.exam_groups['Section'] == section_id, 'has_exam'] = has_exam
        logging.info(f"Статус экзамена для потока {section_id} изменен на {has_exam}")

    

    def _generate_flexible_time_slots(self):
        slots = []
        current_time = self.work_day_start
        while current_time < self.work_day_end:
            end_time = current_time + timedelta(minutes=self.time_step)
            if end_time > self.work_day_end:
                break
            slots.append(
                f"{current_time.strftime('%H:%M')}-{end_time.strftime('%H:%M')}"
            )
            current_time = end_time
        return slots

    def _load_from_session(self, session_data):
        """
        Загружает все данные из сохраненной сессии, включая распределение мест
        """
        logging.info("Начало загрузки данных из сессии")

        try:
            # 1. Загружаем основные атрибуты
            self.title = session_data.title
            self.original_start_date = session_data.original_start_date
            self.original_num_days = session_data.original_num_days
            self.custom_dates = [session_data.start_date + timedelta(days=i)
                                 for i in range(session_data.days)]

            # 2. Загружаем DataFrame с нормализацией
            if session_data.schedule_data:
                self.schedule_df = pd.read_json(StringIO(session_data.schedule_data))
                if 'Date' in self.schedule_df.columns:
                    self.schedule_df['Date'] = pd.to_datetime(
                        self.schedule_df['Date'], errors='coerce').dt.date  # Нормализуем дату к date (без времени)
                if 'Time_Slot' in self.schedule_df.columns:
                    self.schedule_df['Time_Slot'] = self.schedule_df['Time_Slot'].str.strip()  # Убираем пробелы
                logging.info(f"Загружено расписание: {len(self.schedule_df)} записей")

            if session_data.exams_data:
                self.exams_df = pd.read_json(StringIO(session_data.exams_data))
                self.exams_df['fake_id'] = self.exams_df['fake_id'].astype(str)
                if 'Subject' in self.exams_df.columns:
                    self.exams_df['Subject'] = self.exams_df['Subject'].str.strip()  # Убираем пробелы в предметах
                logging.info(f"Загружены студенты: {len(self.exams_df)} записей")

            if session_data.rooms_data:
                self.rooms_df = pd.read_json(StringIO(session_data.rooms_data))

            if session_data.faculties_data:
                self.faculties_df = pd.read_json(StringIO(session_data.faculties_data))

            # 3. Загружаем распределение мест БЕЗ преобразования в кортежи
            self.seat_assignments = {}
            if hasattr(session_data, 'seat_assignments') and session_data.seat_assignments:
                try:
                    loaded_assignments = session_data.seat_assignments

                    # Если данные хранятся как JSON строка
                    if isinstance(loaded_assignments, str):
                        loaded_assignments = json.loads(loaded_assignments)

                    # Нормализуем ключи как строки (date в 'YYYY-MM-DD', strip остальных)
                    for key_str, value in list(loaded_assignments.items()):
                        try:
                            # Разбираем ключ (формат: "date|time|subject|student")
                            parts = key_str.split('|')
                            if len(parts) == 4:
                                date_str = pd.to_datetime(
                                    parts[0]).date().isoformat()  # Нормализуем дату к 'YYYY-MM-DD'
                                time = parts[1].strip()
                                subject = parts[2].strip()
                                student = parts[3].strip()
                                normalized_key = f"{date_str}|{time}|{subject}|{student}"
                                self.seat_assignments[normalized_key] = value
                        except Exception as e:
                            logging.error(f"Ошибка обработки ключа '{key_str}': {str(e)}")
                            continue

                    logging.info(f"Успешно загружено {len(self.seat_assignments)} записей о местах")

                    # Проверка загрузки
                    if self.seat_assignments:
                        sample_key = next(iter(self.seat_assignments))
                        logging.info(f"Пример записи о месте: {sample_key} => {self.seat_assignments[sample_key]}")

                except Exception as e:
                    logging.error(f"Ошибка загрузки seat_assignments: {str(e)}")
                    self.seat_assignments = {}
            else:
                logging.warning("Данные о местах не найдены в сессии - будет выполнено новое распределение")
                self.assign_seats()  # Распределяем места заново

            # 4. Выполняем финальную подготовку данных
            self._prepare_data()
            logging.info("Загрузка сессии завершена успешно")

        except Exception as e:
            logging.error(f"Критическая ошибка загрузки сессии: {traceback.format_exc()}")
            raise ValueError(f"Ошибка загрузки сессии: {str(e)}")


    def _derive_metadata(self):
        """Извлечение метаданных из существующего расписания"""
        if not self.schedule_df.empty:
            self.exam_groups = self.schedule_df.groupby(
                ['Subject', 'Instructor', 'EduProgram', 'Section']
            ).size().reset_index(name='counts')

            self.rooms = self.schedule_df['Room'].unique().tolist()
            self.room_capacities = self.schedule_df.groupby('Room')['Students_Count'].max().to_dict()

    def sched(self, data):
        """Обновление расписания с перерасчетом метаданных"""
        self.schedule_df = data
        self._derive_metadata()
        logging.info("Данные расписания обновлены.")

    def _prepare_data(self):
        logging.info("Подготовка данных для планирования.")

        # Адаптация под новые названия столбцов
        column_mapping = {
            'Student ID': 'fake_id',
            'Full Name': 'fake_name',
            'Факультет студента': 'Faculty'
        }
        self.exams_df.rename(columns=column_mapping, inplace=True)
        if 'fake_name' not in self.exams_df.columns:
            if 'name' in self.exams_df.columns:
                self.exams_df.rename(columns={'name': 'fake_name'}, inplace=True)
        
        if 'Дисциплина' in self.exams_df.columns and 'Subject' not in self.exams_df.columns:
            self.exams_df.rename(columns={'Дисциплина': 'Subject'}, inplace=True)

        # Создаем exam_groups с колонкой Duration
        self.exam_groups = self.exams_df.drop_duplicates(subset=['Section'], keep='first').groupby(
            ['Subject', 'Instructor', 'EduProgram', 'YearsOfStudy', 'Section']
        ).agg({'fake_id': 'count'}).reset_index()

        if 'proctor_needed' not in self.exam_groups.columns:
            self.exam_groups['proctor_needed'] = True

        self.exam_groups['two_rooms_needed'] = False  # Добавляем новое поле
        self.exam_groups['classroom_type'] = 'regular'
        # Добавляем колонку Duration (по умолчанию 180 минут)
        self.exam_groups["Duration"] = 180
        self.exam_groups["Proctor_Needed"] = False  # По умолчанию проктор не требуется
        self.exam_groups['has_exam'] = True

        # Нормализация списка комнат
        self.rooms = list(self.rooms_df['Аудитория'].astype(str).str.strip())

        logging.info(f"Загружено комнат: {len(self.rooms)}")
        logging.info(f"Пример комнат: {self.rooms[:5]}")  # Логируем первые 5 комнат для проверки

        self.room_capacities = dict(zip(
            self.rooms_df['Аудитория'].astype(str).str.strip(),
            self.rooms_df['Вместительность аудитории']
        ))

        # Load room types, defaulting to 'regular' if column is missing
        if 'Type' in self.rooms_df.columns:
            self.room_types = dict(zip(
                self.rooms_df['Аудитория'].astype(str).str.strip(),
                self.rooms_df['Type']
            ))
            logging.info("Загружены типы аудиторий.")
        else:
            self.room_types = {room: 'regular' for room in self.rooms}
            logging.warning("Колонка 'Type' не найдена в файле аудиторий. Все аудитории считаются 'regular'.")

        room_type_counts = Counter(self.room_types.values())
        logging.info("Сводка по типам аудиторий:")
        for room_type, count in room_type_counts.items():
            logging.info(f"  - Тип: {room_type}, Количество: {count}")
        
        self.all_students_dict = self.exams_df[['fake_id', 'fake_name']].drop_duplicates().to_dict('records')
        self.exam_groups["Duration"] = 180  # Дефолтная длительность
        self.subject_faculty_map = self.faculties_df.groupby('Subject')['Faculty'].apply(set).to_dict()
        self.faculty_proctors = self.faculties_df.groupby('Faculty')['Instructor'].apply(list).to_dict()

        total_duration_minutes = (self.work_day_end - self.work_day_start).total_seconds() / 60
        num_blocks_in_day = int(total_duration_minutes / self.time_step)
        total_blocks = len(self.rooms) * num_blocks_in_day * self.original_num_days
        
        # Кэширование списка студентов по секциям для O(1) доступа
        self.section_students_map = self.exams_df.groupby('Section')['fake_id'].apply(list).to_dict()
        
        logging.info(f"Всего экзаменов: {len(self.exam_groups)}")
        logging.info(f"Всего доступно блоков для планирования: {total_blocks}")

    def get_by_faculty(self, faculty):
        """
        Возвращает список уникальных предметов (Subject) для указанного факультета (Faculty).

        :param faculty: Название факультета (например, 'Школа экономики и менеджмента')
        :return: Список уникальных предметов
        """
        logging.info(f"Получение списка предметов для факультета: {faculty}")

        try:
            # Ищем предметы из exams_df, где указанный факультет есть в столбце Faculty
            subjects = self.exams_df[self.exams_df['Faculty'] == faculty]['Subject'].unique()
            subjects = sorted(subjects)  # Сортируем для консистентности
            logging.info(f"Найдено {len(subjects)} уникальных предметов для факультета {faculty}")
            return subjects
        except Exception as e:
            logging.error(f"Ошибка при получении предметов для факультета {faculty}: {str(e)}")
            return []

    def update_room_requirement(self, section_id, two_rooms_needed):
        if section_id not in self.exam_groups['Section'].values:
            raise ValueError(f"Section {section_id} not found")
        self.exam_groups.loc[self.exam_groups['Section'] == section_id, 'two_rooms_needed'] = two_rooms_needed
        logging.info(f"Обновлено требование к аудиториям для {section_id}: two_rooms_needed={two_rooms_needed}")

    def update_classroom_type(self, section_id, classroom_type):
        if section_id not in self.exam_groups['Section'].values:
            raise ValueError(f"Section {section_id} not found")
        if classroom_type not in ['regular', 'it_lab']:
            raise ValueError(f"Invalid classroom type: {classroom_type}. Must be 'regular' or 'it_lab'.")
        self.exam_groups.loc[self.exam_groups['Section'] == section_id, 'classroom_type'] = classroom_type
        logging.info(f"Updated classroom type for section {section_id} to {classroom_type}")

    def update_exam_durations(self, exam_data):
        """
        Обновляет длительность экзаменов в exam_groups.

        :param exam_data: Список словарей с данными об экзаменах.
            Пример: [{"section_id": "Opt Math-1532", "duration": 60}, ...]
        """
        # Готовим заранее очищенные и приведенные к нижнему регистру данные для сравнения
        section_series_cleaned = self.exam_groups['Section'].str.strip().str.lower()

        for exam in exam_data:
            # Очищаем и приводим к нижнему регистру входные данные
            original_section_id = exam["section_id"]
            section_id_cleaned = original_section_id.strip().lower()
            duration = exam["duration"]

            # Проверяем допустимые значения длительности
            if duration not in [60, 90, 120, 150, 180]:
                raise ValueError(f"Недопустимая длительность {duration}. Допустимые значения: 60, 90, 120, 150 или 180 минут.")

            # Находим и обновляем запись, используя очищенные данные
            if section_id_cleaned not in section_series_cleaned.values:
                # Диагностическое логирование на случай повторной ошибки
                logging.error(f"ПОИСК НЕ УДАЛСЯ. Искомая секция (очищенная): '{section_id_cleaned}'")
                logging.error("Доступные секции (очищенные, первые 30 для примера):")
                for s in list(section_series_cleaned.values)[:30]:
                    logging.error(f"  - '{s}'")
                raise ValueError(f"Секция {original_section_id} не найдена")

            # Обновляем в оригинальном DataFrame, где очищенная секция совпадает
            self.exam_groups.loc[section_series_cleaned == section_id_cleaned, "Duration"] = duration

        logging.info(f"Обновлены длительности для {len(exam_data)} экзаменов.")

    def batch_update_exams(self, exams_data):
        """
        Batch updates exams with has_exam, proctor_needed, and two_rooms_needed status.

        :param exams_data: A list of dictionaries, where each dictionary has
                           'section_id', 'has_exam', 'has_proctor', and 'two_rooms_needed'.
        """
        logging.info(f"Starting batch update for {len(exams_data)} exams.")
        for exam in exams_data:
            section_id = exam.get('section_id')
            has_exam = exam.get('has_exam')
            has_proctor = exam.get('has_proctor')
            two_rooms_needed = exam.get('two_rooms_needed')

            if section_id not in self.exam_groups['Section'].values:
                logging.warning(f"Section {section_id} not found, skipping.")
                continue

            if has_exam is not None:
                self.exam_groups.loc[self.exam_groups['Section'] == section_id, 'has_exam'] = has_exam
                logging.info(f"Updated has_exam for section {section_id} to {has_exam}")

            if has_proctor is not None:
                self.exam_groups.loc[self.exam_groups['Section'] == section_id, 'proctor_needed'] = has_proctor
                logging.info(f"Updated proctor_needed for section {section_id} to {has_proctor}")

            if two_rooms_needed is not None:
                self.exam_groups.loc[self.exam_groups['Section'] == section_id, 'two_rooms_needed'] = two_rooms_needed
                logging.info(f"Updated two_rooms_needed for section {section_id} to {two_rooms_needed}")
        logging.info("Finished batch update.")


    def _generate_initial_dates(self):
        """Генерирует начальный список дат"""
        return [
            self.original_start_date + timedelta(days=i)
            for i in range(self.original_num_days)
        ]

    def get_current_dates(self):
        """Возвращает текущий список дат в формате строк"""
        return [d.strftime('%Y-%m-%d') for d in self.custom_dates]

    def remove_date(self, date_str):
        """
        Удаляет дату и добавляет новую (следующую) в конец.
        Если удаляется последняя дата, добавляется следующая по дате.
        """
        try:
            date_to_remove = datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            raise ValueError("Некорректный формат даты. Используйте YYYY-MM-DD")

        if date_to_remove not in self.custom_dates:
            raise ValueError("Указанная дата не найдена в расписании")

        # Сортируем список, чтобы определить последнюю дату корректно
        self.custom_dates.sort()

        # Определяем последнюю дату ДО удаления
        last_before_removal = self.custom_dates[-1]

        # Удаляем указанную дату
        self.custom_dates.remove(date_to_remove)

        # Определяем базовую дату, от которой создаём новую
        # Если удалили последнюю, берём last_before_removal
        # Если остались даты, берём последнюю из оставшихся
        base_date = (
            last_before_removal
            if date_to_remove == last_before_removal
            else self.custom_dates[-1]
        )

        # Добавляем новую дату (+1 день от базовой)
        new_date = base_date + timedelta(days=1)
        self.custom_dates.append(new_date)

    def add_custom_date(self, date_str):
        """Добавляет произвольную дату в список"""
        try:
            new_date = datetime.strptime(date_str, '%Y-%m-%d')
            if new_date not in self.custom_dates:
                self.custom_dates.append(new_date)
        except ValueError:
            raise ValueError("Некорректный формат даты. Используйте YYYY-MM-DD")

    def restore_default_dates(self):
        """Восстанавливает исходные даты"""
        self.custom_dates = self._generate_initial_dates()

    # Метод перенесен в отдельную логику классификации (в будущем)
    # Оставляем здесь для совместимости, но помечаем как deprecated
    @staticmethod
    def get_exam_type(exam_info):
        """Определяет тип экзамена на основе его атрибутов."""
        # TODO: Переместить в services/exam_classifier.py
        has_exam = exam_info.get('has_exam', False)
        proctor_needed = exam_info.get('proctor_needed', False)
        two_rooms_needed = exam_info.get('two_rooms_needed', False)

        if has_exam and proctor_needed and two_rooms_needed:
            return "written"
        elif not has_exam and not proctor_needed and not two_rooms_needed:
            return "summative"
        elif has_exam and not proctor_needed and not two_rooms_needed:
            return "defense"
        return "unknown"

    def manage_subjects_before_scheduling(self):

        while True:
            print("\n=== Управление предметами перед составлением расписания ===")
            print("1. Показать все предметы и их секции")
            print("2. Продолжить с генерацией расписания")
            choice = input("Выберите действие (1-2): ")

            if choice == "1":
                changed = self.show_subjects_and_delete()
                if changed:
                    print("\nСписок предметов был изменен.")
                    self._prepare_data()  # Обновляем данные после изменений
            elif choice == "2":
                break
            else:
                print("Неверный выбор. Пожалуйста, выберите 1 или 2")

    def run_scheduling_process(self, skip_management=False):
        if not skip_management:
            # Переносим логику в API
            pass

        self.create_schedule()
        print("Расписание успешно создано!")

    def get_student_sections(self, student_id):
        logging.info(f"Поиск секций для студента {student_id}.")

        # Проверка наличия данных
        if self.exams_df is None or self.exams_df.empty:
            logging.error("Ошибка: Данные о студентах не загружены!")
            return pd.DataFrame()

        if self.schedule_df is None or self.schedule_df.empty:
            logging.warning("Расписание не создано. Сначала создайте расписание.")
            return pd.DataFrame()

        try:
            # Приведение student_id к строке
            student_id = str(student_id)

            # Поиск секций студента
            student_sections = self.exams_df[self.exams_df['fake_id'] == student_id]['Section'].unique()

            if len(student_sections) == 0:
                logging.warning(f"Студент {student_id} не найден в базе данных")
                return pd.DataFrame()

            # Фильтрация расписания
            student_schedule = self.schedule_df[self.schedule_df['Section'].isin(student_sections)]

            return student_schedule.sort_values(['Date', 'Time_Slot'])

        except Exception as e:
            logging.error(f"Ошибка при поиске секций: {str(e)}")
            return pd.DataFrame()

    def assign_proctors(self, proctors_path=None):
        return self.proctor_manager.assign_proctors(proctors_path)

    def get_all_proctors(self):
        return self.proctor_manager.get_all_proctors()

    def get_all_section_names(self):
        """
        Возвращает простой список всех известных имен секций для диагностики.
        """
        if not hasattr(self, 'exam_groups') or self.exam_groups.empty:
            return []
        
        return self.exam_groups['Section'].unique().tolist()

    def analyze_student_sections(self):
        student_sections = self.exams_df.groupby('fake_id')['Section'].nunique()
        avg_sections = student_sections.mean()
        max_sections = student_sections.max()
        print(f"Среднее количество секций на студента: {avg_sections}")
        print(f"Максимальное количество секций у одного студента: {max_sections}")
        # Студенты с количеством секций больше 14
        students_over_limit = student_sections[student_sections > 14]
        print(f"Количество студентов с более чем 14 секциями: {len(students_over_limit)}")
        if not students_over_limit.empty:
            print("Студенты с более чем 14 секциями:")
            print(students_over_limit)
        return student_sections

    def create_schedule(self):
        from .scheduler_core.planner import Planner
        logging.info("Делегирование планирования в Planner...")
        planner = Planner(self)
        try:
            planner.create_schedule()
        except Exception as e:
            import traceback
            logging.error(f"Ошибка при создании расписания в Planner: {str(e)}")
            logging.error(traceback.format_exc())
            self.schedule_df = pd.DataFrame()
            return self.schedule_df

        self.schedule_df = pd.DataFrame(self.schedule) if hasattr(self, 'schedule') and self.schedule else pd.DataFrame()
        if not self.schedule_df.empty:
            self.assign_seats()

        return self.schedule_df

    def optimize_schedule(self, student_exams):
        return self.optimizer.optimize(student_exams)

    def get_total_conflicts(self, student_exams_dict):
        return self.optimizer.calculate_total_conflicts(student_exams_dict)

    def check_overlap(self, slot1, slot2):
        return self.optimizer.check_overlap(slot1, slot2)


    def _log_schedule_stats(self):
        if self.schedule_df.empty:
            logging.info("Расписание пустое.")
            return

        exams_per_day = self.schedule_df.groupby('Date').size()
        for date, count in exams_per_day.items():
            slots_used = count // len(self.rooms) + (1 if count % len(self.rooms) > 0 else 0)
            logging.info(f"День {date}: использовано {slots_used} из {len(self.time_slots)} слотов, экзаменов: {count}")
        logging.info(
            f"Успешно запланировано экзаменов: {len(self.schedule_df)} из {len(self.exam_groups[self.exam_groups['has_exam'] == True])}")

    def export_schedule(self, output_excel):
        """Экспорт расписания с учетом разделенных потоков"""
        if self.schedule_df is None or self.schedule_df.empty:
            if self.schedule:
                self.schedule_df = pd.DataFrame(self.schedule)
            else:
                logging.warning("Нет данных для экспорта в Excel.")
                # Создаем пустой файл, чтобы избежать ошибки
                pd.DataFrame().to_excel(output_excel, index=False)
                return

        # Group consecutive slots before exporting
        schedule_records = self.schedule_df.to_dict('records')
        grouped_records = group_consecutive_slots(schedule_records)
        grouped_df = pd.DataFrame(grouped_records)

        # Drop seat_info if it exists, as it's a dict and causes issues with Excel export
        if 'seat_info' in grouped_df.columns:
            grouped_df = grouped_df.drop(columns=['seat_info'])

        grouped_df.to_excel(output_excel, index=False)

    def export_html_schedule(self, output_html):
        logging.info("Экспорт общего расписания в файл HTML.")
        self.schedule_df.to_html(output_html, index=False)
        logging.info(f"Расписание сохранено в файл {output_html}.")

    def find_available_rooms(self, day, time_slot):
        logging.info(f"Поиск свободных аудиторий на {day} в слот {time_slot}.")

        try:
            day = datetime.strptime(day, '%d-%m-%Y')
        except ValueError:
            logging.error(f"Некорректный формат даты: {day}. Ожидается 'YYYY-MM-DD'.")
            return []

        if day not in self.room_availability or time_slot not in self.room_availability[day]:
            logging.warning(f"Нет данных о занятости для {day} и слота {time_slot}.")
            return self.rooms  # Если данных нет, считаем все аудитории свободными

        busy_rooms = self.room_availability[day][time_slot]

        available_rooms = [room for room in self.rooms if room not in busy_rooms]

        logging.info(f"Найдено {len(available_rooms)} свободных аудиторий.")
        return available_rooms

    def print_student_schedule(self, student_id):
        student_schedule = self.get_student_sections(student_id)
        if student_schedule.empty:
            print(f"Для студента {student_id} не найдено расписания.")
            return

        print(f"Расписание для студента {student_id}:\n")
        print("=" * 50)
        for _, row in student_schedule.iterrows():
            print(f"Секция: {row['Section']}")
            print(f"Предмет: {row['Subject']}")
            print(f"Дата: {row['Date']}")
            print(f"Время: {row['Time_Slot']}")
            print(f"Аудитория: {row['Room']}")
            print("-" * 50)

    def export_student_schedule_to_excel(self, student_id, output_file):
        student_schedule_df = self.get_student_sections(student_id)
        if student_schedule_df.empty:
            logging.warning(f"Для студента {student_id} не найдено расписания.")
            # Create an empty excel file to avoid errors
            pd.DataFrame().to_excel(output_file, index=False)
            return

        # Group consecutive slots
        schedule_records = student_schedule_df.to_dict('records')
        grouped_records = group_consecutive_slots(schedule_records)
        grouped_df = pd.DataFrame(grouped_records)

        # Drop seat_info if it exists
        if 'seat_info' in grouped_df.columns:
            grouped_df = grouped_df.drop(columns=['seat_info'])

        grouped_df.to_excel(output_file, index=False)
        logging.info(f"Расписание для студента {student_id} сохранено в файл {output_file}.")

    def get_section_info(self, section_id):
        logging.info(f"Поиск информации для секции: {section_id}")

        section_data = self.exams_df[self.exams_df['Section'] == section_id]

        if section_data.empty:
            logging.warning(f"Секция {section_id} не найдена")
            return {
                'error': 'Section not found',
                'message': f'Секция {section_id} не найдена в базе данных'
            }

        section_info = {
            'section_id': section_id,
            'section_info': {
                'instructor': section_data['Instructor'].iloc[0],
                'subject': section_data['Subject'].iloc[0],
                'edu_program': section_data['EduProgram'].iloc[0],
                'years_of_study': section_data['YearsOfStudy'].iloc[0],
                'total_students': len(section_data)
            }
        }

        if self.schedule_df is not None and not self.schedule_df.empty:
            schedule_info = self.schedule_df[self.schedule_df['Section'] == section_id].to_dict('records')
            section_info['schedule'] = schedule_info[0] if schedule_info else None
        else:
            section_info['schedule'] = None

        section_students = section_data[['fake_id', 'fake_name']].drop_duplicates().to_dict('records')
        # Add seat and room information for each student
        for student in section_students:
            student_id = str(student['fake_id'])
            exam_date = section_info['schedule']['Date'] if section_info['schedule'] else None
            time_slot = section_info['schedule']['Time_Slot'] if section_info['schedule'] else None
            subject = section_info['section_info']['subject']
            if exam_date and time_slot:
                key = f"{pd.to_datetime(exam_date).date()}|{time_slot.strip()}|{subject.strip()}|{student_id}"
                seat_info = self.seat_assignments.get(key, {})
                student['seat'] = seat_info.get('seat', None)
                student['room'] = seat_info.get('room', None)

        section_info['students'] = section_students

        logging.info(f"Собрана информация о секции {section_id}: "
                     f"{section_info['section_info']['total_students']} студентов")

        return section_info

    def get_all_student_schedules_data(self):
        """
        Собирает расписание всех студентов в один DataFrame для экспорта.
        """
        logging.info("Сбор расписания для всех студентов...")

        if self.schedule_df is None or self.schedule_df.empty:
            logging.warning("Расписание не сгенерировано. Нечего экспортировать.")
            return pd.DataFrame()

        # 1. Получаем список всех студентов
        all_students_df = self.exams_df[['fake_id', 'fake_name', 'Faculty']].drop_duplicates(subset=['fake_id'])

        # 2. Создаем словарь для быстрого доступа к метаданным по секциям
        section_meta_map = self.exam_groups.set_index('Section').to_dict('index')

        all_exams_list = []

        # 3. Итерируемся по каждому студенту
        for _, student in all_students_df.iterrows():
            student_id = str(student['fake_id'])
            student_name = student['fake_name']
            student_faculty = student['Faculty']

            # Получаем расписание для одного студента
            student_schedule_df = self.get_student_sections(student_id)

            if student_schedule_df.empty:
                continue

            # Группируем последовательные слоты
            schedule_records = student_schedule_df.to_dict('records')
            grouped_records = group_consecutive_slots(schedule_records)

            # 4. Обогащаем каждую запись и добавляем в общий список
            for exam in grouped_records:
                section_id = exam.get('Section')
                section_meta = section_meta_map.get(section_id, {})

                enriched_exam = {
                    'ID Студента': student_id,
                    'ФИО Студента': student_name,
                    'Факультет': student_faculty,
                    'Предмет': exam.get('Subject'),
                    'Секция': section_id,
                    'Дата': exam.get('Date'),
                    'Время': exam.get('Time_Slot'),
                    'Аудитория': exam.get('Room'),
                    'Преподаватель': exam.get('Instructor'),
                    'Образовательная программа': exam.get('EduProgram'),
                    'Год обучения': section_meta.get('YearsOfStudy'),
                    'Длительность экзамена': exam.get('Duration'),
                    'Тип аудитории': section_meta.get('classroom_type', 'regular'),
                    'Нужен ли проктор': exam.get('proctor_needed')
                }
                all_exams_list.append(enriched_exam)

        if not all_exams_list:
            logging.warning("Не найдено ни одного экзамена для студентов.")
            return pd.DataFrame()

        # 5. Создаем итоговый DataFrame
        final_df = pd.DataFrame(all_exams_list)

        # Преобразуем дату в нужный формат, если она не строка
        if 'Дата' in final_df.columns:
             final_df['Дата'] = pd.to_datetime(final_df['Дата']).dt.strftime('%Y-%m-%d')


        # 6. Сортируем для удобства
        final_df.sort_values(by=['ФИО Студента', 'Дата', 'Время'], inplace=True)

        logging.info(f"Собрано {len(final_df)} записей о экзаменах для {len(all_students_df)} студентов.")

        return final_df

    def export_section_info_to_excel(self, section_info, output_file):
        logging.info(f"Экспорт информации о секции в файл {output_file}.")

        # Создаем DataFrame для основной информации о секции
        section_data = {
            'Section ID': [section_info['section_id']],
            'Instructor': [section_info['section_info']['instructor']],
            'Subject': [section_info['section_info']['subject']],
            'EduProgram': [section_info['section_info']['edu_program']],
            'YearsOfStudy': [section_info['section_info']['years_of_study']],
            'Total Students': [section_info['section_info']['total_students']]
        }
        if section_info['schedule']:
            section_data.update({
                'Date': [section_info['schedule']['Date']],
                'Time Slot': [section_info['schedule']['Time_Slot']],
                'Room': [section_info['schedule']['Room']],
                'Duration': [section_info['schedule']['Duration']],
                'Proctor Needed': [section_info['schedule']['proctor_needed']]
            })
        section_df = pd.DataFrame(section_data)

        # Создаем DataFrame для студентов с местами и аудиториями
        students_data = [
            {
                'Student ID': student['fake_id'],
                'Student Name': student['fake_name'],
                'Seat': student.get('seat', 'N/A'),
                'Room': student.get('room', 'N/A')
            }
            for student in section_info['students']
        ]
        students_df = pd.DataFrame(students_data)
        students_header = f"Студенты секции ({len(students_data)}):"

        # Сохраняем все в один Excel файл
        with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
            section_df.to_excel(writer, sheet_name='Section Info', index=False)
            students_df.to_excel(writer, sheet_name='Section Info', startrow=len(section_df) + 2, index=False,
                                 header=['Student ID', 'Student Name', 'Seat', 'Room'])

        logging.info(f"Информация о секции успешно сохранена в файл {output_file}.")


    def get_unique_subjects(self):

        logging.info("Получение списка уникальных предметов")

        unique_subjects = sorted(self.exam_groups['Subject'].unique())
        return unique_subjects

    def show_subjects_and_delete(self):

        logging.info("Получение списка уникальных предметов")

        subjects = self.get_unique_subjects()
        print("\nСписок всех предметов:")
        for idx, subject in enumerate(subjects, 1):
            print(f"{idx}. {subject}")

        while True:
            try:
                choice = input("\nВыберите номер предмета для просмотра групп (0 - для выхода): ")
                if choice == '0':
                    return False

                subject_idx = int(choice) - 1
                if 0 <= subject_idx < len(subjects):
                    selected_subject = subjects[subject_idx]
                    self.delete_subject_groups(selected_subject)
                    return True
                else:
                    print("Неверный номер предмета")
            except ValueError:
                print("Пожалуйста, введите число")

    def delete_subject_groups(self, subject):

        logging.info(f"Поиск групп для предмета: {subject}")

        subject_groups = self.exam_groups[self.exam_groups['Subject'] == subject]

        if subject_groups.empty:
            logging.warning(f"Группы для предмета {subject} не найдены")
            return False

        print(f"\nГруппы по предмету {subject}:")
        print("=" * 50)

        for edu_program in subject_groups['EduProgram'].unique():
            print(f"\nПрограмма: {edu_program}")
            print("-" * 30)

            program_groups = subject_groups[subject_groups['EduProgram'] == edu_program]
            for idx, group in program_groups.iterrows():
                print(f"Секция: {group['Section']}")
                print(f"Преподаватель: {group['Instructor']}")
                print(f"Количество студентов: {group['fake_id']}")
                print("-" * 20)

        while True:
            choice = input("\nВыберите действие:\n" "1 - Удалить все группы этого предмета\n" "2 - Удалить конкретную группу\n" "3 - Вернуться к списку предметов\n" "Ваш выбор: ")

            if choice == "1":
                sections_to_delete = subject_groups['Section'].tolist()
                self._delete_sections(sections_to_delete)
                logging.info(f"Удалены все группы предмета {subject}")
                return True

            elif choice == "2":
                print("\nСписок секций:")
                sections = subject_groups['Section'].tolist()
                for idx, section in enumerate(sections, 1):
                    print(f"{idx}. {section}")

                while True:
                    try:
                        section_idx = int(input("\nВведите номер секции для удаления (0 - отмена): ")) - 1
                        if section_idx == -1:
                            return False
                        if 0 <= section_idx < len(sections):
                            section_to_delete = sections[section_idx]
                            self._delete_sections([section_to_delete])
                            logging.info(f"Удалена секция {section_to_delete}")
                            return True
                        else:
                            print("Неверный номер секции")
                    except ValueError:
                        print("Пожалуйста, введите число")

            elif choice == "3":
                return False

            else:
                print("Неверный выбор. Пожалуйста, выберите 1, 2 или 3")

    def _delete_sections(self, sections):

        # Удаляем из основного DataFrame с экзаменами
        if self.exams_df is not None and 'Section' in self.exams_df.columns:
            self.exams_df = self.exams_df[~self.exams_df['Section'].isin(sections)]

        # Удаляем из сгруппированных экзаменов
        if self.exam_groups is not None and 'Section' in self.exam_groups.columns:
            self.exam_groups = self.exam_groups[~self.exam_groups['Section'].isin(sections)]

        # Если расписание уже создано, удаляем и из него
        if self.schedule_df is not None and not self.schedule_df.empty and 'Section' in self.schedule_df.columns:
            self.schedule_df = self.schedule_df[~self.schedule_df['Section'].isin(sections)]

        # Пересчитываем данные
        self._prepare_data()

    def load_schedule(self, input_excel):
        logging.info("Загрузка расписания из файла.")
        self.schedule_df = pd.read_excel(input_excel)
        logging.info("Расписание успешно загружено.")

    def edit_schedule_entry(self, section, room=None, date=None, time_slot=None, proctor=None):

        logging.info(f"Редактирование записи расписания для секции: {section}")

        if self.schedule_df is None:
            raise ValueError("Расписание не загружено.")

        if section not in self.schedule_df['Section'].values:
            raise ValueError(f"Секция {section} не найдена в расписании.")

        index = self.schedule_df[self.schedule_df['Section'] == section].index[0]

        if room is not None:
            self.schedule_df.at[index, 'Room'] = room
            logging.info(f"Аудитория изменена на: {room}")
        if date is not None:
            # Проверяем формат даты
            try:
                datetime.strptime(date, '%Y-%m-%d')  # Проверка формата даты
                self.schedule_df.at[index, 'Date'] = date
                logging.info(f"Дата изменена на: {date}")
            except ValueError:
                raise ValueError("Некорректный формат даты. Ожидается 'YYYY-MM-DD'.")
        if time_slot is not None:
            self.schedule_df.at[index, 'Time_Slot'] = time_slot
            logging.info(f"Временной слот изменен на: {time_slot}")
        if proctor is not None:
            self.schedule_df.at[index, 'Proctor'] = proctor
            logging.info(f"Проктор изменен на: {proctor}")

        logging.info(f"Запись для секции {section} успешно изменена.")

    def save_schedule(self, output_excel):
        logging.info("Сохранение изменений расписания в файл.")
        self.schedule_df.to_excel(output_excel, index=False)
        logging.info(f"Измененное расписание сохранено в файл {output_excel}.")

    def assign_seats(self):
        """
        Распределяет студентов по аудиториям с учетом вместимости и требования двух комнат,
        сохраняя информацию о местах в seat_assignments.
        """
        return self.seating_manager.assign_seats()

    def validate_schedule(self):
        """Validates the schedule before finalizing"""
        errors = []

        # Check all students have sections
        orphan_students = set(self.exams_df['fake_id']) - set(self.schedule_df['Section'].explode())
        if orphan_students:
            errors.append(f"{len(orphan_students)} students without scheduled sections")

        # Check room assignments
        for room in self.schedule_df['Room'].unique():
            if room not in self.room_capacities:
                errors.append(f"Room {room} not found in room capacities")

        return errors

    def get_seat_assignment(self, student_id, exam_date, time_slot, subject):
        """Возвращает информацию о месте студента"""
        return self.seating_manager.get_seat_assignment(student_id, exam_date, time_slot, subject)

    def get_students_for_section(self, section_id):
        """
        Возвращает список student_id для указанной секции
        """
        if not hasattr(self, 'exams_df'):
            raise AttributeError("exams_df not loaded - please activate a complete session")

        section_students = self.exams_df[self.exams_df['Section'] == section_id]
        return section_students['fake_id'].tolist()

    def analyze_failed_sections_details(self):
        logging.info("Начало анализа незапланированных секций.")
        report = []

        if not self.failed_sections:
            report.append("Все секции успешно запланированы!")
            logging.info("Все секции успешно запланированы!")
            return "\n".join(report)

        report.append(f"Всего незапланированных секций: {len(self.failed_sections)}")
        report.append("-" * 50)

        for failed_section_dict in self.failed_sections:
            section_id = failed_section_dict.get('Section', 'N/A')
            subject = failed_section_dict.get('Subject', 'N/A')
            students_count = failed_section_dict.get('student_count', 'N/A') # This is from the group, not exams_df

            report.append(f"Секция: {section_id} (Предмет: {subject}, Студентов: {students_count})")
            
            # Get actual student IDs for this section
            section_students_df = self.exams_df[self.exams_df['Section'] == section_id]
            student_ids_in_failed_section = section_students_df['fake_id'].unique().tolist()

            if not student_ids_in_failed_section:
                report.append("  Нет студентов в этой секции.")
                continue

            report.append("  Студенты в этой секции и их текущее расписание:")
            for student_id in student_ids_in_failed_section:
                student_id_str = str(student_id)
                scheduled_exams_for_student = [
                    exam for exam in self.student_exams.get(student_id_str, [])
                    if exam['Section'] != section_id # Exclude the failed section itself if it somehow got partially added
                ]

                if scheduled_exams_for_student:
                    report.append(f"    Студент {student_id_str} уже имеет экзамены:")
                    for exam in scheduled_exams_for_student:
                        report.append(f"      - {exam.get('Subject')} на {exam.get('Date')} в {exam.get('Time_Slot')}")
                else:
                    report.append(f"    Студент {student_id_str} не имеет запланированных экзаменов (кроме этой секции).")
            report.append("-" * 50)
        
        logging.info("Анализ незапланированных секций завершен.")
        return "\n".join(report)
