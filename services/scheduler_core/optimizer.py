import logging
import random
import math
from collections import defaultdict
from datetime import datetime, timedelta
import pandas as pd
from .utils import check_overlap

class SimulatedAnnealingOptimizer:
    """
    Класс для оптимизации расписания методом имитации отжига (Simulated Annealing).
    Сфокусирован на минимизации конфликтов у студентов.
    """
    def __init__(self, scheduler):
        self.scheduler = scheduler

    def optimize(self, student_exams):
        """
        Основной метод оптимизации.
        """
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
                        # Если на один день попало больше 1 экзамена, 
                        # все эти секции считаем кандидатами на перенос
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

        current_cost = self.calculate_total_conflicts(student_exams)
        logging.info(f"[Оптимизация, старт] Начальная стоимость (конфликты): {current_cost}")

        if current_cost == 0:
            logging.info("Конфликтов не найдено, оптимизация не требуется.")
            return student_exams, []

        conflicting_indices = get_conflicting_exam_indices(self.scheduler.schedule, student_exams)

        for i in range(max_iterations):
            if not conflicting_indices:
                logging.info(f"Все конфликты разрешены на итерации {i}.")
                current_cost = 0
                break

            schedule_idx = random.choice(conflicting_indices)
            exam_to_move = self.scheduler.schedule[schedule_idx].copy()

            # Pinned экзамены не трогаем
            if exam_to_move.get('pinned'):
                continue

            # Пробуем найти новый слот в случайный день
            new_day = random.choice(self.scheduler.custom_dates)
            new_slot_info = self._find_free_slot_for_exam(new_day.strftime('%Y-%m-%d'), exam_to_move)

            if new_slot_info and new_slot_info['Date'] != exam_to_move['Date']:
                delta_cost = self._calculate_move_delta_cost(exam_to_move, new_slot_info, student_exams)

                # Критерий принятия по алгоритму Метрополиса
                if delta_cost < 0 or (temperature > 0 and random.random() < math.exp(-delta_cost / temperature)):
                    # Применяем перенос
                    self._release_slot(schedule_idx)
                    self._move_exam(schedule_idx, new_slot_info, student_exams)
                    
                    current_cost += delta_cost
                    no_improvement_streak = 0
                    if i % 100 == 0 or delta_cost < 0:
                        logging.info(f"[Итерация {i}] Изменение стоимости: {delta_cost}. Новая стоимость: {current_cost}.")
                    
                    # Периодически обновляем список конфликтующих для точности (т.к. мы могли разрешить или создать новые)
                    if i % 500 == 0:
                         conflicting_indices = get_conflicting_exam_indices(self.scheduler.schedule, student_exams)
                else:
                    no_improvement_streak += 1
            else:
                no_improvement_streak += 1

            temperature *= cooling_rate

            if no_improvement_streak > max_no_improvement:
                logging.warning(f"Оптимизация остановлена из-за отсутствия улучшений в течение {max_no_improvement} итераций.")
                break
        
        # По требованию: считаем конфликтом даже ситуации, когда у студента 2 и более экзаменов в день (с нахлестом или без)
        final_conflicting_students = {sid for sid, exams in student_exams.items() if self.calculate_total_conflicts({sid: exams}) > 0}
        logging.info(f"Оптимизация завершена. Финальная стоимость: {current_cost}. Студентов с конфликтами (2+ в день или нахлест): {len(final_conflicting_students)}")
        
        return student_exams, list(final_conflicting_students)

    def calculate_total_conflicts(self, student_exams_dict):
        """Вычисляет общее количество конфликтов в расписании."""
        total_conflicts = 0
        for student_id, exams in student_exams_dict.items():
            exams_on_days = defaultdict(list)
            for exam in exams:
                if exam.get('Date') and exam['Date'] != 'N/A':
                    exams_on_days[exam['Date']].append(exam)

            for date, daily_exams in exams_on_days.items():
                if len(daily_exams) > 1:
                    # Проверяем реальные пересечения во времени внутри одного дня
                    for i in range(len(daily_exams)):
                        for j in range(i + 1, len(daily_exams)):
                            exam1 = daily_exams[i]
                            exam2 = daily_exams[j]
                            if check_overlap(exam1.get('Time_Slot'), exam2.get('Time_Slot')):
                                total_conflicts += 1000
                    
                    # Дополнительно штрафуем просто за наличие 2+ экзаменов в день (soft conflict)
                    # Это можно настроить, сейчас штраф = 1 за каждую доп. секцию сверх первой
                    total_conflicts += (len(daily_exams) - 1) 
                    
        return total_conflicts


    def _calculate_move_delta_cost(self, exam_to_move, new_slot_info, student_exams):
        """Вычисляет изменение 'стоимости' (количества конфликтов) при переносе экзамена."""
        section_id = exam_to_move['Section']
        students = self.scheduler.section_students_map.get(section_id, [])
        old_day_str = exam_to_move['Date']
        new_day_str = new_slot_info['Date']
        new_time_slot_str = new_slot_info['Time_Slot']
        
        delta_cost = 0
        
        for sid in students:
            exams_all = student_exams.get(sid, [])
            
            # --- OLD DAY COST ---
            exams_in_old_day = [e for e in exams_all if e['Date'] == old_day_str]
            old_cost_before = max(0, len(exams_in_old_day) - 1)
            for i in range(len(exams_in_old_day)):
                for j in range(i+1, len(exams_in_old_day)):
                    if check_overlap(exams_in_old_day[i].get('Time_Slot'), exams_in_old_day[j].get('Time_Slot')):
                       old_cost_before += 1000
                       
            exams_in_old_after = [e for e in exams_in_old_day if e['Section'] != section_id]
            old_cost_after = max(0, len(exams_in_old_after) - 1)
            for i in range(len(exams_in_old_after)):
                for j in range(i+1, len(exams_in_old_after)):
                    if check_overlap(exams_in_old_after[i].get('Time_Slot'), exams_in_old_after[j].get('Time_Slot')):
                       old_cost_after += 1000
                       
            delta_cost += (old_cost_after - old_cost_before)

            # --- NEW DAY COST ---
            exams_in_new_day = [e for e in exams_all if e['Date'] == new_day_str and e['Section'] != section_id]
            new_cost_before = max(0, len(exams_in_new_day) - 1)
            for i in range(len(exams_in_new_day)):
                for j in range(i+1, len(exams_in_new_day)):
                    if check_overlap(exams_in_new_day[i].get('Time_Slot'), exams_in_new_day[j].get('Time_Slot')):
                       new_cost_before += 1000
                       
            exams_in_new_after = exams_in_new_day + [{'Time_Slot': new_time_slot_str}]
            new_cost_after = max(0, len(exams_in_new_after) - 1)
            for i in range(len(exams_in_new_after)):
                for j in range(i+1, len(exams_in_new_after)):
                    if check_overlap(exams_in_new_after[i].get('Time_Slot'), exams_in_new_after[j].get('Time_Slot')):
                       new_cost_after += 1000
                       
            delta_cost += (new_cost_after - new_cost_before)
            
        return delta_cost

    def _find_free_slot_for_exam(self, day_str, exam_rec):
        """Ищет подходящий свободный слот для экзамена в указанный день."""
        time_step_minutes = self.scheduler.time_step
        work_day_start_dt = self.scheduler.work_day_start
        # Используем значение из планировщика
        num_blocks_in_day = self.scheduler.num_blocks_in_day

        duration_minutes = exam_rec['Duration']
        exam_blocks = math.ceil(duration_minutes / time_step_minutes)
        buffer_blocks = math.ceil(self.scheduler.buffer_time / time_step_minutes)
        total_blocks_needed = exam_blocks + buffer_blocks
        
        section_id = exam_rec['Section']
        students = self.scheduler.section_students_map.get(section_id, [])
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

            # Проверка доступности преподавателя
            if not self.scheduler.constraint_engine.is_instructor_available(instructor, day_str, exam_time_slot_str, exam_rec):
                continue
                
            # Проверка на жесткие нахлесты у студентов
            if not all(self.scheduler.constraint_engine.is_student_available(s, day_str, time_slot=exam_time_slot_str, strict=False) for s in students):
                continue

            # Проверка доступности хотя бы одной комнаты (или двух если нужно)
            grid_available_rooms = [
                r for r in self.scheduler.rooms 
                if r in self.scheduler.room_availability_grid[day_str] and 
                not any(self.scheduler.room_availability_grid[day_str][r][i] for i in range(start_block, end_block_with_buffer))
            ]
            
            # Проверка исключений аудиторий
            available_rooms = [
                r for r in grid_available_rooms
                if not self.scheduler.constraint_engine.is_room_excluded(r, day_obj.date(), exam_start_dt, exam_end_dt)
            ]

            classroom_type = exam_rec.get('classroom_type', 'regular')
            final_room_str, rooms_to_book = self.scheduler.constraint_engine.find_suitable_rooms(available_rooms, num_students, exam_rec)

            if final_room_str:
                return {
                    'Date': day_str, 
                    'Time_Slot': exam_time_slot_str, 
                    'Room': final_room_str, 
                    'start_block': start_block, 
                    'rooms_to_book': rooms_to_book
                }
        return None

    def _move_exam(self, schedule_idx, new_slot_info, student_exams):
        """Механически перемещает экзамен в новый слот."""
        time_step_minutes = self.scheduler.time_step
        num_blocks_in_day = self.scheduler.num_blocks_in_day
        
        # 1. Занимаем новый слот
        new_day = new_slot_info['Date']
        new_start_block = new_slot_info['start_block']
        new_rooms = new_slot_info['rooms_to_book']
        
        # Получаем параметры длительности из записи
        exam_rec = self.scheduler.schedule[schedule_idx]
        duration_minutes = exam_rec['Duration']
        exam_blocks = math.ceil(duration_minutes / time_step_minutes)
        buffer_blocks = math.ceil(self.scheduler.buffer_time / time_step_minutes)
        total_blocks_needed = exam_blocks + buffer_blocks

        for room in new_rooms:
            if room in self.scheduler.room_availability_grid[new_day]:
                for i in range(new_start_block, new_start_block + total_blocks_needed):
                    if i < num_blocks_in_day: self.scheduler.room_availability_grid[new_day][room][i] = True

        # 2. Обновляем записи в расписании
        exam_rec['Date'] = new_slot_info['Date']
        exam_rec['Time_Slot'] = new_slot_info['Time_Slot']
        exam_rec['Room'] = new_slot_info['Room']

        # 3. Обновляем записи у студентов (т.к. у студентов хранятся ссылки на те же словари)
        # В текущей реализации ExamScheduler.student_exams хранит ссылки, 
        # но на всякий случай проверяем и обновляем если нужно.
        # index-based update usually sufficient since they are internal references.

    def _release_slot(self, schedule_idx):
        """Освобождает слот, занимаемый экзаменом."""
        exam_rec = self.scheduler.schedule[schedule_idx]
        day_str = exam_rec['Date']
        rooms = str(exam_rec['Room']).split(',')
        duration = exam_rec['Duration']
        
        time_step = self.scheduler.time_step
        num_blocks_in_day = self.scheduler.num_blocks_in_day
        
        exam_blocks = math.ceil(duration / time_step)
        buffer_blocks = math.ceil(self.scheduler.buffer_time / time_step)
        total_blocks_needed = exam_blocks + buffer_blocks
        
        start_h, start_m = map(int, exam_rec['Time_Slot'].split('-')[0].split(':'))
        start_dt = self.scheduler.work_day_start.replace(hour=start_h, minute=start_m)
        start_block = int((start_dt - self.scheduler.work_day_start).total_seconds() / 60 / time_step)

        for room in rooms:
            room = room.strip()
            if room in self.scheduler.room_availability_grid.get(day_str, {}):
                for i in range(start_block, start_block + total_blocks_needed):
                    if i < num_blocks_in_day: self.scheduler.room_availability_grid[day_str][room][i] = False
