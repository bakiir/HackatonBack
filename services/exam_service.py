import logging
import pandas as pd
from repositories.exam_repository import ExamRepository, ExamDraftRepository
from create_db import AdminStatusDraft

class ExamService:
    def __init__(self, exam_repo: ExamRepository, draft_repo: ExamDraftRepository):
        self.exam_repo = exam_repo
        self.draft_repo = draft_repo

    def get_subjects_by_faculty(self, scheduler, faculty: str, user_role: str):
        """Returns unique subjects for a faculty, considering user role."""
        if not scheduler:
            raise ValueError("Scheduler not initialized")
            
        return scheduler.get_by_faculty(faculty)

    def delete_subject(self, scheduler, subject: str, user_role: str):
        """Deletes all sections of a subject and updates the draft."""
        if not scheduler:
            raise ValueError("Scheduler not initialized")

        active_draft = self.draft_repo.get_active_draft()
        if active_draft and user_role in ["admin-sdt", "admin-sem", "admin-gum", "admin-spigu"]:
            self._ensure_admin_status_in_progress(active_draft.id, user_role)

        sections_to_delete = scheduler.exam_groups[
            scheduler.exam_groups['Subject'] == subject
        ]['Section'].tolist()

        scheduler._delete_sections(sections_to_delete)

        if active_draft:
            active_draft.exams_data = scheduler.exam_groups.to_json(orient='records')
            self.draft_repo.update()

        return sections_to_delete

    def delete_section(self, scheduler, section: str, user_role: str):
        """Deletes a specific section and updates the draft."""
        if not scheduler:
            raise ValueError("Scheduler not initialized")

        active_draft = self.draft_repo.get_active_draft()
        if active_draft and user_role in ["admin-sdt", "admin-sem", "admin-gum", "admin-spigu"]:
            self._ensure_admin_status_in_progress(active_draft.id, user_role)

        if not isinstance(scheduler.schedule_df, pd.DataFrame):
            scheduler.schedule_df = pd.DataFrame(columns=['Section', 'Date', 'Time_Slot', 'Room', 'Proctor'])

        scheduler._delete_sections([section])

        if active_draft:
            active_draft.exams_data = scheduler.exam_groups.to_json(orient='records')
            self.draft_repo.update()

        return section

    def generate_schedule(self, scheduler):
        """Orchestrates the schedule generation and database persistence."""
        from create_db import ExamSession, ExamSessionDraft, AdminStatusDraft
        from users_db import are_all_admins_ready, get_all_admin_statuses
        from services.scheduler_core.utils import handle_nan_values

        active_draft = self.draft_repo.get_active_draft()
        if not active_draft:
            raise ValueError("Active draft not found")

        if not are_all_admins_ready(self.draft_repo.session, active_draft.id, model=AdminStatusDraft):
            statuses = get_all_admin_statuses(self.draft_repo.session, active_draft.id, model=AdminStatusDraft)
            ready_roles = {s.role for s in statuses if s.status == 'ready'}
            not_ready_roles = [role for role in ["admin-sdt", "admin-sem", "admin-gum", "admin-spigu"] if role not in ready_roles]
            raise ValueError(f"Administrators not ready: {not_ready_roles}")

        # 1. Generate
        scheduler.create_schedule()

        # 2. Analyze
        analysis_report = scheduler.analyze_failed_sections_details()

        # 3. Save as Final Session
        self.exam_repo.session.query(ExamSession).update({'is_active': False})
        new_session = ExamSession(
            title=scheduler.title,
            start_date=scheduler.original_start_date,
            original_start_date=scheduler.original_start_date,
            days=scheduler.original_num_days,
            original_num_days=scheduler.original_num_days,
            schedule_data=scheduler.schedule_df.to_json(orient='records'),
            exams_data=scheduler.exams_df.to_json(orient='records'),
            rooms_data=scheduler.rooms_df.to_json(orient='records'),
            faculties_data=scheduler.faculties_df.to_json(orient='records'),
            seat_assignments=scheduler.seat_assignments,
            is_active=True
        )
        self.exam_repo.add(new_session)

        # 4. Cleanup Drafts
        self.draft_repo.session.query(AdminStatusDraft).delete()
        self.draft_repo.session.query(ExamSessionDraft).delete()
        self.draft_repo.session.commit()

        sanitized_schedule = handle_nan_values(scheduler.schedule_df)
        
        return sanitized_schedule, analysis_report

    def _ensure_admin_status_in_progress(self, session_id: int, role: str):
        """Ensures that the admin status is created/updated (logic from legacy code)."""
        from users_db import get_or_create_admin_status
        get_or_create_admin_status(self.draft_repo.session, session_id, role, model=AdminStatusDraft)
        self.draft_repo.session.commit()

    def update_exam_durations(self, scheduler, exams_data, user_role: str):
        """Updates durations for multiple sections and updates the draft."""
        if not scheduler:
            raise ValueError("Scheduler not initialized")

        active_draft = self.draft_repo.get_active_draft()
        if active_draft and user_role in ["admin-sdt", "admin-sem", "admin-gum", "admin-spigu"]:
            self._ensure_admin_status_in_progress(active_draft.id, user_role)

        scheduler.update_exam_durations(exams_data)

        if active_draft:
            active_draft.exams_data = scheduler.exam_groups.to_json(orient='records')
            self.draft_repo.update()

    def batch_update_exams(self, scheduler, exams_data, user_role: str):
        """Batch updates exam records and updates the draft."""
        if not scheduler:
            raise ValueError("Scheduler not initialized")

        active_draft = self.draft_repo.get_active_draft()
        if active_draft and user_role in ["admin-sdt", "admin-sem", "admin-gum", "admin-spigu"]:
            self._ensure_admin_status_in_progress(active_draft.id, user_role)

        scheduler.batch_update_exams(exams_data)

        if active_draft:
            active_draft.exams_data = scheduler.exam_groups.to_json(orient='records')
            self.draft_repo.update()

    def get_subject_groups(self, scheduler, subject: str):
        """Returns grouped data for a subject."""
        if not scheduler:
            raise ValueError("Scheduler not initialized")
            
        subject_groups = scheduler.exam_groups[scheduler.exam_groups['Subject'] == subject]
        if subject_groups.empty:
            return None

        grouped_data = {}
        for edu_program in subject_groups['EduProgram'].unique():
            program_groups = subject_groups[subject_groups['EduProgram'] == edu_program]
            grouped_data[edu_program] = program_groups.to_dict('records')
            
        return grouped_data
