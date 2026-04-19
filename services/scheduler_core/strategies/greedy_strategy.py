import logging
import math
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Dict, Any
from .base_strategy import BaseStrategy

class GreedyStrategy(BaseStrategy):
    def execute(self, exams_to_schedule: pd.DataFrame) -> List[Dict[str, Any]]:
        logging.info("Executing GreedyStrategy.")
        failed = []
        
        # Sort by student count (largest first)
        exams_to_schedule['student_count'] = exams_to_schedule['Section'].map(
            lambda x: len(self.scheduler.section_students_map.get(x, []))
        )
        sorted_exams = exams_to_schedule.sort_values('student_count', ascending=False)

        for _, group in sorted_exams.iterrows():
            section_id = group['Section']
            students = self.scheduler.section_students_map.get(section_id, [])
            num_students = len(students)
            duration = int(group.get('Duration', 180))
            instructor = group['Instructor']
            two_rooms = group.get('two_rooms_needed', False)
            classroom_type = group.get('classroom_type', 'regular')

            effective_capacity = num_students * 2 if two_rooms else num_students
            blocks_needed = math.ceil(duration / self.scheduler.time_step) + math.ceil(self.scheduler.buffer_time / self.scheduler.time_step)

            found_slot = False
            # Search days
            for day_dt in self.scheduler.custom_dates:
                day_str = day_dt.strftime('%Y-%m-%d')
                
                # Strict student check (1 exam per day)
                if not all(self.constraint_engine.is_student_available(s, day_str, strict=True) for s in students):
                    continue

                # Search grid for earliest free block
                for start_block in range(self.scheduler.num_blocks_in_day - blocks_needed + 1):
                    day_start_dt = datetime.combine(day_dt.date(), self.scheduler.work_day_start.time())
                    exam_start = day_start_dt + timedelta(minutes=start_block * self.scheduler.time_step)
                    exam_end = exam_start + timedelta(minutes=duration)
                    time_slot = f"{exam_start.strftime('%H:%M')}-{exam_end.strftime('%H:%M')}"

                    if not self.constraint_engine.is_instructor_available(instructor, day_str, time_slot, group.to_dict()):
                        continue

                    # Filter available rooms in grid
                    available_rooms = [
                        r for r in self.scheduler.rooms
                        if self.grid_manager.is_slot_free(day_str, str(r), start_block, blocks_needed) and
                        not self.constraint_engine.is_room_excluded(r, day_dt.date(), exam_start, exam_end)
                    ]

                    room_str, rooms_to_book = self.constraint_engine.find_suitable_rooms(available_rooms, effective_capacity, group.to_dict())

                    if room_str:
                        # Success!
                        self.grid_manager.book_slot(day_str, rooms_to_book, start_block, blocks_needed)
                        record = self._create_record(group, day_str, time_slot, room_str, num_students)
                        self.scheduler.schedule.append(record)
                        
                        for s_id in students:
                            if s_id not in self.scheduler.student_exams: self.scheduler.student_exams[s_id] = []
                            self.scheduler.student_exams[s_id].append(record)
                        
                        self.scheduler.exams_per_day_count[day_str] += 1
                        found_slot = True
                        break
                
                if found_slot: break
            
            if not found_slot:
                failed.append(group.to_dict())
                
        return failed
