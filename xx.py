import json
import math
import random
import traceback
from collections import defaultdict
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
            time_step = 30,  # Шаг временных слотов в минутах
            work_day_start = "08:00",  # Начало рабочего дня
            work_day_end = "18:00"  # Конец рабочего дня
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

            # 2. Загружаем DataFrame
            if session_data.schedule_data:
                self.schedule_df = pd.read_json(StringIO(session_data.schedule_data))
                logging.info(f"Загружено расписание: {len(self.schedule_df)} записей")

            if session_data.exams_data:
                self.exams_df = pd.read_json(StringIO(session_data.exams_data))
                self.exams_df['fake_id'] = self.exams_df['fake_id'].astype(str)
                logging.info(f"Загружены студенты: {len(self.exams_df)} записей")

            if session_data.rooms_data:
                self.rooms_df = pd.read_json(StringIO(session_data.rooms_data))

            if session_data.faculties_data:
                self.faculties_df = pd.read_json(StringIO(session_data.faculties_data))

            # 3. Загружаем распределение мест
            self.seat_assignments = {}
            if hasattr(session_data, 'seat_assignments') and session_data.seat_assignments:
                try:
                    loaded_assignments = session_data.seat_assignments

                    # Если данные хранятся как JSON строка
                    if isinstance(loaded_assignments, str):
                        loaded_assignments = json.loads(loaded_assignments)

                    # Преобразуем ключи обратно в кортежи
                    for key_str, value in loaded_assignments.items():
                        try:
                            # Разбираем ключ (формат: "date|time|subject|student")
                            parts = key_str.split('|')
                            if len(parts) == 4:
                                date_obj = datetime.strptime(parts[0], "%Y-%m-%d").date()
                                key = (date_obj, parts[1].strip(), parts[2].strip(), parts[3].strip())
                                self.seat_assignments[key] = value
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
        self.all_students_dict = self.exams_df[['fake_id', 'fake_name']].drop_duplicates().to_dict('records')
        self.exam_groups["Duration"] = 180  # Дефолтная длительность
        self.subject_faculty_map = self.faculties_df.groupby('Subject')['Faculty'].apply(set).to_dict()
        self.faculty_proctors = self.faculties_df.groupby('Faculty')['Instructor'].apply(list).to_dict()

        total_slots = len(self.rooms) * len(self.time_slots) * self.original_num_days
        logging.info(f"Всего экзаменов: {len(self.exam_groups)}")
        logging.info(f"Всего временных слотов: {total_slots}")

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
        """Удаляет дату и добавляет новую в конец"""
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

    from collections import defaultdict, deque

    def assign_proctors(self, proctors_path=None):
        logging.info("Назначение прокторов.")
        assigned_proctors = []
        section_proctors = {}

        non_proctors = []

        if proctors_path:
            non_proctors_df = pd.read_excel(proctors_path, header=3)
            non_proctors = non_proctors_df['ФИО'].dropna().tolist()

        # Получаем всех прокторов
        all_proctors = [p for proctors in self.faculty_proctors.values() for p in proctors]

        # Исключаем non_proctors по частичному совпадению
        def is_excluded(name, exclusions):
            return any(excl in name for excl in exclusions)

        available_proctors = [
            p for p in set(all_proctors)
            if not is_excluded(p, non_proctors)
        ]

        if not available_proctors:
            raise ValueError("Нет доступных прокторов для назначения.")

        # Сортируем для предсказуемости
        available_proctors.sort()

        # Прокторы из ШЦТ
        sct_proctors = self.faculty_proctors.get('Школа цифровых технологий', [])
        sct_proctors = [p for p in sct_proctors if p in available_proctors]
        if not sct_proctors:
            logging.warning("Нет доступных прокторов из ШЦТ, хотя они могут быть нужны.")

        sct_proctor_index = 0
        total_sct_proctors = len(sct_proctors) if sct_proctors else 0

        proctor_index = 0
        total_proctors = len(available_proctors)

        for _, row in self.schedule_df.iterrows():
            section_id = row['Section']
            subject = row['Subject']
            exam_date = row['Date']
            proctor_needed = row.get('proctor_needed', True)

            if not proctor_needed:
                section_proctors[section_id] = {
                    'proctor': None,
                    'subject': subject,
                    'exam_name': subject,
                    'date': exam_date
                }
                assigned_proctors.append(None)
                continue

            # Проверяем, относится ли предмет к ШЦТ
            is_sct_subject = False
            subject_faculties = self.subject_faculty_map.get(subject, set())
            if 'Школа цифровых технологий' in subject_faculties:
                is_sct_subject = True

            if is_sct_subject:
                if total_sct_proctors == 0:
                    logging.error(f"Нет прокторов из ШЦТ для предмета {subject}!")
                    raise ValueError(f"Нет прокторов из ШЦТ для предмета {subject}!")
                assigned = sct_proctors[sct_proctor_index % total_sct_proctors]
                sct_proctor_index += 1
            else:
                assigned = available_proctors[proctor_index % total_proctors]
                proctor_index += 1

            assigned_proctors.append(assigned)
            section_proctors[section_id] = {
                'proctor': assigned,
                'subject': subject,
                'exam_name': subject,
                'date': exam_date
            }

        # Сохраняем в DataFrame и объект
        self.schedule_df['Proctor'] = assigned_proctors
        self.section_proctors = section_proctors

        logging.info(f"section_proctors после назначения: {self.section_proctors}")
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
            if (exam['Instructor'] == instructor and
                    exam['Date'] == day.strftime('%Y-%m-%d')):
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



    def create_schedule(self):
        logging.info("Начало создания расписания")
        self.schedule = []
        self.student_exams = {}  # Для отслеживания экзаменов студентов
        success_count = 0
        failed_sections = []

        try:
            # 1. Проверка и подготовка данных
            if not hasattr(self, 'exam_groups') or self.exam_groups.empty:
                raise ValueError("Нет данных о группах экзаменов")

            exam_groups = self.exam_groups[self.exam_groups['has_exam'] == True].copy()
            if exam_groups.empty:
                raise ValueError("Нет групп с флагом has_exam=True")

            # 2. Инициализация системы учета аудиторий
            self.room_usage = {
                day: {slot: set() for slot in self.time_slots}
                for day in self.custom_dates
            }

            # 3. Сортировка групп по количеству студентов и степени конфликтов
            exam_groups['student_count'] = exam_groups['Section'].map(
                lambda x: len(self.exams_df[self.exams_df['Section'] == x])
            )

            # Построение отображения студент → секции для вычисления степени конфликтов
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
            day_load = {day.strftime('%Y-%m-%d'): 0 for day in self.custom_dates}  # Количество экзаменов в день
            student_exams_per_day = defaultdict(lambda: defaultdict(int))  # Для учёта числа экзаменов в день

            # 5. Первый проход: планирование с жёсткими ограничениями
            for _, group in exam_groups.iterrows():
                section = group['Section']
                students = self.exams_df[self.exams_df['Section'] == section]['fake_id'].tolist()
                num_students = len(students)
                duration = int(group.get('Duration', 180))  # 120 минут по умолчанию
                instructor = group['Instructor']
                proctor_needed = group.get('proctor_needed', False)

                scheduled = False
                best_slots = []

                # Перебор дней и слотов
                for day in self.custom_dates:
                    exam_date = day.strftime('%Y-%m-%d')
                    # Жёсткое ограничение: не более одного экзамена в день
                    day_conflicts = sum(1 for student in students if student_exams_per_day[student][exam_date] >= 1)
                    if day_conflicts > 0:
                        continue  # Если у студента уже есть экзамен в этот день, пропускаем день

                    load_cost = day_load[exam_date]  # Учёт нагрузки на день

                    for slot in self.time_slots:
                        start, end = slot.split('-')
                        slot_start_dt = datetime.strptime(start, '%H:%M')
                        slot_end_dt = datetime.strptime(end, '%H:%M')
                        slot_duration = (slot_end_dt - slot_start_dt).total_seconds() / 60
                        if slot_duration < duration:
                            continue  # Слот слишком короткий

                        actual_end = (slot_start_dt + timedelta(minutes=duration)).strftime('%H:%M')
                        actual_slot = f"{start}-{actual_end}"

                        # Проверка преподавателя
                        instructor_busy = any(
                            exam['Instructor'] == instructor and
                            exam['Date'] == exam_date and
                            exam['Time_Slot'] == slot
                            for exam in self.schedule
                        )
                        if instructor_busy:
                            continue

                        # Проверка пересечения по времени (хотя с новым ограничением это избыточно)
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
                            continue  # Есть конфликт по времени

                        # Поиск подходящей аудитории
                        available_rooms = [
                            room for room in self.rooms
                            if (room not in self.room_usage[day][slot] and
                                self.room_capacities[room] >= num_students)
                        ]

                        if not available_rooms:
                            continue

                        # Выбираем первую подходящую аудиторию
                        room = available_rooms[0]
                        best_slots.append((day, slot, actual_slot, room, load_cost))

                if best_slots:
                    # Выбираем слот с минимальной нагрузкой на день
                    best_day, best_base_slot, best_actual_slot, best_room, _ = min(
                        best_slots, key=lambda x: x[4]
                    )
                    exam_date = best_day.strftime('%Y-%m-%d')

                    # Назначение экзамена
                    exam_record = {
                        'Date': exam_date,
                        'Subject': group['Subject'],
                        'Instructor': instructor,
                        'EduProgram': group['EduProgram'],
                        'Section': section,
                        'Students_Count': num_students,
                        'Room': f"{best_room}({self.room_capacities[best_room]})",
                        'Time_Slot': best_actual_slot,
                        'Base_Time_Slot': best_base_slot,
                        'Duration': duration,
                        'Student_Conflicts': 0,
                        'proctor_needed': proctor_needed
                    }

                    self.schedule.append(exam_record)
                    self.room_usage[best_day][best_base_slot].add(best_room)

                    # Обновление занятости студентов
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

            # 6. Второй проход: размещаем оставшиеся секции с минимизацией конфликтов
            if failed_sections:
                logging.info(
                    f"Первый проход завершён. Не удалось запланировать {len(failed_sections)} секций. Пробуем второй проход.")
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

                    best_slots = []
                    for day in self.custom_dates:
                        exam_date = day.strftime('%Y-%m-%d')
                        # Минимизируем число новых конфликтов
                        day_conflicts = sum(1 for student in students if student_exams_per_day[student][exam_date] >= 1)
                        load_cost = day_load[exam_date]

                        for slot in self.time_slots:
                            start, end = slot.split('-')
                            slot_start_dt = datetime.strptime(start, '%H:%M')
                            slot_end_dt = datetime.strptime(end, '%H:%M')
                            slot_duration = (slot_end_dt - slot_start_dt).total_seconds() / 60
                            if slot_duration < duration:
                                continue

                            actual_end = (slot_start_dt + timedelta(minutes=duration)).strftime('%H:%M')
                            actual_slot = f"{start}-{actual_end}"

                            # Проверка преподавателя
                            instructor_busy = any(
                                exam['Instructor'] == instructor and
                                exam['Date'] == exam_date and
                                exam['Time_Slot'] == slot
                                for exam in self.schedule
                            )
                            if instructor_busy:
                                continue

                            # Проверка конфликтов по времени
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
                                if (room not in self.room_usage[day][slot] and
                                    self.room_capacities[room] >= num_students)
                            ]

                            if not available_rooms:
                                continue

                            room = available_rooms[0]
                            best_slots.append((day, slot, actual_slot, room, day_conflicts, load_cost))

                    if best_slots:
                        best_day, best_base_slot, best_actual_slot, best_room, conflict_cost, _ = min(
                            best_slots, key=lambda x: (x[4], x[5])
                        )
                        exam_date = best_day.strftime('%Y-%m-%d')

                        exam_record = {
                            'Date': exam_date,
                            'Subject': group['Subject'],
                            'Instructor': instructor,
                            'EduProgram': group['EduProgram'],
                            'Section': section,
                            'Students_Count': num_students,
                            'Room': f"{best_room}({self.room_capacities[best_room]})",
                            'Time_Slot': actual_slot,
                            'Base_Time_Slot': best_base_slot,
                            'Duration': duration,
                            'Student_Conflicts': conflict_cost,
                            'proctor_needed': proctor_needed
                        }

                        self.schedule.append(exam_record)
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
                self.student_exams = self.optimize_schedule(self.student_exams)            # 9. Назначение прокторов и мест
            if not self.schedule_df.empty:
                # if hasattr(self, 'assign_proctors'):
                #     self.assign_proctors()
                if hasattr(self, 'assign_seats'):
                    self.assign_seats()

            # 10. Логирование результатов
            total_groups = len(exam_groups)
            logging.info(f"Успешно запланировано: {success_count} из {total_groups} групп")
            if failed_sections:
                logging.warning(f"Не удалось запланировать {len(failed_sections)} секций: {failed_sections}")

            return self.schedule_df

        except Exception as e:
            logging.error(f"Ошибка при создании расписания: {str(e)}")
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

        # Функция для подсчёта стоимости (число студентов с конфликтами)
        def calculate_cost(student_exams, changed_students=None):
            conflict_count = 0

            if changed_students is not None:
                students_to_check = changed_students
            else:
                students_to_check = student_exams.keys()

            for student in students_to_check:
                exams = student_exams[student]
                exams_by_date = defaultdict(list)
                for exam in exams:
                    exams_by_date[exam['Date']].append(exam)
                for date, daily_exams in exams_by_date.items():
                    if len(daily_exams) > 1:
                        conflict_count += 1
                        break

            return conflict_count * 1000

        # Основной алгоритм simulated annealing
        random.seed(42)
        current_schedule = [exam.copy() for exam in self.schedule]
        current_student_exams = {student: exams.copy() for student, exams in student_exams.items()}
        current_room_usage = {
            day: {slot: set(rooms) for slot, rooms in slots.items()}
            for day, slots in self.room_usage.items()
        }

        student_exams_per_day = defaultdict(lambda: defaultdict(int))
        for student, exams in current_student_exams.items():
            for exam in exams:
                student_exams_per_day[student][exam['Date']] += 1

        # Ищем студентов с конфликтами
        conflict_students = set()
        for student, exams in current_student_exams.items():
            exams_by_date = defaultdict(list)
            for exam in exams:
                exams_by_date[exam['Date']].append(exam)
            for date, daily_exams in exams_by_date.items():
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

        temperature = 10000.0
        cooling_rate = 0.999
        max_iterations = 2000

        for iteration in range(max_iterations):
            # Полный пересчёт conflict_students для точности
            conflict_students = set()
            for student, exams in current_student_exams.items():
                exams_by_date = defaultdict(list)
                for exam in exams:
                    exams_by_date[exam['Date']].append(exam)
                for date, daily_exams in exams_by_date.items():
                    if len(daily_exams) > 1:
                        conflict_students.add(student)
                        break

            if not conflict_students:
                logging.info("Все конфликты устранены. Завершаем оптимизацию.")
                break

            # Случайно выбираем экзамен, пока не найдём конфликтный
            max_attempts = 10
            attempt = 0
            while attempt < max_attempts:
                exam_idx = random.randint(0, len(current_schedule) - 1)
                exam = current_schedule[exam_idx]
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
                student_exams_per_day[student][exam['Date']] -= 1
                if student_exams_per_day[student][exam['Date']] == 0:
                    del student_exams_per_day[student][exam['Date']]

            for room in old_rooms:
                if room in current_room_usage[old_date][old_base_slot]:
                    current_room_usage[old_date][old_base_slot].discard(room)

            # Вычисление delta_conflicts для новых дней
            delta_conflicts = {}
            for new_day in self.custom_dates:
                new_date_str = new_day.strftime('%Y-%m-%d')
                delta = 0
                for student in section_students:
                    exams_on_old_day = student_exams_per_day[student].get(old_date.strftime('%Y-%m-%d'), 0)
                    exams_on_new_day = student_exams_per_day[student].get(new_date_str, 0)
                    if exams_on_old_day >= 1:
                        delta -= 1
                    if exams_on_new_day >= 1:
                        delta += 1
                delta_conflicts[new_day] = delta

            # Перебираем только 3 лучших дня
            sorted_new_days = sorted(self.custom_dates, key=lambda d: delta_conflicts[d])[:3]
            found_slot = False

            for new_day in sorted_new_days:
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
                        'proctor_needed': proctor_needed
                    }

                    for student in section_students:
                        current_student_exams[student].append({
                            'Date': new_date_str,
                            'Time_Slot': actual_slot,
                            'Subject': exam['Subject']
                        })
                        student_exams_per_day[student][new_date_str] += 1

                    current_room_usage[new_day][base_slot].add(new_room)

                    new_cost = calculate_cost(current_student_exams)
                    if new_cost < current_cost or random.random() < math.exp(
                            (current_cost - new_cost) / temperature):
                        current_cost = new_cost
                        if new_cost < best_cost:
                            best_cost = new_cost
                            best_schedule = current_schedule.copy()
                            best_student_exams = {student: exams.copy() for student, exams in
                                                  current_student_exams.items()}
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
                            student_exams_per_day[student][new_date_str] -= 1
                            if student_exams_per_day[student][new_date_str] == 0:
                                del student_exams_per_day[student][new_date_str]
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
                            student_exams_per_day[student][exam['Date']] += 1
                    found_slot = True
                    break
                if found_slot:
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
                    student_exams_per_day[student][exam['Date']] += 1

            temperature *= cooling_rate
            if iteration % 100 == 0:
                logging.info(
                    f"Итерация {iteration}, текущая стоимость: {current_cost}, лучшая стоимость: {best_cost}")

        # Применяем лучший результат
        self.schedule, student_exams, self.room_usage = best_schedule, best_student_exams, best_room_usage

        # Подсчёт конфликтов после оптимизации и сбор конфликтных студентов
        conflict_count = 0
        conflict_students_list = []  # Список конфликтных студентов
        conflict_details = []  # Детали конфликтов для Excel

        for student, exams in student_exams.items():
            exams_by_date = defaultdict(list)
            for exam in exams:
                exams_by_date[exam['Date']].append(exam)
            for date, daily_exams in exams_by_date.items():
                if len(daily_exams) > 1:
                    conflict_count += 1
                    conflict_students_list.append(student)
                    # Собираем информацию о конфликте
                    conflict_details.append({
                        'Student_ID': student,
                        'Conflict_Date': date,
                        'Exams': '; '.join([f"{exam['Subject']} (Time: {exam['Time_Slot']})" for exam in daily_exams])
                    })
                    break

        logging.info(f"Оптимизация завершена. Лучшая стоимость: {best_cost}")
        logging.info(f"Количество студентов с конфликтами после оптимизации: {conflict_count}")
        if conflict_students_list:
            logging.info(f"Список конфликтных студентов: {conflict_students_list}")
        else:
            logging.info("Конфликтных студентов нет.")

        # Сохранение конфликтных студентов в Excel
        if conflict_details:
            conflict_df = pd.DataFrame(conflict_details)
            output_file = "conflict_students.xlsx"
            try:
                conflict_df.to_excel(output_file, index=False)
                logging.info(f"Конфликтные студенты сохранены в файл: {output_file}")
            except Exception as e:
                logging.error(f"Ошибка при сохранении конфликтных студентов в Excel: {str(e)}")
        else:
            logging.info("Нет конфликтных студентов для сохранения в Excel.")

        return student_exams

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

        if self.schedule_df is not None:
            schedule_info = self.schedule_df[self.schedule_df['Section'] == section_id].to_dict('records')
            if schedule_info:
                section_info['schedule'] = schedule_info[0]
            else:
                section_info['schedule'] = None
        else:
            section_info['schedule'] = None

        section_students = section_data[['fake_id', 'fake_name']].drop_duplicates().to_dict('records')
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
        section_df = pd.DataFrame(section_data)

        # Создаем список студентов в нужном формате
        students_list = [f"ID: {student['fake_id']}, Имя: {student['fake_name']}"
                         for student in section_info['students']]
        students_header = f"Студенты секции ({len(students_list)}):"
        students_df = pd.DataFrame({students_header: students_list})

        # Сохраняем все в один Excel файл
        with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
            section_df.to_excel(writer, sheet_name='Section Info', index=False)
            students_df.to_excel(writer, sheet_name='Section Info', startrow=len(section_df) + 2, index=False,
                                 header=True)

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
        Распределяет студентов по аудиториям с учетом вместимости
        и сохраняет информацию о местах в seat_assignments
        """

        if not hasattr(self, 'schedule_df') or self.schedule_df.empty:
            logging.warning("Нет данных расписания для распределения мест")
            return

        self.seat_assignments = {}  # Очищаем предыдущее распределение
        total_assigned = 0
        problem_sections = []

        # Проходим по всем экзаменам в расписании
        for _, exam in self.schedule_df.iterrows():
            try:
                # Проверяем обязательные поля
                if not all(
                        field in exam for field in ['Date', 'Time_Slot', 'Subject', 'Section', 'Room', 'Instructor']):
                    problem_sections.append(exam.get('Section', 'unknown'))
                    continue

                # Парсим информацию об аудиториях (формат: "230(28) + 233(28)")
                rooms = []
                for room_part in str(exam['Room']).split('+'):
                    try:
                        room_info = room_part.strip().split('(')
                        room_name = room_info[0].strip()
                        capacity = int(room_info[1].replace(')', '')) if len(room_info) > 1 else 25
                        rooms.append((room_name, capacity))
                    except Exception as e:
                        logging.error(f"Ошибка парсинга аудитории '{room_part}': {str(e)}")
                        continue

                # Получаем список студентов для этой секции
                students = self.get_students_for_section(exam['Section'])
                if not students:
                    logging.warning(f"Нет студентов в секции {exam['Section']}")
                    continue

                # Перемешиваем студентов для случайного распределения мест
                random.shuffle(students)

                # Распределяем студентов по местам
                student_index = 0
                for room_name, capacity in rooms:
                    for seat_num in range(1, capacity + 1):
                        if student_index >= len(students):
                            break

                        student_id = str(students[student_index])
                        exam_date = pd.to_datetime(exam['Date']).date()

                        # Создаем уникальный ключ для этого места
                        key = f"{exam_date}|{exam['Time_Slot'].strip()}|{exam['Subject'].strip()}|{student_id}"

                        # Сохраняем информацию о месте
                        self.seat_assignments[key] = {
                            'seat': seat_num,
                        }

                        student_index += 1
                        total_assigned += 1

            except Exception as e:
                section = exam.get('Section', 'unknown')
                problem_sections.append(section)
                logging.error(f"Ошибка распределения мест для секции {section}: {str(e)}")
                continue

        # Логируем результаты
        logging.info(f"Успешно распределено мест: {total_assigned}")
        if problem_sections:
            logging.warning(f"Проблемы в секциях: {set(problem_sections)}")

        # Пример для отладки
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