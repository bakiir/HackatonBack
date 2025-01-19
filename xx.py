import pandas as pd
import networkx as nx
from datetime import datetime, timedelta
import logging

# Настройка логгера
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class ExamScheduler:
    def __init__(self, exams_file, rooms_file, start_date=None, num_days=14):
        logging.info("Инициализация планировщика экзаменов.")
        self.exams_df = pd.read_excel(exams_file)
        self.rooms_df = pd.read_excel(rooms_file)

        self.start_date = datetime.strptime(start_date, '%Y-%m-%d') if start_date else datetime.now()
        self.num_days = num_days
        self.schedule_df = None  # Атрибут для хранения расписания

        self.time_slots = ["09:00-13:00", "14:00-18:00"]
        self.days = [self.start_date + timedelta(days=i) for i in range(self.num_days)]

        # Подготовка данных
        self._prepare_data()

    def _prepare_data(self):
        logging.info("Подготовка данных для планирования.")

        # Группировка экзаменов по секциям
        self.exam_groups = self.exams_df.drop_duplicates(subset=['Section'], keep='first').groupby(
            ['Subject', 'Instructor', 'Course', 'EduProgram', 'YearsOfStudy', 'Section']
        ).agg({'fake_id': 'count'}).reset_index()

        self.instructors = list(self.exam_groups['Instructor'].unique())
        self.rooms = list(self.rooms_df['Аудитория'])
        self.room_capacities = dict(zip(
            self.rooms_df['Аудитория'], self.rooms_df['Вместительность аудитории']
        ))

        # Создаем словарь всех студентов для быстрого доступа
        self.all_students_dict = self.exams_df[['fake_id', 'fake_name']].drop_duplicates().to_dict('records')

        total_slots = len(self.rooms) * len(self.time_slots) * self.num_days
        logging.info(f"Всего экзаменов: {len(self.exam_groups)}")
        logging.info(f"Всего временных слотов: {total_slots}")

    def get_student_sections(self, student_id):
        logging.info(f"Поиск секций для студента {student_id}.")
        student_sections = self.exams_df[self.exams_df['fake_id'] == student_id][
            ['Subject', 'Instructor', 'Course', 'EduProgram', 'YearsOfStudy', 'Section']
        ]

        if student_sections.empty:
            logging.warning(f"Для студента {student_id} не найдено секций.")
            return student_sections

        # Используем общее расписание
        student_schedule = self.schedule_df[self.schedule_df['Section'].isin(student_sections['Section'])]
        return student_schedule

    def create_schedule(self):
        logging.info("Создание графа для планирования.")
        G = nx.Graph()

        # Узлы для экзаменов
        logging.info("Добавление узлов для экзаменов в граф.")
        for i, exam in self.exam_groups.iterrows():
            G.add_node(f"exam_{i}", type="exam", data=exam)

        # Узлы для слотов (комбинация день/время/аудитория)
        slots = []
        logging.info("Добавление узлов для временных слотов в граф.")
        for day in range(self.num_days):
            for room in self.rooms:
                for time_slot in range(len(self.time_slots)):
                    slot_id = f"slot_day_{day}_room_{room}_time_{time_slot}"
                    G.add_node(slot_id, type="slot", day=day, room=room, time_slot=time_slot)
                    slots.append(slot_id)

        # Ребра между экзаменами и слотами
        logging.info("Создание ребер между экзаменами и временными слотами.")
        for i, exam in self.exam_groups.iterrows():
            if i % 100 == 0:
                logging.info(f"Обработка экзамена {i + 1} из {len(self.exam_groups)}")

            # Фильтрация слотов для экзамена
            relevant_slots = [slot for slot in slots if
                              exam['fake_id'] <= self.room_capacities[G.nodes[slot]['room']]]

            for slot_id in relevant_slots:
                G.add_edge(f"exam_{i}", slot_id)

        # Решение задачи с помощью паросочетания
        logging.info("Запуск алгоритма паросочетания.")
        matching = nx.algorithms.matching.max_weight_matching(G, maxcardinality=True)

        # Формирование расписания из результата
        logging.info("Формирование расписания из результата паросочетания.")
        schedule = []
        completed_count = 0
        total_exams = len(self.exam_groups)

        # Словарь для отслеживания количества экзаменов на каждый день
        day_counts = {day: 0 for day in range(self.num_days)}

        # Словарь для отслеживания занятых слотов
        used_slots = set()

        # Сортировка экзаменов по количеству студентов (от большего к меньшему)
        sorted_exams = sorted(self.exam_groups.iterrows(), key=lambda x: x[1]['fake_id'], reverse=True)

        for i, exam in sorted_exams:
            # Найти слот с наименьшим количеством экзаменов в этот день
            min_day = min(day_counts, key=day_counts.get)
            relevant_slots = [slot for slot in slots if
                              G.nodes[slot]['day'] == min_day and
                              exam['fake_id'] <= self.room_capacities[G.nodes[slot]['room']] and
                              slot not in used_slots]

            if not relevant_slots:
                logging.warning(f"Не удалось найти подходящий слот для экзамена {i}.")
                continue

            # Выбираем первый подходящий слот
            slot_id = relevant_slots[0]
            slot_data = G.nodes[slot_id]

            # Исправление назначения даты экзамена
            exam_date = (self.start_date + timedelta(days=slot_data['day'])).strftime('%Y-%m-%d')

            schedule.append({
                'Date': exam_date,
                'Subject': exam['Subject'],
                'Instructor': exam['Instructor'],
                'Course': exam['Course'],
                'EduProgram': exam['EduProgram'],
                'YearsOfStudy': exam['YearsOfStudy'],
                'Section': exam['Section'],
                'Students_Count': exam['fake_id'],
                'Room': slot_data['room'],
                'Time_Slot': self.time_slots[slot_data['time_slot']]
            })

            # Увеличиваем счетчик экзаменов для этого дня
            day_counts[slot_data['day']] += 1

            # Помечаем слот как занятый
            used_slots.add(slot_id)

            completed_count += 1
            logging.info(f"Назначено экзаменов: {completed_count}/{total_exams}")

        # Логирование распределения экзаменов по дням
        logging.info("Распределение экзаменов по дням:")
        for day, count in day_counts.items():
            logging.info(f"День {day + 1}: {count} экзаменов")

        logging.info("Расписание успешно создано.")
        self.schedule_df = pd.DataFrame(schedule)  # Сохраняем расписание в атрибут класса

    def export_schedule(self, output_excel):
        logging.info("Экспорт общего расписания в Excel.")
        self.schedule_df.to_excel(output_excel, index=False)
        logging.info(f"Расписание сохранено в файл {output_excel}.")

    def export_html_schedule(self, output_html):
        logging.info("Экспорт общего расписания в файл HTML.")
        self.schedule_df.to_html(output_html, index=False)
        logging.info(f"Расписание сохранено в файл {output_html}.")

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



    # session info
    def get_section_info(self, section_id):
        """
        Получение полной информации о секции, включая список студентов, участвующих в данной секции.

        Args:
            section_id (str): Идентификатор секции (например, 'KRL 1104-34-Ch')

        Returns:
            dict: Полная информация о секции, включая список студентов и расписание
        """
        logging.info(f"Поиск информации для секции: {section_id}")

        # Получаем данные о секции
        section_data = self.exams_df[self.exams_df['Section'] == section_id]

        if section_data.empty:
            logging.warning(f"Секция {section_id} не найдена")
            return {
                'error': 'Section not found',
                'message': f'Секция {section_id} не найдена в базе данных'
            }

        # Получаем базовую информацию о секции
        section_info = {
            'section_id': section_id,
            'section_info': {
                'instructor': section_data['Instructor'].iloc[0],
                'subject': section_data['Subject'].iloc[0],
                'course': section_data['Course'].iloc[0],
                'edu_program': section_data['EduProgram'].iloc[0],
                'years_of_study': section_data['YearsOfStudy'].iloc[0],
                'total_students': len(section_data)
            }
        }

        # Добавляем информацию о расписании
        if self.schedule_df is not None:
            schedule_info = self.schedule_df[self.schedule_df['Section'] == section_id].to_dict('records')
            if schedule_info:
                section_info['schedule'] = schedule_info[0]
            else:
                section_info['schedule'] = None
        else:
            section_info['schedule'] = None

        # Добавляем список студентов секции
        section_students = section_data[['fake_id', 'fake_name']].drop_duplicates().to_dict('records')
        section_info['students'] = section_students

        logging.info(f"Собрана информация о секции {section_id}: "
                     f"{section_info['section_info']['total_students']} студентов")

        return section_info


    def export_section_info_to_excel(self, section_info, output_file):
        """
        Экспорт информации о секции в Excel файл с основными данными и списком студентов.

        Args:
            section_info (dict): Информация о секции, полученная из метода get_section_info.
            output_file (str): Путь к файлу для сохранения.
        """
        logging.info(f"Экспорт информации о секции в файл {output_file}.")

        # Создаем DataFrame для основной информации о секции
        section_data = {
            'Section ID': [section_info['section_id']],
            'Instructor': [section_info['section_info']['instructor']],
            'Subject': [section_info['section_info']['subject']],
            'Course': [section_info['section_info']['course']],
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


    def print_all_slots(self):
        """Вывод всех доступных слотов с указанием дня, времени и комнаты."""
        print("Доступные слоты:")
        for day in range(self.num_days):
            for room in self.rooms:
                for time_slot in range(len(self.time_slots)):
                    slot_id = f"slot_day_{day}_room_{room}_time_{time_slot}"
                    day_date = (self.start_date + timedelta(days=day)).strftime('%Y-%m-%d')
                    time_range = self.time_slots[time_slot]
                    print(f"Слот ID: {slot_id}, День: {day_date}, Время: {time_range}, Комната: {room}")


if __name__ == "__main__":
    scheduler = ExamScheduler(
        exams_file=r"C:\\Users\\User\\Downloads\\FakedNarxozData (2).xlsx",
        rooms_file=r"C:\\Users\\User\\Downloads\\auditoriums.xlsx",
        start_date='2024-01-15',
        num_days=14
    )

    scheduler.print_all_slots()

    # Создание общего расписания
    scheduler.create_schedule()

    # Экспорт общего расписания в Excel
    output_schedule_file = "general_schedule.xlsx"
    scheduler.export_schedule(output_schedule_file)

    # Вывод расписания для конкретного студента в консоль
    student_id = "Student0001"
    scheduler.print_student_schedule(student_id)

    # Получение информации о секции
    section_id = "KRL 1104-34-Ch"
    section_info = scheduler.get_section_info(section_id)

    # Экспорт информации о секции в Excel
    output_section_file = "section_info.xlsx"
    scheduler.export_section_info_to_excel(section_info, output_section_file)

    # Экспорт расписания для конкретного студента в Excel
    output_student_file = "student_schedule.xlsx"
    scheduler.export_student_schedule_to_excel(student_id, output_student_file)