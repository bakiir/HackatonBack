from celery.worker.state import requests
from flask import Blueprint, jsonify, request, send_file

from app import allowed_roles
from create_db import  engine
from sqlalchemy.orm import sessionmaker
import threading


from users_db import User
from services.jwt_service import admin_required
from flask_jwt_extended import jwt_required

Session = sessionmaker(bind=engine)
session = Session()

auth_bp = Blueprint('auth_bp', __name__)

role_to_faculty = {
    "admin-sdt": "Школа цифровых технологий",
    "admin-sem": "Школа экономики и менеджмента",
    "admin-gum": "Гуманитарная школа",
    "admin-spigu": "Школа права и государственного управления"
}

def send_emails_to_admins():
    try:
        users = session.query(User).filter(User.role.in_([
            'admin-sdt', 'admin-sem', 'admin-spigu', 'admin_gum'
        ])).all()

        emails = [user.email for user in users if user.email]

        if not emails:
            print("Нет админов для отправки.")
            return

        response = requests.post(
            "http://localhost:8080/simple",
            json={
                "to": emails,
                "subject": "Привет от шедулера",
                "body": (
                    "Письмо отправлено через почтовый сервис. "
                    "Админ инициализировал все данные и ждет работы с вашей стороны."
                )
            },
            timeout=5
        )

        print(f"[EMAIL]: Статус: {response.status_code}, Ответ: {response.text}")

    except Exception as e:
        print(f"[EMAIL ERROR]: {str(e)}")

    finally:
        session.close()

@auth_bp.route("/api/send_emails_admins")
@admin_required("admin")
def send_email_to_admins():
    thread = threading.Thread(target=send_emails_to_admins)
    thread.start()
    return jsonify({
        'status': 'success',
        'message': 'Фоновая отправка писем запущена.'
    })

@auth_bp.route('/api/register', methods=['POST'])
def register():
    """
    Регистрация нового пользователя.
    """
    data = request.get_json()  # Получаем данные из запроса
    if not data or 'email' not in data or 'password' not in data:
        return jsonify({"error": "Email and password are required"}), 400

    email = data['email']
    password = data['password']
    role = data.get('role', 'student')  # По умолчанию роль 'student'
    full_name = data['full_name']

    session = Session()
    try:
        # Регистрируем пользователя
        user = User.register_user(session, email, password, full_name, role)
        return jsonify({
            "message": "User registered successfully",
            "user": user.to_dict()
        }), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": "An internal error occurred"}), 500
    finally:
        session.close()

@auth_bp.route('/api/register-admin', methods=['POST'])
@admin_required("admin")
def register_admin():
    """
    Регистрация нового админа.
    """
    data = request.get_json()  # Получаем данные из запроса
    if not data or 'email' not in data or 'password' not in data or 'role' not in data:
        return jsonify({"error": "Email, password and role are required"}), 400

    email = data['email']
    password = data['password']
    role = data.get('role')  # По умолчанию роль 'student'
    full_name = data['full_name']

    if role not in allowed_roles:
        return jsonify({
            "error": f"Недопустимая роль. Разрешены только: {', '.join(allowed_roles)}"
        }), 400

    session = Session()
    try:
        # Регистрируем пользователя
        user = User.register_user(session, email, password, full_name, role)
        return jsonify({
            "message": "Admin registered successfully",
            "user": user.to_dict()
        }), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": "An internal error occurred"}), 500
    finally:
        session.close()

@auth_bp.route('/api/update-user/<int:user_id>', methods=['PUT'])
@admin_required("admin")
def update_user(user_id):
    data = request.get_json()
    session = Session()
    try:
        user = User.update_user(
            session,
            id=user_id,
            email=data.get("email", None),
            password=None,
            full_name=data.get("full_name", None),
            role=data.get("role", None)
        )
        return jsonify({"message": "Пользователь обновлён", "user": user.to_dict()}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        return jsonify({"error": "Ошибка при обновлении"}), 500
    finally:
        session.close()

@auth_bp.route('/api/delete-user/<int:user_id>', methods=['DELETE'])
@admin_required("admin")  # Или @admin_required("admin") — твой декоратор
def delete_user(user_id):
    session = Session()
    try:
        User.delete_user(session, user_id)
        return jsonify({"message": "Пользователь успешно удалён"}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Ошибка при удалении: {str(e)}"}), 500
    finally:
        session.close()

@auth_bp.route('/api/login', methods=['POST'])
def login():
    user_session = Session()
    try:
        data = request.json
        email = data.get("email")
        password = data.get("password")

        # Проверка наличия email и password
        if not email or not password:
            return jsonify({"error": "Email и пароль обязательны"}), 400

        # Используем метод login_user вместо дублирования кода
        result = User.login_user(user_session, email, password)
        if not result:
            return jsonify({"error": "Неверные учетные данные"}), 401

        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        user_session.close()

@auth_bp.route('/api/protected', methods=['GET'])
@jwt_required()
def protected():
    return jsonify({"msg": "Access granted"})

@auth_bp.route("/api/init-admins", methods=["GET"])
def init_admins():
    User.register_user(session, "admin@narxoz.kz", "admin123", "main-admin", "admin");
    User.register_user(session, "admin-sdt@narxoz.kz", "admin123", "admin-sdt", "admin-sdt");
    User.register_user(session, "admin-sem@narxoz.kz", "admin123", "admin-sem", "admin-sem");
    User.register_user(session, "admin-gum@narxoz.kz", "admin123", "admin-gum", "admin-gum");
    User.register_user(session, "admin-spigu@narxoz.kz", "admin123", "admin-spigu", "admin-spigu");