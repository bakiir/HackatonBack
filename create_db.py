from sqlalchemy import create_engine, Column, Integer, String, Date, DateTime, Boolean, Text
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime
import json

# Создание соединения с базой данных
engine = create_engine("sqlite:///exam_sessions.db", echo=True)

Base = declarative_base()

class ExamSession(Base):
    __tablename__ = "exam_session"

    id = Column(Integer, primary_key=True)
    title = Column(String(150), nullable=False)
    start_date = Column(Date, nullable=False)  # Важно: нет запятой в конце
    days = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    schedule_data = Column(Text, nullable=False)
    is_active = Column(Boolean, default=False, nullable=True)


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
            "is_active": self.is_active
        }

# Создаем таблицу, если её еще нет
Base.metadata.create_all(engine)

# Создание сессии
Session = sessionmaker(bind=engine)

# Пример использования сессии
with Session() as session:
    # Работа с сессией
    pass