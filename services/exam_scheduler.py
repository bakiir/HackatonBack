import json
import math
import random
import re
import statistics
import traceback
from collections import defaultdict
from itertools import combinations


import pandas as pd
from datetime import datetime, timedelta
import logging
from io import StringIO

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
            work_day_end="18:00"  # Конец рабочего дня
    ):
        logging.info("Инициализация планировщика экзаменов.")
        self.schedule_data = schedule_data
        self.title = title
        self.time_slots = ["08:00-11:00", "11:30-14:30", "15:00-18:00"]
        self.schedule_df = pd.DataFrame
        self.time_step = time_step
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

    def update_exam_durations(self, exam_data):
        """
        Обновляет длительность экзаменов в exam_groups.

        :param exam_data: Список словарей с данными об экзаменах.
            Пример: [{"Subject": "Международное право", "duration": 60, "proctor_needed": False}, ...]
        """
        for exam in exam_data:
            subject = exam["Subject"]
            duration = exam["duration"]
            proctor_needed = exam.get("proctor_needed", False)  # Опционально

            # Ищем предмет в exam_groups
            if subject not in self.exam_groups["Subject"].values:
                raise ValueError(f"Предмет '{subject}' не найден в exam_groups.")

            # Обновляем длительность и флаг проктора
            self.exam_groups.loc[self.exam_groups["Subject"] == subject, "Duration"] = duration
            self.exam_groups.loc[self.exam_groups["Subject"] == subject, "Proctor_Needed"] = proctor_needed

        logging.info("Длительность экзаменов успешно обновлена.")

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
                        self.schedule_df['Date']).dt.date  # Нормализуем дату к date (без времени)
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

        # Создаем exam_groups с колонкой Duration
        self.exam_groups = self.exams_df.drop_duplicates(subset=['Section'], keep='first').groupby(
            ['Subject', 'Instructor', 'EduProgram', 'YearsOfStudy', 'Section']
        ).agg({'fake_id': 'count'}).reset_index()

        if 'proctor_needed' not in self.exam_groups.columns:
            self.exam_groups['proctor_needed'] = True

        self.exam_groups['two_rooms_needed'] = False  # Добавляем новое поле
        # Добавляем колонку Duration (по умолчанию 180 минут)
        self.exam_groups["Duration"] = 180
        self.exam_groups["Proctor_Needed"] = False  # По умолчанию проктор не требуется
        self.exam_groups['has_exam'] = True

        # Нормализация списка комнат
        self.rooms = list(self.rooms_df[self.rooms_df['Аудитория'].astype(str).str.strip() != '107']['Аудитория'].astype(str).str.strip())

        logging.info(f"Загружено комнат: {len(self.rooms)}")
        logging.info(f"Пример комнат: {self.rooms[:5]}")  # Логируем первые 5 комнат для проверки

        self.room_capacities = dict(zip(
            self.rooms_df['Аудитория'].astype(str).str.strip(),
            self.rooms_df['Вместительность аудитории']
        ))
        self.all_students_dict = self.exams_df[['fake_id', 'fake_name']].drop_duplicates().to_dict('records')
        self.exam_groups["Duration"] = 180  # Дефолтная длительность
        self.subject_faculty_map = self.faculties_df.groupby('Subject')['Faculty'].apply(set).to_dict()
        self.faculty_proctors = self.faculties_df.groupby('Faculty')['Instructor'].apply(list).to_dict()

        total_slots = len(self.rooms) * len(self.time_slots) * self.original_num_days
        logging.info(f"Всего экзаменов: {len(self.exam_groups)}")
        logging.info(f"Всего временных слотов: {total_slots}")

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

    def update_exam_durations(self, exam_data):
        """
        Обновляет длительность экзаменов в exam_groups.

        :param exam_data: Список словарей с данными об экзаменах.
            Пример: [{"section_id": "Opt Math-1532", "duration": 60}, ...]
        """
        for exam in exam_data:
            section_id = exam["section_id"]
            duration = exam["duration"]

            # Проверяем допустимые значения длительности
            if duration not in [60, 120, 180]:
                raise ValueError(f"Недопустимая длительность {duration}. Допустимые значения: 60, 120 или 180 минут.")

            # Находим и обновляем запись
            if section_id not in self.exam_groups['Section'].values:
                raise ValueError(f"Секция {section_id} не найдена")

            self.exam_groups.loc[self.exam_groups['Section'] == section_id, "Duration"] = duration

        logging.info(f"Обновлены длительности для {len(exam_data)} экзаменов")

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
        Удаляет дату и добавляет новую в конец
        """
        # Проверяем валидность даты
        try:
            date_to_remove = datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            raise ValueError("Некорректный формат даты. Используйте YYYY-MM-DD")

        # Удаляем дату из списка
        if date_to_remove in self.custom_dates:
            self.custom_dates.remove(date_to_remove)

            # Добавляем новую дату в конец
            last_date = self.custom_dates[-1] if self.custom_dates else self.original_start_date
            new_date = last_date + timedelta(days=1)
            self.custom_dates.append(new_date)
        else:
            raise ValueError("Указанная дата не найдена в расписании")

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
        session = Session()
        try:
            # 1. Найти подходящие группы для объединения
            exam_groups_df['student_count'] = exam_groups_df['Section'].map(
                lambda x: len(self.exams_df[self.exams_df['Section'] == x])
            )
            
            subject_groups = exam_groups_df.groupby('Subject').agg(
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
                logging.info("Не найдено подходящих групп для приоритетного планирования в ауд. 107.")
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
                
                # Пропускаем, если какая-то из секций уже запланирована
                if any(s in scheduled_sections for s in sections_to_schedule):
                    continue

                all_students_in_group = set()
                for section_id in sections_to_schedule:
                    section_students = set(self.exams_df[self.exams_df['Section'] == section_id]['fake_id'])
                    all_students_in_group.update(section_students)

                # Ищем подходящий слот
                for slot in free_slots:
                    if slot.is_booked:
                        continue

                    # Проверка конфликтов студентов
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
                        continue # Переходим к следующему слоту

                    # Если конфликтов нет, бронируем слот
                    slot.is_booked = True
                    slot.booked_groups_info = json.dumps({
                        'subject': candidate_row['Subject'],
                        'sections': sections_to_schedule
                    }, ensure_ascii=False)
                    
                    base_time_slot = f"{slot.start_time.strftime('%H:%M')}-{slot.end_time.strftime('%H:%M')}"

                    # Создаем записи в расписании для каждой секции
                    for section_id in sections_to_schedule:
                        group_info = exam_groups_df[exam_groups_df['Section'] == section_id].iloc[0]
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

                        # Обновляем расписание студентов
                        section_students_list = self.exams_df[self.exams_df['Section'] == section_id]['fake_id'].tolist()
                        for student in section_students_list:
                            if student not in self.student_exams:
                                self.student_exams[student] = []
                            self.student_exams[student].append(exam_record)

                        scheduled_sections.add(section_id)
                        scheduled_count += 1

                    logging.info(f"Аудитория 107 забронирована для предмета '{candidate_row['Subject']}' ({len(sections_to_schedule)} секции) на {slot_date_str} {base_time_slot}")
                    
                    # Удаляем слот из списка доступных, чтобы не использовать его снова
                    free_slots.remove(slot)
                    break # Переходим к следующей группе кандидатов

            session.commit()
            
            # 4. Возвращаем обновленный DataFrame без запланированных секций
            remaining_groups_df = exam_groups_df[~exam_groups_df['Section'].isin(scheduled_sections)].copy()
            remaining_groups_df = remaining_groups_df.drop(columns=['student_count']) # Удаляем временную колонку
            
            logging.info(f"Завершено приоритетное планирование. Запланировано секций в ауд. 107: {scheduled_count}")
            return remaining_groups_df, scheduled_count

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

            logging.info(f"Загружено {len(manual_exams)} вручную забронированных экзаменов.")
            return manual_exams, scheduled_sections
        except Exception as e:
            logging.error(f"Ошибка при загрузке вручную забронированных слотов: {traceback.format_exc()}")
            return [], set()
        finally:
            session.close()

    def create_schedule(self):
        logging.info("Начало создания расписания")
        self.schedule = []
        self.student_exams = {}  # Для отслеживания экзаменов студентов
        success_count = 0
        failed_sections = []

        try:
            # 1. Загрузка вручную забронированных слотов
            manual_exams, scheduled_sections = self._load_manual_bookings()
            self.schedule.extend(manual_exams)
            success_count += len(manual_exams)

            # 2. Проверка и подготовка данных
            if not hasattr(self, 'exam_groups') or self.exam_groups.empty:
                raise ValueError("Нет данных о группах экзаменов")

            exam_groups = self.exam_groups[
                (self.exam_groups['has_exam'] == True) &
                (~self.exam_groups['Section'].isin(scheduled_sections))
            ].copy()
            
            # --- НОВАЯ ЛОГИКА: ПРИОРИТЕТНОЕ ПЛАНИРОВАНИЕ АУДИТОРИИ 107 ---
            # exam_groups, scheduled_in_107 = self._schedule_large_groups_in_107(exam_groups)
            # success_count += scheduled_in_107
            # --- КОНЕЦ НОВОЙ ЛОГИКИ ---

            if exam_groups.empty:
                logging.warning("Нет экзаменов для дальнейшего планирования.")
                self.schedule_df = pd.DataFrame(self.schedule) if self.schedule else pd.DataFrame()
                # ... (остальная часть логирования и возврата)
                return self.schedule_df

            # 3. Инициализация системы учета аудиторий и нагрузки
            self.room_usage = {
                day: {slot: set() for slot in self.time_slots}
                for day in self.custom_dates
            }
            day_load = {day.strftime('%Y-%m-%d'): 0 for day in self.custom_dates}
            student_exams_per_day = defaultdict(lambda: defaultdict(int))

            # Обновляем room_usage на основе уже запланированных вручную
            for exam in self.schedule:
                exam_date = datetime.strptime(exam['Date'], '%Y-%m-%d')
                base_slot = exam['Base_Time_Slot']
                room = exam['Room']
                if exam_date in self.room_usage and base_slot in self.room_usage[exam_date]:
                     self.room_usage[exam_date][base_slot].add(room)
                     day_load[exam['Date']] +=1
                     # Также обновим student_exams_per_day
                     students = self.exams_df[self.exams_df['Section'] == exam['Section']]['fake_id'].tolist()
                     for student in students:
                         student_exams_per_day[student][exam['Date']] += 1


            # 3. Сортировка групп по количеству студентов и степени конфликтов
            exam_groups['student_count'] = exam_groups['Section'].map(
                lambda x: len(self.exams_df[self.exams_df['Section'] == x])
            )

            student_to_sections = defaultdict(set)
            for section in exam_groups['Section'].unique():
                section_students = self.exams_df[self.exams_df['Section'] == section]['fake_id'].unique()
                for student in section_students:
                    student_to_sections[student].add(section)

            section_conflicts = {}
            for section in exam_groups['Section'].unique():
                section_students = set(self.exams_df[self.exams_df['Section'] == section]['fake_id'].unique())
                related_sections = set()
                for student in section_students:
                    related_sections.update(student_to_sections[student])
                section_conflicts[section] = len(related_sections) - 1

            exam_groups['conflict_degree'] = exam_groups['Section'].map(section_conflicts)
            exam_groups = exam_groups.sort_values(by=['conflict_degree', 'student_count'], ascending=False)

            # 4. Учёт нагрузки по дням
            # day_load и student_exams_per_day уже инициализированы и обновлены

            # 5. Первый проход: планирование с жёсткими ограничениями
            for _, group in exam_groups.iterrows():
                section = group['Section']
                students = self.exams_df[self.exams_df['Section'] == section]['fake_id'].tolist()
                num_students = len(students)
                duration = int(group.get('Duration', 180))  # 180 минут по умолчанию
                instructor = group['Instructor']
                proctor_needed = group.get('proctor_needed', False)
                two_rooms_needed = group.get('two_rooms_needed', False)  # Новое поле

                best_slots = []

                for day in self.custom_dates:
                    exam_date = day.strftime('%Y-%m-%d')
                    day_conflicts = sum(1 for student in students if student_exams_per_day[student][exam_date] >= 1)
                    
                    load_cost = day_load[exam_date]
                    
                    cost = day_conflicts * 100 + load_cost

                    for slot in self.time_slots:
                        start, end = slot.split('-')
                        slot_start_dt = datetime.strptime(start, '%H:%M')
                        slot_end_dt = datetime.strptime(end, '%H:%M')
                        slot_duration = (slot_end_dt - slot_start_dt).total_seconds() / 60
                        if slot_duration < duration:
                            continue

                        actual_end = (slot_start_dt + timedelta(minutes=duration)).strftime('%H:%M')
                        actual_slot = f"{start}-{actual_end}"

                        instructor_busy = any(
                            exam['Instructor'] == instructor and
                            exam['Date'] == exam_date and
                            exam['Time_Slot'] == slot
                            for exam in self.schedule
                        )
                        if instructor_busy:
                            continue

                        conflicts = 0
                        for student in students:
                            if student in self.student_exams:
                                for exam in self.student_exams[student]:
                                    if exam['Date'] == exam_date:
                                        exam_start = datetime.strptime(exam['Time_Slot'].split('-')[0], '%H:%M')
                                        exam_end = datetime.strptime(exam['Time_Slot'].split('-')[1], '%H:%M')
                                        curr_start = slot_start_dt
                                        curr_end = slot_start_dt + timedelta(minutes=duration)
                                        if curr_start < exam_end and exam_start < curr_end:
                                            conflicts += 1
                                            break
                                if conflicts > 0:
                                    break

                        if conflicts > 0:
                            continue

                        # Поиск аудиторий
                        available_rooms = [
                            room for room in self.rooms
                            if room not in self.room_usage[day][slot]
                        ]

                        if two_rooms_needed:
                            # Нужны две аудитории
                            room_pairs = list(combinations(available_rooms, 2))
                            for room1, room2 in room_pairs:
                                if self.room_capacities.get(room1, 0) + self.room_capacities.get(room2, 0) >= num_students:
                                    room = f"{room1},{room2}"
                                    best_slots.append((day, slot, actual_slot, room, cost))
                                    break
                        else:
                            # Нужна одна аудитория
                            available_rooms = [
                                room for room in available_rooms
                                if self.room_capacities.get(room, 0) >= num_students
                            ]
                            if available_rooms:
                                room = available_rooms[0]
                                best_slots.append((day, slot, actual_slot, room, cost))

                if best_slots:
                    best_day, best_base_slot, best_actual_slot, best_room, _ = min(
                        best_slots, key=lambda x: x[4]
                    )
                    exam_date = best_day.strftime('%Y-%m-%d')

                    exam_record = {
                        'Date': exam_date,
                        'Subject': group['Subject'],
                        'Instructor': instructor,
                        'EduProgram': group['EduProgram'],
                        'Section': section,
                        'Students_Count': num_students,
                        'Room': best_room,  # Для двух аудиторий: "room1,room2"
                        'Time_Slot': best_actual_slot,
                        'Base_Time_Slot': best_base_slot,
                        'Duration': duration,
                        'Student_Conflicts': 0,
                        'proctor_needed': proctor_needed,
                        'two_rooms_needed': two_rooms_needed,
                        'pinned': False
                    }

                    self.schedule.append(exam_record)
                    if ',' in best_room:
                        room1, room2 = best_room.split(',')
                        self.room_usage[best_day][best_base_slot].add(room1)
                        self.room_usage[best_day][best_base_slot].add(room2)
                    else:
                        self.room_usage[best_day][best_base_slot].add(best_room)

                    for student in students:
                        if student not in self.student_exams:
                            self.student_exams[student] = []
                        self.student_exams[student].append(exam_record)
                        student_exams_per_day[student][exam_date] += 1

                    success_count += 1
                    day_load[exam_date] += 1
                else:
                    failed_sections.append({
                        'section': section,
                        'students': students,
                        'num_students': num_students,
                        'instructor': instructor,
                        'duration': duration,
                        'proctor_needed': proctor_needed,
                        'group': group
                    })

            # 6. Второй проход: размещаем оставшиеся секции
            if failed_sections:
                logging.info(f"Первый проход завершён. Не удалось запланировать {len(failed_sections)} секций.")
                remaining_sections = failed_sections
                failed_sections = []

                for failed in remaining_sections:
                    section = failed['section']
                    students = failed['students']
                    num_students = failed['num_students']
                    instructor = failed['instructor']
                    duration = failed['duration']
                    proctor_needed = failed['proctor_needed']
                    group = failed['group']
                    two_rooms_needed = group.get('two_rooms_needed', False)

                    best_slots = []
                    for day in self.custom_dates:
                        exam_date = day.strftime('%Y-%m-%d')
                        day_conflicts = sum(1 for student in students if student_exams_per_day[student][exam_date] >= 1)
                        load_cost = day_load[exam_date]
                        
                        cost = day_conflicts * 100 + load_cost

                        for slot in self.time_slots:
                            start, end = slot.split('-')
                            slot_start_dt = datetime.strptime(start, '%H:%M')
                            slot_end_dt = datetime.strptime(end, '%H:%M')
                            slot_duration = (slot_end_dt - slot_start_dt).total_seconds() / 60
                            if slot_duration < duration:
                                continue

                            actual_end = (slot_start_dt + timedelta(minutes=duration)).strftime('%H:%M')
                            actual_slot = f"{start}-{actual_end}"

                            instructor_busy = any(
                                exam['Instructor'] == instructor and
                                exam['Date'] == exam_date and
                                exam['Time_Slot'] == slot
                                for exam in self.schedule
                            )
                            if instructor_busy:
                                continue

                            conflicts = 0
                            for student in students:
                                if student in self.student_exams:
                                    for exam in self.student_exams[student]:
                                        if exam['Date'] == exam_date:
                                            exam_start = datetime.strptime(exam['Time_Slot'].split('-')[0], '%H:%M')
                                            exam_end = datetime.strptime(exam['Time_Slot'].split('-')[1], '%H:%M')
                                            curr_start = slot_start_dt
                                            curr_end = slot_start_dt + timedelta(minutes=duration)
                                            if curr_start < exam_end and exam_start < curr_end:
                                                conflicts += 1
                                                break
                                    if conflicts > 0:
                                        break

                            if conflicts > 0:
                                continue

                            available_rooms = [
                                room for room in self.rooms
                                if room not in self.room_usage[day][slot]
                            ]

                            if two_rooms_needed:
                                room_pairs = list(combinations(available_rooms, 2))
                                for room1, room2 in room_pairs:
                                    if self.room_capacities.get(room1, 0) + self.room_capacities.get(room2, 0) >= num_students:
                                        room = f"{room1},{room2}"
                                        best_slots.append((day, slot, actual_slot, room, cost))
                                        break
                            else:
                                available_rooms = [
                                    room for room in available_rooms
                                    if self.room_capacities.get(room, 0) >= num_students
                                ]
                                if available_rooms:
                                    room = available_rooms[0]
                                    best_slots.append((day, slot, actual_slot, room, cost))

                    if best_slots:
                        best_day, best_base_slot, best_actual_slot, best_room, _ = min(
                            best_slots, key=lambda x: x[4]
                        )
                        exam_date = best_day.strftime('%Y-%m-%d')

                        exam_record = {
                            'Date': exam_date,
                            'Subject': group['Subject'],
                            'Instructor': instructor,
                            'EduProgram': group['EduProgram'],
                            'Section': section,
                            'Students_Count': num_students,
                            'Room': best_room,
                            'Time_Slot': best_actual_slot,
                            'Base_Time_Slot': best_base_slot,
                            'Duration': duration,
                        'Student_Conflicts': day_conflicts,
                        'proctor_needed': proctor_needed,
                        'two_rooms_needed': two_rooms_needed,
                        'pinned': False
                    }

                        self.schedule.append(exam_record)
                        if ',' in best_room:
                            room1, room2 = best_room.split(',')
                            self.room_usage[best_day][best_base_slot].add(room1)
                            self.room_usage[best_day][best_base_slot].add(room2)
                        else:
                            self.room_usage[best_day][best_base_slot].add(best_room)

                        for student in students:
                            if student not in self.student_exams:
                                self.student_exams[student] = []
                            self.student_exams[student].append(exam_record)
                            student_exams_per_day[student][exam_date] += 1

                        success_count += 1
                        day_load[exam_date] += 1
                    else:
                        failed_sections.append(failed)

            # 7. Подсчёт конфликтов
            conflict_count = 0
            for student, exams in self.student_exams.items():
                exams_by_date = defaultdict(list)
                for exam in exams:
                    exams_by_date[exam['Date']].append(exam)
                for date, daily_exams in exams_by_date.items():
                    if len(daily_exams) > 1:
                        conflict_count += 1
                        break

            logging.info(f"Количество студентов с конфликтами до оптимизации: {conflict_count}")

            # 8. Создание итогового DataFrame
            self.schedule_df = pd.DataFrame(self.schedule) if self.schedule else pd.DataFrame()
            if self.schedule:
                logging.info("Запускаем финальную оптимизацию с помощью optimize_schedule.")
                self.student_exams, _ = self.optimize_schedule(self.student_exams)

            # 9. Назначение прокторов и мест
            if not self.schedule_df.empty:
                if hasattr(self, 'assign_seats'):
                    self.assign_seats()

            # 10. Логирование результатов
            total_groups_to_schedule = len(self.exam_groups[self.exam_groups['has_exam'] == True])
            logging.info(f"Успешно запланировано: {success_count} из {total_groups_to_schedule + len(failed_sections)} групп")
            if failed_sections:
                logging.warning(f"Не удалось запланировать {len(failed_sections)} секций: {[f['section'] for f in failed_sections]}")

            self.failed_sections = failed_sections
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

    def optimize_schedule(self, student_exams):
        logging.info("Запуск оптимизации расписания")

        # Функция для подсчёта стоимости (число студентов с конфликтами в одном слоте)
        def calculate_cost(student_exams, changed_students=None):
            hard_conflict_penalty = 1000
            soft_conflict_penalty = 100
            cost = 0
            
            students_to_check = changed_students if changed_students is not None else student_exams.keys()

            for student in students_to_check:
                exams = student_exams[student]
                exams_by_date = defaultdict(list)
                for exam in exams:
                    exams_by_date[exam['Date']].append(exam)

                for date, daily_exams in exams_by_date.items():
                    # Hard conflict: more than one exam in the same time slot
                    exams_by_slot = defaultdict(list)
                    for exam in daily_exams:
                        exams_by_slot[exam['Time_Slot']].append(exam)
                    
                    for slot_exams in exams_by_slot.values():
                        if len(slot_exams) > 1:
                            cost += hard_conflict_penalty * (len(slot_exams) - 1)

                    # Soft conflict: more than one exam on the same day
                    if len(daily_exams) > 1:
                        cost += soft_conflict_penalty * (len(daily_exams) - 1)
            
            return cost

        # Основной алгоритм simulated annealing
        random.seed(42)
        current_schedule = [exam.copy() for exam in self.schedule]
        current_student_exams = {student: exams.copy() for student, exams in student_exams.items()}
        current_room_usage = {
            day: {slot: set(rooms) for slot, rooms in slots.items()}
            for day, slots in self.room_usage.items()
        }

        student_exams_per_day_slot = defaultdict(lambda: defaultdict(int))
        for student, exams in current_student_exams.items():
            for exam in exams:
                student_exams_per_day_slot[student][(exam['Date'], exam['Time_Slot'])] += 1

        # Ищем студентов с конфликтами
        conflict_students = set()
        for student, exams in current_student_exams.items():
            exams_by_date_slot = defaultdict(list)
            for exam in exams:
                exams_by_date_slot[(exam['Date'], exam['Time_Slot'])].append(exam)
            for (date, time_slot), daily_exams in exams_by_date_slot.items():
                if len(daily_exams) > 1:
                    conflict_students.add(student)
                    break

        current_cost = calculate_cost(current_student_exams)
        best_schedule = current_schedule.copy()
        best_student_exams = {student: exams.copy() for student, exams in current_student_exams.items()}
        best_room_usage = {
            day: {slot: set(rooms) for slot, rooms in slots.items()}
            for day, slots in current_room_usage.items()
        }
        best_cost = current_cost

        temperature = 20000.0
        cooling_rate = 0.980
        max_iterations = 5000

        for iteration in range(max_iterations):
            conflict_students = set()
            for student, exams in current_student_exams.items():
                exams_by_date_slot = defaultdict(list)
                for exam in exams:
                    exams_by_date_slot[(exam['Date'], exam['Time_Slot'])].append(exam)
                for (date, time_slot), daily_exams in exams_by_date_slot.items():
                    if len(daily_exams) > 1:
                        conflict_students.add(student)
                        break

            if not conflict_students:
                logging.info("Все конфликты устранены. Завершаем оптимизацию.")
                break

            max_attempts = 10
            attempt = 0
            while attempt < max_attempts:
                exam_idx = random.randint(0, len(current_schedule) - 1)
                exam = current_schedule[exam_idx]

                if exam.get('pinned'):
                    attempt += 1
                    continue

                section = exam['Section']
                section_students = self.exams_df[self.exams_df['Section'] == section]['fake_id'].unique()
                if any(student in conflict_students for student in section_students):
                    break
                attempt += 1

            if attempt == max_attempts:
                continue

            old_date = datetime.strptime(exam['Date'], '%Y-%m-%d')
            old_base_slot = exam.get('Base_Time_Slot', exam['Time_Slot'])
            old_actual_slot = exam['Time_Slot']

            try:
                room_str = exam['Room']
                old_rooms = room_str.split(" + ")
                old_rooms = [r.split('(')[0].replace("Room ", "").strip() for r in old_rooms]
            except Exception as e:
                logging.error(f"Не удалось распарсить аудиторию {exam['Room']}: {str(e)}")
                continue

            section_students = self.exams_df[self.exams_df['Section'] == section]['fake_id'].unique()
            num_students = len(section_students)
            instructor = exam['Instructor']
            duration = exam['Duration']
            proctor_needed = exam.get('proctor_needed', False)

            # Удаление экзамена из текущего расписания
            changed_students = set(section_students)
            for student in section_students:
                current_student_exams[student] = [e for e in current_student_exams[student] if
                                                  not (e['Date'] == exam['Date'] and e['Time_Slot'] == exam[
                                                      'Time_Slot'])]
                student_exams_per_day_slot[student][(exam['Date'], exam['Time_Slot'])] -= 1
                if student_exams_per_day_slot[student][(exam['Date'], exam['Time_Slot'])] == 0:
                    del student_exams_per_day_slot[student][(exam['Date'], exam['Time_Slot'])]

            for room in old_rooms:
                if room in current_room_usage[old_date][old_base_slot]:
                    current_room_usage[old_date][old_base_slot].discard(room)

            # Вычисление delta_conflicts для новых дней и слотов
            delta_conflicts = {}
            for new_day in self.custom_dates:
                new_date_str = new_day.strftime('%Y-%m-%d')
                for base_slot in self.time_slots:
                    slot_start, slot_end = base_slot.split('-')
                    slot_start_dt = datetime.strptime(slot_start, '%H:%M')
                    slot_end_dt = datetime.strptime(slot_end, '%H:%M')
                    slot_duration = (slot_end_dt - slot_start_dt).total_seconds() / 60

                    if slot_duration < duration:
                        continue

                    actual_end = (slot_start_dt + timedelta(minutes=duration)).strftime('%H:%M')
                    actual_slot = f"{slot_start}-{actual_end}"

                    delta = 0
                    for student in section_students:
                        exams_on_old_slot = student_exams_per_day_slot[student].get(
                            (old_date.strftime('%Y-%m-%d'), old_actual_slot), 0)
                        exams_on_new_slot = student_exams_per_day_slot[student].get((new_date_str, actual_slot), 0)
                        if exams_on_old_slot >= 1:
                            delta -= 1
                        if exams_on_new_slot >= 1:
                            delta += 1
                    delta_conflicts[(new_day, actual_slot)] = delta

            sorted_new_slots = sorted(delta_conflicts.keys(), key=lambda x: delta_conflicts[x])[:3]
            found_slot = False

            for new_day, actual_slot in sorted_new_slots:
                new_date_str = new_day.strftime('%Y-%m-%d')
                base_slot = next(slot for slot in self.time_slots if actual_slot.startswith(slot.split('-')[0]))
                slot_start_dt = datetime.strptime(actual_slot.split('-')[0], '%H:%M')

                if not self._is_instructor_available(instructor, new_day, actual_slot):
                    continue

                available_rooms = [
                    room for room in self.rooms
                    if (room not in current_room_usage[new_day][base_slot] and
                        self.room_capacities[room] >= num_students)
                ]
                if not available_rooms:
                    continue

                new_room = available_rooms[0]

                current_schedule[exam_idx] = {
                    'Date': new_date_str,
                    'Subject': exam['Subject'],
                    'Instructor': instructor,
                    'EduProgram': exam['EduProgram'],
                    'Section': section,
                    'Students_Count': num_students,
                    'Room': f"{new_room}({self.room_capacities[new_room]})",
                    'Time_Slot': actual_slot,
                    'Base_Time_Slot': base_slot,
                    'Duration': duration,
                    'Student_Conflicts': 0,
                    'proctor_needed': proctor_needed,
                    'pinned': exam.get('pinned', False)
                }

                for student in section_students:
                    current_student_exams[student].append({
                        'Date': new_date_str,
                        'Time_Slot': actual_slot,
                        'Subject': exam['Subject']
                    })
                    student_exams_per_day_slot[student][(new_date_str, actual_slot)] += 1

                current_room_usage[new_day][base_slot].add(new_room)

                new_cost = calculate_cost(current_student_exams)
                if new_cost < current_cost or random.random() < math.exp((current_cost - new_cost) / temperature):
                    current_cost = new_cost
                    if new_cost < best_cost:
                        best_cost = new_cost
                        best_schedule = current_schedule.copy()
                        best_student_exams = {student: exams.copy() for student, exams in current_student_exams.items()}
                        best_room_usage = {
                            day: {slot: set(rooms) for slot, rooms in slots.items()}
                            for day, slots in current_room_usage.items()
                        }
                else:
                    current_schedule[exam_idx] = exam
                    for student in section_students:
                        current_student_exams[student] = [e for e in current_student_exams[student] if
                                                          not (e['Date'] == new_date_str and e[
                                                              'Time_Slot'] == actual_slot)]
                        student_exams_per_day_slot[student][(new_date_str, actual_slot)] -= 1
                        if student_exams_per_day_slot[student][(new_date_str, actual_slot)] == 0:
                            del student_exams_per_day_slot[student][(new_date_str, actual_slot)]
                    current_room_usage[new_day][base_slot].discard(new_room)
                    for room in old_rooms:
                        if room in self.rooms:
                            current_room_usage[old_date][old_base_slot].add(room)
                    for student in section_students:
                        current_student_exams[student].append({
                            'Date': exam['Date'],
                            'Time_Slot': exam['Time_Slot'],
                            'Subject': exam['Subject']
                        })
                        student_exams_per_day_slot[student][(exam['Date'], exam['Time_Slot'])] += 1
                found_slot = True
                break
            if not found_slot:
                for room in old_rooms:
                    if room in self.rooms:
                        current_room_usage[old_date][old_base_slot].add(room)
                for student in section_students:
                    current_student_exams[student].append({
                        'Date': exam['Date'],
                        'Time_Slot': exam['Time_Slot'],
                        'Subject': exam['Subject']
                    })
                    student_exams_per_day_slot[student][(exam['Date'], exam['Time_Slot'])] += 1

            temperature *= cooling_rate
            if iteration % 100 == 0:
                logging.info(f"Итерация {iteration}, текущая стоимость: {current_cost}, лучшая стоимость: {best_cost}")

        # Применяем лучший результат
        self.schedule = best_schedule
        self.student_exams = best_student_exams
        self.room_usage = best_room_usage
        self.schedule_df = pd.DataFrame(self.schedule) if self.schedule else pd.DataFrame()

        # Подсчёт конфликтов после оптимизации
        conflict_count = 0
        conflict_students_list = []
        conflict_details = []

        for student, exams in self.student_exams.items():
            exams_by_date_slot = defaultdict(list)
            for exam in exams:
                exams_by_date_slot[(exam['Date'], exam['Time_Slot'])].append(exam)
            for (date, time_slot), daily_exams in exams_by_date_slot.items():
                if len(daily_exams) > 1:
                    conflict_count += 1
                    conflict_students_list.append(student)
                    subjects_str = ", ".join([exam['Subject'] for exam in daily_exams])
                    conflict_details.append({
                        'Student_ID': student,
                        'Conflict_Date': date,
                        'Time_Slot': time_slot,
                        'Exam_Count': len(daily_exams),
                        'Subjects': subjects_str
                    })
                    break

        logging.info(f"Оптимизация завершена. Лучшая стоимость: {best_cost}")
        logging.info(f"Количество студентов с конфликтами после оптимизации (>1 в слоте): {conflict_count}")
        if conflict_students_list:
            logging.info(f"Список конфликтных студентов после оптимизации: {conflict_students_list}")
        else:
            logging.info("Конфликтных студентов после оптимизации нет.")

        # Дополнительная проверка конфликтов по дням для отладки
        day_conflict_count = 0
        day_conflict_details = []
        for student, exams in self.student_exams.items():
            exams_by_date = defaultdict(list)
            for exam in exams:
                exams_by_date[exam['Date']].append(exam)
            for date, daily_exams in exams_by_date.items():
                if len(daily_exams) > 1:
                    day_conflict_count += 1
                    subjects_str = ", ".join([exam['Subject'] for exam in daily_exams])
                    day_conflict_details.append({
                        'Student_ID': student,
                        'Conflict_Date': date,
                        'Exam_Count': len(daily_exams),
                        'Subjects': subjects_str
                    })
                    break

        if day_conflict_count > 0:
            logging.warning(f"Найдено {day_conflict_count} студентов с конфликтами по дням (>1 экзамена в день):")
            for detail in day_conflict_details:
                logging.warning(
                    f"{detail['Student_ID']}, Дата {detail['Conflict_Date']}: {detail['Exam_Count']} экзаменов (>1 в день), Предметы: {detail['Subjects']}")
            # Сохранение конфликтов по дням в Excel
            day_conflict_df = pd.DataFrame(day_conflict_details)
            day_output_file = "student_day_conflicts_after_optimization.xlsx"
            try:
                day_conflict_df.to_excel(day_output_file, index=False)
                logging.info(f"Конфликтные студенты (>1 в день) сохранены в файл: {day_output_file}")
            except Exception as e:
                logging.error(f"Ошибка при сохранении конфликтов по дням в Excel: {str(e)}")
        else:
            logging.info("Конфликтных студентов по дням (>1 экзамена в день) нет.")

        return self.student_exams, conflict_students_list
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
        if self.schedule_df is None:
            self.schedule_df = pd.DataFrame(self.schedule)

        self.schedule_df.to_excel(output_excel, index=False)

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
        student_schedule = self.get_student_sections(student_id)
        if student_schedule.empty:
            logging.warning(f"Для студента {student_id} не найдено расписания.")
            return

        student_schedule.to_excel(output_file, index=False)
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
            choice = input("\nВыберите действие:\n"
                           "1 - Удалить все группы этого предмета\n"
                           "2 - Удалить конкретную группу\n"
                           "3 - Вернуться к списку предметов\n"
                           "Ваш выбор: ")

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

    def assign_seats(self):
        """
        Распределяет студентов по аудиториям с учетом вместимости и требования двух комнат,
        сохраняя информацию о местах в seat_assignments.
        """
        if not hasattr(self, 'schedule_df') or self.schedule_df.empty:
            logging.warning("Нет данных расписания для распределения мест")
            return

        # Log rooms_df contents
        logging.info(
            f"Содержимое rooms_df: {self.rooms_df.to_dict() if not self.rooms_df.empty else 'Пустой DataFrame'}")
        logging.info(
            f"Колонки rooms_df: {list(self.rooms_df.columns) if not self.rooms_df.empty else 'Нет колонок'}")

        # Кэшируем room_capacities с преобразованием к int
        capacity_column = 'Вместительность аудитории' if 'Вместительность аудитории' in self.rooms_df.columns else 'Capacity'
        room_capacities = {}
        if not self.rooms_df.empty and capacity_column in self.rooms_df.columns:
            for _, row in self.rooms_df.iterrows():
                room_name = str(row['Аудитория']).strip().lower()  # Очистка: strip + lower
                try:
                    capacity = int(row[capacity_column])
                except (ValueError, TypeError):
                    logging.warning(f"Некорректная вместимость для {room_name}: {row[capacity_column]}. Используем 25.")
                    capacity = 25
                room_capacities[room_name] = capacity
        logging.info(f"Кэшированные вместимости: {room_capacities}")

        self.seat_assignments = {}
        total_assigned = 0
        problem_sections = []

        for _, exam in self.schedule_df.iterrows():
            try:
                required_fields = ['Date', 'Time_Slot', 'Subject', 'Section', 'Room', 'Instructor']
                if not all(field in exam and pd.notna(exam[field]) for field in required_fields):
                    problem_sections.append(exam.get('Section', 'unknown'))
                    logging.error(
                        f"Отсутствуют обязательные поля в секции {exam.get('Section', 'unknown')}: {exam.to_dict()}")
                    continue

                students = self.get_students_for_section(exam['Section'])
                if not students:
                    logging.warning(f"Нет студентов в секции {exam['Section']}")
                    continue

                two_rooms_needed = (
                    self.exam_groups[self.exam_groups['Section'] == exam['Section']]['two_rooms_needed'].iloc[0]
                    if exam['Section'] in self.exam_groups['Section'].values else False
                )
                logging.info(
                    f"Секция {exam['Section']}: two_rooms_needed={two_rooms_needed}, студентов={len(students)}, Room={exam['Room']}")

                # Парсим аудитории из schedule_df
                room_string = str(exam['Room']).strip().lower()  # Очистка: strip + lower
                rooms = []
                total_capacity = 0

                if two_rooms_needed:
                    # Для two_rooms_needed ожидаем формат "room1,room2"
                    room_parts = [part.strip().lower() for part in room_string.split(',')]  # Очистка частей
                    if len(room_parts) != 2:
                        logging.error(
                            f"Секция {exam['Section']} требует две аудитории, но найдено {len(room_parts)}: {room_parts}")
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
                                f"Используем вместимость {capacity}.")
                        else:
                            capacity = room_capacities[room_name]
                            logging.info(f"Аудитория {room_name} найдена, вместимость: {capacity}")

                        rooms.append((room_name, capacity))
                        total_capacity += capacity
                    except Exception as e:
                        logging.error(
                            f"Ошибка парсинга аудитории '{room_part}' для секции {exam['Section']}: {str(e)}")
                        problem_sections.append(exam['Section'])
                        continue

                # Проверка вместимости
                if total_capacity < len(students):
                    logging.error(
                        f"Недостаточная вместимость ({total_capacity}) для {len(students)} студентов в секции {exam['Section']}")
                    if not two_rooms_needed:
                        # Пробуем добавить одну аудиторию
                        available_rooms = [
                            r for r in room_capacities.keys()
                            if r not in [room[0] for room in rooms]
                               and room_capacities[r] >= len(students) - total_capacity
                        ]
                        if available_rooms:
                            new_room = available_rooms[0]
                            new_capacity = room_capacities[new_room]
                            rooms.append((new_room, new_capacity))
                            total_capacity += new_capacity
                            logging.info(
                                f"Добавлена аудитория {new_room}({new_capacity}) для секции {exam['Section']}")
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
                            'time_slot': exam['Time_Slot']
                        }
                        student_index += 1
                        assigned_to_room += 1
                        total_assigned += 1

                    if assigned_to_room == 0:
                        logging.warning(
                            f"Не распределены студенты в аудиторию {room_name} для секции {exam['Section']}")

                if student_index < len(students):
                    logging.warning(
                        f"Не все студенты распределены для секции {exam['Section']}: {len(students) - student_index} остались")
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
        """Возвращает список student_id для указанной секции"""
        if not hasattr(self, 'exams_df'):
            raise AttributeError("exams_df not loaded - please activate a complete session")

        section_students = self.exams_df[self.exams_df['Section'] == section_id]
        return section_students['fake_id'].tolist()