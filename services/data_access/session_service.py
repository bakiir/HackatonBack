import logging
import io
import pandas as pd
from create_db import ExamSession, ExamSessionDraft
from repositories.session_repository import SessionRepository, SessionDraftRepository
from .excel_handler import ExcelHandler

class SessionService:
    def __init__(self, session_repo: SessionRepository, draft_repo: SessionDraftRepository):
        self.session_repo = session_repo
        self.draft_repo = draft_repo

    def load_scheduler_from_db(self, scheduler, session_id: int):
        """Initializes scheduler state from a database session."""
        db_session = self.session_repo.get_by_id(session_id)
        if not db_session:
            raise ValueError(f"Session with ID {session_id} not found")

        logging.info(f"Loading scheduler from DB session: {db_session.title}")
        
        scheduler.title = db_session.title
        scheduler.original_start_date = db_session.original_start_date
        scheduler.original_num_days = db_session.original_num_days
        
        # Load dataframes from JSON strings in DB
        if db_session.schedule_data:
            scheduler.schedule_df = pd.read_json(io.StringIO(db_session.schedule_data))
        if db_session.exams_data:
            scheduler.exams_df = pd.read_json(io.StringIO(db_session.exams_data))
        if db_session.rooms_data:
            scheduler.rooms_df = pd.read_json(io.StringIO(db_session.rooms_data))
        if db_session.faculties_data:
            scheduler.faculties_df = pd.read_json(io.StringIO(db_session.faculties_data))

        scheduler.seat_assignments = db_session.get_seat_assignments()
        scheduler._prepare_data()

    def create_new_session(self, title: str, start_date, num_days, exams_df, rooms_df, faculties_df) -> ExamSession:
        """Saves a new session to the database."""
        new_session = ExamSession(
            title=title,
            start_date=start_date,
            original_start_date=start_date,
            days=num_days,
            original_num_days=num_days
        )
        new_session.exams_data = exams_df.to_json(orient='records')
        new_session.rooms_data = rooms_df.to_json(orient='records')
        new_session.faculties_data = faculties_df.to_json(orient='records')
        
        return self.session_repo.add(new_session)

    def create_draft(self, title: str, start_date, num_days, exams_df, rooms_df, faculties_df) -> ExamSessionDraft:
        """Saves a new session draft to the database."""
        new_draft = ExamSessionDraft(
            title=title,
            start_date=start_date,
            days=num_days,
            is_active=True
        )
        new_draft.exams_data = exams_df.to_json(orient='records')
        new_draft.rooms_data = rooms_df.to_json(orient='records')
        new_draft.faculties_data = faculties_df.to_json(orient='records')
        
        return self.draft_repo.add(new_draft)
    def initialize_scheduler_session(self, title, exams_file, rooms_file, faculties_file, start_date, num_days):
        """Orchestrates the initialization of a new scheduling session."""
        from services.exam_scheduler import ExamScheduler
        from create_db import RoomExclusion, update_classroom_slots
        from services.scheduler_core.utils import role_to_faculty
        import tempfile
        import os
        from datetime import datetime

        # 1. Clear old room exclusions
        self.draft_repo.session.query(RoomExclusion).delete()
        self.draft_repo.session.commit()

        # 2. Process files and initialize scheduler
        with tempfile.TemporaryDirectory() as temp_dir:
            exams_path = os.path.join(temp_dir, "exams.xlsx")
            rooms_path = os.path.join(temp_dir, "rooms.xlsx")
            faculties_path = os.path.join(temp_dir, "faculties.xlsx")

            exams_file.save(exams_path)
            rooms_file.save(rooms_path)
            faculties_file.save(faculties_path)

            scheduler = ExamScheduler(
                title=title,
                exams_file=exams_path,
                rooms_file=rooms_path,
                faculties_file=faculties_path,
                start_date=start_date,
                num_days=num_days
            )

        # 3. Update classroom slots
        update_classroom_slots(scheduler.get_current_dates(), scheduler.rooms_df)

        # 4. Create draft
        self.draft_repo.session.query(ExamSessionDraft).update({'is_active': False})
        new_draft = self.create_draft(
            title=title,
            start_date=datetime.strptime(start_date, '%Y-%m-%d').date(),
            num_days=num_days,
            exams_df=scheduler.exams_df,
            rooms_df=scheduler.rooms_df,
            faculties_df=scheduler.faculties_df
        )

        return scheduler, new_draft
