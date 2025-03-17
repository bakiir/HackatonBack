import bcrypt

import jwt

from datetime import datetime, timedelta

# Секретный ключ для подписи JWT
SECRET_KEY = "your-secret-key"  # Замените на реальный секретный ключ
ALGORITHM = "HS256"  # Алгоритм подписи
ACCESS_TOKEN_EXPIRE_MINUTES = 30  # Время жизни токена (в минутах)

def create_access_token(data: dict):
    """
    Создание JWT-токена.
    """
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def decode_access_token(token: str):
    """
    Декодирование JWT-токена.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.PyJWTError:
        return None


def hash_password(password):
    salt = bcrypt.gensalt()
    hashed_password = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed_password.decode('utf-8')

def check_password(password, hashed_password):
    return bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8'))
