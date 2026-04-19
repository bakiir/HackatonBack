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
        
        # 1. Stage 1: Priority Clusters (Room 107)
        lg_strategy = LargeGroupStrategy(self.scheduler, self.constraint_engine, self.grid_manager)
        scheduled_ids = lg_strategy.execute(exam_groups)
        logging.info(f"Stage 1 (LargeGroups) scheduled {len(scheduled_ids)} sections.")
        
        # 2. Stage 2: Greedy Placement (Strict Rules)
        remaining_exams = exam_groups[~exam_groups['Section'].isin(scheduled_ids)]
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
