import logging
import pandas as pd
import re
import random

class SeatingManager:
    """
    Управляет распределением студентов по местам в аудиториях.
    """
    def __init__(self, scheduler):
        self.scheduler = scheduler

    def assign_seats(self):
        """
        Распределяет студентов по аудиториям с учетом вместимости и требования двух комнат,
        сохраняя информацию о местах в seat_assignments.
        """
        if not hasattr(self.scheduler, 'schedule_df') or self.scheduler.schedule_df.empty:
            logging.warning("Нет данных расписания для распределения мест")
            return

        # Log rooms_df contents
        logging.info(
            f"Содержимое rooms_df: {self.scheduler.rooms_df.to_dict() if not self.scheduler.rooms_df.empty else 'Пустой DataFrame'}"
        )
        logging.info(
            f"Колонки rooms_df: {list(self.scheduler.rooms_df.columns) if not self.scheduler.rooms_df.empty else 'Нет колонок'}"
        )

        # Кэшируем room_capacities с преобразованием к int
        capacity_column = (
            'Вместительность аудитории'
            if 'Вместительность аудитории' in self.scheduler.rooms_df.columns
            else 'Capacity'
        )
        room_capacities = {}
        if not self.scheduler.rooms_df.empty and capacity_column in self.scheduler.rooms_df.columns:
            for _, row in self.scheduler.rooms_df.iterrows():
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

        self.scheduler.seat_assignments = {}
        total_assigned = 0
        problem_sections = []

        for _, exam in self.scheduler.schedule_df.iterrows():
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

                students = self.scheduler.get_students_for_section(exam['Section'])
                if not students:
                    logging.warning(f"Нет студентов в секции {exam['Section']}")
                    continue

                two_rooms_needed = (
                    self.scheduler.exam_groups[
                        self.scheduler.exam_groups['Section'] == exam['Section']
                    ]['two_rooms_needed'].iloc[0]
                    if exam['Section'] in self.scheduler.exam_groups['Section'].values
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
                        self.scheduler.seat_assignments[key] = {
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
        if self.scheduler.seat_assignments:
            sample_key = next(iter(self.scheduler.seat_assignments))
            logging.info(f"Пример распределения: {sample_key} => {self.scheduler.seat_assignments[sample_key]}")

    def get_seat_assignment(self, student_id, exam_date, time_slot, subject):
        """Возвращает информацию о месте студента"""
        key = f"{exam_date}|{time_slot}|{subject}|{str(student_id)}"
        return self.scheduler.seat_assignments.get(key, {'room': 'Not assigned', 'seat': None})
