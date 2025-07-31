from sqlalchemy import create_engine, Column, Integer, String, Date, DateTime, Boolean, Text, ForeignKey
from sqlalchemy.dialects.sqlite import JSON  # Изменение 1: Правильный импорт JSON
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime
import json

Base = declarative_base()


class ExamSession(Base):
    __tablename__ = "exam_session"

    id = Column(Integer, primary_key=True)
    title = Column(String(150), nullable=False)
    start_date = Column(Date, nullable=False)
    original_start_date = Column(Date)  # Изменение 2: Добавлено для хранения оригинальной даты
    days = Column(Integer)
    original_num_days = Column(Integer)  # Изменение 3: Добавлено для хранения оригинального количества дней
    created_at = Column(DateTime, default=datetime.utcnow)

    # Изменение 4: Используем Column(JSON) вместо json.dumps()
    seat_assignments = Column(JSON)

    schedule_data = Column(Text)
    exams_data = Column(Text)
    rooms_data = Column(Text)
    faculties_data = Column(Text)
    is_active = Column(Boolean, default=False)

    # Изменение 5: Добавлены методы для работы с JSON данными
    def set_schedule(self, schedule_dict):
        self.schedule_data = json.dumps(schedule_dict, ensure_ascii=False)

    def get_schedule(self):
        return json.loads(self.schedule_data) if self.schedule_data else None

    def set_seat_assignments(self, assignments):
        self.seat_assignments = assignments

    def get_seat_assignments(self):
        return self.seat_assignments if self.seat_assignments else {}

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "original_start_date": self.original_start_date.isoformat() if self.original_start_date else None,
            "days": self.days,
            "original_num_days": self.original_num_days,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "seat_assignments": self.seat_assignments if self.seat_assignments else {},
            "schedule_data": json.loads(self.schedule_data) if self.schedule_data else None,
            "exams_data": json.loads(self.exams_data) if self.exams_data else None,
            "rooms_data": json.loads(self.rooms_data) if self.rooms_data else None,
            "faculties_data": json.loads(self.faculties_data) if self.faculties_data else None,
            "is_active": self.is_active
        }


# Новая модель для статусов администраторов черновиков
class AdminStatusDraft(Base):
    __tablename__ = "admin_status_draft"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("exam_session_draft.id"), nullable=False)
    role = Column(String(50), nullable=False)
    status = Column(String(20), default="in_progress")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "status": self.status,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

class ExamSessionDraft(Base):
    __tablename__ = "exam_session_draft"

    id = Column(Integer, primary_key=True)
    title = Column(String(150), nullable=False)
    start_date = Column(Date, nullable=False)
    days = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    faculties_data = Column(Text)  # Данные о факультетах
    exams_data = Column(Text)     # Данные об экзаменах
    rooms_data = Column(Text)     # Данные о помещениях
    is_active = Column(Boolean, default=False)

    def set_faculties(self, faculties_dict):
        self.faculties_data = json.dumps(faculties_dict, ensure_ascii=False)

    def get_faculties(self):
        return json.loads(self.faculties_data) if self.faculties_data else None

    def set_exams(self, exams_dict):
        self.exams_data = json.dumps(exams_dict, ensure_ascii=False)

    def get_exams(self):
        return json.loads(self.exams_data) if self.exams_data else None

    def set_rooms(self, rooms_dict):
        self.rooms_data = json.dumps(rooms_dict, ensure_ascii=False)

    def get_rooms(self):
        return json.loads(self.rooms_data) if self.rooms_data else None

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "days": self.days,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "faculties_data": json.loads(self.faculties_data) if self.faculties_data else None,
            "exams_data": json.loads(self.exams_data) if self.exams_data else None,
            "rooms_data": json.loads(self.rooms_data) if self.rooms_data else None,
            "is_active": self.is_active
        }

engine = create_engine("sqlite:///exam_sessions.db", echo=True)
Base.metadata.create_all(engine)

# Создаем фабрику сессий
Session = sessionmaker(bind=engine)
