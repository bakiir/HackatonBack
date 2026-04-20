import logging
import pandas as pd
from typing import List, Dict, Any
from .constraint_engine import ConstraintEngine
from .grid_manager import GridManager
from .strategies.large_group_strategy import LargeGroupStrategy
from .strategies.greedy_strategy import GreedyStrategy
from .strategies.flexible_strategy import FlexibleStrategy
from .strategies.relaxed_strategy import RelaxedStrategy
from .strategies.push_out_strategy import PushOutStrategy

class PlannerOrchestrator:
    def __init__(self, scheduler):
        self.scheduler = scheduler
        self.constraint_engine = ConstraintEngine(scheduler)
        self.grid_manager = GridManager(
            rooms=scheduler.rooms,
            dates=[d.strftime('%Y-%m-%d') for d in scheduler.custom_dates],
            num_blocks=scheduler.num_blocks_in_day
        )

    def _load_manual_bookings(self):
        from create_db import ClassroomSlot, Session
        import json
        import logging
        import math

        session = Session()
        scheduled_ids = set()
        try:
            booked_slots = session.query(ClassroomSlot).filter_by(is_booked=True).all()
            for slot in booked_slots:
                if not slot.booked_groups_info: continue
                booking_info = json.loads(slot.booked_groups_info)
                subject = booking_info.get('subject')
                sections = booking_info.get('sections')
                if not subject or not sections: continue

                slot_date_str = slot.start_time.strftime('%Y-%m-%d')
                base_time_slot = f"{slot.start_time.strftime('%H:%M')}-{slot.end_time.strftime('%H:%M')}"

                # Обновляем GridManager
                start_mins = slot.start_time.hour * 60 + slot.start_time.minute
                work_day_mins = self.scheduler.work_day_start.hour * 60 + self.scheduler.work_day_start.minute
                start_block = int((start_mins - work_day_mins) / self.scheduler.time_step)
                dur_minutes = (slot.end_time - slot.start_time).total_seconds() / 60
                exam_blocks = math.ceil(dur_minutes / self.scheduler.time_step)
                total_blocks = exam_blocks + math.ceil(self.scheduler.buffer_time / self.scheduler.time_step)

                # Бронируем сетку (может выбросить ошибку если аудитория не существует в GridManager, проверяем)
                if slot.classroom_number in self.scheduler.rooms:
                    try:
                        self.grid_manager.book_slot(slot_date_str, [slot.classroom_number], start_block, total_blocks)
                    except ValueError:
                        pass # Ignore if out of bounds

                for section_id in sections:
                    if section_id in self.scheduler.exam_groups['Section'].values:
                        group_info = self.scheduler.exam_groups[self.scheduler.exam_groups['Section'] == section_id].iloc[0]
                        num_students = len(self.scheduler.section_students_map.get(section_id, []))

                        exam_record = {
                            'Date': slot_date_str, 'Subject': subject, 'Instructor': group_info['Instructor'],
                            'EduProgram': group_info['EduProgram'], 'Section': section_id,
                            'Students_Count': int(num_students), 'Room': slot.classroom_number,
                            'Time_Slot': base_time_slot, 'Base_Time_Slot': base_time_slot,
                            'Duration': int(dur_minutes), 'pinned': True
                        }
                        
                        self.scheduler.schedule.append(exam_record)
                        scheduled_ids.add(section_id)
                        
                        for sid in self.scheduler.section_students_map.get(section_id, []):
                            if str(sid) not in self.scheduler.student_exams:
                                self.scheduler.student_exams[str(sid)] = []
                            self.scheduler.student_exams[str(sid)].append(exam_record)
                        
                        self.scheduler.exams_per_day_count[slot_date_str] = self.scheduler.exams_per_day_count.get(slot_date_str, 0) + 1
            
            logging.info(f"Loaded manual bookings for {len(scheduled_ids)} sections.")
            return list(scheduled_ids)
        except Exception as e:
            import traceback
            logging.error(f"Error loading manual bookings: {traceback.format_exc()}")
            return []
        finally:
            session.close()

    def run(self):
        logging.info("Starting PlannerOrchestrator execution.")
        
        # 0. Initial state reset
        self.scheduler.schedule = []
        self.scheduler.student_exams = {}
        self.scheduler.failed_sections = []
        self.scheduler.exams_per_day_count = {d.strftime('%Y-%m-%d'): 0 for d in self.scheduler.custom_dates}
        
        # Attach engines for legacy optimizer compatibility
        self.scheduler.constraint_engine = self.constraint_engine
        self.scheduler.grid_manager = self.grid_manager
        self.scheduler.room_availability_grid = self.grid_manager.grid
        
        # 0.5. Load Room Exclusions
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
            logging.info(f"Loaded {len(excls)} room exclusions.")
        except Exception as e:
            logging.error(f"Error loading room exclusions: {e}")
            self.scheduler.exclusions_by_date = {}
        finally:
            sess.close()

        exam_groups = self.scheduler.exam_groups.copy()
        
        # 0.6. Load Manual Bookings
        manually_scheduled_ids = self._load_manual_bookings()
        
        # 1. Stage 1: Priority Clusters (Room 107)
        lg_strategy = LargeGroupStrategy(self.scheduler, self.constraint_engine, self.grid_manager)
        
        # Передаем только те, что еще не запланированы
        exam_groups_for_lg = exam_groups[~exam_groups['Section'].isin(manually_scheduled_ids)].copy()
        lg_scheduled_ids = lg_strategy.execute(exam_groups_for_lg)
        
        # Объединяем запланированные
        all_scheduled_ids = set(manually_scheduled_ids).union(set(lg_scheduled_ids))
        logging.info(f"Stage 1 (LargeGroups) scheduled {len(lg_scheduled_ids)} sections.")
        
        # 2. Stage 2: Greedy Placement (Strict Rules)
        remaining_exams = exam_groups[~exam_groups['Section'].isin(all_scheduled_ids)]
        remaining_exams = remaining_exams[remaining_exams['has_exam'] == True]
        
        greedy_strategy = GreedyStrategy(self.scheduler, self.constraint_engine, self.grid_manager)
        failed_after_greedy = greedy_strategy.execute(remaining_exams)
        logging.info(f"Stage 2 (Greedy) finished. {len(failed_after_greedy)} sections failed.")
        
        # 3. Stage 3: Flexible Placement (Allowing 2 per day)
        if failed_after_greedy:
            flex_strategy = FlexibleStrategy(self.scheduler, self.constraint_engine, self.grid_manager)
            failed_after_flex = flex_strategy.execute(failed_after_greedy)
            logging.info(f"Stage 3 (Flexible) finished. {len(failed_after_flex)} sections still failed.")
            self.scheduler.failed_sections = failed_after_flex
        
        # Stage 4: Push-Out (Rip-and-Repair)
        if failed_after_flex:
            po_strategy = PushOutStrategy(self.scheduler, self.constraint_engine, self.grid_manager)
            failed_after_po = po_strategy.execute(failed_after_flex)
            logging.info(f"Stage 4 (PushOut) finished. {len(failed_after_po)} sections still failed.")
            failed_after_greedy = failed_after_po # For next stages

        # Stage 5: Relaxed Placement (Minimal Conflicts)
        if failed_after_greedy:
            relaxed_strategy = RelaxedStrategy(self.scheduler, self.constraint_engine, self.grid_manager)
            failed_after_relaxed = relaxed_strategy.execute(pd.DataFrame(failed_after_greedy))
            logging.info(f"Stage 5 (Relaxed) finished. {len(failed_after_relaxed)} sections still failed.")
            self.scheduler.failed_sections = failed_after_relaxed
        
        # Stage 5: Optimization
        if self.scheduler.schedule:
            logging.info("Starting Simulated Annealing optimization.")
            self.scheduler.optimize_schedule(self.scheduler.student_exams)
            self.scheduler.schedule_df = pd.DataFrame(self.scheduler.schedule)
            
        logging.info(f"Orchestration complete. Scheduled: {len(self.scheduler.schedule)}, Failed: {len(self.scheduler.failed_sections)}")
