import json
import random
import pandas as pd
from datetime import datetime, timedelta
import logging


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')



class ExamScheduler:
    def __init__(self, exams_file=None, rooms_file=None, faculties_file=None, start_date=None, num_days=14, title="Сезон беp имени", schedule_data=None, session_data=None):
        logging.info("Инициализация планировщика экзаменов.")
        self.schedule_data = schedule_data
        self.title = title
        self.time_slots = ["08:00-11:00", "11:30-14:30", "15:00-18:00"]
        self.schedule_df = None

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

    def _load_from_session(self, session_data):
        """Загрузка данных из активированной сессии"""
        self.title = session_data.title
        self.start_date = session_data.start_date
        self.num_days = session_data.days
        self.custom_dates = [self.start_date + timedelta(days=i) for i in range(self.num_days)]

        # Если есть данные расписания
        if session_data.schedule_data:
            self.schedule_df = pd.DataFrame(json.loads(session_data.schedule_data))
            self._derive_metadata()

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

        self.exam_groups = self.exams_df.drop_duplicates(subset=['Section'], keep='first').groupby(
            ['Subject', 'Instructor', 'EduProgram', 'YearsOfStudy', 'Section']
        ).agg({'fake_id': 'count'}).reset_index()

        self.instructors = list(self.exam_groups['Instructor'].unique())
        self.rooms = list(self.rooms_df['Аудитория'])
        self.room_capacities = dict(zip(
            self.rooms_df['Аудитория'], self.rooms_df['Вместительность аудитории']
        ))

        self.all_students_dict = self.exams_df[['fake_id', 'fake_name']].drop_duplicates().to_dict('records')

        self.subject_faculty_map = self.faculties_df.groupby('Subject')['Faculty'].apply(set).to_dict()

        self.faculty_proctors = self.faculties_df.groupby('Faculty')['Instructor'].apply(list).to_dict()

        total_slots = len(self.rooms) * len(self.time_slots) * self.original_num_days
        logging.info(f"Всего экзаменов: {len(self.exam_groups)}")
        logging.info(f"Всего временных слотов: {total_slots}")

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

        if self.schedule_df is None:
            logging.warning("Расписание не создано. Сначала создайте расписание.")
            return pd.DataFrame()

        student_sections = self.exams_df[self.exams_df['fake_id'] == student_id]['Section'].unique()

        if len(student_sections) == 0:
            logging.warning(f"Для студента {student_id} не найдено секций.")
            return pd.DataFrame()

        student_schedule = self.schedule_df[self.schedule_df['Section'].isin(student_sections)]

        if not student_schedule.empty:
            student_schedule = student_schedule.sort_values(['Date', 'Time_Slot'])

        return student_schedule



    def assign_proctors(self):
        logging.info("Назначение прокторов.")
        assigned_proctors = []

        for _, row in self.schedule_df.iterrows():
            subject = row['Subject']
            exam_faculty = list(self.subject_faculty_map.get(subject, []))

            if not exam_faculty:
                assigned_proctors.append(None)
                continue

            exam_faculty = exam_faculty[0]

            if exam_faculty == 'ШЦТ':
                possible_proctors = self.faculty_proctors.get('ШЦТ', [])
            else:
                possible_proctors = [
                    instr for fac, instrs in self.faculty_proctors.items()
                    if fac not in (exam_faculty, 'ШЦТ') for instr in instrs
                ]

            assigned_proctors.append(random.choice(possible_proctors) if possible_proctors else None)

        self.schedule_df['Proctor'] = assigned_proctors
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




    def create_schedule(self):
        logging.info("Создание расписания")
        schedule = []
        student_exams_per_day = {}
        failed_sections = []

        self.room_availability = {
            day: {slot: set() for slot in self.time_slots}
            for day in self.custom_dates
        }

        sections_df = self.exam_groups.copy()
        sections_df['student_count'] = sections_df['Section'].map(
            self.exams_df.groupby('Section')['fake_id'].nunique()
        )
        sections = sections_df.sort_values(by='student_count', ascending=False)['Section'].unique()

        scheduled_sections = set()

        slot_usage_count = {
            slot: 0 for slot in self.time_slots
        }

        for section in sections:
            if section in scheduled_sections:
                continue

            section_data = self.exam_groups[self.exam_groups['Section'] == section].iloc[0]
            section_students = self.exams_df[self.exams_df['Section'] == section]['fake_id'].unique()
            num_students = len(section_students)

            best_slots = []

            for day in self.custom_dates:
                exam_date = day.strftime('%Y-%m-%d')

                time_slots = list(self.time_slots)
                random.shuffle(time_slots)

                for time_slot in time_slots:
                    student_conflicts = sum(
                        1 for student in section_students
                        if student_exams_per_day.get(student, {}).get(exam_date, 0) == 1
                    )

                    available_rooms = [
                        room for room in self.rooms
                        if (num_students <= self.room_capacities[room] and
                            room not in self.room_availability[day][time_slot])
                    ]

                    if available_rooms:
                        best_room = min(
                            available_rooms,
                            key=lambda r: abs(self.room_capacities[r] - num_students)
                        )

                        best_slots.append((day, time_slot, best_room, student_conflicts, slot_usage_count[time_slot]))

            if best_slots:
                best_slots.sort(key=lambda x: x[3])

                min_conflicts = best_slots[0][3]
                best_slots_with_min_conflicts = [slot for slot in best_slots if slot[3] == min_conflicts]

                best_day, best_slot, best_room, min_conflicts, _ = min(
                    best_slots_with_min_conflicts,
                    key=lambda x: x[4]
                )

                slot_usage_count[best_slot] += 1

                exam_date = best_day.strftime('%Y-%m-%d')

                schedule.append({
                    'Date': exam_date,
                    'Subject': section_data['Subject'],
                    'Instructor': section_data['Instructor'],
                    'EduProgram': section_data['EduProgram'],
                    'Section': section,
                    'Students_Count': num_students,
                    'Room': best_room,
                    'Time_Slot': best_slot,
                    'Student_Conflicts': min_conflicts
                })

                # Обновляем занятость
                for student in section_students:
                    student_exams_per_day.setdefault(student, {})[exam_date] = 1
                self.room_availability[best_day][best_slot].add(best_room)
                scheduled_sections.add(section)

            else:
                failed_sections.append({
                    'section': section,
                    'reason': "Не найдено подходящих слотов в основные дни",
                    'students_count': num_students
                })

        schedule.sort(key=lambda x: (x['Date'], x['Time_Slot']))

        slot_usage = {day: {slot: 0 for slot in self.time_slots} for day in self.custom_dates}
        for exam in schedule:
            day = datetime.strptime(exam['Date'], '%Y-%m-%d')
            slot_usage[day][exam['Time_Slot']] += 1

        for day in self.custom_dates:
            total_slots = len(self.time_slots)
            used_slots = sum(1 for slot in self.time_slots if slot_usage[day][slot] > 0)
            logging.info(f"День {day.strftime('%Y-%m-%d')}: использовано {used_slots} из {total_slots} слотов")
            for slot in self.time_slots:
                logging.info(f"  Слот {slot}: {slot_usage[day][slot]} экзаменов")

        logging.info("Общая статистика использования слотов:")
        for slot in self.time_slots:
            total = sum(slot_usage[day][slot] for day in self.custom_dates)
            logging.info(f"  Слот {slot}: {total} экзаменов")

        total_sections = len(sections)
        successful_sections = len(scheduled_sections)
        logging.info(f"Успешно запланировано экзаменов: {successful_sections} из {total_sections}")

        if failed_sections:
            logging.warning(f"Не удалось запланировать {len(failed_sections)} экзаменов:")
            for failed in failed_sections:
                logging.warning(f"Секция: {failed['section']}, Причина: {failed['reason']}")

        self.schedule_df = pd.DataFrame(schedule)
        self.assign_proctors()


    def export_schedule(self, output_excel):
        logging.info("Экспорт общего расписания в Excel.")
        self.schedule_df.to_excel(output_excel, index=False)
        logging.info(f"Расписание сохранено в файл {output_excel}.")


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

    def load_from_session(self, session_data: dict):
        """Загрузка расписания из сохраненной сессии"""
        self.schedule_df = pd.DataFrame(session_data['schedule'])
        self._prepare_data()
