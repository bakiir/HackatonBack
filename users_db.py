from flask_jwt_extended import create_access_token
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import declarative_base, sessionmaker
import bcrypt
import jwt
from datetime import datetime, timedelta

from create_db import AdminStatusDraft, ResolvedConflict


# Конфигурация JWT
SECRET_KEY = "your-secret-key"  # Замените на реальный секретный ключ
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Подключение к базе данных
engine = create_engine("sqlite:///exam_sessions.db", echo=True)
Base = declarative_base()


# Модель пользователя
class User(Base):
    __tablename__ = "exam_users"

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    password = Column(String, nullable=False)
    full_name = Column(String, nullable=True)  # Это поле теперь будет создано
    role = Column(String, nullable=False, default='student')

    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "role": self.role,
            "full_name": self.full_name
        }

    @classmethod
    def login_user(cls, session, email, password):
        """
        Аутентификация пользователя и генерация JWT токена (совместимая с flask_jwt_extended)
        """
        user = session.query(cls).filter_by(email=email).first()
        if not user:
            return None

        if not bcrypt.checkpw(password.encode('utf-8'), user.password.encode('utf-8')):
            return None

        # ✅ Токен, совместимый с flask_jwt_extended
        access_token = create_access_token(
            identity=str(user.id),
            additional_claims={"role": user.role}
        )

        return {"user": user.to_dict(), "access_token": access_token}

    @classmethod
    def register_user(cls, session, email, password, full_name=None, role="student"):
        """
        Регистрация нового пользователя
        """
        if session.query(cls).filter_by(email=email).first():
            raise ValueError("Пользователь с таким email уже существует")

        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        new_user = cls(
            email=email,
            password=hashed_password,
            role=role,
            full_name=full_name
        )
        session.add(new_user)
        session.commit()
        return new_user

    @classmethod
    def update_user(cls, session, id, email=None, password=None, full_name=None, role=None):
        user = session.query(cls).filter_by(id=id).first();
        if not user:
            raise ValueError("Пользователь не найден")

        if email and email != user.email:
            if session.query(cls).filter_by(email=email).first():
                raise ValueError("Пользователь с таким email уже существует")
            user.email = email
        if password:
            hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            user.password = hashed_password

        if full_name:
            user.full_name = full_name

        if role:
            user.role = role
        session.commit()
        return user

    @classmethod
    def delete_user(cls, session, user_id):
        user = session.query(cls).filter_by(id=user_id).first()
        if not user:
            raise ValueError("Пользователь не найден")

        session.delete(user)
        session.commit()
        return True

    @classmethod
    def get_by_email(cls, session, email):
        """
        Получение пользователя по email
        """
        return session.query(cls).filter_by(email=email).first()

    @classmethod
    def create_access_token(cls, data):
        """
        Создание JWT токена
        """
        to_encode = data.copy()
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        to_encode.update({"exp": expire})
        return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_or_create_admin_status(session, session_id: int, role: str, model=AdminStatusDraft):
    """
    Get or create an admin status record for a given session and role.
    :param session: SQLAlchemy session
    :param session_id: ID of the session (ExamSession or ExamSessionDraft)
    :param role: Admin role (e.g., admin-sdt, admin-sem)
    :param model: The model to use (AdminStatus or AdminStatusDraft)
    :return: The AdminStatus or AdminStatusDraft instance
    """
    status = session.query(model).filter_by(session_id=session_id, role=role).first()
    if not status:
        status = model(session_id=session_id, role=role, status="in_progress")
        session.add(status)
        session.commit()
    return status

def set_admin_status_ready(session, session_id: int, role: str, model=AdminStatusDraft):
    """
    Set the status to 'ready' for a given admin role and session.
    :param session: SQLAlchemy session
    :param session_id: ID of the session (ExamSession or ExamSessionDraft)
    :param role: Admin role (e.g., admin-sdt, admin-sem)
    :param model: The model to use (AdminStatus or AdminStatusDraft)
    :return: The updated AdminStatus or AdminStatusDraft instance
    """
    status = session.query(model).filter_by(session_id=session_id, role=role).first()
    if status:
        status.status = "ready"
        status.updated_at = datetime.utcnow()
        session.commit()
    return status


def get_all_admin_statuses(session, session_id: int, model=AdminStatusDraft):
    """
    Get all admin status records for a given session.
    :param session: SQLAlchemy session
    :param session_id: ID of the session (ExamSession or ExamSessionDraft)
    :param model: The model to use (AdminStatus or AdminStatusDraft)
    :return: List of AdminStatus or AdminStatusDraft instances
    """
    return session.query(model).filter_by(session_id=session_id).all()


def are_all_admins_ready(session, session_id: int, model=AdminStatusDraft):
    """
    Check if all admins for a given session have 'ready' status.
    :param session: SQLAlchemy session
    :param session_id: ID of the session (ExamSession or ExamSessionDraft)
    :param model: The model to use (AdminStatus or AdminStatusDraft)
    :return: Boolean indicating if all admins are ready
    """
    statuses = get_all_admin_statuses(session, session_id, model)
    return all(status.status == "ready" for status in statuses)

def get_resolved_conflicts_by_session_id(session, session_id: int):
    """
    Retrieves all resolved conflicts for a given session ID.
    """
    return session.query(ResolvedConflict).filter_by(session_id=session_id).all()


# Создание таблиц в базе данных
Base.metadata.create_all(engine)

# Создание сессии
Session = sessionmaker(bind=engine)

