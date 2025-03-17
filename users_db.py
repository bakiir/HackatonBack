from flask import Flask, request, jsonify
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import declarative_base, sessionmaker
import bcrypt
import jwt_service , jwt
from datetime import datetime, timedelta

# Инициализация Flask-приложения
app = Flask(__name__)

# Конфигурация JWT
SECRET_KEY = "your-secret-key"  # Замените на реальный секретный ключ
ALGORITHM = "HS256"  # Алгоритм подписи
ACCESS_TOKEN_EXPIRE_MINUTES = 30  # Время жизни токена (в минутах)

# Подключение к базе данных
engine = create_engine("sqlite:///exam_users.db", echo=True)
Base = declarative_base()

# Модель пользователя
class User(Base):
    __tablename__ = "exam_users"

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    password = Column(String, nullable=False)  # Хэшированный пароль
    role = Column(String, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "role": self.role
        }

    @classmethod
    def login_user(cls, session, email, password):
        """
        Вход пользователя и генерация JWT-токена.
        """
        user = session.query(cls).filter_by(email=email).first()
        if not user:
            return None  # Пользователь не найден

        # Проверка пароля
        if not bcrypt.checkpw(password.encode('utf-8'), user.password.encode('utf-8')):
            return None  # Неверный пароль

        # Создание JWT-токена
        token_data = {"sub": str(user.id), "role": user.role}
        access_token = jwt_service.create_access_token(token_data)
        return {"user": user.to_dict(), "access_token": access_token}

    @classmethod
    def register_user(cls, session, email, password, role="student"):
        """
        Регистрация нового пользователя.
        """
        # Проверка, существует ли пользователь с таким email
        existing_user = session.query(cls).filter_by(email=email).first()
        if existing_user:
            raise ValueError("Пользователь с таким email уже существует")

        # Хэширование пароля
        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())

        # Создание нового пользователя
        new_user = cls(
            email=email,
            password=hashed_password.decode('utf-8'),  # Сохраняем хэшированный пароль
            role=role
        )
        session.add(new_user)
        session.commit()
        return new_user



    @classmethod
    def get_by_email(cls, session, email):
        """
        Получение пользователя по email.
        """
        return session.query(cls).filter_by(email=email).first()

    @classmethod
    def create_access_token(data: dict):
        """
        Создание JWT-токена.
        """
        to_encode = data.copy()
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
        return encoded_jwt



# Создание таблиц в базе данных
Base.metadata.create_all(engine)

# Создание сессии
Session = sessionmaker(bind=engine)

