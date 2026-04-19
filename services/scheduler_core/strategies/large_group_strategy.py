import logging
import json
import pandas as pd
from typing import List, Dict, Any
from .base_strategy import BaseStrategy

class LargeGroupStrategy(BaseStrategy):
    def execute(self, exams_to_schedule: pd.DataFrame) -> List[Dict[str, Any]]:
        logging.info("Executing LargeGroupStrategy (Room 107 priority).")
        
        # 1. Selection criteria: Proctored, 2-rooms, with exam
        written_mask = (
            (exams_to_schedule['has_exam'] == True) &
            (exams_to_schedule['proctor_needed'] == True) &
            (exams_to_schedule['two_rooms_needed'] == True)
        )
        
        candidates_df = exams_to_schedule[written_mask].copy()
        if candidates_df.empty:
            return []

        # 2. Group by subject to find "clusters" for room 107
        candidates_df['student_count'] = candidates_df['Section'].map(
            lambda x: len(self.scheduler.section_students_map.get(x, []))
        )
        
        subject_groups = candidates_df.groupby('Subject').agg(
            total_students=('student_count', 'sum'),
            section_count=('Section', 'count'),
            sections=('Section', lambda x: list(x))
        ).reset_index()

        clusters = subject_groups[
            (subject_groups['section_count'].between(2, 4)) &
            (subject_groups['total_students'].between(80, 200))
        ].sort_values('total_students', ascending=False)

        if clusters.empty:
            return []

        scheduled_sections = set()
        
        # 3. Try to place clusters in room 107 slots
        from create_db import Session, ClassroomSlot
        db_session = Session()
        try:
            free_slots = db_session.query(ClassroomSlot).filter_by(
                classroom_number='107', is_booked=False
            ).order_by(ClassroomSlot.start_time).all()

            for _, cluster in clusters.iterrows():
                sections = cluster['sections']
                if any(s in scheduled_sections for s in sections):
                    continue

                all_students = set()
                for s_id in sections:
                    all_students.update(self.scheduler.section_students_map.get(s_id, []))

                for slot in free_slots:
                    if slot.is_booked: continue
                    
                    slot_day = slot.start_time.strftime('%Y-%m-%d')
                    
                    # Check student conflicts
                    has_conflict = False
                    for student in all_students:
                        if not self.constraint_engine.is_student_available(student, slot_day):
                            has_conflict = True
                            break
                    
                    if has_conflict: continue

                    # Book it
                    slot.is_booked = True
                    slot.booked_groups_info = json.dumps({
                        'subject': cluster['Subject'],
                        'sections': sections
                    }, ensure_ascii=False)
                    
                    time_slot = f"{slot.start_time.strftime('%H:%M')}-{slot.end_time.strftime('%H:%M')}"

                    for s_id in sections:
                        group_info = candidates_df[candidates_df['Section'] == s_id].iloc[0]
                        record = self._create_record(
                            group_info, slot_day, time_slot, '107', group_info['student_count']
                        )
                        record['pinned'] = True # Manual room 107 bookings are pinned
                        self.scheduler.schedule.append(record)
                        
                        # Update student maps
                        for student in self.scheduler.section_students_map.get(s_id, []):
                            if student not in self.scheduler.student_exams:
                                self.scheduler.student_exams[student] = []
                            self.scheduler.student_exams[student].append(record)
                        
                        scheduled_sections.add(s_id)
                        self.scheduler.exams_per_day_count[slot_day] += 1
                    
                    free_slots.remove(slot)
                    break
            
            db_session.commit()
            return list(scheduled_sections)
            
        except Exception as e:
            logging.error(f"Error in LargeGroupStrategy: {str(e)}")
            db_session.rollback()
            return []
        finally:
            db_session.close()
