import logging
import math
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Dict
from ..utils import check_overlap

class RelaxedStrategy:
    def __init__(self, scheduler, constraint_engine, grid_manager):
        self.scheduler = scheduler
        self.constraint_engine = constraint_engine
        self.grid_manager = grid_manager

    def execute(self, exam_groups: pd.DataFrame) -> List[Dict]:
        """
        Stage 2.5: Relaxed Placement.
        Attempts to schedule failed sections by allowing some soft constraint violations
        (like having 2 exams in one day for a student) and picking the slot with minimal conflicts.
        """
        logging.info(f"Starting RelaxedStrategy for {len(exam_groups)} sections.")
        failed_sections = []
        
        for _, group in exam_groups.iterrows():
            section_id = group['Section']
            students = self.scheduler.section_students_map.get(section_id, [])
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
                
                # Check instructor availability (Hard Constraint)
                # Note: We still keep some hard constraints
                
                for start_block in range(self.scheduler.num_blocks_in_day - total_blocks_needed + 1):
                    day_start_dt = datetime.combine(day, self.scheduler.work_day_start.time())
                    exam_start_dt = day_start_dt + timedelta(minutes=start_block * self.scheduler.time_step)
                    exam_end_dt = exam_start_dt + timedelta(minutes=duration_minutes)
                    exam_time_slot_str = f"{exam_start_dt.strftime('%H:%M')}-{exam_end_dt.strftime('%H:%M')}"

                    if not self.constraint_engine.is_instructor_available(instructor, day_str, exam_time_slot_str, group.to_dict()):
                        continue

                    # Check room availability
                    available_rooms = self.grid_manager.get_available_rooms(day_str, start_block, total_blocks_needed)
                    
                    # Filter by exceptions
                    available_rooms = [
                        r for r in available_rooms
                        if not self.constraint_engine.is_room_excluded(r, day.date(), exam_start_dt, exam_end_dt)
                    ]

                    classroom_type = group.get('classroom_type', 'regular')
                    final_room_str, rooms_to_book = self.constraint_engine.find_suitable_rooms(
                        available_rooms, effective_capacity, group.to_dict()
                    )
                    
                    if final_room_str:
                        # Calculate conflicts (Student overlaps)
                        conflicts = 0
                        for s_id in students:
                            s_exams = self.scheduler.student_exams.get(str(s_id), [])
                            day_exams = [e for e in s_exams if e['Date'] == day_str]
                            if day_exams:
                                # Overlap check
                                if any(check_overlap(e['Time_Slot'], exam_time_slot_str) for e in day_exams):
                                    conflicts += 10 # High penalty for direct overlap
                                else:
                                    conflicts += 1 # Low penalty for 2nd exam in day
                        
                        possible_slots.append({
                            'day_str': day_str, 'start_block': start_block, 'time_slot_str': exam_time_slot_str, 
                            'room_str': final_room_str, 'rooms_to_book': rooms_to_book, 'conflicts': conflicts
                        })
                        if conflicts == 0: break # Found an ideal slot
                if any(ps['conflicts'] == 0 for ps in possible_slots): break

            if not possible_slots:
                failed_sections.append(group.to_dict())
            else:
                # Pick the best slot: min conflicts -> min exams per day -> earliest start
                best_slot = min(possible_slots, key=lambda s: (s['conflicts'], self.scheduler.exams_per_day_count[s['day_str']], s['start_block']))
                
                # Book slot and record
                self.grid_manager.book_slot(best_slot['day_str'], best_slot['rooms_to_book'], best_slot['start_block'], total_blocks_needed)
                
                exam_record = self._create_exam_record(group, best_slot['day_str'], best_slot['time_slot_str'], best_slot['room_str'], num_students)
                self.scheduler.schedule.append(exam_record)
                
                for student_id in students:
                    if str(student_id) not in self.scheduler.student_exams: 
                        self.scheduler.student_exams[str(student_id)] = []
                    self.scheduler.student_exams[str(student_id)].append(exam_record)
                
                self.scheduler.exams_per_day_count[best_slot['day_str']] += 1

        return failed_sections

    def _create_exam_record(self, group, day_str, time_slot, room, num_students):
        return {
            'Subject': group['Subject'],
            'Section': group['Section'],
            'Instructor': group['Instructor'],
            'Date': day_str,
            'Time_Slot': time_slot,
            'Room': room,
            'Duration': int(group.get('Duration', 180)),
            'Num_Students': num_students,
            'Faculty': group.get('Faculty', '')
        }
