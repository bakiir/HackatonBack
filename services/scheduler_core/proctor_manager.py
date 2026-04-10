import logging
import pandas as pd
import random
import re
from collections import defaultdict
from datetime import datetime
from .utils import group_consecutive_slots, normalize_room

class ProctorManager:
    """
    Управляет назначением и балансировкой прокторов.
    """
    def __init__(self, scheduler):
        self.scheduler = scheduler

    def assign_proctors(self, proctors_path=None):
        """
        Назначает прокторов на экзамены.
        """
        # --- Group consecutive slots first ---
        logging.info("Предварительная группировка последовательных временных слотов...")
        if not self.scheduler.schedule_df.empty and 'Time_Slot' in self.scheduler.schedule_df.columns:
            # Ensure correct date format before grouping
            if 'Date' in self.scheduler.schedule_df.columns:
                 self.scheduler.schedule_df['Date'] = pd.to_datetime(self.scheduler.schedule_df['Date']).dt.strftime('%Y-%m-%d')

            schedule_records = self.scheduler.schedule_df.to_dict('records')
            grouped_records = group_consecutive_slots(schedule_records)
            self.scheduler.schedule_df = pd.DataFrame(grouped_records)
            logging.info(f"Группировка завершена. Количество записей в расписании: {len(self.scheduler.schedule_df)}")

        logging.info("Назначение прокторов.")
        section_proctor_map = {}
        section_proctors = {}
        non_proctors = []

        if proctors_path:
            try:
                non_proctors_df = pd.read_excel(proctors_path, header=3)
                non_proctors = non_proctors_df['ФИО'].dropna().tolist()
            except Exception as e:
                logging.error(f"Не удалось загрузить файл исключений прокторов: {e}")

        all_proctors = [p for proctors in self.scheduler.faculty_proctors.values() for p in proctors]

        def is_excluded(name, exclusions):
            return any(excl in name for excl in exclusions)

        available_proctors = [p for p in set(all_proctors) if not is_excluded(p, non_proctors)]

        if not available_proctors:
            raise ValueError("Нет доступных прокторов для назначения.")

        sct_proctors = self.scheduler.faculty_proctors.get('Школа цифровых технологий', [])
        sct_proctors = [p for p in sct_proctors if p in available_proctors]
        if not sct_proctors:
            logging.warning("Нет доступных прокторов из ШЦТ, хотя они могут быть нужны.")

        non_sct_proctors = [p for p in available_proctors if p not in sct_proctors]
        if not non_sct_proctors:
            logging.warning("Нет доступных прокторов вне ШЦТ, хотя они могут быть нужны.")

        faculty_exams = {faculty: 0 for faculty in self.scheduler.faculty_proctors.keys()}
        for _, row in self.scheduler.schedule_df.iterrows():
            subject = row['Subject']
            proctor_needed = row.get('proctor_needed', True)
            if not proctor_needed:
                continue
            faculties = self.scheduler.subject_faculty_map.get(subject, set())
            for faculty in faculties:
                num_proctors = 4 if '107' in str(row['Room']) else (2 if row.get('two_rooms_needed', False) else 1)
                faculty_exams[faculty] += num_proctors

        logging.info("Информация о прокторах и экзаменах по школам:")
        for faculty, proctors in self.scheduler.faculty_proctors.items():
            valid_proctors = [p for p in proctors if p in available_proctors]
            logging.info(f"{faculty}: {len(valid_proctors)} прокторов, {faculty_exams.get(faculty, 0)} мест для прокторов")

        # --- Calculate availability score for each proctor ---
        logging.info("Calculating proctor availability scores...")
        proctor_availability = defaultdict(int)
        exams_to_proctor_df = self.scheduler.schedule_df[self.scheduler.schedule_df['proctor_needed'] == True].copy()
        proctor_to_faculties = defaultdict(list)
        if hasattr(self.scheduler, 'faculty_proctors') and self.scheduler.faculty_proctors:
            for faculty, proctors in self.scheduler.faculty_proctors.items():
                for proctor in proctors:
                    proctor_to_faculties[proctor].append(faculty)
        sct_proctor_set = set(sct_proctors)

        for proctor in available_proctors:
            is_sct_proctor = proctor in sct_proctor_set
            for _, row in exams_to_proctor_df.iterrows():
                subject = row['Subject']
                is_sct_subject = 'Школа цифровых технологий' in self.scheduler.subject_faculty_map.get(subject, set())

                eligible = False
                if is_sct_subject:
                    if is_sct_proctor:
                        eligible = True
                else:  # not an SCT subject
                    if not is_sct_proctor:
                        exam_faculties = self.scheduler.subject_faculty_map.get(subject, set())
                        proctor_faculties = set(proctor_to_faculties.get(proctor, []))
                        if not exam_faculties.intersection(proctor_faculties):
                            eligible = True

                if eligible:
                    proctor_availability[proctor] += 1
        logging.info("Finished calculating availability scores.")

        # --- New Grouping-Based Proctor Assignment ---
        proctor_load = {proctor: 0 for proctor in available_proctors}
        proctor_schedule = {}  # Tracks busy proctors for a given (date, time_slot)
        MAX_PROCTOR_LOAD = 100

        # Handle sections that don't need proctors first
        no_proctor_df = self.scheduler.schedule_df[self.scheduler.schedule_df['proctor_needed'] != True]
        for _, row in no_proctor_df.iterrows():
            section_id = row['Section']
            section_proctors[section_id] = {'proctor': [], 'subject': row['Subject'], 'exam_name': row['Subject'],
                                            'date': row['Date']}
            section_proctor_map[section_id] = ''

        # Process sections that need proctors
        proctor_needed_df = self.scheduler.schedule_df[self.scheduler.schedule_df['proctor_needed'] == True].copy()
        proctor_needed_df['normalized_room'] = proctor_needed_df['Room'].apply(normalize_room)
        proctor_needed_df['stripped_time_slot'] = proctor_needed_df['Time_Slot'].str.strip()

        # Group by the logical exam event
        logging.info("Группировка экзаменов по дате, времени и предмету для назначения прокторов.")
        grouped_events = proctor_needed_df.groupby(['Date', 'stripped_time_slot', 'Subject'])

        # --- Новый подход к распределению нагрузки ---
        total_proctoring_hours = 0
        schedule_records = proctor_needed_df.to_dict('records')
        grouped_records = group_consecutive_slots(schedule_records)
        
        for exam in grouped_records:
            room = exam.get('Room', '')
            two_rooms_needed = exam.get('two_rooms_needed', False)
            duration_hours = exam.get('Duration', 180) / 60
            
            if '107' in str(room):
                num_proctors_needed = 4
            elif two_rooms_needed or ',' in str(room): 
                num_proctors_needed = 2
            else:
                num_proctors_needed = 1
            
            total_proctoring_hours += num_proctors_needed * duration_hours

        if available_proctors:
            target_load_per_proctor = total_proctoring_hours / len(available_proctors)
            logging.info(f"Общее количество часов для прокторинга: {total_proctoring_hours:.2f}")
            logging.info(f"Целевая нагрузка на одного проктора: {target_load_per_proctor:.2f} часов")
        else:
            target_load_per_proctor = 0

        # 2. Основной цикл назначения
        events_to_process = []
        for event_key, g_df in grouped_events:
            representative_row = g_df.iloc[0]
            unique_rooms = g_df['Room'].unique()
            num_unique_rooms = len(unique_rooms)
            duration_hours = representative_row.get('Duration', 180) / 60

            if '107' in unique_rooms:
                num_proctors_needed = 4
            elif num_unique_rooms > 1:
                num_proctors_needed = num_unique_rooms
            else:
                num_proctors_needed = 1

            demand_key = (num_proctors_needed, duration_hours)
            events_to_process.append({'key': event_key, 'df': g_df, 'demand': demand_key})

        events_to_process.sort(key=lambda x: x['demand'], reverse=True)
        
        logging.info(f"Начинается назначение прокторов для {len(events_to_process)} экзаменационных событий.")

        for event in events_to_process:
            event_key = event['key']
            g_df = event['df']
            exam_date, time_slot, subject = event_key

            unique_rooms = g_df['normalized_room'].unique()
            num_unique_rooms = len(unique_rooms)
            representative_row = g_df.iloc[0]
            two_rooms_needed_flag = representative_row.get('two_rooms_needed', False)
            PROCTOR_STUDENT_THRESHOLD = 50

            total_proctors_needed = 0
            proctors_per_room_map = defaultdict(int)

            if '107' in unique_rooms:
                total_proctors_needed = 4
                proctors_per_room_map['107'] = 4
            elif two_rooms_needed_flag and num_unique_rooms > 1:
                total_proctors_needed = num_unique_rooms
                for room in unique_rooms:
                    proctors_per_room_map[room] = 1
            else:
                room_name = unique_rooms[0]
                total_students = g_df['Students_Count'].sum()
                if total_students > PROCTOR_STUDENT_THRESHOLD:
                    num_proctors = 2
                else:
                    num_proctors = 1
                total_proctors_needed = num_proctors
                proctors_per_room_map[room_name] = num_proctors

            instructor_faculty_map = {
                instructor: faculty
                for faculty, instructors in self.scheduler.faculty_proctors.items()
                for instructor in instructors
            }
            
            instructor = representative_row['Instructor']
            is_sct_by_subject = 'Школа цифровых технологий' in self.scheduler.subject_faculty_map.get(subject, set())
            is_sct_by_instructor = instructor_faculty_map.get(instructor) == 'Школа цифровых технологий'
            is_sct_subject = is_sct_by_subject or is_sct_by_instructor
            
            if is_sct_subject:
                proctor_pool = sct_proctors
            else:
                exam_faculties = self.scheduler.subject_faculty_map.get(subject, set())
                primary_pool = [
                    p for p in non_sct_proctors if not any(
                        p in self.scheduler.faculty_proctors.get(f, []) for f in exam_faculties
                    )
                ]
                backup_pool = sct_proctors
                proctor_pool = primary_pool + backup_pool

            assigned_proctors = []
            if not proctor_pool:
                logging.error(f"Нет доступных прокторов для события: {subject} на {exam_date} {time_slot}")
            else:
                slot_key = (exam_date, time_slot)
                busy_proctors = proctor_schedule.get(slot_key, [])
                exam_day_obj = datetime.strptime(exam_date, '%Y-%m-%d')
                exam_info_dict = representative_row.to_dict()

                available = [
                    p for p in proctor_pool if
                    p not in busy_proctors and
                    proctor_load.get(p, 0) < MAX_PROCTOR_LOAD and
                    self.scheduler._is_instructor_available(p, exam_day_obj, time_slot, exam_info_dict)
                ]

                if len(available) < total_proctors_needed:
                    logging.error(f"Недостаточно свободных прокторов для {subject}: требуется {total_proctors_needed}, доступно {len(available)}.")
                else:
                    available.sort(key=lambda p: (proctor_load.get(p, 0), random.random()))
                    assigned_proctors = available[:total_proctors_needed]

            proctor_iterator = iter(assigned_proctors)
            for room in unique_rooms:
                num_needed_for_room = proctors_per_room_map[room]
                proctors_for_this_room = [next(proctor_iterator, None) for _ in range(num_needed_for_room)]
                proctors_for_this_room = [p for p in proctors_for_this_room if p is not None]

                proctor_str = ', '.join(proctors_for_this_room)
                room_df = g_df[g_df['normalized_room'] == room]
                for _, row in room_df.iterrows():
                    map_key = (row['Section'], row['normalized_room'])
                    section_proctor_map[map_key] = proctor_str
                    section_proctors[row['Section']] = {'proctor': proctors_for_this_room, 'subject': subject,
                                                        'exam_name': subject, 'date': exam_date}

            exam_duration_hours = representative_row.get('Duration', 180) / 60
            slot_key = (exam_date, time_slot)
            for proctor in assigned_proctors:
                proctor_load[proctor] = proctor_load.get(proctor, 0) + exam_duration_hours
                proctor_schedule.setdefault(slot_key, []).append(proctor)

        self.scheduler.schedule_df['normalized_room'] = self.scheduler.schedule_df['Room'].apply(normalize_room)
        
        def get_proctor_for_row(row):
            key = (row['Section'], row['normalized_room'])
            return section_proctor_map.get(key, '')
            
        self.scheduler.schedule_df['Proctor'] = self.scheduler.schedule_df.apply(get_proctor_for_row, axis=1)
        self.scheduler.schedule_df.drop(columns=['normalized_room'], inplace=True, errors='ignore')
        self.scheduler.section_proctors = section_proctors

        self.scheduler.proctor_schedule = proctor_schedule
        self.scheduler.sct_proctors = sct_proctors
        self._balance_proctor_load()

        logging.info("Прокторы успешно назначены.")

    def _balance_proctor_load(self):
        """
        Балансирует нагрузку между прокторами внутри одной школы.
        """
        logging.info("Запуск балансировки нагрузки прокторов.")
        MAX_ITERATIONS = 20
        MIN_IMPROVEMENT_HOURS = 1.5
        
        for iteration in range(MAX_ITERATIONS):
            schedule_records = self.scheduler.schedule_df.to_dict('records')
            grouped_records = group_consecutive_slots(schedule_records)
            grouped_df = pd.DataFrame(grouped_records)
            final_proctor_load = defaultdict(float)
            for _, row in grouped_df.iterrows():
                proctors_str = row.get('Proctor', '')
                if not proctors_str or pd.isna(proctors_str): continue
                proctors = [p.strip() for p in proctors_str.split(',')]
                exam_duration_hours = row.get('Duration', 180) / 60
                for proctor in proctors:
                    if proctor: final_proctor_load[proctor] += exam_duration_hours
            
            if not hasattr(self.scheduler, 'faculty_proctors'):
                break

            improvement_found_in_iteration = False
            for faculty, proctors_in_faculty in self.scheduler.faculty_proctors.items():
                faculty_loads = {p: final_proctor_load.get(p, 0) for p in proctors_in_faculty if final_proctor_load.get(p, 0) > 0}
                if len(faculty_loads) < 2: continue

                max_p = max(faculty_loads, key=faculty_loads.get)
                min_p = min(faculty_loads, key=faculty_loads.get)

                if (faculty_loads[max_p] - faculty_loads[min_p]) < MIN_IMPROVEMENT_HOURS * 1.5:
                    continue

                exams_of_max_p = self.scheduler.schedule_df[self.scheduler.schedule_df['Proctor'].str.contains(f"\\b{re.escape(max_p)}\\b", na=False)].copy()
                if exams_of_max_p.empty: continue
                
                exams_of_max_p['duration_hours'] = exams_of_max_p['Duration'] / 60
                exams_of_max_p = exams_of_max_p.sort_values(by='duration_hours', ascending=False)

                for _, exam_row in exams_of_max_p.iterrows():
                    exam_duration_hours = exam_row['duration_hours']
                    if exam_duration_hours < MIN_IMPROVEMENT_HOURS: continue
                    if (faculty_loads[min_p] + exam_duration_hours) >= faculty_loads[max_p]: continue

                    date_for_key = exam_row['Date']
                    if not isinstance(date_for_key, str):
                        date_for_key = pd.to_datetime(date_for_key).strftime('%Y-%m-%d')

                    slot_key = (date_for_key, exam_row['Time_Slot'].strip())
                    subject = exam_row['Subject']
                    is_sct_subject = 'Школа цифровых технологий' in self.scheduler.subject_faculty_map.get(subject, set())
                    is_min_p_sct = min_p in self.scheduler.sct_proctors
                    
                    eligible = (is_sct_subject and is_min_p_sct) or (not is_sct_subject and not is_min_p_sct)
                    if not eligible: continue

                    if min_p not in self.scheduler.proctor_schedule.get(slot_key, []):
                        if max_p not in self.scheduler.proctor_schedule.get(slot_key, []):
                            continue 

                        logging.info(f"  БАЛАНСИРОВКА: Перемещение экзамена ({exam_duration_hours:.2f}ч) от {max_p} к {min_p} в {faculty}")
                        
                        old_proctors_list = [p.strip() for p in exam_row['Proctor'].split(',')]
                        if len(old_proctors_list) > 1:
                            try:
                                old_proctors_list.remove(max_p)
                                old_proctors_list.append(min_p)
                                new_proctors_str = ', '.join(sorted(old_proctors_list))
                            except ValueError: continue
                        else:
                            new_proctors_str = min_p
                        
                        self.scheduler.schedule_df.loc[self.scheduler.schedule_df.index == exam_row.name, 'Proctor'] = new_proctors_str
                        self.scheduler.proctor_schedule[slot_key].remove(max_p)
                        self.scheduler.proctor_schedule[slot_key].append(min_p)
                        
                        improvement_found_in_iteration = True
                        break
                if improvement_found_in_iteration: break
            
            if not improvement_found_in_iteration:
                break
        
        logging.info("Балансировка нагрузки завершена.")

    def get_all_proctors(self):
        """Возвращает полный список уникальных прокторов."""
        all_proctors = []
        for faculty, proctors in self.scheduler.faculty_proctors.items():
            all_proctors.extend(proctors)
        return sorted(set(p for p in all_proctors if isinstance(p, str)))
