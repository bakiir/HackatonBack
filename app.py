import tempfile
import math
import threading
from datetime import datetime, timedelta
import traceback
from venv import logger
import bcrypt
import requests
from flask_jwt_extended import JWTManager, jwt_required, get_jwt_identity, get_jwt
from services.jwt_service import  admin_required
from users_db import User, get_or_create_admin_status, set_admin_status_ready, get_all_admin_statuses, are_all_admins_ready, get_resolved_conflicts_by_session_id
from flask import Flask, jsonify, send_file
import logging
from flask_cors import CORS
from services.exam_scheduler import ExamScheduler
from services.check_student_conflicts import get_student_conflicts
import numpy as np
import pandas as pd
import os
from flask import request
from create_db import ExamSession, engine, ExamSessionDraft, AdminStatusDraft
from sqlalchemy.orm import sessionmaker
import json # Added this import

allowed_roles = {"admin-sdt", "admin-gum", "admin-spigu", "admin-sem", "admin"}
role_to_faculty = {
    "admin-sdt": "Школа цифровых технологий",
    "admin-sem": "Школа экономики и менеджмента",
    "admin-gum": "Гуманитарная школа",
    "admin-spigu": "Школа права и государственного управления"
}


def handle_nan_values(obj):
    if isinstance(obj, (float, np.float64, np.float32)) and (math.isnan(obj) or np.isnan(obj)):
        return None
    elif isinstance(obj, dict):
        return {key: handle_nan_values(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [handle_nan_values(item) for item in obj]
    elif isinstance(obj, pd.DataFrame):
        return obj.replace({np.nan: None}).to_dict('records')
    else:
        return obj


# это второй варик если ммикросервисный сделаем, сервис уже готов:)
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

app = Flask(__name__)

app.config["JWT_SECRET_KEY"] = "your-secret-key"  # тот же, что и в модели
app.config["JWT_TOKEN_LOCATION"] = ["headers"]     # обязательно!
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(minutes=30)

jwt = JWTManager(app)

CORS(app)
# Глобальная переменная для хранения планировщика
current_scheduler = None


from create_db import ExamSession, engine, ExamSessionDraft, AdminStatusDraft, ClassroomSlot, update_classroom_slots


@app.route('/api/classroom/107/free-slots', methods=['GET'])
def get_free_classroom_107_slots():
    session = Session()
    try:
        free_slots = session.query(ClassroomSlot).filter_by(classroom_number='107', is_booked=False).all()
        return jsonify([slot.to_dict() for slot in free_slots]), 200
    except Exception as e:
        logging.error(f"Error getting free slots for classroom 107: {traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()

@app.route('/api/slots/book', methods=['POST'])
# @admin_required("admin")  # Temporarily commented out
def book_classroom_slot():
    session = Session()
    try:
        data = request.json
        # Convert single object to list for consistent processing
        if not isinstance(data, list):
            data = [data]

        results = []
        for booking in data:
            slot_id = booking.get('slot_id')
            subject = booking.get('subject')
            sections = booking.get('sections')

            # Validate input fields
            if not slot_id or not subject or not sections or not isinstance(sections, list):
                results.append({
                    'slot_id': slot_id,
                    'status': 'error',
                    'message': 'Request body must contain "slot_id", "subject", and a list of "sections"'
                })
                continue

            # Check if slot exists
            slot = session.query(ClassroomSlot).get(slot_id)
            if not slot:
                results.append({
                    'slot_id': slot_id,
                    'status': 'error',
                    'message': 'Slot not found'
                })
                continue

            # Check if slot is already booked
            if slot.is_booked:
                results.append({
                    'slot_id': slot_id,
                    'status': 'error',
                    'message': 'Slot is already booked'
                })
                continue

            # Additional validation (optional, commented as per original code)
            # - Check if sections exist in exam_groups
            # - Check if total students in sections <= 200
            # - Check for scheduling conflicts

            booking_info = {
                "subject": subject,
                "sections": sections
            }

            # Update slot
            slot.is_booked = True
            slot.booked_groups_info = json.dumps(booking_info, ensure_ascii=False)
            session.commit()

            logging.info(f"Slot {slot_id} in classroom 107 manually booked for subject '{subject}' with sections {sections}.")
            results.append({
                'slot_id': slot_id,
                'status': 'success',
                'message': f'Slot {slot_id} successfully booked.'
            })

        return jsonify({'results': results}), 200

    except Exception as e:
        session.rollback()
        logging.error(f"Error booking slots: {traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()
@app.route('/api/proctors/assign', methods=['POST'])
@admin_required("admin")
def assign_proctors():
    global current_scheduler

    if current_scheduler is None:
        return jsonify({'error': 'Планировщик не инициализирован'}), 400

    session = Session()
    try:
        proctors_file = request.files['proctors']

        with tempfile.TemporaryDirectory() as temp_dir:
            proctors_path = os.path.join(temp_dir, 'proctors.xlsx')
            proctors_file.save(proctors_path)

            current_scheduler.assign_proctors(proctors_path)

        # Update the session
        active_session = session.query(ExamSession).filter_by(is_active=True).first()
        if active_session:
            active_session.schedule_data = current_scheduler.schedule_df.to_json(orient='records')
            session.commit()
            logging.info("Сессия успешно обновлена с данными о прокторах.")

        return jsonify({'message': 'Прокторы успешно назначены'}), 200

    except Exception as e:
        session.rollback()
        logging.error(f"Ошибка назначения прокторов: {str(e)}")
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()

@app.route('/api/proctors/save_excel', methods=['GET'])
def save_proctor_list_excel():
    if current_scheduler is None:
        return jsonify({'error': 'Планировщик не инициализирован'}), 400

    try:
        # Логируем содержимое section_proctors для отладки
        logging.info(f"section_proctors: {current_scheduler.section_proctors}")

        # Генерация DataFrame с назначениями прокторов
        section_ids = list(current_scheduler.section_proctors.keys())
        proctors_data = list(current_scheduler.section_proctors.values())

        # Извлекаем данные с обработкой отсутствующих ключей
        proctors = [data.get('proctor', None) for data in proctors_data]
        subjects = [data.get('exam_name', data.get('subject', 'Не указано')) for data in proctors_data]  # Если exam_name отсутствует, берём subject
        dates = [data.get('date', 'Не указано') for data in proctors_data]  # Если date отсутствует, ставим заглушку

        # Создаём DataFrame с нужными колонками
        df = pd.DataFrame({
            'Section': section_ids,
            'Subject': subjects,
            'Date': dates,
            'Proctor': proctors
        })

        # Сохранение в Excel
        output_path = 'proctors_list.xlsx'
        df.to_excel(output_path, index=False)

        return jsonify({'status': f'Прокторы успешно сохранены в {output_path}'}), 200

    except Exception as e:
        logging.error(f"Ошибка при сохранении прокторов: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/init', methods=['POST'])
@admin_required("admin")
def handle_initialization():
    global current_scheduler

    try:
        # 1. Загрузка файлов
        title = request.form.get('title', 'Сезон без имени')
        exams_file = request.files['exams']
        rooms_file = request.files['rooms']
        faculties_file = request.files['faculties']
        start_date = request.form['start_date']
        num_days = int(request.form.get('num_days', 14))

        # 2. Сохранение файлов
        with tempfile.TemporaryDirectory() as temp_dir:
            exams_path = os.path.join(temp_dir, 'exams.xlsx')
            rooms_path = os.path.join(temp_dir, 'rooms.xlsx')
            faculties_path = os.path.join(temp_dir, 'faculties.xlsx')

            exams_file.save(exams_path)
            rooms_file.save(rooms_path)
            faculties_file.save(faculties_path)

            # 3. Инициализация планировщика
            current_scheduler = ExamScheduler(
                title=title,
                exams_file=exams_path,
                rooms_file=rooms_path,
                faculties_file=faculties_path,
                start_date=start_date,
                num_days=num_days
            )
            # 4. Update classroom slots
            update_classroom_slots(start_date, num_days, current_scheduler.rooms_df)

            # 5. Создание черновика сессии
            session.query(ExamSessionDraft).update({'is_active': False})  # Деактивируем предыдущие черновики
            new_draft = ExamSessionDraft(
                title=title,
                start_date=datetime.strptime(start_date, '%Y-%m-%d').date(),
                days=num_days,
                exams_data=current_scheduler.exams_df.to_json(orient='records'),
                rooms_data=current_scheduler.rooms_df.to_json(orient='records'),
                faculties_data=current_scheduler.faculties_df.to_json(orient='records'),
                is_active=True
            )
            session.add(new_draft)
            session.commit()

        # 5. Возвращаем данные для управления предметами
        return jsonify({
            'status': 'subject_management',
            'subjects': current_scheduler.get_unique_subjects(),
            'dates': current_scheduler.get_current_dates(),
            'message': 'Управление предметами перед генерацией'
        })

    except Exception as e:
        logging.error(f"Ошибка инициализации: {traceback.format_exc()}")
        return jsonify({
            'status': 'error',
            'message': f'Ошибка инициализации: {str(e)}'
        }), 500


@app.route("/api/send_emails_admins")
@admin_required("admin")
def send_email_to_admins():
    thread = threading.Thread(target=send_emails_to_admins)
    thread.start()
    return jsonify({
        'status': 'success',
        'message': 'Фоновая отправка писем запущена.'
    })

@app.route('/api/manage_dates', methods=['POST'])
@admin_required("admin")
def manage_dates():
    global current_scheduler

    # Проверка инициализации планировщика
    if current_scheduler is None:
        return jsonify({
            'status': 'error',
            'message': 'Планировщик не инициализирован'
        }), 400

    try:
        action = request.json.get('action')
        if action not in ['remove', 'add_custom', 'restore']:
            return jsonify({
                'status': 'error',
                'message': 'Некорректное действие'
            }), 400

        if action == 'remove':
            date_to_remove = request.json.get('date')
            if not date_to_remove:
                return jsonify({
                    'status': 'error',
                    'message': 'Не указана дата для удаления'
                }), 400

            current_scheduler.remove_date(date_to_remove)
            logging.info(f'Дата {date_to_remove} удалена')
            return jsonify({
                'status': 'success',
                'message': f'Дата {date_to_remove} удалена',
                'dates': current_scheduler.get_current_dates()
            })

        elif action == 'add_custom':
            custom_date = request.json.get('custom_date')
            if not custom_date:
                return jsonify({
                    'status': 'error',
                    'message': 'Не указана дата для добавления'
                }), 400

            current_scheduler.add_custom_date(custom_date)
            logging.info(f'Дата {custom_date} добавлена')
            return jsonify({
                'status': 'success',
                'message': f'Дата {custom_date} добавлена',
                'dates': current_scheduler.get_current_dates()
            })

        elif action == 'restore':
            current_scheduler.restore_default_dates()
            logging.info('Исходные даты восстановлены')
            return jsonify({
                'status': 'success',
                'message': 'Исходные даты восстановлены',
                'dates': current_scheduler.get_current_dates()
            })

    except Exception as e:
        logging.error(f"Ошибка управления датами: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Ошибка управления датами: {str(e)}'
        }), 500


Session = sessionmaker(bind=engine)
session = Session()
@app.route('/api/manage', methods=['POST'])
@jwt_required()
def handle_management():
    global current_scheduler
    logging.info(f"Запрос API /manage с данными: {request.json}")
    claims = get_jwt()
    user_role = claims.get('role')
    current_user = get_jwt_identity()
    logging.info(f"Роль текущего пользователя: {user_role}, пользователь: {current_user}")

    role_to_faculty = {
        "admin-sdt": "Школа цифровых технологий",
        "admin-sem": "Школа экономики и менеджмента",
        "admin-gum": "Гуманитарная школа",
        "admin-spigu": "Школа права и государственного управления"
    }

    if current_scheduler is None:
        logging.error("current_scheduler не инициализирован")
        return jsonify({"error": "Планировщик не инициализирован"}), 500

    session = Session()
    try:
        data = request.json
        action = data.get('action')

        if action == 'get_subjects':
            requested_faculty = data.get('faculty')
            if user_role in role_to_faculty:
                faculty = role_to_faculty[user_role]
                logging.info(f"Факультет переопределён для роли {user_role}: {faculty}")
            elif user_role == "admin":
                if requested_faculty is None:
                    logging.error("Для роли admin требуется указать факультет в запросе")
                    return jsonify({"error": "Факультет не указан в запросе"}), 400
                faculty = requested_faculty
                logging.info(f"Роль admin, используется запрошенный факультет: {faculty}")
            elif user_role == "student":
                if requested_faculty is None:
                    logging.error("Для роли student требуется указать факультет в запросе")
                    return jsonify({"error": "Факультет не указан в запросе"}), 400
                faculty = requested_faculty
                logging.info(f"Студент запрашивает факультет: {faculty}")
            else:
                logging.error(f"Недопустимая роль пользователя: {user_role}")
                return jsonify({"error": "Недопустимая роль пользователя"}), 403

            subjects = current_scheduler.get_by_faculty(faculty)
            return jsonify({
                "status": "success",
                "subjects": subjects,
                "user": current_user,
                "role": user_role,
                "requested_faculty": requested_faculty if requested_faculty else "не указан",
                "used_faculty": faculty
            })


        elif action == 'delete_subject':

            active_draft = session.query(ExamSessionDraft).filter_by(is_active=True).first()

            if active_draft and user_role in ["admin-sdt", "admin-sem", "admin-gum", "admin-spigu"]:
                get_or_create_admin_status(session, active_draft.id, user_role, model=AdminStatusDraft)
                logging.info(f"Статус 'in_progress' для {user_role} установлен автоматически.")

            subject = data['subject']

            sections_to_delete = current_scheduler.exam_groups[
                current_scheduler.exam_groups['Subject'] == subject
                ]['Section'].tolist()

            current_scheduler._delete_sections(sections_to_delete)

            # Update the draft with the latest exam_groups

            if active_draft:
                active_draft.exams_data = current_scheduler.exam_groups.to_json(orient='records')
                session.commit()

            return jsonify({
                'status': 'success',
                'message': f'Предмет {subject} удален',
                'remaining_subjects': current_scheduler.get_unique_subjects()
            })



        elif action == 'delete_section':

            active_draft = session.query(ExamSessionDraft).filter_by(is_active=True).first()

            if active_draft and user_role in ["admin-sdt", "admin-sem", "admin-gum", "admin-spigu"]:
                get_or_create_admin_status(session, active_draft.id, user_role, model=AdminStatusDraft)
                logging.info(f"Статус 'in_progress' для {user_role} установлен автоматически.")

            section = data['section']

            if not isinstance(current_scheduler.schedule_df, pd.DataFrame):
                logging.info("Schedule not yet created, initializing empty schedule_df")
                current_scheduler.schedule_df = pd.DataFrame(
                    columns=['Section', 'Date', 'Time_Slot', 'Room', 'Proctor'])

            current_scheduler._delete_sections([section])

            # Update the draft with the latest exam_groups

            if active_draft:
                active_draft.exams_data = current_scheduler.exam_groups.to_json(orient='records')
                session.commit()

            return jsonify({
                'status': 'success',
                'message': f'Секция {section} удалена',
                'remaining_subjects': current_scheduler.get_unique_subjects()
            })
        elif action == 'generate':
            if user_role != "admin":
                logging.error(f"Доступ запрещён для роли {user_role}")
                return jsonify({"error": "Доступ запрещён! Только admin может генерировать расписание"}), 403

            active_draft = session.query(ExamSessionDraft).filter_by(is_active=True).first()
            if not active_draft:
                return jsonify({"error": "Активный черновик сессии не найден"}), 404

            if not are_all_admins_ready(session, active_draft.id, model=AdminStatusDraft):
                statuses = get_all_admin_statuses(session, active_draft.id, model=AdminStatusDraft)
                ready_roles = {s.role for s in statuses if s.status == 'ready'}
                not_ready_roles = [role for role in ["admin-sdt", "admin-sem", "admin-gum", "admin-spigu"] if role not in ready_roles]
                return jsonify({"error": f"Не все администраторы готовы. Не готовы: {not_ready_roles}"}), 400

            # Generate the schedule
            current_scheduler.create_schedule()

            # Деактивируем все предыдущие сессии и создаём новую
            session.query(ExamSession).update({'is_active': False})
            new_session = ExamSession(
                title=current_scheduler.title,
                start_date=current_scheduler.original_start_date,
                original_start_date=current_scheduler.original_start_date,
                days=current_scheduler.original_num_days,
                original_num_days=current_scheduler.original_num_days,
                schedule_data=current_scheduler.schedule_df.to_json(orient='records'),
                exams_data=current_scheduler.exams_df.to_json(orient='records'),
                rooms_data=current_scheduler.rooms_df.to_json(orient='records'),
                faculties_data=current_scheduler.faculties_df.to_json(orient='records'),
                seat_assignments=current_scheduler.seat_assignments,
                is_active=True
            )
            session.add(new_session)

            # Удаляем черновик и связанные статусы
            session.query(AdminStatusDraft).filter_by(session_id=active_draft.id).delete()
            session.delete(active_draft)
            session.commit()

            # После успешного создания основной сессии - подчистим все остальные черновики
            session.query(AdminStatusDraft).delete()  # на всякий случай чистим статусы
            session.query(ExamSessionDraft).delete()  # удаляем все черновики
            session.commit()

            sanitized_schedule = handle_nan_values(current_scheduler.schedule_df)
            return jsonify({
                'status': 'success',
                'schedule': sanitized_schedule,
                'stats': {
                    'total': len(current_scheduler.exam_groups),
                    'scheduled': len(current_scheduler.schedule_df)
                }
            })

    except Exception as e:
        session.rollback()
        logger.error(f"Management error: {traceback.format_exc()}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500
    finally:
        session.close()



@app.route('/api/set_admin_status_draft', methods=['POST'])
@jwt_required()
def set_admin_status_draft():
    claims = get_jwt()
    user_role = claims.get('role')

    if user_role not in ["admin-sdt", "admin-sem", "admin-gum", "admin-spigu"]:
        return jsonify({"error": "Доступ запрещён"}), 403

    session = Session()
    try:
        active_draft = session.query(ExamSessionDraft).filter_by(is_active=True).first()
        if not active_draft:
            return jsonify({"error": "Активный черновик сессии не найден"}), 404

        # Используем новую модель AdminStatusDraft
        get_or_create_admin_status(session, active_draft.id, user_role, model=AdminStatusDraft)
        set_admin_status_ready(session, active_draft.id, user_role, model=AdminStatusDraft)
        logging.info(f"Администратор {user_role} установил статус 'ready' для черновика сессии {active_draft.id}")
        return jsonify({"message": f"Статус для {user_role} установлен на 'ready'"}), 200
    except Exception as e:
        logging.error(f"Ошибка при установке статуса: {str(e)}")
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()

@app.route('/api/check_all_drafts', methods=['GET'])
@admin_required("admin")
def check_all_drafts():
    session = Session()
    try:
        active_draft = session.query(ExamSessionDraft).filter_by(is_active=True).first()
        if not active_draft:
            return jsonify({"have_drafts": False}), 200

        all_ready = are_all_admins_ready(session, active_draft.id, model=AdminStatusDraft)
        return jsonify({"have_drafts": all_ready}), 200
    except Exception as e:
        logging.error(f"Ошибка при проверке статусов: {str(e)}")
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route('/api/admin_statuses', methods=['GET'])
@jwt_required()
def admin_statuses():
    session = Session()
    try:
        # Проверяем наличие активной сессии
        active_session = session.query(ExamSessionDraft).filter_by(is_active=True).first()

        # Проверяем наличие любых черновиков
        draft_count = session.query(ExamSessionDraft).count()
        has_drafts = draft_count > 0

        # Если активная сессия есть, получаем статусы
        if active_session:
            statuses = get_all_admin_statuses(session, active_session.id)
            return jsonify({
                "statuses": [s.to_dict() for s in statuses],
                "has_drafts": has_drafts
            }), 200
        else:
            # Если активной сессии нет, возвращаем только has_drafts
            return jsonify({
                "has_drafts": has_drafts
            }), 200

    except Exception as e:
        logging.error(f"Ошибка при получении статусов: {str(e)}")
        return jsonify({"error": str(e), "has_drafts": False}), 500
    finally:
        session.close()

@app.route('/api/get-subjects-by-faculty/', defaults={'faculty': None})
@app.route('/api/get-subjects-by-faculty/<faculty>', methods=['GET'])
@jwt_required()
def get_subjects_by_faculty(faculty):
    global current_scheduler
    logging.info(f"Запрос API для предметов факультета: {faculty}")

    if current_scheduler is None:
        logging.error("current_scheduler не инициализирован")
        return jsonify({"error": "Планировщик не инициализирован"}), 500

    try:
        # Получаем данные из JWT-токена
        claims = get_jwt()
        user_role = claims.get('role')
        current_user = get_jwt_identity()
        logging.info(f"Роль текущего пользователя: {user_role}, пользователь: {current_user}")

        # Проверяем роль и определяем факультет
        requested_faculty = faculty  # Сохраняем запрошенный факультет
        if user_role in role_to_faculty:
            faculty = role_to_faculty[user_role]
            logging.info(f"Факультет переопределён для роли {user_role}: {faculty}")
        elif user_role == "admin":
            # Для роли admin используем факультет из URL
            logging.info(f"Роль admin, используется запрошенный факультет: {faculty}")
        elif user_role == "student":
            # Для студентов используем переданный факультет
            logging.info(f"Студент запрашивает факультет: {faculty}")
        else:
            # Неизвестная роль
            logging.error(f"Недопустимая роль пользователя: {user_role}")
            return jsonify({"error": "Недопустимая роль пользователя"}), 403

        # Получаем предметы для факультета
        subjects = current_scheduler.get_by_faculty(faculty)
        return jsonify({
            "subjects": subjects,
            "user": current_user,
            "role": user_role,
            "requested_faculty": requested_faculty,
            "used_faculty": faculty
        })
    except Exception as e:
        logging.error(f"Ошибка в get_subjects_by_faculty для факультета {faculty}: {str(e)}")
        return jsonify({"error": str(e)}), 500

# Получить список сессий
@app.route('/api/sessions', methods=['GET'])
@admin_required("admin")
def get_all_sessions():
    db_session = Session()
    try:
        sessions = db_session.query(ExamSession).all()

        sessions_data = [
            {
                'title': exam_session.title,
                'start_date': exam_session.start_date,
                'days': exam_session.days,
                'created_at': exam_session.created_at,
                'id': exam_session.id

            }
            for exam_session in sessions
        ]

        return jsonify(sessions_data), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        db_session.close()


@app.route('/api/sessions/<int:session_id>/data', methods=['GET'])
@admin_required("admin")
def get_data_by_id(session_id):
    db_session = Session()
    try:
        data = db_session.query(ExamSession).get(session_id)
        data = data.to_dict()
        if(data):
            return jsonify(data), 200
        else:
            return jsonify({"error": "Session not found"}), 404

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        db_session.close()


# Получить детали конкретной сессии
@app.route('/api/sessions/<int:session_id>', methods=['GET'])
@admin_required("admin")
def get_session_details(session_id):
    db_session = Session()
    try:
        session = db_session.query(ExamSession).get(session_id)
        if session:
            return jsonify(session.to_dict()), 200
        else:
            return jsonify({"error": "Session not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        db_session.close()


@app.route('/api/sessions/<int:session_id>/activate', methods=['POST'])
@admin_required("admin")
def activate_session(session_id):
    db_session = Session()
    try:
        session = db_session.query(ExamSession).get(session_id)
        if not session:
            return jsonify({"error": "Session not found"}), 404

        global current_scheduler

        # Загружаем данные сессии
        current_scheduler = ExamScheduler(session_data=session)

        # Дополнительная проверка seat_assignments
        if not hasattr(current_scheduler, 'seat_assignments') or not current_scheduler.seat_assignments:
            if session.seat_assignments:
                current_scheduler.seat_assignments = session.seat_assignments
                logging.info("Loaded seat_assignments directly from session")
            else:
                logging.warning("No seat_assignments in session data")

        db_session.query(ExamSession).update({"is_active": False})
        session.is_active = True
        db_session.commit()

        return jsonify({
            "status": "success",
            "seat_assignments_loaded": bool(hasattr(current_scheduler, 'seat_assignments') and
                                            current_scheduler.seat_assignments)
        })

    except Exception as e:
        db_session.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db_session.close()


@app.route('/api/sessions/<int:session_id>', methods=['DELETE'])
@admin_required("admin")
def delete_session(session_id):
    db = Session()
    try:
        session_obj = db.query(ExamSession).get(session_id)
        db.delete(session_obj)
        db.commit()
        return jsonify({"message": "Сессия удалена"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/schedule')
def get_schedule():
    if not current_scheduler:
        return jsonify({'error': 'Расписание не сгенерировано'}), 400

    schedule_data = current_scheduler.schedule_df.replace({np.nan: None}).to_dict('records')
    return jsonify(schedule_data)



@app.route('/schedule/stats')
def get_schedule_stats():
    if not current_scheduler:
        return jsonify({'error': 'Расписание не сгенерировано'}), 400

    total = len(current_scheduler.exam_groups)
    scheduled = len(current_scheduler.schedule_df)

    return jsonify({
        'total_exams': total,
        'scheduled': scheduled,
        'failed': total - scheduled
    })


@app.route('/api/schedule/status', methods=['GET'])
def get_scheduling_status():
    """
    Возвращает статистику по последнему запуску генерации расписания,
    включая количество запланированных, общее количество и список незапланированных экзаменов.
    """
    if not current_scheduler:
        return jsonify({'error': 'Планировщик не инициализирован или расписание не создано'}), 400

    # Общее количество экзаменов, которые должны были быть запланированы
    try:
        total_to_schedule = len(current_scheduler.exam_groups[current_scheduler.exam_groups['has_exam'] == True])
    except (AttributeError, KeyError):
        total_to_schedule = 0

    # Количество успешно запланированных экзаменов
    try:
        successfully_scheduled = len(current_scheduler.schedule_df)
    except (AttributeError, TypeError):
        successfully_scheduled = 0

    # Список незапланированных секций
    failed_sections_list = []
    if hasattr(current_scheduler, 'failed_sections') and current_scheduler.failed_sections:
        for failed in current_scheduler.failed_sections:
            group_info = failed.get('group', pd.Series())
            failed_sections_list.append({
                'section': failed.get('section'),
                'subject': group_info.get('Subject', 'N/A'),
                'instructor': group_info.get('Instructor', 'N/A'),
                'num_students': failed.get('num_students'),
                'duration': failed.get('duration')
            })
    
    failed_count = len(failed_sections_list)

    return jsonify({
        'total_exams_to_schedule': total_to_schedule,
        'successfully_scheduled': successfully_scheduled,
        'failed_to_schedule_count': failed_count,
        'failed_sections': failed_sections_list
    })


@app.route('/schedule/export')
def export_schedule():
    output_file = "general_schedule.xlsx"
    current_scheduler.export_schedule(output_file)
    return send_file(output_file, as_attachment=True)


@app.route('/api/report/conflicts', methods=['GET'])
def get_conflict_report():
    global current_scheduler
    if not current_scheduler:
        return jsonify({'error': 'Планировщик не инициализирован'}), 400

    # Scheduled stats
    total = len(current_scheduler.exam_groups[current_scheduler.exam_groups['has_exam'] == True])
    scheduled_count = len(current_scheduler.schedule_df)
    scheduled_str = f"{scheduled_count}/{total}"

    # Non-scheduled subjects
    non_scheduled_subjects = []
    if hasattr(current_scheduler, 'failed_sections'):
        for failed in current_scheduler.failed_sections:
            # Extract relevant info from the 'group' series
            group_info = failed.get('group', pd.Series())
            non_scheduled_subjects.append({
                'section': failed.get('section'),
                'subject': group_info.get('Subject'),
                'instructor': group_info.get('Instructor'),
                'num_students': failed.get('num_students'),
            })

    # Conflicts
    conflicts = get_student_conflicts(current_scheduler)

    report = {
        "scheduled": scheduled_str,
        "non_scheduled_subjects": non_scheduled_subjects,
        "conflicts": conflicts
    }

    return jsonify(report)


@app.route('/api/resolve-day-conflicts', methods=['POST'])
@admin_required("admin")
def resolve_conflicts_api():
    db_session = Session()
    try:
        active_session = db_session.query(ExamSession).filter_by(is_active=True).first()
        if not active_session:
            return jsonify({"error": "Активная сессия не найдена"}), 404

        # Инициализируем планировщик с данными из активной сессии
        scheduler = ExamScheduler(session_data=active_session)
        
        # Импортируем и вызываем новую функцию
        from services.conflict_resolver import resolve_day_conflicts
        changes = resolve_day_conflicts(scheduler, active_session.id)

        if changes:
            # Если были внесены изменения, обновляем данные сессии в БД
            active_session.exams_data = scheduler.exam_groups.to_json(orient='records')
            active_session.schedule_data = scheduler.schedule_df.to_json(orient='records')
            
            # Перераспределяем места после изменения секций
            scheduler.assign_seats()
            active_session.seat_assignments = scheduler.seat_assignments

            db_session.commit()
            logging.info(f"Успешно разрешено {len(changes)} конфликтов. Изменения сохранены в сессии {active_session.id}.")

            # Обновляем глобальный планировщик, чтобы изменения были видны сразу
            global current_scheduler
            current_scheduler = scheduler

        return jsonify({
            "message": f"Обработка конфликтов завершена. Перемещено студентов: {len(changes)}.",
            "changes": changes
        }), 200

    except Exception as e:
        db_session.rollback()
        logging.error(f"Ошибка при разрешении конфликтов: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500
    finally:
        db_session.close()


@app.route('/api/resolved_conflicts', methods=['GET'])
@admin_required("admin")
def get_resolved_conflicts():
    db_session = Session()
    try:
        active_session = db_session.query(ExamSession).filter_by(is_active=True).first()
        if not active_session:
            return jsonify({"error": "Активная сессия не найдена"}), 404

        resolved_conflicts = get_resolved_conflicts_by_session_id(db_session, active_session.id)
        return jsonify([conflict.to_dict() for conflict in resolved_conflicts]), 200
    except Exception as e:
        logging.error(f"Error retrieving resolved conflicts: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500
    finally:
        db_session.close()


def convert_numpy_types(obj):
    if isinstance(obj, (np.int64, np.int32)):
        return int(obj)
    elif isinstance(obj, (np.float64, np.float32)):
        return float(obj)
    elif isinstance(obj, dict):
        return {key: convert_numpy_types(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [convert_numpy_types(item) for item in obj]
    else:
        return obj


@app.route('/section/<section_id>')
def get_section_info(section_id):
    section_info = current_scheduler.get_section_info(section_id)
    # Преобразуем numpy типы в стандартные типы Python
    section_info = convert_numpy_types(section_info)
    return jsonify(section_info)  # Возвращаем словарь как JSON


@app.route('/section/<section_id>/export')
def export_section_info(section_id):
    output_file = f"section_{section_id}_info.xlsx"
    section_info = current_scheduler.get_section_info(section_id)  # Получаем информацию о секции
    current_scheduler.export_section_info_to_excel(section_info, output_file)  # Экспортируем в Excel
    return send_file(output_file, as_attachment=True)  # Отправляем файл пользователю


@app.route('/subjects', methods=['GET'])
def get_subjects():
    try:
        subjects = current_scheduler.get_unique_subjects()
        return jsonify({
            'success': True,
            'subjects': subjects
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/subjects/<subject>/groups', methods=['GET'])
def get_subject_groups(subject):
    try:
        subject_groups = current_scheduler.exam_groups[current_scheduler.exam_groups['Subject'] == subject]

        if subject_groups.empty:
            return jsonify({
                'success': False,
                'error': f'Группы для предмета {subject} не найдены'
            }), 404

        grouped_data = {}
        for edu_program in subject_groups['EduProgram'].unique():
            program_groups = subject_groups[subject_groups['EduProgram'] == edu_program]
            grouped_data[edu_program] = program_groups.to_dict('records')

        return jsonify({
            'success': True,
            'subject': subject,
            'groups': grouped_data
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/subjects/<subject>/delete', methods=['DELETE'])
@jwt_required()
def delete_subject(subject):
    claims = get_jwt()
    user_role = claims.get('role')
    session = Session()
    try:
        active_session = session.query(ExamSession).filter_by(is_active=True).first()
        if active_session and user_role in ["admin-sdt", "admin-sem", "admin-gum", "admin-spigu"]:
            get_or_create_admin_status(session, active_session.id, user_role)
            logging.info(f"Статус 'in_progress' для {user_role} установлен автоматически.")

        subject_groups = current_scheduler.exam_groups[current_scheduler.exam_groups['Subject'] == subject]

        if subject_groups.empty:
            return jsonify({
                'success': False,
                'error': f'Предмет {subject} не найден'
            }), 404

        sections_to_delete = subject_groups['Section'].tolist()
        current_scheduler._delete_sections(sections_to_delete)

        current_scheduler.create_schedule()

        return jsonify({
            'success': True,
            'message': f'Все группы предмета {subject} успешно удалены',
            'deleted_sections': sections_to_delete
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
    finally:
        session.close()


@app.route('/subjects/<subject>/sections/<section>', methods=['DELETE'])
@jwt_required()
def delete_section(subject, section):
    claims = get_jwt()
    user_role = claims.get('role')
    session = Session()
    try:
        active_session = session.query(ExamSession).filter_by(is_active=True).first()
        if active_session and user_role in ["admin-sdt", "admin-sem", "admin-gum", "admin-spigu"]:
            get_or_create_admin_status(session, active_session.id, user_role)
            logging.info(f"Статус 'in_progress' для {user_role} установлен автоматически.")

        subject_groups = current_scheduler.exam_groups[current_scheduler.exam_groups['Subject'] == subject]
        if section not in subject_groups['Section'].values:
            return jsonify({
                'success': False,
                'error': f'Секция {section} не найдена для предмета {subject}'
            }), 404

        current_scheduler._delete_sections([section])

        current_scheduler.create_schedule()

        return jsonify({
            'success': True,
            'message': f'Секция {section} успешно удалена',
            'deleted_section': section
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
    finally:
        session.close()


@app.route('/available-rooms/<day>/<time_slot>', methods=['GET'])
def get_available_rooms(day, time_slot):
    try:
        # Проверяем, существует ли расписание
        if not hasattr(current_scheduler, 'room_availability'):
            return jsonify({
                'success': False,
                'error': 'Расписание не создано. Сначала создайте расписание.'
            }), 400

        logging.info(f"Logging time slot and day: {day}, {time_slot}")

        available_rooms = current_scheduler.find_available_rooms(day, time_slot)

        return jsonify({
            'success': True,
            'day': day,
            'time_slot': time_slot,
            'available_rooms': available_rooms
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/schedule/edit/<string:section>', methods=['POST'])
def edit_schedule(section):
    logging.info(f"Получен запрос на редактирование секции: {section}")

    try:
        data = request.json
        if not data:
            logging.warning("Нет данных для обновления.")
            return jsonify({
                'success': False,
                'error': 'No data provided'
            }), 400

        if not any([data.get('room'), data.get('date'), data.get('time_slot'), data.get('proctor')]):
            logging.warning("Все поля для обновления пусты.")
            return jsonify({
                'success': False,
                'error': 'At least one field (room, date, time_slot, proctor) must be provided'
            }), 400

        current_scheduler.edit_schedule_entry(
            section=section,
            room=data.get('room'),
            date=data.get('date'),
            time_slot=data.get('time_slot'),
            proctor=data.get('proctor')
        )

        return jsonify({
            'success': True,
            'message': f'Запись для секции {section} успешно обновлена',
            'updated_schedule': current_scheduler.schedule_df.to_dict('records')
        })
    except Exception as e:
        logging.error(f"Ошибка при обработке запроса: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/available-proctors/<string:date>/<path:time_slot>', methods=['GET'])
def get_available_proctors(date, time_slot):
    try:
        try:
            datetime.strptime(date, '%d-%m-%Y')
        except ValueError:
            return jsonify({'success': False, 'error': 'Некорректный формат даты. Ожидается DD-MM-YYYY'}), 400

        logging.info(f"Запрос доступных прокторов: дата={date}, слот={time_slot}")

        all_proctors = current_scheduler.get_all_proctors()

        if current_scheduler.schedule_df is None or not {'Date', 'Time_Slot', 'Proctor'}.issubset(
                current_scheduler.schedule_df.columns):
            return jsonify({'success': False, 'error': 'Данные расписания отсутствуют или некорректны'}), 500

        current_scheduler.schedule_df['Date'] = current_scheduler.schedule_df['Date'].astype(str)
        current_scheduler.schedule_df['Time_Slot'] = current_scheduler.schedule_df['Time_Slot'].astype(str)

        busy_proctors = set(
            current_scheduler.schedule_df.loc[
                (current_scheduler.schedule_df['Date'] == date) &
                (current_scheduler.schedule_df['Time_Slot'] == time_slot),
                'Proctor'
            ].dropna()
        )

        available_proctors = [proctor for proctor in all_proctors if proctor not in busy_proctors]

        return jsonify({
            'success': True,
            'date': date,
            'time_slot': time_slot,
            'proctors': available_proctors
        })
    except Exception as e:
        logging.error(f"Ошибка в get_available_proctors: {traceback.format_exc()}")
        return jsonify({'success': False, 'error': 'Внутренняя ошибка сервера'}), 500

@app.route('/api/register', methods=['POST'])
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

@app.route('/api/register-admin', methods=['POST'])
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

@app.route('/api/update-user/<int:user_id>', methods=['PUT'])
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


@app.route('/api/delete-user/<int:user_id>', methods=['DELETE'])
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



@app.route('/api/login', methods=['POST'])
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

@app.route('/api/protected', methods=['GET'])
@jwt_required()
def protected():
    return jsonify({"msg": "Access granted"})

@app.route('/api/update_exam_status', methods=['POST'])
@jwt_required()
def update_exam_status():
    global current_scheduler
    claims = get_jwt()
    user_role = claims.get('role')
    session = Session()
    try:
        active_draft = session.query(ExamSessionDraft).filter_by(is_active=True).first()
        if active_draft and user_role in ["admin-sdt", "admin-sem", "admin-gum", "admin-spigu"]:
            get_or_create_admin_status(session, active_draft.id, user_role, model=AdminStatusDraft)
            logging.info(f"Статус 'in_progress' для {user_role} установлен автоматически.")

        if not current_scheduler:
            return jsonify({
                'status': 'error',
                'message': 'Планировщик не инициализирован'
            }), 400

        data = request.json
        if not data or 'exams' not in data:
            return jsonify({
                'status': 'error',
                'message': 'Не предоставлены данные об экзаменах'
            }), 400

        for exam in data['exams']:
            section_id = exam.get('section_id')
            has_exam = exam.get('has_exam', True)
            current_scheduler.update_exam_status(section_id, has_exam)

        return jsonify({
            'status': 'success',
            'message': 'Статус экзаменов успешно обновлен'
        }), 200

    except Exception as e:
        logging.error(f"Ошибка при обновлении статуса экзаменов: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Ошибка при обновлении статуса экзаменов: {str(e)}'
        }), 500
    finally:
        session.close()


@app.route('/api/update_proctor_status', methods=['POST'])
@jwt_required()
def update_proctor_status():
    global current_scheduler
    claims = get_jwt()
    user_role = claims.get('role')
    session = Session()
    try:
        # Проверяем наличие активного черновика
        active_draft = session.query(ExamSessionDraft).filter_by(is_active=True).first()
        if not active_draft:
            return jsonify({
                'status': 'error',
                'message': 'Активный черновик не найден'
            }), 404

        # Устанавливаем статус 'in_progress' для администратора
        if user_role in ["admin-sdt", "admin-sem", "admin-gum", "admin-spigu"]:
            get_or_create_admin_status(session, active_draft.id, user_role, model=AdminStatusDraft)
            logging.info(f"Статус 'in_progress' для {user_role} установлен автоматически.")

        # Проверяем входные данные
        data = request.json
        if not data or 'exams' not in data:
            return jsonify({
                'status': 'error',
                'message': 'Не предоставлены данные об экзаменах'
            }), 400

        # Обновляем proctor_needed в exam_groups
        for exam in data['exams']:
            section_id = exam.get('section_id')
            proctor_needed = exam.get('proctor_needed', False)
            if section_id not in current_scheduler.exam_groups['Section'].values:
                logging.warning(f"Секция {section_id} не найдена в exam_groups")
                continue
            current_scheduler.exam_groups.loc[
                current_scheduler.exam_groups['Section'] == section_id, 'proctor_needed'
            ] = proctor_needed
            logging.info(f"Обновлён proctor_needed={proctor_needed} для секции {section_id}")

        # Синхронизируем exam_groups с exams_data в базе
        active_draft.exams_data = current_scheduler.exam_groups.to_json(orient='records')

        # Фиксируем изменения в базе
        session.commit()
        logging.info("Изменения в базе данных успешно зафиксированы")

        return jsonify({
            'status': 'success',
            'message': 'Статус прокторинга успешно обновлен'
        }), 200

    except Exception as e:
        session.rollback()
        logging.error(f"Ошибка при обновлении статуса прокторинга: {traceback.format_exc()}")
        return jsonify({
            'status': 'error',
            'message': f'Ошибка при обновлении статуса прокторинга: {str(e)}'
        }), 500
    finally:
        session.close()

@app.route('/api/update_room_requirement', methods=['POST'])
@jwt_required()
def update_room_requirement():
    global current_scheduler
    claims = get_jwt()
    user_role = claims.get('role')
    session = Session(bind=engine)
    try:
        active_draft = session.query(ExamSessionDraft).filter_by(is_active=True).first()
        if active_draft and user_role in ["admin-sdt", "admin-sem", "admin-gum", "admin-spigu"]:
            get_or_create_admin_status(session, active_draft.id, user_role, model=AdminStatusDraft)
            logging.info(f"Статус 'in_progress' для {user_role} установлен автоматически.")

        if not current_scheduler:
            return jsonify({
                'status': 'error',
                'message': 'Планировщик не инициализирован'
            }), 400

        data = request.json
        if not data or 'exams' not in data:
            return jsonify({
                'status': 'error',
                'message': 'Не предоставлены данные об экзаменах'
            }), 400

        for exam in data['exams']:
            section_id = exam.get('section_id')
            # Align with the request field 'two_rooms_needed'
            two_rooms_needed = exam.get('two_rooms_needed', False)
            logging.info(f"Обновлено требование к аудиториям для {section_id}: two_rooms_needed={two_rooms_needed}")
            current_scheduler.update_room_requirement(section_id, two_rooms_needed)

        # Update the draft with the latest exam_groups
        active_draft.exams_data = current_scheduler.exam_groups.to_json(orient='records')
        session.commit()

        return jsonify({
            'status': 'success',
            'message': 'Требования к аудиториям успешно обновлены'
        }), 200

    except Exception as e:
        session.rollback()
        logging.error(f"Ошибка при обновлении требований к аудиториям: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Ошибка при обновлении требований к аудиториям: {str(e)}'
        }), 500
    finally:
        session.close()

@app.route('/api/exams/batch-update', methods=['POST'])
@jwt_required()
def batch_update_exams():
    global current_scheduler
    claims = get_jwt()
    user_role = claims.get('role')
    session = Session()
    try:
        logging.info(f"User {user_role} initiated batch exam update with data: {request.json}")
        active_draft = session.query(ExamSessionDraft).filter_by(is_active=True).first()
        if active_draft and user_role in ["admin-sdt", "admin-sem", "admin-gum", "admin-spigu"]:
            get_or_create_admin_status(session, active_draft.id, user_role, model=AdminStatusDraft)
            logging.info(f"Статус 'in_progress' для {user_role} установлен автоматически.")

        if not current_scheduler:
            logging.error("Scheduler not initialized.")
            return jsonify({
                'status': 'error',
                'message': 'Планировщик не инициализирован'
            }), 400

        data = request.json
        if not data or 'exams' not in data:
            logging.error("No exam data provided in batch update.")
            return jsonify({
                'status': 'error',
                'message': 'Не предоставлены данные об экзаменах'
            }), 400

        current_scheduler.batch_update_exams(data['exams'])

        # Update the draft with the latest exam_groups
        if active_draft:
            active_draft.exams_data = current_scheduler.exam_groups.to_json(orient='records')
            session.commit()
            logging.info("Exam draft session data updated successfully.")

        logging.info("Batch exam update completed successfully.")
        return jsonify({
            'status': 'success',
            'message': 'Экзамены успешно обновлены'
        }), 200

    except Exception as e:
        session.rollback()
        logging.error(f"Ошибка при обновлении экзаменов: {traceback.format_exc()}")
        return jsonify({
            'status': 'error',
            'message': f'Ошибка при обновлении экзаменов: {str(e)}'
        }), 500
    finally:
        session.close()

@app.route('/api/upload-students', methods=['POST'])
@admin_required("admin")
def upload_students():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    if not file.filename.endswith(('.xlsx', '.xls')):
        return jsonify({'error': 'Invalid file format'}), 400

    try:
        df = pd.read_excel(file)
        required_columns = ['fake_id', 'fake_name']  # Только обязательные поля

        if not all(col in df.columns for col in required_columns):
            return jsonify({'error': 'Missing required columns'}), 400

        df = df.drop_duplicates(subset=['fake_id'])
        session = Session()
        results = {'created': [], 'errors': []}

        for index, row in df.iterrows():
            try:
                # Проверка существования пользователя
                if session.query(User).filter_by(email=str(row['fake_id'])).first():
                    results['errors'].append(f"User {row['fake_id']} already exists")
                    continue

                # Создание пользователя
                new_user = User(
                    email=str(row['fake_id']),
                    password=bcrypt.hashpw(row['fake_name'].encode('utf-8'), bcrypt.gensalt()).decode('utf-8'),
                    full_name=row['fake_name'],
                    role='student'
                )
                session.add(new_user)
                session.commit()

                results['created'].append({
                    'id': new_user.id,
                    'fake_id': row['fake_id'],
                    'name': row['fake_name']
                })

            except Exception as e:
                session.rollback()
                results['errors'].append(f"Row {index + 2}: {str(e)}")

        return jsonify({
            'message': f"Processed {len(df)} rows",
            'results': results
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()


@app.route('/schedule/student/<string:student_id>')
def get_student_schedule(student_id):
    try:
        if not current_scheduler:
            return jsonify({"error": "Планировщик не инициализирован"}), 500

        # Диагностика: логируем первые 5 ключей из seat_assignments
        if hasattr(current_scheduler, 'seat_assignments'):
            sample_keys = list(current_scheduler.seat_assignments.keys())[:5]
            logging.info(f"Sample seat assignment keys: {sample_keys}")
        else:
            logging.error("No seat_assignments in scheduler!")

        student_schedule = current_scheduler.get_student_sections(student_id)

        if student_schedule.empty:
            return jsonify({"error": "Расписание не найдено"}), 404

        result = student_schedule.drop(
            columns=['Student_Conflicts', 'proctor_needed'],  # Убрали 'Proctor'
            errors='ignore'
        ).replace({np.nan: None}).to_dict('records')

        for exam in result:
            try:
                # Формируем ключ для поиска
                date_part = exam['Date']
                time_slot = exam['Time_Slot'].strip()
                subject = exam['Subject'].strip()
                proctor = exam.get('Proctor', None)  # Берем проктора из schedule_df

                # Вариант 1: точное совпадение
                exact_key = f"{date_part}|{time_slot}|{subject}|{student_id}"

                # Вариант 2: без учёта пробелов
                clean_key = f"{date_part}|{time_slot}|{subject.replace(' ', '')}|{student_id}"

                # Вариант 3: с нормализацией Unicode
                normalized_key = f"{date_part}|{time_slot}|{subject.encode('unicode-escape').decode()}|{student_id}"

                logging.info(f"Searching seat for key: {exact_key}")

                # Пробуем разные варианты ключей
                seat_info = (current_scheduler.seat_assignments.get(exact_key) or
                             current_scheduler.seat_assignments.get(clean_key) or
                             next((v for k, v in current_scheduler.seat_assignments.items()
                                   if student_id in k and subject in k and time_slot in k), None))

                if seat_info:
                    exam['seat_info'] = {
                        'seat_number': seat_info.get('seat'),
                        'room': seat_info.get('room'),
                        'proctor': proctor  # Добавляем проктора
                    }
                    logging.info(f"Found seat info: {exam['seat_info']}")
                else:
                    exam['seat_info'] = {
                        'seat_number': None,
                        'room': None,
                        'proctor': proctor
                    }
                    logging.warning(f"No seat found for student {student_id} in {subject} on {date_part} {time_slot}")

            except Exception as e:
                logging.error(f"Error processing seat info: {str(e)}")
                exam['seat_info'] = {
                    'seat_number': None,
                    'room': 'Ошибка',
                    'proctor': 'Ошибка обработки'
                }

        return jsonify(result)

    except Exception as e:
        logging.error(f"Ошибка при получении расписания: {traceback.format_exc()}")
        return jsonify({
            "error": "Внутренняя ошибка сервера",
            "details": str(e)
        }), 500


@app.route('/schedule/student/<student_id>/export')
def export_student_schedule(student_id):
    output_file = f"student_{student_id}_schedule.xlsx"
    current_scheduler.export_student_schedule_to_excel(student_id, output_file)
    return send_file(output_file, as_attachment=True)


@app.route('/api/update_exam_durations', methods=['POST'])
@jwt_required()
def handle_update_durations():
    global current_scheduler
    claims = get_jwt()
    user_role = claims.get('role')
    session = Session()
    try:
        active_draft = session.query(ExamSessionDraft).filter_by(is_active=True).first()
        if active_draft and user_role in ["admin-sdt", "admin-sem", "admin-gum", "admin-spigu"]:
            get_or_create_admin_status(session, active_draft.id, user_role, model=AdminStatusDraft)
            logging.info(f"Статус 'in_progress' для {user_role} установлен автоматически.")

        if not current_scheduler:
            return jsonify({
                'status': 'error',
                'message': 'Планировщик не инициализирован'
            }), 400

        data = request.json
        if not data or 'exams' not in data:
            return jsonify({
                'status': 'error',
                'message': 'Не предоставлены данные об экзаменах'
            }), 400

        for exam in data['exams']:
            if 'section_id' not in exam or 'duration' not in exam:
                return jsonify({
                    'status': 'error',
                    'message': 'Каждый экзамен должен содержать section_id и duration'
                }), 400

            if exam['duration'] not in [60, 120, 180]:
                return jsonify({
                    'status': 'error',
                    'message': 'Длительность экзамена может быть только 60, 120 или 180 минут'
                }), 400

        current_scheduler.update_exam_durations(data['exams'])

        return jsonify({
            'status': 'success',
            'message': 'Длительности экзаменов успешно обновлены'
        }), 200

    except Exception as e:
        logging.error(f"Ошибка при обновлении длительностей: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Ошибка при обновлении длительностей: {str(e)}'
        }), 500
    finally:
        session.close()

@app.route('/api/delete_draft/<int:draft_id>', methods=['DELETE'])
@admin_required("admin")
def delete_draft(draft_id):
    session = Session()
    try:
        draft = session.query(ExamSessionDraft).get(draft_id)
        if not draft:
            return jsonify({"error": "Черновик не найден"}), 404

        session.query(AdminStatusDraft).filter_by(session_id=draft_id).delete()
        session.delete(draft)
        session.commit()
        return jsonify({"message": "Черновик успешно удалён"}), 200
    except Exception as e:
        session.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()

@app.route("/api/init-admins", methods=["GET"])
def init_admins():
    User.register_user(session, "admin@narxoz.kz", "admin123", "main-admin", "admin");
    User.register_user(session, "admin-sdt@narxoz.kz", "admin123", "admin-sdt", "admin-sdt");
    User.register_user(session, "admin-sem@narxoz.kz", "admin123", "admin-sem", "admin-sem");
    User.register_user(session, "admin-gum@narxoz.kz", "admin123", "admin-gum", "admin-gum");
    User.register_user(session, "admin-spigu@narxoz.kz", "admin123", "admin-spigu", "admin-spigu");


# Получить список всех черновиков
@app.route('/api/drafts', methods=['GET'])
@admin_required("admin")
def get_all_drafts():
    session = Session(bind=engine)
    try:
        drafts = session.query(ExamSessionDraft).all()
        drafts_data = [
            {
                'id': draft.id,
                'title': draft.title,
                'start_date': draft.start_date.isoformat() if draft.start_date else None,
                'days': draft.days,
                'created_at': draft.created_at.isoformat() if draft.created_at else None,
                'is_active': draft.is_active
            }
            for draft in drafts
        ]
        return jsonify(drafts_data), 200
    except Exception as e:
        logging.error(f"Ошибка при получении списка черновиков: {str(e)}")
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()

# Получить детали конкретного черновика
@app.route('/api/drafts/<int:draft_id>', methods=['GET'])
@admin_required("admin")
def get_draft_details(draft_id):
    session = Session(bind=engine)
    try:
        draft = session.query(ExamSessionDraft).get(draft_id)
        if draft:
            return jsonify(draft.to_dict()), 200
        else:
            return jsonify({"error": "Черновик не найден"}), 404
    except Exception as e:
        logging.error(f"Ошибка при получении черновика {draft_id}: {str(e)}")
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()

# Удалить черновик
@app.route('/api/drafts/<int:draft_id>', methods=['DELETE'])
@admin_required("admin")
def delete_draft_by_id(draft_id):
    session = Session(bind=engine)
    try:
        draft = session.query(ExamSessionDraft).get(draft_id)
        if not draft:
            return jsonify({"error": "Черновик не найден"}), 404

        # Удаляем связанные статусы администраторов
        session.query(AdminStatusDraft).filter_by(session_id=draft_id).delete()
        session.delete(draft)
        session.commit()
        return jsonify({"message": "Черновик успешно удалён"}), 200
    except Exception as e:
        session.rollback()
        logging.error(f"Ошибка при удалении черновика {draft_id}: {str(e)}")
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()

if __name__ == '__main__':
    app.run(debug=True, port=5000)