from sqlalchemy import create_engine, Column, Integer, String, Date, DateTime, Boolean, Text
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime
import json

from xx import ExamScheduler

# Создание соединения с базой данных
engine = create_engine("sqlite:///exam_sessions.db", echo=False)

Base = declarative_base()

class ExamSession(Base):
    __tablename__ = "exam_session"
    id = Column(Integer, primary_key=True)
    title = Column(String(150), nullable=False)
    start_date = Column(Date, nullable=False)
    days = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)

    original_start_date = Column(Date)  # Добавлено
    original_num_days = Column(Integer)  # Добавлено

    # Измененные поля для хранения всех данных
    schedule_data = Column(Text)
    exams_data = Column(Text)
    rooms_data = Column(Text)
    faculties_data = Column(Text)
    is_active = Column(Boolean, default=False)


    def set_schedule(self, schedule_dict):
        self.schedule_data = json.dumps(schedule_dict)

    def get_schedule(self):
        return json.loads(self.schedule_data)

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "start_date": self.start_date.isoformat(),
            "days": self.days,
            "created_at": self.created_at.isoformat(),
            "schedule_data": self.get_schedule(),  # Добавлено расписание
            "is_active": self.is_active,
            "rooms_data": self.rooms_data,
            "faculties_data": self.faculties_data
        }

# Создаем таблицу, если её еще нет
Base.metadata.create_all(engine)

# Создание сессии
Session = sessionmaker(bind=engine)

# Пример использования сессии
with Session() as session:
    # Работа с сессией
    pass