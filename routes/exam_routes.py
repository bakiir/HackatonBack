from venv import logger

import bcrypt
from flask import Blueprint, jsonify, request, send_file
from create_db import ExamSession, engine, ExamSessionDraft, AdminStatusDraft, RoomExclusion, ClassroomSlot, update_classroom_slots
from sqlalchemy.orm import sessionmaker
import logging
import traceback
import pandas as pd

from users_db import User, get_or_create_admin_status, get_all_admin_statuses, are_all_admins_ready, get_resolved_conflicts_by_session_id
from services.jwt_service import admin_required
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt

import services.scheduler_store as store

Session = sessionmaker(bind=engine)
session = Session()

exam_bp = Blueprint('exam_bp', __name__)

role_to_faculty = {
    "admin-sdt": "Школа цифровых технологий",
    "admin-sem": "Школа экономики и менеджмента",
    "admin-gum": "Гуманитарная школа",
    "admin-spigu": "Школа права и государственного управления"
}

from app import handle_nan_values

def handle_nan_values(obj):
    import math, numpy as np, pandas as pd
    if isinstance(obj, (float, np.float64, np.float32)) and (math.isnan(obj) or np.isnan(obj)):
        return None
    if isinstance(obj, (np.int64, np.int32)):
        return int(obj)
    if isinstance(obj, (np.float64, np.float32)):
        return float(obj)
    elif isinstance(obj, dict):
        return {key: handle_nan_values(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [handle_nan_values(item) for item in obj]
    elif isinstance(obj, pd.DataFrame):
        return obj.replace({np.nan: None}).to_dict('records')
    else:
        return obj


@exam_bp.route('/api/manage', methods=['POST'])
@jwt_required()
def handle_management():
    import services.scheduler_store as store
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

    if store.current_scheduler is None:
        logging.error("store.current_scheduler не инициализирован")
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

            subjects = store.current_scheduler.get_by_faculty(faculty)
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

            sections_to_delete = store.current_scheduler.exam_groups[
                store.current_scheduler.exam_groups['Subject'] == subject
                ]['Section'].tolist()

            store.current_scheduler._delete_sections(sections_to_delete)

            # Update the draft with the latest exam_groups

            if active_draft:
                active_draft.exams_data = store.current_scheduler.exam_groups.to_json(orient='records')
                session.commit()

            return jsonify({
                'status': 'success',
                'message': f'Предмет {subject} удален',
                'remaining_subjects': store.current_scheduler.get_unique_subjects()
            })



        elif action == 'delete_section':

            active_draft = session.query(ExamSessionDraft).filter_by(is_active=True).first()

            if active_draft and user_role in ["admin-sdt", "admin-sem", "admin-gum", "admin-spigu"]:
                get_or_create_admin_status(session, active_draft.id, user_role, model=AdminStatusDraft)
                logging.info(f"Статус 'in_progress' для {user_role} установлен автоматически.")

            section = data['section']

            if not isinstance(store.current_scheduler.schedule_df, pd.DataFrame):
                logging.info("Schedule not yet created, initializing empty schedule_df")
                store.current_scheduler.schedule_df = pd.DataFrame(
                    columns=['Section', 'Date', 'Time_Slot', 'Room', 'Proctor'])

            store.current_scheduler._delete_sections([section])

            # Update the draft with the latest exam_groups

            if active_draft:
                active_draft.exams_data = store.current_scheduler.exam_groups.to_json(orient='records')
                session.commit()

            return jsonify({
                'status': 'success',
                'message': f'Секция {section} удалена',
                'remaining_subjects': store.current_scheduler.get_unique_subjects()
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
            store.current_scheduler.create_schedule()

            # Resolve IT lab conflicts
            # resolve_it_lab_conflicts(store.current_scheduler)

            # Analyze failed sections
            analysis_report = store.current_scheduler.analyze_failed_sections_details()

            # Деактивируем все предыдущие сессии и создаём новую
            session.query(ExamSession).update({'is_active': False})
            new_session = ExamSession(
                title=store.current_scheduler.title,
                start_date=store.current_scheduler.original_start_date,
                original_start_date=store.current_scheduler.original_start_date,
                days=store.current_scheduler.original_num_days,
                original_num_days=store.current_scheduler.original_num_days,
                schedule_data=store.current_scheduler.schedule_df.to_json(orient='records'),
                exams_data=store.current_scheduler.exams_df.to_json(orient='records'),
                rooms_data=store.current_scheduler.rooms_df.to_json(orient='records'),
                faculties_data=store.current_scheduler.faculties_df.to_json(orient='records'),
                seat_assignments=store.current_scheduler.seat_assignments,
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

            sanitized_schedule = handle_nan_values(store.current_scheduler.schedule_df)
            return jsonify({
                'status': 'success',
                'schedule': sanitized_schedule,
                'stats': {
                    'total': len(store.current_scheduler.exam_groups),
                    'scheduled': len(store.current_scheduler.schedule_df),
                    'failed_sections_report': analysis_report
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

@exam_bp.route('/api/get-subjects-by-faculty/', defaults={'faculty': None})
@exam_bp.route('/api/get-subjects-by-faculty/<faculty>', methods=['GET'])
@jwt_required()
def get_subjects_by_faculty(faculty):
    import services.scheduler_store as store
    logging.info(f"Запрос API для предметов факультета: {faculty}")

    if store.current_scheduler is None:
        logging.error("store.current_scheduler не инициализирован")
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
        subjects = store.current_scheduler.get_by_faculty(faculty)
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

@exam_bp.route('/section/<section_id>')
def get_section_info(section_id):
    section_info = store.current_scheduler.get_section_info(section_id)
    # Преобразуем numpy типы и NaN в стандартные типы Python
    section_info = handle_nan_values(section_info)
    return jsonify(section_info)  # Возвращаем словарь как JSON

@exam_bp.route('/section/<section_id>/export')
def export_section_info(section_id):
    output_file = f"section_{section_id}_info.xlsx"
    section_info = store.current_scheduler.get_section_info(section_id)  # Получаем информацию о секции
    store.current_scheduler.export_section_info_to_excel(section_info, output_file)  # Экспортируем в Excel
    return send_file(output_file, as_attachment=True)  # Отправляем файл пользователю

@exam_bp.route('/subjects', methods=['GET'])
def get_subjects():
    try:
        subjects = store.current_scheduler.get_unique_subjects()
        return jsonify({
            'success': True,
            'subjects': subjects
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@exam_bp.route('/subjects/<subject>/groups', methods=['GET'])
def get_subject_groups(subject):
    try:
        subject_groups = store.current_scheduler.exam_groups[store.current_scheduler.exam_groups['Subject'] == subject]

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

@exam_bp.route('/subjects/<subject>/delete', methods=['DELETE'])
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

        subject_groups = store.current_scheduler.exam_groups[store.current_scheduler.exam_groups['Subject'] == subject]

        if subject_groups.empty:
            return jsonify({
                'success': False,
                'error': f'Предмет {subject} не найден'
            }), 404

        sections_to_delete = subject_groups['Section'].tolist()
        store.current_scheduler._delete_sections(sections_to_delete)

        store.current_scheduler.create_schedule()

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

@exam_bp.route('/subjects/<subject>/sections/<section>', methods=['DELETE'])
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

        subject_groups = store.current_scheduler.exam_groups[store.current_scheduler.exam_groups['Subject'] == subject]
        if section not in subject_groups['Section'].values:
            return jsonify({
                'success': False,
                'error': f'Секция {section} не найдена для предмета {subject}'
            }), 404

        store.current_scheduler._delete_sections([section])

        store.current_scheduler.create_schedule()

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

@exam_bp.route('/schedule/edit/<string:section>', methods=['POST'])
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

        store.current_scheduler.edit_schedule_entry(
            section=section,
            room=data.get('room'),
            date=data.get('date'),
            time_slot=data.get('time_slot'),
            proctor=data.get('proctor')
        )

        return jsonify({
            'success': True,
            'message': f'Запись для секции {section} успешно обновлена',
            'updated_schedule': store.current_scheduler.schedule_df.to_dict('records')
        })
    except Exception as e:
        logging.error(f"Ошибка при обработке запроса: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@exam_bp.route('/api/update_exam_status', methods=['POST'])
@jwt_required()
def update_exam_status():
    import services.scheduler_store as store
    claims = get_jwt()
    user_role = claims.get('role')
    session = Session()
    try:
        active_draft = session.query(ExamSessionDraft).filter_by(is_active=True).first()
        if active_draft and user_role in ["admin-sdt", "admin-sem", "admin-gum", "admin-spigu"]:
            get_or_create_admin_status(session, active_draft.id, user_role, model=AdminStatusDraft)
            logging.info(f"Статус 'in_progress' для {user_role} установлен автоматически.")

        if not store.current_scheduler:
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
            store.current_scheduler.update_exam_status(section_id, has_exam)

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

@exam_bp.route('/api/exams/batch-update', methods=['POST'])
@jwt_required()
def batch_update_exams():
    import services.scheduler_store as store
    claims = get_jwt()
    user_role = claims.get('role')
    session = Session()
    try:
        logging.info(f"User {user_role} initiated batch exam update with data: {request.json}")
        active_draft = session.query(ExamSessionDraft).filter_by(is_active=True).first()
        if active_draft and user_role in ["admin-sdt", "admin-sem", "admin-gum", "admin-spigu"]:
            get_or_create_admin_status(session, active_draft.id, user_role, model=AdminStatusDraft)
            logging.info(f"Статус 'in_progress' для {user_role} установлен автоматически.")

        if not store.current_scheduler:
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

        store.current_scheduler.batch_update_exams(data['exams'])

        # Update the draft with the latest exam_groups
        if active_draft:
            active_draft.exams_data = store.current_scheduler.exam_groups.to_json(orient='records')
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

@exam_bp.route('/api/upload-students', methods=['POST'])
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

@exam_bp.route('/schedule/student/<student_id>/export')
def export_student_schedule(student_id):
    output_file = f"student_{student_id}_schedule.xlsx"
    store.current_scheduler.export_student_schedule_to_excel(student_id, output_file)
    return send_file(output_file, as_attachment=True)