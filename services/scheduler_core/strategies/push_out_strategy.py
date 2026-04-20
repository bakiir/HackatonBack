import logging
import math
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Dict

class PushOutStrategy:
    def __init__(self, scheduler, constraint_engine, grid_manager):
        self.scheduler = scheduler
        self.constraint_engine = constraint_engine
        self.grid_manager = grid_manager

    def execute(self, exam_groups: List[Dict]) -> List[Dict]:
        """
        Stage 2.7: Rip-and-Repair (Push-Out).
        Attempts to schedule failed sections by temporarily evicting 1-2 existing exams.
        """
        logging.info(f"Starting PushOutStrategy for {len(exam_groups)} sections.")
        failed_sections = []
        
        for group_dict in exam_groups:
            group = pd.Series(group_dict)
            success = self._try_push_out_for_section(group)
            if not success:
                failed_sections.append(group_dict)
        
        return failed_sections

    def _try_push_out_for_section(self, group):
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

        for day in self.scheduler.custom_dates:
            day_str = day.strftime('%Y-%m-%d')
            
            # Basic student availability check (soft) - at least allowed to have an exam this day
            if not all(self.constraint_engine.is_student_available(s_id, day_str, time_slot=None, strict=False) for s_id in students):
                continue

            for start_block in range(self.scheduler.num_blocks_in_day - total_blocks_needed + 1):
                day_start_dt = datetime.combine(day, self.scheduler.work_day_start.time())
                exam_start_dt = day_start_dt + timedelta(minutes=start_block * self.scheduler.time_step)
                exam_end_dt = exam_start_dt + timedelta(minutes=duration_minutes)
                exam_time_slot_str = f"{exam_start_dt.strftime('%H:%M')}-{exam_end_dt.strftime('%H:%M')}"
                
                # Strict overlap check for this specific timeslot
                if not all(self.constraint_engine.is_student_available(s_id, day_str, time_slot=exam_time_slot_str, strict=False) for s_id in students):
                    continue

                if not self.constraint_engine.is_instructor_available(instructor, day_str, exam_time_slot_str, group.to_dict()):
                    continue

                classroom_type = group.get('classroom_type', 'regular')
                candidate_rooms = [
                    r for r in self.scheduler.rooms 
                    if self.scheduler.room_types.get(r, 'regular') == classroom_type and
                    not self.constraint_engine.is_room_excluded(r, day.date(), exam_start_dt, exam_end_dt)
                ]
                
                for r in candidate_rooms:
                    room_str = str(r)
                    if self.scheduler.room_capacities.get(r, 0) < effective_capacity:
                        continue
                    
                    blockers = self._get_blocking_exams(room_str, day_str, start_block, total_blocks_needed)
                    
                    # Evict if 1-2 exams are blocking and not pinned
                    if 0 < len(blockers) <= 2 and not any(b.get('pinned', False) for b in blockers):
                        if self._attempt_eviction(group, day_str, start_block, total_blocks_needed, [room_str], blockers, exam_time_slot_str):
                            return True
        return False

    def _get_blocking_exams(self, room_str, day_str, start_block, total_blocks_needed):
        from ..utils import time_str_to_mins
        blockers = []
        for exam in self.scheduler.schedule:
            if exam['Date'] == day_str and room_str in str(exam['Room']):
                try:
                    s_str, _ = exam['Time_Slot'].split('-')
                    e_start_mins = time_str_to_mins(s_str)
                    
                    work_day_start_mins = self.scheduler.work_day_start.hour * 60 + self.scheduler.work_day_start.minute
                    e_start_block = int((e_start_mins - work_day_start_mins) / self.scheduler.time_step)
                    
                    e_dur_blocks = math.ceil(exam['Duration'] / self.scheduler.time_step)
                    e_total_blocks = e_dur_blocks + math.ceil(self.scheduler.buffer_time / self.scheduler.time_step)
                    
                    if max(start_block, e_start_block) < min(start_block + total_blocks_needed, e_start_block + e_total_blocks):
                        blockers.append(exam)
                except Exception:
                    continue
        return blockers

    def _attempt_eviction(self, group, day_str, start_block, total_blocks_needed, rooms_to_book, blockers, time_slot_str):
        # Implementation of eviction involves removing blockers and placing new exam
        # This requires the orchestrator to handle the re-scheduling of evicted exams
        # For simplicity and to avoid circular logic, we follow the old Planner's approach
        
        # 1. Store state for rollback if needed (Simplified)
        # 2. Rip: Remove blockers
        for b in blockers:
            self.scheduler._remove_exam_from_state(b)
        
        # 3. Place: Book new exam
        self.grid_manager.book_slot(day_str, rooms_to_book, start_block, total_blocks_needed)
        num_students = len(self.scheduler.section_students_map.get(group['Section'], []))
        exam_record = self._create_exam_record(group, day_str, time_slot_str, ",".join(rooms_to_book), num_students)
        self.scheduler.schedule.append(exam_record)
        
        for s_id in self.scheduler.section_students_map.get(group['Section'], []):
            if str(s_id) not in self.scheduler.student_exams: self.scheduler.student_exams[str(s_id)] = []
            self.scheduler.student_exams[str(s_id)].append(exam_record)
        
        # 4. Attempt to re-schedule blockers (Greedy)
        # Note: In a fully modular design, this would be a separate push back to the queue
        # For now, we try to put them back immediatey or add to failed_sections
        for b in blockers:
            # Re-wrap as Series for GreedyStrategy if needed
            self.scheduler.failed_sections.append(b) 
            
        return True

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
