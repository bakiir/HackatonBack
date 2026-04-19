from abc import ABC, abstractmethod
import pandas as pd
from typing import List, Dict, Any

class BaseStrategy(ABC):
    def __init__(self, scheduler, constraint_engine, grid_manager):
        self.scheduler = scheduler
        self.constraint_engine = constraint_engine
        self.grid_manager = grid_manager

    @abstractmethod
    def execute(self, exams_to_schedule: pd.DataFrame) -> List[Dict[str, Any]]:
        """Executes the scheduling strategy and returns a list of failed sections."""
        pass

    def _create_record(self, group, day_str, time_slot, room_str, student_count):
        return {
            'Date': day_str,
            'Subject': group['Subject'],
            'Instructor': group['Instructor'],
            'EduProgram': group['EduProgram'],
            'Section': group['Section'],
            'Students_Count': student_count,
            'Room': room_str,
            'Time_Slot': time_slot,
            'Duration': int(group.get('Duration', 180)),
            'proctor_needed': group.get('proctor_needed', False),
            'two_rooms_needed': group.get('two_rooms_needed', False),
            'pinned': False
        }
