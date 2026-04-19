import logging
import pandas as pd
from typing import List, Dict, Any, Optional

class QueryManager:
    def __init__(self, scheduler):
        self.scheduler = scheduler

    def get_student_schedule_data(self, student_id: str) -> pd.DataFrame:
        """Find sections for a specific student."""
        logging.info(f"Searching sections for student {student_id}")
        
        if self.scheduler.exams_df is None or self.scheduler.exams_df.empty:
            return pd.DataFrame()

        if self.scheduler.schedule_df is None or self.scheduler.schedule_df.empty:
            return pd.DataFrame()

        student_id = str(student_id)
        student_sections = self.scheduler.exams_df[
            self.scheduler.exams_df['fake_id'] == student_id
        ]['Section'].unique()

        if len(student_sections) == 0:
            return pd.DataFrame()

        return self.scheduler.schedule_df[
            self.scheduler.schedule_df['Section'].isin(student_sections)
        ].sort_values(['Date', 'Time_Slot'])

    def get_section_details(self, section_id: str) -> Dict[str, Any]:
        """Get full details for a section including students and schedule."""
        section_data = self.scheduler.exams_df[
            self.scheduler.exams_df['Section'] == section_id
        ]

        if section_data.empty:
            return {"error": "Section not found"}

        info = {
            'section_id': section_id,
            'instructor': section_data['Instructor'].iloc[0],
            'subject': section_data['Subject'].iloc[0],
            'edu_program': section_data['EduProgram'].iloc[0],
            'years_of_study': section_data['YearsOfStudy'].iloc[0],
            'total_students': len(section_data)
        }

        schedule = None
        if self.scheduler.schedule_df is not None and not self.scheduler.schedule_df.empty:
            sched_row = self.scheduler.schedule_df[
                self.scheduler.schedule_df['Section'] == section_id
            ].to_dict('records')
            schedule = sched_row[0] if sched_row else None

        students = section_data[['fake_id', 'fake_name']].drop_duplicates().to_dict('records')
        
        # Enrich student data with seats if available
        for student in students:
            student_id = str(student['fake_id'])
            if schedule:
                date_part = pd.to_datetime(schedule['Date']).date()
                key = f"{date_part}|{schedule['Time_Slot'].strip()}|{info['subject'].strip()}|{student_id}"
                seat_info = self.scheduler.seat_assignments.get(key, {})
                student['seat'] = seat_info.get('seat')
                student['room'] = seat_info.get('room')

        return {
            "info": info,
            "schedule": schedule,
            "students": students
        }
