from typing import Optional, List
from sqlalchemy.orm import Session
from create_db import ExamSession, ExamSessionDraft, AdminStatusDraft
from .base_repository import BaseRepository

class ExamRepository(BaseRepository[ExamSession]):
    def __init__(self, session: Session):
        super().__init__(ExamSession, session)

    def get_active_session(self) -> Optional[ExamSession]:
        return self.session.query(ExamSession).filter(ExamSession.is_active == True).first()

class ExamDraftRepository(BaseRepository[ExamSessionDraft]):
    def __init__(self, session: Session):
        super().__init__(ExamSessionDraft, session)

    def get_active_draft(self) -> Optional[ExamSessionDraft]:
        return self.session.query(ExamSessionDraft).filter(ExamSessionDraft.is_active == True).first()
