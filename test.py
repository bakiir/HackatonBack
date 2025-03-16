from sqlalchemy.orm import sessionmaker
from create_db import ExamSession, engine
from datetime import date

Session = sessionmaker(bind=engine)
session = Session()

# Создаем тестовую запись
new_session = ExamSession(
    title="Тестовый экзамен",
    start_date=date(2025, 3, 20),
    end_date=date(2025, 3, 21),
    schedule_data="{}"  # JSON-строка
)

session.add(new_session)

try:
    session.commit()
    print("Данные успешно добавлены!")
except Exception as e:
    print("Ошибка при добавлении данных:", e)
    session.rollback()

session.close()
