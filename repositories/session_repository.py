from typing import Optional, List
from sqlalchemy.orm import Session
from create_db import ExamSession, ExamSessionDraft, AdminStatusDraft
from .base_repository import BaseRepository

class SessionRepository(BaseRepository[ExamSession]):
    def __init__(self, session: Session):
        super().__init__(ExamSession, session)

    def get_active_session(self) -> Optional[ExamSession]:
        return self.session.query(ExamSession).filter(ExamSession.is_active == True).first()

    def set_active_session(self, session_id: int) -> bool:
        # Deactivate all sessions
        self.session.query(ExamSession).update({ExamSession.is_active: False})
        
        # Activate the specific session
        session = self.get_by_id(session_id)
        if session:
            session.is_active = True
            self.session.commit()
            return True
        return False

class SessionDraftRepository(BaseRepository[ExamSessionDraft]):
    def __init__(self, session: Session):
        super().__init__(ExamSessionDraft, session)

    def get_draft_by_title(self, title: str) -> Optional[ExamSessionDraft]:
        return self.session.query(ExamSessionDraft).filter(ExamSessionDraft.title == title).first()

    def get_admin_statuses(self, session_id: int) -> List[AdminStatusDraft]:
        return self.session.query(AdminStatusDraft).filter(AdminStatusDraft.session_id == session_id).all()

    def update_admin_status(self, session_id: int, role: str, status: str) -> None:
        admin_status = self.session.query(AdminStatusDraft).filter(
            AdminStatusDraft.session_id == session_id,
            AdminStatusDraft.role == role
        ).first()
        
        if admin_status:
            admin_status.status = status
        else:
            admin_status = AdminStatusDraft(session_id=session_id, role=role, status=status)
            self.session.add(admin_status)
        
        self.session.commit()
