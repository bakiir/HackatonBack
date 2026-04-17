import logging
import math
import traceback
from datetime import datetime, timedelta
from itertools import combinations
import pandas as pd
from .utils import get_exam_type, check_overlap

class Planner:
    """
    Основной движок планирования (Core Planning Engine).
    Реализует 3-этапный алгоритм распределения экзаменов.
    """
    def __init__(self, scheduler):
        self.scheduler = scheduler

    def create_schedule(self):
        """
        Основной цикл планирования.
        """
        logging.info("Запуск основного цикла планирования (3 этапа).")
        
        # Расчет количества блоков в дне
        total_dur = (self.scheduler.work_day_end - self.scheduler.work_day_start).total_seconds() / 60
        self.scheduler.num_blocks_in_day = int(total_dur / self.scheduler.time_step)

        # Загрузка исключений аудиторий
        from create_db import RoomExclusion, Session
        sess = Session()
        try:
            excls = sess.query(RoomExclusion).all()
            self.scheduler.exclusions_by_date = {}
            for ex in excls:
                d_key = ex.exclusion_date
                if d_key not in self.scheduler.exclusions_by_date:
                    self.scheduler.exclusions_by_date[d_key] = []
                self.scheduler.exclusions_by_date[d_key].append(ex)
        except Exception as e:
            logging.error(f"Ошибка при загрузке исключений: {e}")
            self.scheduler.exclusions_by_date = {}
        finally:
            sess.close()

        # Сброс текущего состояния перед планированием
        self.scheduler.schedule = []
        self.scheduler.student_exams = {}
        # Инициализация сетки доступности
        self.scheduler.room_availability_grid = {
            day.strftime('%Y-%m-%d'): {
                str(room): [False] * self.scheduler.num_blocks_in_day 
                for room in self.scheduler.rooms
            }
            for day in self.scheduler.custom_dates
        }
        self.scheduler.exams_per_day_count = {
            day.strftime('%Y-%m-%d'): 0 
            for day in self.scheduler.custom_dates
        }

        try:
            # Загрузка ручных броней
            manual_exams, scheduled_sections = self._load_manual_bookings()
            self.scheduler.schedule.extend(manual_exams)
            for exam in manual_exams:
                section_id = exam['Section']
                students = self.scheduler.section_students_map.get(section_id, [])
                for sid in students:
                    if sid not in self.scheduler.student_exams:
                        self.scheduler.student_exams[sid] = []
                    self.scheduler.student_exams[sid].append(exam)
                day_str = exam['Date']
                self.scheduler.exams_per_day_count[day_str] = self.scheduler.exams_per_day_count.get(day_str, 0) + 1

            # Этап 1: Приоритетное планирование для 107 аудитории
            remaining_groups, scheduled_107_count = self._schedule_large_groups_in_107(self.scheduler.exam_groups)
            
            # Предварительная фильтрация: планируем только те, у кого has_exam=True и которые еще не запланированы
            exams_to_process = remaining_groups[
                (remaining_groups['has_exam'] == True) & 
                (~remaining_groups['Section'].isin(scheduled_sections))
            ].copy()
            
            logging.info(f"--- Этап 1: Строгое планирование для {len(exams_to_process)} секций ---")
            
            self.scheduler.failed_sections = []
            
            # Сортировка по количеству студентов (сначала большие)
            exams_to_process['student_count'] = exams_to_process['Section'].map(
                lambda x: len(self.scheduler.section_students_map.get(x, []))
            )
            exams_to_process = exams_to_process.sort_values('student_count', ascending=False)

            # Проход по секциям
            for _, group in exams_to_process.iterrows():
                section_id = group['Section']
                students = self.scheduler.section_students_map.get(section_id, [])
                num_students = group['student_count']
                duration_minutes = int(group.get('Duration', 180))
                instructor = group['Instructor']
                two_rooms_needed = group.get('two_rooms_needed', False)
                
                # Если нужно 2 комнаты, удваиваем требуемую вместимость для поиска (упрощенно)
                effective_capacity = num_students * 2 if two_rooms_needed else num_students
                
                exam_blocks = math.ceil(duration_minutes / self.scheduler.time_step)
                buffer_blocks = math.ceil(self.scheduler.buffer_time / self.scheduler.time_step)
                total_blocks_needed = exam_blocks + buffer_blocks

                possible_slots = []
                # Ищем по дням
                for day in self.scheduler.custom_dates:
                    day_str = day.strftime('%Y-%m-%d')
                    
                    # Проверка студентов (Строго: не более 1 экзамена в день)
                    if not all(self._is_student_available_for_exam(s_id, day_str, group.to_dict()) for s_id in students):
                        continue

                    # Ищем окно в сетке
                    for start_block in range(self.scheduler.num_blocks_in_day - total_blocks_needed + 1):
                        day_start_dt = datetime.combine(day, self.scheduler.work_day_start.time())
                        exam_start_dt = day_start_dt + timedelta(minutes=start_block * self.scheduler.time_step)
                        exam_end_dt = exam_start_dt + timedelta(minutes=duration_minutes)
                        exam_time_slot_str = f"{exam_start_dt.strftime('%H:%M')}-{exam_end_dt.strftime('%H:%M')}"

                        # Проверка преподавателя
                        if not self._is_instructor_available(instructor, day, exam_time_slot_str, group.to_dict()):
                            continue

                        # Проверка аудиторий
                        grid_available_rooms = [
                            r for r in self.scheduler.rooms 
                            if str(r) in self.scheduler.room_availability_grid[day_str] and 
                            not any(self.scheduler.room_availability_grid[day_str][str(r)][i] for i in range(start_block, start_block + total_blocks_needed))
                        ]
                        
                        # Фильтр исключений
                        available_rooms = [
                            r for r in grid_available_rooms
                            if not self._is_room_excluded(r, day, exam_start_dt, exam_end_dt)
                        ]

                        classroom_type = group.get('classroom_type', 'regular')
                        final_room_str, rooms_to_book = self._find_suitable_rooms(available_rooms, effective_capacity, group.to_dict(), classroom_type)
                        
                        if final_room_str:
                            possible_slots.append({
                                'day_str': day_str,
                                'start_block': start_block,
                                'time_slot_str': exam_time_slot_str,
                                'room_str': final_room_str,
                                'rooms_to_book': rooms_to_book
                            })
                            # Для Этапа 1 берем первый попавшийся слот (жадный подход)
                            break
                    if possible_slots:
                        break

                if not possible_slots:
                    self.scheduler.failed_sections.append(group.to_dict())
                else:
                    # Выбираем лучший слот (по загруженности дня)
                    best_slot = min(possible_slots, key=lambda s: (self.scheduler.exams_per_day_count[s['day_str']], s['start_block']))
                    
                    self._book_slot(best_slot['day_str'], best_slot['start_block'], total_blocks_needed, best_slot['rooms_to_book'], self.scheduler.num_blocks_in_day)
                    exam_record = self._create_exam_record(group, best_slot['day_str'], best_slot['time_slot_str'], best_slot['room_str'], num_students)
                    self.scheduler.schedule.append(exam_record)
                    
                    for s_id in students:
                        if s_id not in self.scheduler.student_exams:
                            self.scheduler.student_exams[s_id] = []
                        self.scheduler.student_exams[s_id].append(exam_record)
                    self.scheduler.exams_per_day_count[best_slot['day_str']] += 1

            logging.info(f"--- Этап 2: Гибкое планирование для {len(self.scheduler.failed_sections)} сложных секций ---")
            self._schedule_failed_sections_flexible()

            if self.scheduler.failed_sections:
                logging.info(f"--- Этап 2.5: Смягченное планирование для {len(self.scheduler.failed_sections)} секций ---")
                self._schedule_failed_sections_relaxed()

            if self.scheduler.failed_sections:
                logging.info(f"--- Этап 2.7: Планирование с 'вытеснением' для {len(self.scheduler.failed_sections)} секций ---")
                self._schedule_failed_sections_push_out()

            # Обработка секций без экзамена (has_exam == False)
            all_groups = self.scheduler.exam_groups
            no_exam_groups = all_groups[all_groups['has_exam'] == False]
            if not no_exam_groups.empty and self.scheduler.custom_dates:
                import random
                for _, group in no_exam_groups.iterrows():
                    section_id = group['Section']
                    num_students = len(self.scheduler.section_students_map.get(section_id, []))
                    self.scheduler.schedule.append({
                        'Date': random.choice(self.scheduler.custom_dates).strftime('%Y-%m-%d'),
                        'Subject': group['Subject'],
                        'Instructor': group['Instructor'],
                        'EduProgram': group['EduProgram'],
                        'Section': section_id,
                        'Students_Count': num_students,
                        'Room': 'N/A',
                        'Time_Slot': 'N/A',
                        'Duration': 0,
                        'proctor_needed': False,
                        'two_rooms_needed': False,
                        'pinned': True
                    })

            # Этап 3: Оптимизация расписания (Имитация отжига)
            logging.info("--- Этап 3: Оптимизация расписания (Simulated Annealing) ---")
            self.scheduler.schedule, _ = self.scheduler.optimize_schedule(self.scheduler.student_exams)

            self.scheduler.schedule_df = pd.DataFrame(self.scheduler.schedule)
            logging.info(f"Планирование завершено. Итог: {len(self.scheduler.schedule)} секций запланировано, {len(self.scheduler.failed_sections)} не удалось.")
            
        except Exception as e:
            logging.error(f"Ошибка при создании расписания: {traceback.format_exc()}")
            raise

    def _schedule_large_groups_in_107(self, exam_groups_df):
        from create_db import ClassroomSlot, Session
        import json

        logging.info("Запуск приоритетного планирования для аудитории 107.")

        # --- Правило: Только для "письменных" экзаменов ---
        written_exam_mask = (
            (exam_groups_df['has_exam'] == True) &
            (exam_groups_df['proctor_needed'] == True) &
            (exam_groups_df['two_rooms_needed'] == True)
        )
        
        written_exams_to_process = exam_groups_df[written_exam_mask].copy()
        other_exams = exam_groups_df[~written_exam_mask]

        if written_exams_to_process.empty:
            logging.info("Не найдено 'письменных' экзаменов для 107.")
            return exam_groups_df, 0

        session = Session()
        try:
            written_exams_to_process['student_count'] = written_exams_to_process['Section'].map(
                lambda x: len(self.scheduler.section_students_map.get(x, []))
            )
            
            subject_groups = written_exams_to_process.groupby('Subject').agg(
                total_students=('student_count', 'sum'),
                section_count=('Section', 'count'),
                sections=('Section', lambda x: list(x))
            ).reset_index()

            candidates = subject_groups[
                (subject_groups['section_count'].between(2, 4)) &
                (subject_groups['total_students'].between(80, 200))
            ].sort_values('total_students', ascending=False)

            if candidates.empty:
                return exam_groups_df, 0

            free_slots = session.query(ClassroomSlot).filter_by(
                classroom_number='107', is_booked=False
            ).order_by(ClassroomSlot.start_time).all()

            if not free_slots:
                return exam_groups_df, 0

            scheduled_sections = set()
            scheduled_count = 0

            for _, candidate_row in candidates.iterrows():
                sections_to_schedule = candidate_row['sections']
                if any(s in scheduled_sections for s in sections_to_schedule):
                    continue

                all_students_in_group = set()
                for section_id in sections_to_schedule:
                    all_students_in_group.update(self.scheduler.section_students_map.get(section_id, []))

                for slot in free_slots:
                    if slot.is_booked:
                        continue

                    has_conflict = False
                    slot_date_str = slot.start_time.strftime('%Y-%m-%d')
                    for student in all_students_in_group:
                        if student in self.scheduler.student_exams:
                            if any(e['Date'] == slot_date_str for e in self.scheduler.student_exams[student]):
                                has_conflict = True
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
                            'proctor_needed': group_info.get('proctor_needed', False)
                        }
                        self.scheduler.schedule.append(exam_record)

                        section_students_list = self.scheduler.section_students_map.get(section_id, [])
                        for student in section_students_list:
                            if student not in self.scheduler.student_exams:
                                self.scheduler.student_exams[student] = []
                            self.scheduler.student_exams[student].append(exam_record)

                        scheduled_sections.add(section_id)
                        scheduled_count += 1
                        
                    free_slots.remove(slot)
                    break

            session.commit()
            remaining_written = written_exams_to_process[~written_exams_to_process['Section'].isin(scheduled_sections)]
            final_remaining_df = pd.concat([remaining_written, other_exams], ignore_index=True)
            if 'student_count' in final_remaining_df.columns:
                final_remaining_df = final_remaining_df.drop(columns=['student_count'])
            
            return final_remaining_df, scheduled_count
        except Exception as e:
            logging.error(f"Ошибка в 107 планировании: {traceback.format_exc()}")
            session.rollback()
            return exam_groups_df, 0
        finally:
            session.close()

    def _schedule_failed_sections_flexible(self):
        """Этап 2: Позволяет 2 экзамена в день, если они не пересекаются по времени."""
        sections_to_process = list(self.scheduler.failed_sections)
        self.scheduler.failed_sections = []

        for group_dict in sections_to_process:
            group = pd.Series(group_dict)
            students = self.scheduler.section_students_map.get(group['Section'], [])
            num_students = len(students)
            duration_minutes = int(group.get('Duration', 180))
            instructor = group['Instructor']
            two_rooms_needed = group.get('two_rooms_needed', False)
            
            effective_capacity = num_students * 2 if two_rooms_needed else num_students
            exam_blocks = math.ceil(duration_minutes / self.scheduler.time_step)
            buffer_blocks = math.ceil(self.scheduler.buffer_time / self.scheduler.time_step)
            total_blocks_needed = exam_blocks + buffer_blocks

            possible_slots = []
            for day in self.scheduler.custom_dates:
                day_str = day.strftime('%Y-%m-%d')
                
                # Проверка: не более 2 экзаменов в день и отсутствие наложения по времени
                if not all(self._is_student_available_with_time_check(s_id, day_str, None) for s_id in students): # time_slot check inside
                    continue

                for start_block in range(self.scheduler.num_blocks_in_day - total_blocks_needed + 1):
                    day_start_dt = datetime.combine(day, self.scheduler.work_day_start.time())
                    exam_start_dt = day_start_dt + timedelta(minutes=start_block * self.scheduler.time_step)
                    exam_end_dt = exam_start_dt + timedelta(minutes=duration_minutes)
                    exam_time_slot_str = f"{exam_start_dt.strftime('%H:%M')}-{exam_end_dt.strftime('%H:%M')}"

                    # Дополнительная проверка наложения по времени для всех студентов
                    if not all(self._is_student_available_with_time_check(s_id, day_str, exam_time_slot_str) for s_id in students):
                        continue

                    if not self._is_instructor_available(instructor, day, exam_time_slot_str, group.to_dict()):
                        continue

                    grid_available_rooms = [
                        r for r in self.scheduler.rooms 
                        if str(r) in self.scheduler.room_availability_grid[day_str] and 
                        not any(self.scheduler.room_availability_grid[day_str][str(r)][i] for i in range(start_block, start_block + total_blocks_needed))
                    ]
                    
                    available_rooms = [
                        r for r in grid_available_rooms
                        if not self._is_room_excluded(r, day, exam_start_dt, exam_end_dt)
                    ]

                    classroom_type = group.get('classroom_type', 'regular')
                    final_room_str, rooms_to_book = self._find_suitable_rooms(available_rooms, effective_capacity, group.to_dict(), classroom_type)
                    
                    if final_room_str:
                        possible_slots.append({
                            'day_str': day_str, 'start_block': start_block, 'time_slot_str': exam_time_slot_str, 
                            'room_str': final_room_str, 'rooms_to_book': rooms_to_book
                        })
                        break # Нашли слот в этот день
                if possible_slots: break

            if not possible_slots:
                self.scheduler.failed_sections.append(group_dict)
            else:
                best_slot = min(possible_slots, key=lambda s: (self.scheduler.exams_per_day_count[s['day_str']], s['start_block']))
                self._book_slot(best_slot['day_str'], best_slot['start_block'], total_blocks_needed, best_slot['rooms_to_book'], self.scheduler.num_blocks_in_day)
                exam_record = self._create_exam_record(group, best_slot['day_str'], best_slot['time_slot_str'], best_slot['room_str'], num_students)
                self.scheduler.schedule.append(exam_record)
                for student_id in students:
                    if student_id not in self.scheduler.student_exams: self.scheduler.student_exams[student_id] = []
                    self.scheduler.student_exams[student_id].append(exam_record)
                self.scheduler.exams_per_day_count[best_slot['day_str']] += 1

    def _schedule_failed_sections_relaxed(self):
        """Этап 2.5: Смягченное планирование. Выбирает слот с минимальным количеством конфликтов."""
        sections_to_process = list(self.scheduler.failed_sections)
        self.scheduler.failed_sections = []

        for group_dict in sections_to_process:
            group = pd.Series(group_dict)
            students = self.scheduler.section_students_map.get(group['Section'], [])
            num_students = len(students)
            duration_minutes = int(group.get('Duration', 180))
            instructor = group['Instructor']
            two_rooms_needed = group.get('two_rooms_needed', False)
            
            effective_capacity = num_students * 2 if two_rooms_needed else num_students
            exam_blocks = math.ceil(duration_minutes / self.scheduler.time_step)
            buffer_blocks = math.ceil(self.scheduler.buffer_time / self.scheduler.time_step)
            total_blocks_needed = exam_blocks + buffer_blocks

            possible_slots = []
            for day in self.scheduler.custom_dates:
                day_str = day.strftime('%Y-%m-%d')
                
                for start_block in range(self.scheduler.num_blocks_in_day - total_blocks_needed + 1):
                    day_start_dt = datetime.combine(day, self.scheduler.work_day_start.time())
                    exam_start_dt = day_start_dt + timedelta(minutes=start_block * self.scheduler.time_step)
                    exam_end_dt = exam_start_dt + timedelta(minutes=duration_minutes)
                    exam_time_slot_str = f"{exam_start_dt.strftime('%H:%M')}-{exam_end_dt.strftime('%H:%M')}"

                    if not self._is_instructor_available(instructor, day, exam_time_slot_str, group.to_dict()):
                        continue

                    grid_available_rooms = [
                        r for r in self.scheduler.rooms 
                        if str(r) in self.scheduler.room_availability_grid[day_str] and 
                        not any(self.scheduler.room_availability_grid[day_str][str(r)][i] for i in range(start_block, start_block + total_blocks_needed))
                    ]
                    
                    available_rooms = [
                        r for r in grid_available_rooms
                        if not self._is_room_excluded(r, day, exam_start_dt, exam_end_dt)
                    ]

                    classroom_type = group.get('classroom_type', 'regular')
                    final_room_str, rooms_to_book = self._find_suitable_rooms(available_rooms, effective_capacity, group.to_dict(), classroom_type)
                    
                    if final_room_str:
                        # Считаем общее количество конфликтов для этого слота
                        conflicts = 0
                        for s_id in students:
                            s_exams = self.scheduler.student_exams.get(str(s_id), [])
                            day_exams = [e for e in s_exams if e['Date'] == day_str]
                            if day_exams:
                                # Конфликт по наложению времени
                                if any(check_overlap(e['Time_Slot'], exam_time_slot_str) for e in day_exams):
                                    conflicts += 10 # Высокий штраф за наложение
                                else:
                                    conflicts += 1 # Низкий штраф за 2-й экзамен в день
                        
                        possible_slots.append({
                            'day_str': day_str, 'start_block': start_block, 'time_slot_str': exam_time_slot_str, 
                            'room_str': final_room_str, 'rooms_to_book': rooms_to_book, 'conflicts': conflicts
                        })
                        if conflicts == 0: break # Идеальный слот

            if not possible_slots:
                self.scheduler.failed_sections.append(group_dict)
            else:
                best_slot = min(possible_slots, key=lambda s: (s['conflicts'], self.scheduler.exams_per_day_count[s['day_str']], s['start_block']))
                self._book_slot(best_slot['day_str'], best_slot['start_block'], total_blocks_needed, best_slot['rooms_to_book'], self.scheduler.num_blocks_in_day)
                exam_record = self._create_exam_record(group, best_slot['day_str'], best_slot['time_slot_str'], best_slot['room_str'], num_students)
                self.scheduler.schedule.append(exam_record)
                for student_id in students:
                    if str(student_id) not in self.scheduler.student_exams: self.scheduler.student_exams[str(student_id)] = []
                    self.scheduler.student_exams[str(student_id)].append(exam_record)
                self.scheduler.exams_per_day_count[best_slot['day_str']] += 1

    def _schedule_failed_sections_push_out(self):
        """
        Этап 2.7: Rip-and-Repair. 
        Пытается вытеснить 1-2 экзамена, чтобы освободить место для проваленной секции.
        """
        sections_to_process = list(self.scheduler.failed_sections)
        self.scheduler.failed_sections = []
        
        for group_dict in sections_to_process:
            group = pd.Series(group_dict)
            success = self._try_push_out_for_section(group)
            if not success:
                self.scheduler.failed_sections.append(group_dict)

    def _try_push_out_for_section(self, group):
        students = self.scheduler.section_students_map.get(group['Section'], [])
        num_students = len(students)
        duration_minutes = int(group.get('Duration', 180))
        instructor = group['Instructor']
        two_rooms_needed = group.get('two_rooms_needed', False)
        
        effective_capacity = num_students * 2 if two_rooms_needed else num_students
        exam_blocks = math.ceil(duration_minutes / self.scheduler.time_step)
        buffer_blocks = math.ceil(self.scheduler.buffer_time / self.scheduler.time_step)
        total_blocks_needed = exam_blocks + buffer_blocks

        # Ищем слоты, где мешает только 1-2 экзамена (по аудитории)
        for day in self.scheduler.custom_dates:
            day_str = day.strftime('%Y-%m-%d')
            
            # Проверка студентов (хотя бы мягкая: не более 2 в день)
            if not all(self._is_student_available_with_time_check(s_id, day_str, None) for s_id in students):
                continue

            for start_block in range(self.scheduler.num_blocks_in_day - total_blocks_needed + 1):
                day_start_dt = datetime.combine(day, self.scheduler.work_day_start.time())
                exam_start_dt = day_start_dt + timedelta(minutes=start_block * self.scheduler.time_step)
                exam_end_dt = exam_start_dt + timedelta(minutes=duration_minutes)
                exam_time_slot_str = f"{exam_start_dt.strftime('%H:%M')}-{exam_end_dt.strftime('%H:%M')}"

                if not self._is_instructor_available(instructor, day, exam_time_slot_str, group.to_dict()):
                    continue

                # Ищем подходящую комнату(ы), которые заняты не более чем 1-2 'чужими' экзаменами
                potential_rooms = []
                classroom_type = group.get('classroom_type', 'regular')
                
                # Фильтруем комнаты по типу и исключениям
                candidate_rooms = [
                    r for r in self.scheduler.rooms 
                    if self.scheduler.room_types.get(r, 'regular') == classroom_type and
                    not self._is_room_excluded(r, day, exam_start_dt, exam_end_dt)
                ]
                
                for r in candidate_rooms:
                    room_str = str(r)
                    if self.scheduler.room_capacities.get(r, 0) < effective_capacity:
                        continue
                    
                    # Кто занимает комнату в это время?
                    blockers = self._get_blocking_exams(room_str, day_str, start_block, total_blocks_needed)
                    
                    # Если мешает 1-2 экзамена и они не закреплены (pinned)
                    if 0 < len(blockers) <= 2 and not any(b.get('pinned', False) for b in blockers):
                        # Пробуем "многоходовочку"
                        if self._attempt_eviction(group, day_str, start_block, total_blocks_needed, [room_str], blockers, exam_time_slot_str):
                            return True
        return False

    def _get_blocking_exams(self, room_str, day_str, start_block, total_blocks_needed):
        blockers = []
        # Проверяем сетку занятости
        if not any(self.scheduler.room_availability_grid[day_str][room_str][i] for i in range(start_block, start_block + total_blocks_needed)):
            return [] # Место свободно (не должно быть в push_out, но на всякий случай)
            
        # Ищем в расписании записи, которые накладываются
        for exam in self.scheduler.schedule:
            if exam['Date'] == day_str and room_str in str(exam['Room']):
                # Простая проверка наложения блоков (упрощенно через время)
                e_start_dt, e_end_dt = [datetime.strptime(t, '%H:%M') for t in exam['Time_Slot'].split('-')]
                # Переводим в блоки
                e_start_block = int((e_start_dt - datetime.combine(e_start_dt.date(), self.scheduler.work_day_start.time())).total_seconds() / 60 / self.scheduler.time_step)
                e_dur_blocks = math.ceil(exam['Duration'] / self.scheduler.time_step)
                e_total_blocks = e_dur_blocks + math.ceil(self.scheduler.buffer_time / self.scheduler.time_step)
                
                if max(start_block, e_start_block) < min(start_block + total_blocks_needed, e_start_block + e_total_blocks):
                    blockers.append(exam)
        return blockers

    def _attempt_eviction(self, group, day_str, start_block, total_blocks_needed, rooms_to_book, blockers, time_slot_str):
        # 1. Сохраняем состояние для отката
        saved_schedule = list(self.scheduler.schedule)
        saved_student_exams = {k: list(v) for k, v in self.scheduler.student_exams.items()}
        # Сетку сохранять целиком дорого, будем откатывать точечно
        
        try:
            # 2. Rip: Выселяем блокировщиков
            for b in blockers:
                self._remove_exam_from_state(b)
            
            # 3. Place: Ставим новую секцию
            self._book_slot(day_str, start_block, total_blocks_needed, rooms_to_book, self.scheduler.num_blocks_in_day)
            num_students = len(self.scheduler.section_students_map.get(group['Section'], []))
            exam_record = self._create_exam_record(group, day_str, time_slot_str, ",".join(rooms_to_book), num_students)
            self.scheduler.schedule.append(exam_record)
            for s_id in self.scheduler.section_students_map.get(group['Section'], []):
                if str(s_id) not in self.scheduler.student_exams: self.scheduler.student_exams[str(s_id)] = []
                self.scheduler.student_exams[str(s_id)].append(exam_record)
            
            # 4. Repair: Пробуем переприцепить выселенных
            for b in blockers:
                b_group = self._find_group_for_exam(b)
                if not b_group:
                    raise Exception("Original group not found for blocker")
                
                re_scheduled = self._re_schedule_single_section(b_group)
                if not re_scheduled:
                    raise Exception(f"Failed to re-schedule evicted exam: {b['Section']}")
            
            logging.info(f"SUCCESS: Pushed out {len(blockers)} exams to fit section {group['Section']}")
            return True
            
        except Exception as e:
            # Rollback
            # logging.debug(f"Rollback push-out for {group['Section']}: {e}")
            self.scheduler.schedule = saved_schedule
            self.scheduler.student_exams = saved_student_exams
            # Откатываем сетку (упрощенно: пересчитываем из schedule или точечно)
            self._rebuild_availability_grid()
            return False

    def _remove_exam_from_state(self, exam):
        # Удаляем из schedule
        self.scheduler.schedule = [e for e in self.scheduler.schedule if not (e['Section'] == exam['Section'] and e['Date'] == exam['Date'] and e['Time_Slot'] == exam['Time_Slot'])]
        
        # Удаляем из student_exams
        students = self.scheduler.section_students_map.get(exam['Section'], [])
        for s_id in students:
            s_id_str = str(s_id)
            if s_id_str in self.scheduler.student_exams:
                self.scheduler.student_exams[s_id_str] = [e for e in self.scheduler.student_exams[s_id_str] if not (e['Section'] == exam['Section'] and e['Date'] == exam['Date'])]

        # Освобождаем в сетке
        start_dt = datetime.strptime(exam['Time_Slot'].split('-')[0], '%H:%M')
        start_block = int((start_dt - datetime.combine(start_dt.date(), self.scheduler.work_day_start.time())).total_seconds() / 60 / self.scheduler.time_step)
        dur_blocks = math.ceil(exam['Duration'] / self.scheduler.time_step)
        buffer_blocks = math.ceil(self.scheduler.buffer_time / self.scheduler.time_step)
        total_blocks = dur_blocks + buffer_blocks
        
        rooms = str(exam['Room']).split(',')
        for r in rooms:
            r = r.strip()
            if exam['Date'] in self.scheduler.room_availability_grid and r in self.scheduler.room_availability_grid[exam['Date']]:
                for i in range(start_block, start_block + total_blocks):
                    if i < self.scheduler.num_blocks_in_day:
                        self.scheduler.room_availability_grid[exam['Date']][r][i] = False

    def _rebuild_availability_grid(self):
        # Сброс
        for day_str in self.scheduler.room_availability_grid:
            for room_str in self.scheduler.room_availability_grid[day_str]:
                self.scheduler.room_availability_grid[day_str][room_str] = [False] * self.scheduler.num_blocks_in_day
        
        # Заполнение заново из текущего schedule
        for exam in self.scheduler.schedule:
            start_dt = datetime.strptime(exam['Time_Slot'].split('-')[0], '%H:%M')
            start_block = int((start_dt - datetime.combine(start_dt.date(), self.scheduler.work_day_start.time())).total_seconds() / 60 / self.scheduler.time_step)
            dur_blocks = math.ceil(exam['Duration'] / self.scheduler.time_step)
            buffer_blocks = math.ceil(self.scheduler.buffer_time / self.scheduler.time_step)
            total_blocks = dur_blocks + buffer_blocks
            rooms = str(exam['Room']).split(',')
            for r in rooms:
                r = r.strip()
                if exam['Date'] in self.scheduler.room_availability_grid and r in self.scheduler.room_availability_grid[exam['Date']]:
                    for i in range(start_block, start_block + total_blocks):
                        if i < self.scheduler.num_blocks_in_day:
                            self.scheduler.room_availability_grid[exam['Date']][r][i] = True

    def _find_group_for_exam(self, exam):
        section_id = exam['Section']
        match = self.scheduler.exam_groups[self.scheduler.exam_groups['Section'] == section_id]
        if not match.empty:
            return match.iloc[0].to_dict()
        return None

    def _re_schedule_single_section(self, group_dict):
        """Пытается найти новое место для одной секции (аналог Stage 2.5)."""
        group = pd.Series(group_dict)
        # Параметры поиска те же, что в _schedule_failed_sections_relaxed
        # Но без рекурсии в push-out!
        students = self.scheduler.section_students_map.get(group['Section'], [])
        num_students = len(students)
        duration_minutes = int(group.get('Duration', 180))
        instructor = group['Instructor']
        two_rooms_needed = group.get('two_rooms_needed', False)
        effective_capacity = num_students * 2 if two_rooms_needed else num_students
        exam_blocks = math.ceil(duration_minutes / self.scheduler.time_step)
        buffer_blocks = math.ceil(self.scheduler.buffer_time / self.scheduler.time_step)
        total_blocks_needed = exam_blocks + buffer_blocks

        possible_slots = []
        for day in self.scheduler.custom_dates:
            day_str = day.strftime('%Y-%m-%d')
            if not all(self._is_student_available_with_time_check(s_id, day_str, None) for s_id in students):
                continue

            for start_block in range(self.scheduler.num_blocks_in_day - total_blocks_needed + 1):
                day_start_dt = datetime.combine(day, self.scheduler.work_day_start.time())
                exam_start_dt = day_start_dt + timedelta(minutes=start_block * self.scheduler.time_step)
                exam_end_dt = exam_start_dt + timedelta(minutes=duration_minutes)
                exam_time_slot_str = f"{exam_start_dt.strftime('%H:%M')}-{exam_end_dt.strftime('%H:%M')}"

                if not all(self._is_student_available_with_time_check(s_id, day_str, exam_time_slot_str) for s_id in students):
                    continue
                if not self._is_instructor_available(instructor, day, exam_time_slot_str, group_dict):
                    continue

                grid_available_rooms = [
                    r for r in self.scheduler.rooms 
                    if str(r) in self.scheduler.room_availability_grid[day_str] and 
                    not any(self.scheduler.room_availability_grid[day_str][str(r)][i] for i in range(start_block, start_block + total_blocks_needed))
                ]
                available_rooms = [r for r in grid_available_rooms if not self._is_room_excluded(r, day, exam_start_dt, exam_end_dt)]

                classroom_type = group.get('classroom_type', 'regular')
                final_room_str, rooms_to_book = self._find_suitable_rooms(available_rooms, effective_capacity, group_dict, classroom_type)
                
                if final_room_str:
                    conflicts = 0
                    for s_id in students:
                        s_exams = self.scheduler.student_exams.get(str(s_id), [])
                        day_exams = [e for e in s_exams if e['Date'] == day_str]
                        if day_exams: conflicts += 1
                    
                    possible_slots.append({'day_str': day_str, 'start_block': start_block, 'time_slot_str': exam_time_slot_str, 'room_str': final_room_str, 'rooms_to_book': rooms_to_book, 'conflicts': conflicts})
                    if conflicts == 0: break
            if possible_slots: break

        if possible_slots:
            best_slot = min(possible_slots, key=lambda s: (s['conflicts'], self.scheduler.exams_per_day_count[s['day_str']], s['start_block']))
            self._book_slot(best_slot['day_str'], best_slot['start_block'], total_blocks_needed, best_slot['rooms_to_book'], self.scheduler.num_blocks_in_day)
            record = self._create_exam_record(group, best_slot['day_str'], best_slot['time_slot_str'], best_slot['room_str'], num_students)
            self.scheduler.schedule.append(record)
            for s_id in students:
                if str(s_id) not in self.scheduler.student_exams: self.scheduler.student_exams[str(s_id)] = []
                self.scheduler.student_exams[str(s_id)].append(record)
            return True
        return False

    def _load_manual_bookings(self):
        from create_db import ClassroomSlot, Session
        import json

        session = Session()
        try:
            booked_slots = session.query(ClassroomSlot).filter_by(is_booked=True).all()
            manual_exams = []
            scheduled_sections = set()

            for slot in booked_slots:
                if not slot.booked_groups_info: continue
                booking_info = json.loads(slot.booked_groups_info)
                subject = booking_info.get('subject')
                sections = booking_info.get('sections')
                if not subject or not sections: continue

                slot_date_str = slot.start_time.strftime('%Y-%m-%d')
                base_time_slot = f"{slot.start_time.strftime('%H:%M')}-{slot.end_time.strftime('%H:%M')}"

                for section_id in sections:
                    if section_id in self.scheduler.exam_groups['Section'].values:
                        group_info = self.scheduler.exam_groups[self.scheduler.exam_groups['Section'] == section_id].iloc[0]
                        num_students = len(self.scheduler.section_students_map.get(section_id, []))

                        exam_record = {
                            'Date': slot_date_str, 'Subject': subject, 'Instructor': group_info['Instructor'],
                            'EduProgram': group_info['EduProgram'], 'Section': section_id,
                            'Students_Count': int(num_students), 'Room': slot.classroom_number,
                            'Time_Slot': base_time_slot, 'Base_Time_Slot': base_time_slot,
                            'Duration': int(group_info.get('Duration', 180)), 'pinned': True
                        }
                        manual_exams.append(exam_record)
                        scheduled_sections.add(section_id)
            return manual_exams, scheduled_sections
        except Exception as e:
            logging.error(f"Ошибка загрузки броней: {traceback.format_exc()}")
            return [], set()
        finally:
            session.close()

    def _find_suitable_rooms(self, available_rooms, required_capacity, group_info, classroom_type='regular'):
        two_rooms_needed = group_info.get('two_rooms_needed', False)
        proctor_needed = group_info.get('proctor_needed', False)
        has_exam = group_info.get('has_exam', True)

        forbidden_rooms = {'319', '419', '436', '526', '536', '338/1', '334/1'}
        if not two_rooms_needed and has_exam and not proctor_needed:
            available_rooms = [r for r in available_rooms if str(r) not in forbidden_rooms]
        
        is_written_exam = (two_rooms_needed and proctor_needed and has_exam)
        if not is_written_exam and '107' in available_rooms:
            available_rooms = [r for r in available_rooms if str(r) != '107']

        typed_available_rooms = [r for r in available_rooms if self.scheduler.room_types.get(r, 'regular') == classroom_type]
        if not typed_available_rooms and classroom_type == 'regular':
            typed_available_rooms = [r for r in available_rooms if self.scheduler.room_types.get(r, 'regular') == 'it_lab']

        if not typed_available_rooms: return None, []

        if not two_rooms_needed:
            suitable = [r for r in typed_available_rooms if self.scheduler.room_capacities.get(r, 0) >= required_capacity]
            if not suitable: return None, []
            best_room = min(suitable, key=lambda r: self.scheduler.room_capacities.get(r, 0))
            return str(best_room), [str(best_room)]
        else:
            # Логика для 2 комнат
            single_large = [r for r in typed_available_rooms if self.scheduler.room_capacities.get(r, 0) >= required_capacity]
            if single_large:
                best = min(single_large, key=lambda r: self.scheduler.room_capacities.get(r, 0))
                return str(best), [str(best)]
            
            room_pairs = list(combinations(typed_available_rooms, 2))
            suitable_pairs = [p for p in room_pairs if self.scheduler.room_capacities.get(str(p[0]), 0) + self.scheduler.room_capacities.get(str(p[1]), 0) >= required_capacity]
            if not suitable_pairs: return None, []
            
            scored_pairs = []
            for p in suitable_pairs:
                r1, r2 = str(p[0]), str(p[1])
                n1 = int(''.join(filter(str.isdigit, r1)) or 0)
                n2 = int(''.join(filter(str.isdigit, r2)) or 0)
                floor1, floor2 = (n1//100) if n1>=100 else -1, (n2//100) if n2>=100 else -2
                score = 0 if floor1 == floor2 else 1
                scored_pairs.append(((r1, r2), score, abs(n1-n2), self.scheduler.room_capacities.get(r1,0)+self.scheduler.room_capacities.get(r2,0)))
            
            scored_pairs.sort(key=lambda x: (x[1], x[2], x[3]))
            best_pair = scored_pairs[0][0]
            return f"{best_pair[0]},{best_pair[1]}", list(best_pair)

    def _book_slot(self, day_str, start_block, total_blocks_needed, rooms_to_book, num_blocks_in_day):
        for room in rooms_to_book:
            for i in range(start_block, start_block + total_blocks_needed):
                if i < num_blocks_in_day:
                    self.scheduler.room_availability_grid[day_str][str(room)][i] = True

    def _create_exam_record(self, group, day_str, time_slot_str, room_str, num_students):
        return {
            'Date': day_str, 'Subject': group['Subject'], 'Instructor': group['Instructor'],
            'EduProgram': group['EduProgram'], 'Section': group['Section'],
            'Students_Count': num_students, 'Room': room_str, 'Time_Slot': time_slot_str,
            'Duration': int(group.get('Duration', 180)), 'proctor_needed': group.get('proctor_needed', False),
            'two_rooms_needed': group.get('two_rooms_needed', False), 'pinned': False
        }

    def _is_student_available_for_exam(self, student_id, day_str, new_exam_group):
        student_id = str(student_id)
        exams_on_day = [exam for exam in self.scheduler.student_exams.get(student_id, []) if exam['Date'] == day_str]
        return len(exams_on_day) == 0

    def _is_student_available_with_time_check(self, student_id, day_str, new_time_slot=None):
        student_id = str(student_id)
        exams_on_day = [exam for exam in self.scheduler.student_exams.get(student_id, []) if exam['Date'] == day_str]
        
        if not exams_on_day:
            return True
            
        # Правило: не более 2 экзаменов в день вообще
        if len(exams_on_day) >= 2:
            return False
            
        # Если передан тайм-слот, проверяем наложение
        if new_time_slot:
            for exam in exams_on_day:
                if check_overlap(exam['Time_Slot'], new_time_slot):
                    return False
        
        return True

    def _is_instructor_available(self, instructor, day, time_slot, new_exam_group):
        if not time_slot: return False
        start_t, end_t = [datetime.strptime(t, '%H:%M') for t in time_slot.split('-')]
        new_type = get_exam_type(new_exam_group)

        # Проверяем текущее расписание
        for exam in self.scheduler.schedule:
            if exam.get('Instructor') == instructor and str(exam.get('Date', '')).startswith(day.strftime('%Y-%m-%d')):
                exam_time = exam.get('Time_Slot')
                if not exam_time or '-' not in exam_time: continue
                e_start, e_end = [datetime.strptime(t, '%H:%M') for t in exam_time.split('-')]
                
                if start_t < e_end and e_start < end_t:
                    # Наложение! Проверяем мягкий конфликт для инструктора
                    old_type = get_exam_type(exam)
                    if new_type == "written" and old_type == "written":
                        if start_t >= e_start + timedelta(hours=1) or e_start >= start_t + timedelta(hours=1):
                            continue
                    return False
        return True

    def _is_room_excluded(self, room, day_dt, start_dt, end_dt):
        day_date = day_dt.date()
        if day_date in self.scheduler.exclusions_by_date:
            for ex in self.scheduler.exclusions_by_date[day_date]:
                if str(ex.room_number) == str(room):
                    if max(start_dt, ex.start_time) < min(end_dt, ex.end_time): return True
        return False
