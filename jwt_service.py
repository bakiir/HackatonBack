from functools import wraps

import bcrypt

import jwt  

from datetime import datetime, timedelta

from flask_jwt_extended import jwt_required, get_jwt, create_access_token

# Секретный ключ для подписи JWT
SECRET_KEY = "your-secret-key"  # Замените на реальный секретный ключ
ALGORITHM = "HS256"  # Алгоритм подписи
ACCESS_TOKEN_EXPIRE_MINUTES = 30  # Время жизни токена (в минутах)

def generate_access_token(identity: str, role: str):
    """
    Создание JWT-токена с использованием flask-jwt-extended.
    """
    additional_claims = {"role": role}
    expires_delta = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return create_access_token(identity=identity, additional_claims=additional_claims, expires_delta=expires_delta)


def role_required(role):
    def decorator(f):
        @wraps(f)
        @jwt_required()
        def decorated_function(*args, **kwargs):
            claims = get_jwt()
            if role in claims.get('role', ["admin"]):
                return f(*args, **kwargs)
            return {'msg': 'Forbidden'}, 403
        return decorated_function
    return decorator

def role_required_school(role):
    def decorator(f):
        @wraps(f)
        @jwt_required()
        def decorated_function(*args, **kwargs):
            claims = get_jwt()
            if role in claims.get('role', ["admin-sdt", "admin-gum", "admin-slpa", "admin-sem"]):
                return f(*args, **kwargs)
            return {'msg': 'Forbidden'}, 403
        return decorated_function
    return decorator



def hash_password(password):
    """
    Хэширование пароля.
    """
    salt = bcrypt.gensalt()
    hashed_password = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed_password.decode('utf-8')

def check_password(password, hashed_password):
    """
    Проверка пароля.
    """
    return bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8'))
