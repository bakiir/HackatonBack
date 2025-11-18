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

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def group_consecutive_slots(slots):
    if not slots:
        return []

    # Define the key for grouping exams that are the same event
    def get_exam_key(slot):
        return (
            str(slot.get('Date')),
            slot.get('Subject'),
            slot.get('Instructor'),
            slot.get('Room'),
            slot.get('EduProgram'),
            slot.get('Section'),
            # Do not group by duration, as it's the same for all small slots
            # slot.get('Duration'), 
            slot.get('Students_Count'),
            slot.get('pinned'),
            slot.get('two_rooms_needed')
        )

    # Sort slots by the grouping key and then by time
    try:
        slots.sort(key=lambda s: (get_exam_key(s), datetime.strptime(s.get('Time_Slot', '00:00-00:00').split('-')[0], '%H:%M')))
    except (ValueError, IndexError):
        # If time format is unexpected, fall back to string sort for time
        slots.sort(key=lambda s: (get_exam_key(s), s.get('Time_Slot', '')))


    merged_slots = []
    i = 0
    while i < len(slots):
        # Start of a potential group
        group = [slots[i]]
        j = i + 1
        while j < len(slots):
            # Check if the next slot is part of the same exam event
            if get_exam_key(slots[j]) == get_exam_key(slots[i]):
                try:
                    # Check if the time slots are consecutive
                    prev_end_time_str = group[-1]['Time_Slot'].split('-')[1]
                    curr_start_time_str = slots[j]['Time_Slot'].split('-')[0]
                    
                    prev_end_time = datetime.strptime(prev_end_time_str, '%H:%M')
                    curr_start_time = datetime.strptime(curr_start_time_str, '%H:%M')

                    # If they are consecutive
                    if prev_end_time == curr_start_time:
                        group.append(slots[j])
                        j += 1
                    else:
                        # Time is not consecutive, so break the group
                        break
                except (ValueError, IndexError):
                    # Time format is wrong, break the group
                    break
            else:
                # Different exam, break the group
                break
        
        # If the group has more than one slot, merge them
        if len(group) > 1:
            first_slot = group[0]
            last_slot = group[-1]
            
            start_time = first_slot['Time_Slot'].split('-')[0]
            end_time = last_slot['Time_Slot'].split('-')[1]
            
            merged_slot = first_slot.copy()
            merged_slot['Time_Slot'] = f"{start_time}-{end_time}"
            
            # Per user request, use the last slot's Base_Time_Slot and seat_info
            if 'Base_Time_Slot' in last_slot:
                merged_slot['Base_Time_Slot'] = last_slot['Base_Time_Slot']
            if 'seat_info' in last_slot:
                merged_slot['seat_info'] = last_slot.get('seat_info') # Use .get for safety
            
            merged_slots.append(merged_slot)
        else:
            # Single slot, just add it
            merged_slots.append(group[0])
            
        # Move index to the next un-processed slot
        i = j

    return merged_slots


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

    def _schedule_large_groups_in_107(self, exam_groups_df):
        from create_db import ClassroomSlot, Session
        import json

        logging.info("Запуск приоритетного планирования для аудитории 107.")

        # --- Новое правило: Только для "письменных" экзаменов ---
        written_exam_mask = (
            (exam_groups_df['has_exam'] == True) &
            (exam_groups_df['proctor_needed'] == True) &
            (exam_groups_df['two_rooms_needed'] == True)
        )
        
        # Разделяем на письменные и все остальные
        written_exams_to_process = exam_groups_df[written_exam_mask].copy()
        other_exams = exam_groups_df[~written_exam_mask]

        if written_exams_to_process.empty:
            logging.info("Не найдено 'письменных' экзаменов, подходящих для приоритетного планирования в ауд. 107.")
            return exam_groups_df, 0  # Возвращаем исходный DF без изменений

        logging.info(f"Найдено {len(written_exams_to_process)} 'письменных' секций для рассмотрения в ауд. 107.")
        
        session = Session()
        try:
            # 1. Найти подходящие группы для объединения (работаем только с письменными)
            written_exams_to_process['student_count'] = written_exams_to_process['Section'].map(
                lambda x: len(self.exams_df[self.exams_df['Section'] == x])
            )
            
            subject_groups = written_exams_to_process.groupby('Subject').agg(
                total_students=('student_count', 'sum'),
                section_count=('Section', 'count'),
                sections=('Section', lambda x: list(x))
            ).reset_index()

            # Фильтр для кандидатов в 107 аудиторию (от 2 до 4 секций, от 80 до 200 студентов)
            candidates = subject_groups[
                (subject_groups['section_count'].between(2, 4)) &
                (subject_groups['total_students'].between(80, 200))
            ].sort_values('total_students', ascending=False)

            if candidates.empty:
                logging.info("Не найдено подходящих групп 'письменных' экзаменов для приоритетного планирования в ауд. 107.")
                return exam_groups_df, 0

            # 2. Получить свободные слоты из БД
            free_slots = session.query(ClassroomSlot).filter_by(
                classroom_number='107', is_booked=False
            ).order_by(ClassroomSlot.start_time).all()

            if not free_slots:
                logging.info("Нет свободных слотов в ауд. 107.")
                return exam_groups_df, 0

            scheduled_sections = set()
            scheduled_count = 0

            # 3. Итерация по кандидатам и слотам
            for _, candidate_row in candidates.iterrows():
                sections_to_schedule = candidate_row['sections']
                
                if any(s in scheduled_sections for s in sections_to_schedule):
                    continue

                all_students_in_group = set()
                for section_id in sections_to_schedule:
                    section_students = set(self.exams_df[self.exams_df['Section'] == section_id]['fake_id'])
                    all_students_in_group.update(section_students)

                for slot in free_slots:
                    if slot.is_booked:
                        continue

                    has_conflict = False
                    slot_date_str = slot.start_time.strftime('%Y-%m-%d')
                    for student in all_students_in_group:
                        if student in self.student_exams:
                            for exam in self.student_exams[student]:
                                if exam['Date'] == slot_date_str:
                                    has_conflict = True
                                    break
                        if has_conflict:
                            break
                    
                    if has_conflict:
                        continue

                    slot.is_booked = True
                    slot.booked_groups_info = json.dumps({
                        'subject': candidate_row['Subject'],
                        'sections': sections_to_schedule
                    }, ensure_ascii=False)
                    
                    base_time_slot = f"{slot.start_time.strftime('%H:%M')}-{slot.end_time.strftime('%H:%M')}"

                    for section_id in sections_to_schedule:
                        group_info = written_exams_to_process[written_exams_to_process['Section'] == section_id].iloc[0]
                        num_students = group_info['student_count']
                        
                        exam_record = {
                            'Date': slot_date_str,
                            'Subject': group_info['Subject'],
                            'Instructor': group_info['Instructor'],
                            'EduProgram': group_info['EduProgram'],
                            'Section': section_id,
                            'Students_Count': int(num_students),
                            'Room': '107',
                            'Time_Slot': base_time_slot,
                            'Base_Time_Slot': base_time_slot,
                            'Duration': int(group_info.get('Duration', 180)),
                            'Student_Conflicts': 0,
                            'proctor_needed': group_info.get('proctor_needed', False)
                        }
                        self.schedule.append(exam_record)

                        section_students_list = self.exams_df[self.exams_df['Section'] == section_id]['fake_id'].tolist()
                        for student in section_students_list:
                            if student not in self.student_exams:
                                self.student_exams[student] = []
                            self.student_exams[student].append(exam_record)

                        scheduled_sections.add(section_id)
                        scheduled_count += 1

                    logging.info(f"Аудитория 107 забронирована для предмета '{candidate_row['Subject']}' ({len(sections_to_schedule)} секции) на {slot_date_str} {base_time_slot}")
                    
                    free_slots.remove(slot)
                    break

            session.commit()
            
            # 4. Возвращаем DataFrame, объединив нераспределенные письменные и все остальные экзамены
            remaining_written_exams = written_exams_to_process[~written_exams_to_process['Section'].isin(scheduled_sections)]
            final_remaining_df = pd.concat([remaining_written_exams, other_exams], ignore_index=True)
            
            # Удаляем временную колонку, если она есть
            if 'student_count' in final_remaining_df.columns:
                final_remaining_df = final_remaining_df.drop(columns=['student_count'])
            
            logging.info(f"Завершено приоритетное планирование. Запланировано секций в ауд. 107: {scheduled_count}")
            return final_remaining_df, scheduled_count

        except Exception as e:
            logging.error(f"Ошибка в приоритетном планировании для ауд. 107: {traceback.format_exc()}")
            session.rollback()
            return exam_groups_df, 0
        finally:
            session.close()

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
        logging.info("Назначение прокторов.")
        assigned_proctors = []
        section_proctors = {}
        non_proctors = []

        if proctors_path:
            non_proctors_df = pd.read_excel(proctors_path, header=3)
            non_proctors = non_proctors_df['ФИО'].dropna().tolist()

        all_proctors = [p for proctors in self.faculty_proctors.values() for p in proctors]

        def is_excluded(name, exclusions):
            return any(excl in name for excl in exclusions)

        available_proctors = [p for p in set(all_proctors) if not is_excluded(p, non_proctors)]

        if not available_proctors:
            raise ValueError("Нет доступных прокторов для назначения.")

        sct_proctors = self.faculty_proctors.get('Школа цифровых технологий', [])
        sct_proctors = [p for p in sct_proctors if p in available_proctors]
        if not sct_proctors:
            logging.warning("Нет доступных прокторов из ШЦТ, хотя они могут быть нужны.")

        non_sct_proctors = [p for p in available_proctors if p not in sct_proctors]
        if not non_sct_proctors:
            logging.warning("Нет доступных прокторов вне ШЦТ, хотя они могут быть нужны.")

        faculty_exams = {faculty: 0 for faculty in self.faculty_proctors.keys()}
        for _, row in self.schedule_df.iterrows():
            subject = row['Subject']
            proctor_needed = row.get('proctor_needed', True)
            if not proctor_needed:
                continue
            faculties = self.subject_faculty_map.get(subject, set())
            for faculty in faculties:
                num_proctors = 4 if '107' in str(row['Room']) else (2 if row.get('two_rooms_needed', False) else 1)
                faculty_exams[faculty] += num_proctors

        logging.info("Информация о прокторах и экзаменах по школам:")
        for faculty, proctors in self.faculty_proctors.items():
            valid_proctors = [p for p in proctors if p in available_proctors]
            logging.info(f"{faculty}: {len(valid_proctors)} прокторов, {faculty_exams.get(faculty, 0)} мест для прокторов")

        sct_proctor_load = {proctor: 0 for proctor in sct_proctors}
        non_sct_proctor_load = {proctor: 0 for proctor in non_sct_proctors}
        proctor_schedule = {}
        room_proctor_assignment = {}  # Для отслеживания прокторов по аудиториям и слотам

        MAX_PROCTOR_LOAD = 100

        for _, row in self.schedule_df.iterrows():
            section_id = row['Section']
            subject = row['Subject']
            exam_date = row['Date']
            time_slot = row['Time_Slot'].strip()
            room = str(row['Room'])
            proctor_needed = row.get('proctor_needed', True)
            two_rooms_needed = row.get('two_rooms_needed', False)

            if not proctor_needed:
                section_proctors[section_id] = {'proctor': [], 'subject': subject, 'exam_name': subject, 'date': exam_date}
                assigned_proctors.append('')
                continue

            assignment_key = (exam_date, time_slot, room)
            if assignment_key in room_proctor_assignment:
                assigned = room_proctor_assignment[assignment_key]
                logging.info(f"Для секции {section_id} в слоте ({room} {time_slot}) используются уже назначенные прокторы.")
            else:
                if '107' in room:
                    num_proctors = 4
                elif two_rooms_needed:
                    num_proctors = 2
                else:
                    num_proctors = 1

                is_sct_subject = 'Школа цифровых технологий' in self.subject_faculty_map.get(subject, set())
                if is_sct_subject:
                    proctor_pool = sct_proctors
                    proctor_load = sct_proctor_load
                else:
                    excluded_faculties = self.subject_faculty_map.get(subject, set()) | {'Школа цифровых технологий'}
                    proctor_pool = [p for p in non_sct_proctors if not any(p in self.faculty_proctors.get(faculty, []) for faculty in excluded_faculties)]
                    proctor_load = non_sct_proctor_load

                if not proctor_pool:
                    logging.error(f"Нет доступных прокторов для {section_id}")
                    raise ValueError(f"Нет доступных прокторов для {section_id}")

                slot_key = (exam_date, time_slot)
                busy_proctors = proctor_schedule.get(slot_key, [])
                available = [p for p in proctor_pool if proctor_load.get(p, 0) < MAX_PROCTOR_LOAD and p not in busy_proctors]

                if len(available) < num_proctors:
                    logging.error(f"Недостаточно свободных прокторов для {section_id}: требуется {num_proctors}, доступно {len(available)}")
                    raise ValueError(f"Недостаточно свободных прокторов для {section_id}")

                assigned = []
                for _ in range(num_proctors):
                    min_load_proctor = min(available, key=lambda p: proctor_load.get(p, 0))
                    assigned.append(min_load_proctor)
                    available.remove(min_load_proctor)

                for proctor in assigned:
                    proctor_load[proctor] = proctor_load.get(proctor, 0) + 1
                    if proctor_load[proctor] >= MAX_PROCTOR_LOAD:
                        logging.warning(f"Проктор {proctor} достиг максимальной нагрузки.")
                    proctor_schedule.setdefault(slot_key, []).append(proctor)
                
                room_proctor_assignment[assignment_key] = assigned

            proctor_str = ', '.join(assigned) if assigned else ''
            assigned_proctors.append(proctor_str)
            section_proctors[section_id] = {'proctor': assigned, 'subject': subject, 'exam_name': subject, 'date': exam_date}

        sct_loads = [v for v in sct_proctor_load.values() if v > 0]
        non_sct_loads = [v for v in non_sct_proctor_load.values() if v > 0]
        if sct_loads: logging.info(f"Средняя нагрузка на проктора ШЦТ: {statistics.mean(sct_loads):.2f}")
        if non_sct_loads: logging.info(f"Средняя нагрузка на проктора вне ШЦТ: {statistics.mean(non_sct_loads):.2f}")

        self.schedule_df['Proctor'] = assigned_proctors
        self.section_proctors = section_proctors
        logging.info("Прокторы успешно назначены.")

    def get_all_proctors(self):

        logging.info("Получение полного списка прокторов.")

        all_proctors = []
        for faculty, proctors in self.faculty_proctors.items():
            all_proctors.extend(proctors)

        filtered_proctors = [p for p in all_proctors if isinstance(p, str)]

        unique_proctors = sorted(set(filtered_proctors))

        logging.info(f"Найдено {len(unique_proctors)} уникальных прокторов.")
        return unique_proctors

    def get_all_section_names(self):
        """
        Возвращает простой список всех известных имен секций для диагностики.
        """
        if not hasattr(self, 'exam_groups') or self.exam_groups.empty:
            return []
        
        return self.exam_groups['Section'].unique().tolist()

    def _is_instructor_available(self, instructor, day, time_slot):
        if time_slot is None:
            raise ValueError("time_slot не должен быть None при проверке доступности преподавателя")

        start_time, end_time = time_slot.split('-')
        start_dt = datetime.strptime(start_time, '%H:%M')
        end_dt = datetime.strptime(end_time, '%H:%M')

        for exam in self.schedule:
            if (
                    exam['Instructor'] == instructor and
                    exam['Date'] == day.strftime('%Y-%m-%d')
            ):
                exam_start, exam_end = exam['Time_Slot'].split('-')
                exam_start_dt = datetime.strptime(exam_start, '%H:%M')
                exam_end_dt = datetime.strptime(exam_end, '%H:%M')

                if start_dt < exam_end_dt and exam_start_dt < end_dt:
                    logging.debug(
                        f"Преподаватель {instructor} занят в {day} {time_slot} из-за экзамена {exam['Section']} в {exam['Time_Slot']}")
                    return False

        return True

    def _find_adjacent_rooms(self, day, time_slot, required_capacity):
        """
        Ищет соседние аудитории, которые вместе могут вместить всех студентов.
        Возвращает список подходящих аудиторий или None, если не хватает мест.
        """
        available_rooms = [
            room for room in self.rooms
            if room not in self.room_availability[day][time_slot]
        ]

        if not available_rooms:
            return None

        # Сортируем аудитории по номеру (предполагаем, что 101, 102, 103 идут подряд)
        available_rooms_sorted = sorted(available_rooms, key=lambda x: int(''.join(filter(str.isdigit, x))))

        selected_rooms = []
        remaining_capacity = required_capacity

        for room in available_rooms_sorted:
            room_capacity = self.room_capacities[room]
            if remaining_capacity <= 0:
                break

            selected_rooms.append(room)
            remaining_capacity -= room_capacity

        if remaining_capacity > 0:
            return None  # Не хватило аудиторий

        return selected_rooms

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

    def _load_manual_bookings(self):
        from create_db import ClassroomSlot, Session
        import json

        logging.info("Загрузка вручную забронированных слотов.")
        session = Session()
        try:
            booked_slots = session.query(ClassroomSlot).filter_by(is_booked=True).all()
            manual_exams = []
            scheduled_sections = set()

            for slot in booked_slots:
                if not slot.booked_groups_info:
                    continue

                booking_info = json.loads(slot.booked_groups_info)
                subject = booking_info.get('subject')
                sections = booking_info.get('sections')

                if not subject or not sections:
                    continue

                slot_date_str = slot.start_time.strftime('%Y-%m-%d')
                base_time_slot = f"{slot.start_time.strftime('%H:%M')}-{slot.end_time.strftime('%H:%M')}"

                for section_id in sections:
                    if section_id in self.exam_groups['Section'].values:
                        group_info = self.exam_groups[self.exam_groups['Section'] == section_id].iloc[0]
                        num_students = len(self.exams_df[self.exams_df['Section'] == section_id])

                        exam_record = {
                            'Date': slot_date_str,
                            'Subject': subject,
                            'Instructor': group_info['Instructor'],
                            'EduProgram': group_info['EduProgram'],
                            'Section': section_id,
                            'Students_Count': int(num_students),
                            'Room': slot.classroom_number,
                            'Time_Slot': base_time_slot,
                            'Base_Time_Slot': base_time_slot,
                            'Duration': int(group_info.get('Duration', 180)),
                            'Student_Conflicts': 0,
                            'proctor_needed': group_info.get('proctor_needed', False),
                            'pinned': True  # Add a flag to indicate that this is a manually booked slot
                        }
                        manual_exams.append(exam_record)
                        scheduled_sections.add(section_id)

            logging.info(f"Загружено {len(scheduled_sections)} уникальных вручную забронированных экзаменов.")
            return manual_exams, scheduled_sections
        except Exception as e:
            logging.error(f"Ошибка при загрузке вручную забронированных слотов: {traceback.format_exc()}")
            return [], set()
        finally:
            session.close()

    def _find_suitable_rooms(self, available_rooms, num_students, group_info, classroom_type='regular'):
        """
        Находит подходящие аудитории для экзаменационной группы, применяя специальные ограничения.
        """
        # Извлекаем флаги из group_info
        two_rooms_needed = group_info.get('two_rooms_needed', False)
        has_exam = group_info.get('has_exam', True)
        proctor_needed = group_info.get('proctor_needed', False)

        logging.info(f"Поиск аудитории для секции {group_info.get('Section', '')}. Требования: two_rooms={two_rooms_needed}, proctor={proctor_needed}, has_exam={has_exam}, тип='{classroom_type}', вместимость={num_students}.")

        # --- Ограничение на аудитории для экзаменов без проктора ---
        forbidden_rooms = {'319', '333', '419', '433', '436', '526', '536', '338/1', '334/1'}
        if (two_rooms_needed is False and has_exam is True and proctor_needed is False):
            original_room_count = len(available_rooms)
            available_rooms = [r for r in available_rooms if str(r) not in forbidden_rooms]
            if len(available_rooms) < original_room_count:
                logging.info(f"Применены ограничения на аудитории (экзамен без проктора). Исключены: {forbidden_rooms}")

        # --- Ограничение для аудитории 107 (только для "письменных" экзаменов) ---
        is_written_exam = (two_rooms_needed is True and proctor_needed is True and has_exam is True)
        if not is_written_exam and '107' in available_rooms:
            logging.info("Аудитория 107 доступна только для 'письменных' экзаменов. Исключаем ее для данной секции.")
            available_rooms = [r for r in available_rooms if str(r) != '107']

        # Фильтруем аудитории по требуемому типу
        typed_available_rooms = [
            r for r in available_rooms
            if self.room_types.get(r, 'regular') == classroom_type
        ]

        # Резервный вариант: если нужны 'regular', но их нет, пробуем 'it_lab'
        if not typed_available_rooms and classroom_type == 'regular':
            logging.info("Не найдено свободных аудиторий типа 'regular', пробую найти 'it_lab'.")
            typed_available_rooms = [
                r for r in available_rooms
                if self.room_types.get(r, 'regular') == 'it_lab'
            ]

        if not typed_available_rooms:
            logging.warning(f"Не найдено свободных аудиторий типа '{classroom_type}' для экзамена.")
            return None, []

        required_capacity = num_students

        if not two_rooms_needed:
            # Стандартная логика для одной аудитории
            suitable_rooms = [r for r in typed_available_rooms if self.room_capacities.get(r, 0) >= required_capacity]
            if not suitable_rooms:
                return None, []
            
            best_room = min(suitable_rooms, key=lambda r: self.room_capacities.get(r, 0))
            return str(best_room), [str(best_room)]
        else:
            # --- Логика для two_rooms_needed: ПРИНУДИТЕЛЬНЫЙ ПОИСК ДВУХ АУДИТОРИЙ ---
            logging.info(f"Принудительный поиск пары аудиторий для {required_capacity} мест (two_rooms_needed=True).")

            def get_room_num(room_str):
                try:
                    return int(''.join(filter(str.isdigit, str(room_str))))
                except (ValueError, TypeError):
                    return 0

            room_pairs = list(combinations(typed_available_rooms, 2))
            suitable_pairs = [
                p for p in room_pairs
                if self.room_capacities.get(str(p[0]), 0) + self.room_capacities.get(str(p[1]), 0) >= required_capacity
            ]

            if not suitable_pairs:
                logging.warning(f"Не найдено подходящей пары аудиторий для вместимости {required_capacity}.")
                return None, []

            # Оцениваем и сортируем пары по близости
            scored_pairs = []
            for pair in suitable_pairs:
                room1_str, room2_str = str(pair[0]), str(pair[1])
                num1, num2 = get_room_num(room1_str), get_room_num(room2_str)
                floor1 = int(str(num1)[0]) if num1 >= 100 else -1
                floor2 = int(str(num2)[0]) if num2 >= 100 else -2
                same_floor_score = 0 if floor1 == floor2 else 1
                room_diff = abs(num1 - num2)
                total_capacity = self.room_capacities.get(room1_str, 0) + self.room_capacities.get(room2_str, 0)
                scored_pairs.append(((room1_str, room2_str), same_floor_score, room_diff, total_capacity))

            scored_pairs.sort(key=lambda x: (x[1], x[2], x[3]))
            best_pair_tuple = scored_pairs[0][0]
            logging.info(f"Найдена лучшая пара аудиторий: {best_pair_tuple} (Этаж-скор: {scored_pairs[0][1]}, Разница: {scored_pairs[0][2]})")
            
            final_room_str = f"{best_pair_tuple[0]},{best_pair_tuple[1]}"
            rooms_to_book = list(best_pair_tuple)
            
            return final_room_str, rooms_to_book

    def _book_slot(self, day_str, start_block, total_blocks_needed, rooms_to_book, num_blocks_in_day):
        """Marks the given rooms as booked in the availability grid."""
        for room in rooms_to_book:
            for i in range(start_block, start_block + total_blocks_needed):
                if i < num_blocks_in_day:
                    self.room_availability_grid[day_str][str(room)][i] = True

    def _create_exam_record(self, group, day_str, time_slot_str, room_str, num_students):
        """Creates a dictionary representing a single scheduled exam."""
        return {
            'Date': day_str,
            'Subject': group['Subject'],
            'Instructor': group['Instructor'],
            'EduProgram': group['EduProgram'],
            'Section': group['Section'],
            'Students_Count': num_students,
            'Room': room_str,
            'Time_Slot': time_slot_str,
            'Duration': int(group.get('Duration', 180)),
            'proctor_needed': group.get('proctor_needed', False),
            'two_rooms_needed': group.get('two_rooms_needed', False),
            'pinned': False
        }

    def _is_student_available_for_exam(self, student_id, day_str, new_exam_group):
        """
        Проверяет, доступен ли студент для сдачи нового экзамена в указанный день.
        СТРОГОЕ ПРАВИЛО: Не больше одного экзамена в день.
        """
        student_id = str(student_id)
        exams_on_day = [
            exam for exam in self.student_exams.get(student_id, [])
            if exam['Date'] == day_str
        ]
        return len(exams_on_day) == 0

    def create_schedule(self):
        logging.info("Начало создания расписания (3-этапный гибридный подход).")
        self.schedule = []
        self.student_exams = defaultdict(list)
        self.failed_sections = []

        # Загрузка исключений из БД
        try:
            from create_db import RoomExclusion, engine
            from sqlalchemy.orm import sessionmaker
            Session = sessionmaker(bind=engine)
            db_session = Session()
            all_exclusions = db_session.query(RoomExclusion).all()
            db_session.close()

            self.exclusions_by_date = defaultdict(list)
            for exc in all_exclusions:
                self.exclusions_by_date[exc.exclusion_date].append(exc)
            logging.info(f"Загружено {len(all_exclusions)} правил исключения аудиторий.")
        except Exception as e:
            logging.error(f"Не удалось загрузить исключения аудиторий из БД: {e}")
            self.exclusions_by_date = defaultdict(list)

        try:
            # 1. Инициализация
            time_step_minutes = self.time_step
            buffer_minutes = self.buffer_time
            work_day_start_dt = self.work_day_start
            total_duration_minutes = (self.work_day_end - self.work_day_start).total_seconds() / 60
            num_blocks_in_day = int(total_duration_minutes / time_step_minutes)
            self.room_availability_grid = {
                day.strftime('%Y-%m-%d'): {str(room): [False] * num_blocks_in_day for room in self.rooms}
                for day in self.custom_dates
            }
            manual_exams, scheduled_sections = self._load_manual_bookings()
            self.schedule.extend(manual_exams)
            for exam in manual_exams:
                day_str, room, time_slot = exam['Date'], str(exam['Room']), exam['Time_Slot']
                if day_str not in self.room_availability_grid or room not in self.room_availability_grid.get(day_str, {}): continue
                start_h, start_m = map(int, time_slot.split('-')[0].split(':'))
                end_h, end_m = map(int, time_slot.split('-')[1].split(':'))
                start_dt = self.work_day_start.replace(hour=start_h, minute=start_m)
                end_dt = self.work_day_start.replace(hour=end_h, minute=end_m)
                start_block = int((start_dt - self.work_day_start).total_seconds() / 60 / time_step_minutes)
                end_block = int((end_dt - self.work_day_start).total_seconds() / 60 / time_step_minutes)
                self._book_slot(day_str, start_block, end_block - start_block, [room], num_blocks_in_day)
                students = self.exams_df[self.exams_df['Section'] == exam['Section']]['fake_id'].tolist()
                for student_id in students: self.student_exams[student_id].append(exam)

            # 2. Подготовка групп
            groups_to_schedule = self.exam_groups[(self.exam_groups['has_exam'] == True) & (~self.exam_groups['Section'].isin(scheduled_sections))].copy()
            no_exam_groups = self.exam_groups[self.exam_groups['has_exam'] == False].copy()

            if not groups_to_schedule.empty:
                # Расчет коэффициента сложности
                student_section_counts = self.exams_df['fake_id'].value_counts().to_dict()
                def get_student_busyness(section_id):
                    student_ids = self.exams_df[self.exams_df['Section'] == section_id]['fake_id']
                    if student_ids.empty: return 0
                    total_sections_for_students = sum(student_section_counts.get(sid, 0) for sid in student_ids)
                    return total_sections_for_students / len(student_ids) if student_ids.size > 0 else 0
                groups_to_schedule['student_count'] = groups_to_schedule['Section'].map(lambda x: len(self.exams_df[self.exams_df['Section'] == x]))
                groups_to_schedule['student_busyness'] = groups_to_schedule['Section'].apply(get_student_busyness)
                w_students = 1.5
                w_busyness = 2.0
                w_two_rooms = 250
                groups_to_schedule['constraint_score'] = (w_students * groups_to_schedule['student_count'] + w_busyness * groups_to_schedule['student_busyness'] + w_two_rooms * groups_to_schedule['two_rooms_needed'].astype(int))
                groups_to_schedule = groups_to_schedule.sort_values(by=['constraint_score', 'Section'], ascending=[False, True])
                
                exams_per_day_count = defaultdict(int)
                for exam in self.schedule: exams_per_day_count[exam['Date']] += 1
                
                # --- Этап 1: Строгое планирование ---
                logging.info(f"--- Этап 1: Строгое планирование для {len(groups_to_schedule)} секций ---")
                hard_to_schedule_groups = []
                for _, group in groups_to_schedule.iterrows():
                    logging.info(f"Обработка секции: {group['Section']}, has_exam={group.get('has_exam')}, proctor_needed={group.get('proctor_needed')}, two_rooms_needed={group.get('two_rooms_needed')}")
                    students = self.exams_df[self.exams_df['Section'] == group['Section']]['fake_id'].tolist()
                    num_students = len(students)
                    duration_minutes, instructor, two_rooms_needed = int(group.get('Duration', 180)), group['Instructor'], group.get('two_rooms_needed', False)
                    if two_rooms_needed:
                        num_students *= 2
                    exam_blocks = math.ceil(duration_minutes / time_step_minutes)
                    buffer_blocks = math.ceil(buffer_minutes / time_step_minutes)
                    total_blocks_needed = exam_blocks + buffer_blocks
                    
                    possible_slots = []
                    for day in self.custom_dates:
                        day_str = day.strftime('%Y-%m-%d')
                        if not all(self._is_student_available_for_exam(s_id, day_str, group.to_dict()) for s_id in students):
                            continue
                        for start_block in range(num_blocks_in_day - total_blocks_needed + 1):
                            day_start_dt = datetime.combine(day.date(), work_day_start_dt.time())
                            exam_start_dt = day_start_dt + timedelta(minutes=start_block * time_step_minutes)
                            exam_end_dt = exam_start_dt + timedelta(minutes=exam_blocks * time_step_minutes)
                            exam_time_slot_str = f"{exam_start_dt.strftime('%H:%M')}-{exam_end_dt.strftime('%H:%M')}"
                            if not self._is_instructor_available(instructor, day, exam_time_slot_str): continue
                            
                            grid_available_rooms = [r for r in self.rooms if r in self.room_availability_grid[day_str] and not any(self.room_availability_grid[day_str][r][i] for i in range(start_block, start_block + total_blocks_needed))]
                            
                            available_rooms = [
                                r for r in grid_available_rooms
                                if not self._is_room_excluded(r, day, exam_start_dt, exam_end_dt)
                            ]
                            
                            classroom_type = group.get('classroom_type', 'regular')
                            final_room_str, rooms_to_book = self._find_suitable_rooms(available_rooms, num_students, group.to_dict(), classroom_type)
                            if final_room_str:
                                possible_slots.append({'day_str': day_str, 'start_block': start_block, 'time_slot_str': exam_time_slot_str, 'room_str': final_room_str, 'rooms_to_book': rooms_to_book})
                    
                    if not possible_slots:
                        hard_to_schedule_groups.append(group)
                    else:
                        best_slot = min(possible_slots, key=lambda s: (exams_per_day_count[s['day_str']], s['start_block']))
                        day_str, start_block, time_slot_str, room_str, rooms_to_book = best_slot['day_str'], best_slot['start_block'], best_slot['time_slot_str'], best_slot['room_str'], best_slot['rooms_to_book']
                        self._book_slot(day_str, start_block, total_blocks_needed, rooms_to_book, num_blocks_in_day)
                        exam_record = self._create_exam_record(group, day_str, time_slot_str, room_str, num_students)
                        self.schedule.append(exam_record)
                        for student_id in students: self.student_exams[student_id].append(exam_record)
                        exams_per_day_count[day_str] += 1
                
                # --- Этап 2: Гибкое планирование для "сложных" секций ---
                if hard_to_schedule_groups:
                    logging.info(f"--- Этап 2: Гибкое планирование для {len(hard_to_schedule_groups)} сложных секций ---")
                    for group in hard_to_schedule_groups:
                        logging.info(f"Обработка секции: {group['Section']}, has_exam={group.get('has_exam')}, proctor_needed={group.get('proctor_needed')}, two_rooms_needed={group.get('two_rooms_needed')}")
                        students = self.exams_df[self.exams_df['Section'] == group['Section']]['fake_id'].tolist()
                        num_students = len(students)
                        duration_minutes, instructor, two_rooms_needed = int(group.get('Duration', 180)), group['Instructor'], group.get('two_rooms_needed', False)
                        if two_rooms_needed:
                            num_students *= 2
                        exam_blocks = math.ceil(duration_minutes / time_step_minutes)
                        buffer_blocks = math.ceil(buffer_minutes / time_step_minutes)
                        total_blocks_needed = exam_blocks + buffer_blocks

                        possible_slots = []
                        for day in self.custom_dates:
                            day_str = day.strftime('%Y-%m-%d')
                            conflicts = sum(1 for s_id in students if not self._is_student_available_for_exam(s_id, day_str, group.to_dict()))
                            for start_block in range(num_blocks_in_day - total_blocks_needed + 1):
                                day_start_dt = datetime.combine(day.date(), work_day_start_dt.time())
                                exam_start_dt = day_start_dt + timedelta(minutes=start_block * time_step_minutes)
                                exam_end_dt = exam_start_dt + timedelta(minutes=exam_blocks * time_step_minutes)
                                exam_time_slot_str = f"{exam_start_dt.strftime('%H:%M')}-{exam_end_dt.strftime('%H:%M')}"
                                if not self._is_instructor_available(instructor, day, exam_time_slot_str): continue
                                
                                grid_available_rooms = [r for r in self.rooms if r in self.room_availability_grid[day_str] and not any(self.room_availability_grid[day_str][r][i] for i in range(start_block, start_block + total_blocks_needed))]
                                
                                available_rooms = [
                                    r for r in grid_available_rooms
                                    if not self._is_room_excluded(r, day, exam_start_dt, exam_end_dt)
                                ]

                                classroom_type = group.get('classroom_type', 'regular')
                                final_room_str, rooms_to_book = self._find_suitable_rooms(available_rooms, num_students, group.to_dict(), classroom_type)
                                if final_room_str:
                                    possible_slots.append({'day_str': day_str, 'start_block': start_block, 'time_slot_str': exam_time_slot_str, 'room_str': final_room_str, 'rooms_to_book': rooms_to_book, 'conflicts': conflicts})

                        if not possible_slots:
                            self.failed_sections.append(group.to_dict())
                        else:
                            best_slot = min(possible_slots, key=lambda s: (s['conflicts'], exams_per_day_count[s['day_str']], s['start_block']))
                            day_str, start_block, time_slot_str, room_str, rooms_to_book = best_slot['day_str'], best_slot['start_block'], best_slot['time_slot_str'], best_slot['room_str'], best_slot['rooms_to_book']
                            self._book_slot(day_str, start_block, total_blocks_needed, rooms_to_book, num_blocks_in_day)
                            exam_record = self._create_exam_record(group, day_str, time_slot_str, room_str, num_students)
                            self.schedule.append(exam_record)
                            for student_id in students: self.student_exams[student_id].append(exam_record)
                            exams_per_day_count[day_str] += 1

                # --- Этап 3: Супер-оптимизация ---
                logging.info("--- Этап 3: Оптимизация расписания и разрешение конфликтов ---")
                self.student_exams, final_conflicts = self.optimize_schedule(self.student_exams)
                
            # Финализация
            if not no_exam_groups.empty and self.custom_dates:
                for _, group in no_exam_groups.iterrows():
                    self.schedule.append({'Date': random.choice(self.custom_dates).strftime('%Y-%m-%d'), 'Subject': group['Subject'], 'Instructor': group['Instructor'], 'EduProgram': group['EduProgram'], 'Section': group['Section'], 'Students_Count': len(self.exams_df[self.exams_df['Section'] == group['Section']]), 'Room': 'N/A', 'Time_Slot': 'N/A', 'Duration': 0, 'proctor_needed': False, 'two_rooms_needed': False, 'pinned': True})
            
            self.schedule_df = pd.DataFrame(self.schedule) if self.schedule else pd.DataFrame()
            if not self.schedule_df.empty:
                self.assign_seats()

            total_groups_to_schedule = len(self.exam_groups[self.exam_groups['has_exam'] == True])
            logging.info(f"Успешно запланировано: {total_groups_to_schedule - len(self.failed_sections)} из {total_groups_to_schedule} групп")
            if self.failed_sections:
                logging.warning(f"Не удалось запланировать {len(self.failed_sections)} секций. Детали:")
                for f in self.failed_sections: 
                    student_count = f.get('student_count', 'N/A')
                    logging.warning(f"  - Секция: {f.get('Section')}, Студентов: {student_count}")
            return self.schedule_df

        except Exception as e:
            logging.error(f"Ошибка при создании расписания: {str(e)}")
            logging.error(traceback.format_exc())
            self.schedule_df = pd.DataFrame()
            return self.schedule_df

    def _is_slot_available(self, day, room, slot):
        if not hasattr(self, 'room_availability'):
            self.room_availability = {
                day: {slot: set() for slot in self.time_slots}
                for day in self.custom_dates
            }
        return room not in self.room_availability[day][slot]

    def _mark_slot_busy(self, day, room, slot):
        if not hasattr(self, 'room_availability'):
            self._is_slot_available(day, room, slot)
        self.room_availability[day][slot].add(room)

    def _calculate_move_delta_cost(self, exam_to_move, new_day_str, student_exams):
        """Вычисляет изменение 'стоимости' (количества конфликтов) при переносе экзамена."""
        students = self.exams_df[self.exams_df['Section'] == exam_to_move['Section']]['fake_id'].tolist()
        old_day_str = exam_to_move['Date']
        
        delta_cost = 0
        
        for sid in students:
            # Стоимость в старом дне
            exams_in_old_day = [e for e in student_exams[sid] if e['Date'] == old_day_str]
            cost_before = max(0, len(exams_in_old_day) - 1)
            cost_after = max(0, len(exams_in_old_day) - 2)
            delta_cost += (cost_after - cost_before)

            # Стоимость в новом дне
            exams_in_new_day = [e for e in student_exams[sid] if e['Date'] == new_day_str]
            cost_before = max(0, len(exams_in_new_day) - 1)
            cost_after = max(0, len(exams_in_new_day))
            delta_cost += (cost_after - cost_before)
            
        return delta_cost

    def optimize_schedule(self, student_exams):
        logging.info("Запуск оптимизации расписания (Simulated Annealing)...")

        def get_conflicting_exam_indices(schedule, student_exams_dict):
            conflicting_indices = set()
            for sid, exams in student_exams_dict.items():
                exams_on_days = defaultdict(list)
                for exam in exams:
                    if exam.get('Date') and exam.get('Date') != 'N/A':
                        exams_on_days[exam['Date']].append(exam)
                for day, daily_exams in exams_on_days.items():
                    if len(daily_exams) > 1:
                        for exam in daily_exams:
                            for idx, s_exam in enumerate(schedule):
                                if s_exam['Section'] == exam['Section']:
                                    conflicting_indices.add(idx)
                                    break
            return list(conflicting_indices)

        max_iterations = 100000
        no_improvement_streak = 0
        max_no_improvement = 10000
        
        initial_temperature = 1.0
        cooling_rate = 0.99995
        temperature = initial_temperature

        current_cost = self.get_total_conflicts(student_exams)
        logging.info(f"[Оптимизация, старт] Начальная стоимость (конфликты): {current_cost}")

        if current_cost == 0:
            logging.info("Конфликтов не найдено, оптимизация не требуется.")
            return student_exams, []

        conflicting_indices = get_conflicting_exam_indices(self.schedule, student_exams)

        for i in range(max_iterations):
            if not conflicting_indices:
                logging.info(f"Все конфликты разрешены на итерации {i}.")
                current_cost = 0
                break

            schedule_idx = random.choice(conflicting_indices)
            exam_to_move = self.schedule[schedule_idx].copy()

            if exam_to_move.get('pinned'):
                continue

            new_day = random.choice(self.custom_dates)
            new_slot_info = self._find_free_slot_for_exam(new_day.strftime('%Y-%m-%d'), exam_to_move)

            if new_slot_info and new_slot_info['Date'] != exam_to_move['Date']:
                delta_cost = self._calculate_move_delta_cost(exam_to_move, new_slot_info['Date'], student_exams)

                if delta_cost < 0 or (temperature > 0 and random.random() < math.exp(-delta_cost / temperature)):
                    num_blocks_in_day = int(((self.work_day_end - self.work_day_start).total_seconds() / 60) / self.time_step)
                    self._release_slot(schedule_idx, num_blocks_in_day)
                    self._move_exam(schedule_idx, new_slot_info, student_exams)
                    
                    current_cost += delta_cost
                    no_improvement_streak = 0
                    logging.info(f"[Итерация {i}] Найдено улучшение! Новая стоимость: {current_cost}.")
                    
                    if i % 100 == 0:
                         conflicting_indices = get_conflicting_exam_indices(self.schedule, student_exams)
                else:
                    no_improvement_streak += 1
            else:
                no_improvement_streak += 1

            temperature *= cooling_rate

            if no_improvement_streak > max_no_improvement:
                logging.warning(f"Оптимизация остановлена из-за отсутствия улучшений в течение {max_no_improvement} итераций.")
                break
        
        final_conflicting_students = {sid for sid, exams in student_exams.items() if self.get_total_conflicts({sid: exams}) > 0}
        logging.info(f"Оптимизация завершена. Финальная стоимость: {current_cost}. Студентов с конфликтами: {len(final_conflicting_students)}")
        
        self.schedule_df = pd.DataFrame(self.schedule)
        return student_exams, list(final_conflicting_students)

    def _get_slot_params(self, exam_rec):
        day_str = exam_rec['Date']
        rooms = exam_rec['Room'].split(',')
        duration = exam_rec['Duration']
        exam_blocks = math.ceil(duration / self.time_step)
        buffer_blocks = math.ceil(self.buffer_time / self.time_step)
        total_blocks_needed = exam_blocks + buffer_blocks
        
        start_h, start_m = map(int, exam_rec['Time_Slot'].split('-')[0].split(':'))
        start_dt = self.work_day_start.replace(hour=start_h, minute=start_m)
        start_block = int((start_dt - self.work_day_start).total_seconds() / 60 / self.time_step)
        
        return day_str, start_block, total_blocks_needed, rooms

    def _find_free_slot_for_exam(self, day_str, exam_rec):
        time_step_minutes = self.time_step
        work_day_start_dt = self.work_day_start
        num_blocks_in_day = int(((self.work_day_end - self.work_day_start).total_seconds() / 60) / time_step_minutes)

        duration_minutes = exam_rec['Duration']
        exam_blocks = math.ceil(duration_minutes / time_step_minutes)
        buffer_blocks = math.ceil(self.buffer_time / time_step_minutes)
        total_blocks_needed = exam_blocks + buffer_blocks
        
        students = self.exams_df[self.exams_df['Section'] == exam_rec['Section']]['fake_id'].tolist()
        num_students = len(students)
        instructor = exam_rec['Instructor']
        two_rooms_needed = exam_rec.get('two_rooms_needed', False)

        if two_rooms_needed:
            num_students *= 2
        
        possible_starts = list(range(num_blocks_in_day - total_blocks_needed + 1))
        random.shuffle(possible_starts)

        day_obj = datetime.strptime(day_str, '%Y-%m-%d')

        for start_block in possible_starts:
            end_block_with_buffer = start_block + total_blocks_needed
            
            day_start_dt = datetime.combine(day_obj.date(), work_day_start_dt.time())
            exam_start_dt = day_start_dt + timedelta(minutes=start_block * time_step_minutes)
            exam_end_dt = exam_start_dt + timedelta(minutes=duration_minutes)
            exam_time_slot_str = f"{exam_start_dt.strftime('%H:%M')}-{exam_end_dt.strftime('%H:%M')}"

            if not self._is_instructor_available(instructor, day_obj, exam_time_slot_str):
                continue

            grid_available_rooms = [r for r in self.rooms if r in self.room_availability_grid[day_str] and not any(self.room_availability_grid[day_str][r][i] for i in range(start_block, end_block_with_buffer))]
            
            available_rooms = [
                r for r in grid_available_rooms
                if not self._is_room_excluded(r, day_obj, exam_start_dt, exam_end_dt)
            ]

            classroom_type = exam_rec.get('classroom_type', 'regular')
            final_room_str, rooms_to_book = self._find_suitable_rooms(available_rooms, num_students, exam_rec, classroom_type)

            if final_room_str:
                return {'Date': day_str, 'Time_Slot': exam_time_slot_str, 'Room': final_room_str, 'start_block': start_block, 'rooms_to_book': rooms_to_book}
        return None

    def _move_exam(self, schedule_idx, new_slot_info, student_exams):
        time_step_minutes = self.time_step
        num_blocks_in_day = int(((self.work_day_end - self.work_day_start).total_seconds() / 60) / time_step_minutes)
        
        old_exam_rec = self.schedule[schedule_idx]
        old_day_str = old_exam_rec['Date']
        old_rooms = old_exam_rec['Room'].split(',')
        duration_minutes = old_exam_rec['Duration']
        exam_blocks = math.ceil(duration_minutes / time_step_minutes)
        buffer_blocks = math.ceil(self.buffer_time / time_step_minutes)
        total_blocks_needed = exam_blocks + buffer_blocks
        
        old_start_h, old_start_m = map(int, old_exam_rec['Time_Slot'].split('-')[0].split(':'))
        old_start_dt = self.work_day_start.replace(hour=old_start_h, minute=old_start_m)
        old_start_block = int((old_start_dt - self.work_day_start).total_seconds() / 60 / time_step_minutes)

        for room in old_rooms:
            if room in self.room_availability_grid.get(old_day_str, {}):
                for i in range(old_start_block, old_start_block + total_blocks_needed):
                    if i < num_blocks_in_day: self.room_availability_grid[old_day_str][room][i] = False

        exam_rec = self.schedule[schedule_idx]
        exam_rec['Date'] = new_slot_info['Date']
        exam_rec['Time_Slot'] = new_slot_info['Time_Slot']
        exam_rec['Room'] = new_slot_info['Room']

        section_id = exam_rec['Section']
        students = self.exams_df[self.exams_df['Section'] == section_id]['fake_id'].tolist()
        for sid in students:
            for exam in student_exams[sid]:
                if exam['Section'] == section_id:
                    exam['Date'] = new_slot_info['Date']
                    exam['Time_Slot'] = new_slot_info['Time_Slot']
                    exam['Room'] = new_slot_info['Room']
                    break
        
        new_start_block = new_slot_info['start_block']
        new_rooms = new_slot_info['rooms_to_book']
        for room in new_rooms:
            if room in self.room_availability_grid[new_slot_info['Date']]:
                for i in range(new_start_block, new_start_block + total_blocks_needed):
                    if i < num_blocks_in_day: self.room_availability_grid[new_slot_info['Date']][room][i] = True

    def _release_slot(self, schedule_idx, num_blocks_in_day):
        exam_rec = self.schedule[schedule_idx]
        day_str = exam_rec['Date']
        rooms = exam_rec['Room'].split(',')
        duration = exam_rec['Duration']
        exam_blocks = math.ceil(duration / self.time_step)
        buffer_blocks = math.ceil(self.buffer_time / self.time_step)
        total_blocks_needed = exam_blocks + buffer_blocks
        
        start_h, start_m = map(int, exam_rec['Time_Slot'].split('-')[0].split(':'))
        start_dt = self.work_day_start.replace(hour=start_h, minute=start_m)
        start_block = int((start_dt - self.work_day_start).total_seconds() / 60 / self.time_step)

        for room in rooms:
            if room in self.room_availability_grid.get(day_str, {}):
                for i in range(start_block, start_block + total_blocks_needed):
                    if i < num_blocks_in_day: self.room_availability_grid[day_str][room][i] = False
        return day_str, rooms, duration, exam_blocks, total_blocks_needed, start_block

    def _simulate_move_and_get_cost(self, section_id, students, new_day, new_slot, original_student_exams):
        simulated_exams = {sid: [e.copy() for e in exams] for sid, exams in original_student_exams.items()}

        for sid in students:
            for exam in simulated_exams.get(sid, []):
                if exam['Section'] == section_id:
                    exam['Date'] = new_day
                    exam['Time_Slot'] = new_slot
                    break
        
        total_cost = 0
        involved_students = set(students)
        # Add students from potentially overlapping exams
        for sid in list(involved_students):
            for exam in simulated_exams.get(sid, []):
                if exam['Date'] == new_day:
                    # This is a simplification; a full check would be more complex
                    pass

        # For simplicity and correctness, we recalculate for all students.
        # This can be optimized by only checking affected students.
        return self.get_total_conflicts(simulated_exams)

    def get_total_conflicts(self, student_exams_dict):
        total_conflicts = 0
        for student_id, exams in student_exams_dict.items():
            exams_on_days = defaultdict(list)
            for exam in exams:
                if exam.get('Date') and exam['Date'] != 'N/A':
                    exams_on_days[exam['Date']].append(exam)

            for date, daily_exams in exams_on_days.items():
                if len(daily_exams) > 1:
                    # Check for actual time overlaps
                    for i in range(len(daily_exams)):
                        for j in range(i + 1, len(daily_exams)):
                            exam1 = daily_exams[i]
                            exam2 = daily_exams[j]
                            if self.check_overlap(exam1.get('Time_Slot'), exam2.get('Time_Slot')):
                                total_conflicts += 1
        return total_conflicts

    def _is_room_excluded(self, room, day_dt, start_dt, end_dt):
        day_date = day_dt.date()
        if day_date in self.exclusions_by_date:
            for exclusion in self.exclusions_by_date[day_date]:
                if str(exclusion.room_number) == str(room):
                    # Check for time overlap: (StartA < EndB) and (EndA > StartB)
                    if max(start_dt, exclusion.start_time) < min(end_dt, exclusion.end_time):
                        return True  # The room is excluded
        return False

    def check_overlap(self, slot1, slot2):
        if not all([slot1, slot2]) or slot1 == 'N/A' or slot2 == 'N/A': return False
        start1, end1 = [datetime.strptime(t, '%H:%M') for t in slot1.split('-')]
        start2, end2 = [datetime.strptime(t, '%H:%M') for t in slot2.split('-')]
        return max(start1, start2) < min(end1, end2)

    def _apply_move(self, schedule_idx, new_day, new_slot, new_start_block, total_blocks, rooms, student_exams):
        section_id = self.schedule[schedule_idx]['Section']
        students = self.exams_df[self.exams_df['Section'] == section_id]['fake_id'].tolist()

        self.schedule[schedule_idx]['Date'] = new_day
        self.schedule[schedule_idx]['Time_Slot'] = new_slot

        for sid in students:
            for exam in student_exams[sid]:
                if exam['Section'] == section_id:
                    exam['Date'] = new_day
                    exam['Time_Slot'] = new_slot
                    break
        
        num_blocks_in_day = len(self.room_availability_grid[new_day][rooms[0]])
        for room in rooms:
            if room in self.room_availability_grid[new_day]:
                for i in range(new_start_block, new_start_block + total_blocks):
                    if i < num_blocks_in_day: self.room_availability_grid[new_day][room][i] = True

    def _rebook_slot(self, day_str, start_block, total_blocks, rooms):
        if day_str not in self.room_availability_grid: return
        num_blocks_in_day = len(self.room_availability_grid[day_str].get(rooms[0], []))
        if num_blocks_in_day == 0: return

        for room in rooms:
            if room in self.room_availability_grid[day_str]:
                for i in range(start_block, start_block + total_blocks):
                    if i < num_blocks_in_day: self.room_availability_grid[day_str][room][i] = True
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
        self.exams_df = self.exams_df[~self.exams_df['Section'].isin(sections)]

        # Удаляем из сгруппированных экзаменов
        self.exam_groups = self.exam_groups[~self.exam_groups['Section'].isin(sections)]

        # Если расписание уже создано, удаляем и из него
        if self.schedule_df is not None:
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

    def assign_seats(
        self,
    ):
        """
        Распределяет студентов по аудиториям с учетом вместимости и требования двух комнат,
        сохраняя информацию о местах в seat_assignments.
        """
        if not hasattr(self, 'schedule_df') or self.schedule_df.empty:
            logging.warning("Нет данных расписания для распределения мест")
            return

        # Log rooms_df contents
        logging.info(
            f"Содержимое rooms_df: {self.rooms_df.to_dict() if not self.rooms_df.empty else 'Пустой DataFrame'}"
        )
        logging.info(
            f"Колонки rooms_df: {list(self.rooms_df.columns) if not self.rooms_df.empty else 'Нет колонок'}"
        )

        # Кэшируем room_capacities с преобразованием к int
        capacity_column = (
            'Вместительность аудитории'
            if 'Вместительность аудитории' in self.rooms_df.columns
            else 'Capacity'
        )
        room_capacities = {}
        if not self.rooms_df.empty and capacity_column in self.rooms_df.columns:
            for _, row in self.rooms_df.iterrows():
                room_name = str(row['Аудитория']).strip().lower()  # Очистка: strip + lower
                try:
                    capacity = int(row[capacity_column])
                except (ValueError, TypeError):
                    logging.warning(
                        f"Некорректная вместимость для {room_name}: {row[capacity_column]}. Используем 25."
                    )
                    capacity = 25
                room_capacities[room_name] = capacity
        logging.info(f"Кэшированные вместимости: {room_capacities}")

        self.seat_assignments = {}
        total_assigned = 0
        problem_sections = []

        for _, exam in self.schedule_df.iterrows():
            try:
                if exam['Date'] == 'N/A' or pd.isna(exam['Date']):
                    logging.warning(
                        f"Skipping seat assignment for section {exam.get('Section', 'unknown')} because Date is 'N/A' or empty."
                    )
                    continue

                required_fields = [
                    'Date',
                    'Time_Slot',
                    'Subject',
                    'Section',
                    'Room',
                    'Instructor',
                ]
                if not all(field in exam and pd.notna(exam[field]) for field in required_fields):
                    problem_sections.append(exam.get('Section', 'unknown'))
                    logging.error(
                        f"Отсутствуют обязательные поля в секции {exam.get('Section', 'unknown')}: {exam.to_dict()}"
                    )
                    continue

                students = self.get_students_for_section(exam['Section'])
                if not students:
                    logging.warning(f"Нет студентов в секции {exam['Section']}")
                    continue

                two_rooms_needed = (
                    self.exam_groups[
                        self.exam_groups['Section'] == exam['Section']
                    ]['two_rooms_needed'].iloc[0]
                    if exam['Section'] in self.exam_groups['Section'].values
                    else False
                )
                logging.info(
                    f"Секция {exam['Section']}: two_rooms_needed={two_rooms_needed}, студентов={len(students)}, Room={exam['Room']}"
                )

                # Парсим аудитории из schedule_df
                room_string = str(exam['Room']).strip().lower()  # Очистка: strip + lower
                rooms = []
                total_capacity = 0

                if two_rooms_needed:
                    # Для two_rooms_needed ожидаем формат "room1,room2"
                    room_parts = [part.strip().lower() for part in room_string.split(',')]
                    if len(room_parts) != 2:
                        logging.error(
                            f"Секция {exam['Section']} требует две аудитории, но найдено {len(room_parts)}: {room_parts}"
                        )
                        problem_sections.append(exam['Section'])
                        continue
                else:
                    # Для одной комнаты используем только указанную аудиторию
                    room_parts = [room_string]

                for room_part in room_parts:
                    try:
                        match = re.match(r'(\S+)\((\d+)\)', room_part)
                        room_name = match.group(1).strip().lower() if match else room_part.strip().lower()
                        capacity = int(match.group(2)) if match else room_capacities.get(room_name, 25)

                        if room_name not in room_capacities:
                            logging.warning(
                                f"Аудитория '{room_name}' не найдена в rooms_df для секции {exam['Section']}. "
                                f"Используем вместимость {capacity}."
                            )
                        else:
                            capacity = room_capacities[room_name]
                            logging.info(f"Аудитория {room_name} найдена, вместимость: {capacity}")

                        rooms.append((room_name, capacity))
                        total_capacity += capacity
                    except Exception as e:
                        logging.error(
                            f"Ошибка парсинга аудитории '{room_part}' для секции {exam['Section']}: {str(e)}"
                        )
                        problem_sections.append(exam['Section'])
                        continue

                # Проверка вместимости
                if total_capacity < len(students):
                    logging.error(
                        f"Недостаточная вместимость ({total_capacity}) для {len(students)} студентов в секции {exam['Section']}"
                    )
                    if not two_rooms_needed:
                        # Пробуем добавить одну аудиторию
                        available_rooms = [
                            r
                            for r in room_capacities.keys()
                            if r not in [room[0] for room in rooms]
                            and room_capacities[r] >= len(students) - total_capacity
                        ]
                        if available_rooms:
                            new_room = available_rooms[0]
                            new_capacity = room_capacities[new_room]
                            rooms.append((new_room, new_capacity))
                            total_capacity += new_capacity
                            logging.info(
                                f"Добавлена аудитория {new_room}({new_capacity}) для секции {exam['Section']}"
                            )
                        else:
                            logging.error(f"Нет подходящих аудиторий для секции {exam['Section']}")
                            problem_sections.append(exam['Section'])
                            continue
                    else:
                        problem_sections.append(exam['Section'])
                        continue

                # Распределение студентов
                random.shuffle(students)
                student_index = 0
                exam_date = pd.to_datetime(exam['Date']).date()

                for room_name, capacity in rooms:
                    assigned_to_room = 0
                    # Ограничиваем количество студентов в комнате её вместимостью
                    for seat_num in range(1, capacity + 1):
                        if student_index >= len(students):
                            break
                        student_id = str(students[student_index])
                        key = f"{exam_date}|{exam['Time_Slot'].strip()}|{exam['Subject'].strip()}|{student_id}"
                        self.seat_assignments[key] = {
                            'seat': seat_num,
                            'room': room_name,
                            'section': exam['Section'],
                            'subject': exam['Subject'],
                            'date': exam_date.isoformat(),
                            'time_slot': exam['Time_Slot'],
                        }
                        student_index += 1
                        assigned_to_room += 1
                        total_assigned += 1

                    if assigned_to_room == 0:
                        logging.warning(
                            f"Не распределены студенты в аудиторию {room_name} для секции {exam['Section']}"
                        )

                if student_index < len(students):
                    logging.warning(
                        f"Не все студенты распределены для секции {exam['Section']}: {len(students) - student_index} остались"
                    )
                    problem_sections.append(exam['Section'])

            except Exception as e:
                section = exam.get('Section', 'unknown')
                problem_sections.append(section)
                logging.error(f"Ошибка распределения мест для секции {section}: {str(e)}", exc_info=True)

        logging.info(f"Успешно распределено мест: {total_assigned}")
        if problem_sections:
            logging.warning(f"Проблемы в секциях: {set(problem_sections)}")
        if self.seat_assignments:
            sample_key = next(iter(self.seat_assignments))
            logging.info(f"Пример распределения: {sample_key} => {self.seat_assignments[sample_key]}")

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
        key = f"{exam_date}|{time_slot}|{subject}|{str(student_id)}"
        return self.seat_assignments.get(key, {'room': 'Not assigned', 'seat': None})

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