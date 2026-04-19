import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from .utils import get_exam_type, check_overlap

class ConstraintEngine:
    def __init__(self, scheduler):
        self.scheduler = scheduler

    def is_student_available(self, student_id: str, day_str: str, time_slot: Optional[str] = None, strict: bool = True) -> bool:
        """
        Checks if a student can take an exam on a given day/time.
        :param strict: If True, only 1 exam per day allowed. If False, allows 2 if no time overlap.
        """
        student_id = str(student_id)
        exams_on_day = [exam for exam in self.scheduler.student_exams.get(student_id, []) if exam['Date'] == day_str]
        
        if strict:
            return len(exams_on_day) == 0
        
        if len(exams_on_day) >= 2:
            return False
            
        if time_slot:
            for exam in exams_on_day:
                if check_overlap(exam['Time_Slot'], time_slot):
                    return False
        return True

    def is_instructor_available(self, instructor: str, day_str: str, time_slot: str, group_info: Dict[str, Any]) -> bool:
        """Checks instructor availability considering overlapping and exam types."""
        if not time_slot or '-' not in time_slot:
            return False
            
        start_t, end_t = [datetime.strptime(t, '%H:%M') for t in time_slot.split('-')]
        new_type = get_exam_type(group_info)

        for exam in self.scheduler.schedule:
            if exam.get('Instructor') == instructor and exam.get('Date') == day_str:
                exam_time = exam.get('Time_Slot')
                if not exam_time or '-' not in exam_time:
                    continue
                e_start, e_end = [datetime.strptime(t, '%H:%M') for t in exam_time.split('-')]
                
                # Overlap check
                if start_t < e_end and e_start < end_t:
                    old_type = get_exam_type(exam)
                    # Written exams can overlap if they start/end far enough apart (proctoring rule)
                    if new_type == "written" and old_type == "written":
                        if start_t >= e_start + timedelta(hours=1) or e_start >= start_t + timedelta(hours=1):
                            continue
                    return False
        return True

    def is_room_excluded(self, room: str, day_date, start_dt: datetime, end_dt: datetime) -> bool:
        """Checks if a room is excluded via Admin overrides."""
        if day_date in self.scheduler.exclusions_by_date:
            for ex in self.scheduler.exclusions_by_date[day_date]:
                if str(ex.room_number) == str(room):
                    if max(start_dt, ex.start_time) < min(end_dt, ex.end_time):
                        return True
        return False

    def find_suitable_rooms(self, available_rooms: List[str], required_capacity: int, group_info: Dict[str, Any]) -> Tuple[Optional[str], List[str]]:
        """Selects room(s) based on capacity, type, and proctoring requirements."""
        from itertools import combinations
        
        two_rooms_needed = group_info.get('two_rooms_needed', False)
        proctor_needed = group_info.get('proctor_needed', False)
        has_exam = group_info.get('has_exam', True)
        classroom_type = group_info.get('classroom_type', 'regular')

        # Prohibited rooms for specific exam types (non-proctored)
        forbidden_rooms = {'319', '419', '436', '526', '536', '338/1', '334/1'}
        if not two_rooms_needed and has_exam and not proctor_needed:
            available_rooms = [r for r in available_rooms if str(r) not in forbidden_rooms]
        
        # Room 107 priority rule
        is_written_exam = (two_rooms_needed and proctor_needed and has_exam)
        if not is_written_exam and '107' in available_rooms:
            available_rooms = [r for r in available_rooms if str(r) != '107']

        # Filter by type (IT lab vs regular)
        typed_rooms = [r for r in available_rooms if self.scheduler.room_types.get(r, 'regular') == classroom_type]
        if not typed_rooms and classroom_type == 'regular':
            typed_rooms = [r for r in available_rooms if self.scheduler.room_types.get(r, 'regular') == 'it_lab']

        if not typed_rooms:
            return None, []

        if not two_rooms_needed:
            suitable = [r for r in typed_rooms if self.scheduler.room_capacities.get(r, 0) >= required_capacity]
            if not suitable:
                return None, []
            best_room = min(suitable, key=lambda r: self.scheduler.room_capacities.get(r, 0))
            return str(best_room), [str(best_room)]
        else:
            # Multi-room logic
            single_large = [r for r in typed_rooms if self.scheduler.room_capacities.get(r, 0) >= required_capacity]
            if single_large:
                best = min(single_large, key=lambda r: self.scheduler.room_capacities.get(r, 0))
                return str(best), [str(best)]
            
            # Pair logic
            room_pairs = list(combinations(typed_rooms, 2))
            suitable_pairs = [p for p in room_pairs if self.scheduler.room_capacities.get(str(p[0]), 0) + self.scheduler.room_capacities.get(str(p[1]), 0) >= required_capacity]
            if not suitable_pairs:
                return None, []
            
            scored_pairs = []
            for p in suitable_pairs:
                r1, r2 = str(p[0]), str(p[1])
                # Extraction logic for floor proximity
                n1 = int(''.join(filter(str.isdigit, r1)) or 0)
                n2 = int(''.join(filter(str.isdigit, r2)) or 0)
                floor1, floor2 = (n1//100) if n1>=100 else -1, (n2//100) if n2>=100 else -2
                score = 0 if floor1 == floor2 else 1
                scored_pairs.append(((r1, r2), score, abs(n1-n2), self.scheduler.room_capacities.get(r1,0)+self.scheduler.room_capacities.get(r2,0)))
            
            scored_pairs.sort(key=lambda x: (x[1], x[2], x[3]))
            best_pair = scored_pairs[0][0]
            return f"{best_pair[0]},{best_pair[1]}", list(best_pair)
