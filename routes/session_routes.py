from flask import Blueprint, jsonify, request
from create_db import ExamSession, engine, ExamSessionDraft, AdminStatusDraft, RoomExclusion, ClassroomSlot, update_classroom_slots
from sqlalchemy.orm import sessionmaker
import logging
import traceback
from datetime import datetime
import tempfile
import os

from users_db import  get_or_create_admin_status, set_admin_status_ready, get_all_admin_statuses, are_all_admins_ready
from services.jwt_service import admin_required
from flask_jwt_extended import jwt_required, get_jwt

from services.exam_scheduler import ExamScheduler
from services.scheduler_core.utils import role_to_faculty

Session = sessionmaker(bind=engine)
session = Session()

session_bp = Blueprint('session_bp', __name__)

@session_bp.route('/api/init', methods=['POST'])
@admin_required("admin")
def handle_initialization():
    import services.scheduler_store as store

    try:
        # Clear old room exclusions at the start of initialization
        try:
            num_deleted = session.query(RoomExclusion).delete()
            session.commit()
            logging.info(f"Удалено {num_deleted} старых правил блокировки аудиторий.")
        except Exception as e:
            session.rollback()
            logging.error(f"Ошибка при удалении старых блокировок: {str(e)}")
            return jsonify({'status': 'error', 'message': f'Ошибка при очистке старых блокировок: {str(e)}'}), 500

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
            store.current_scheduler = ExamScheduler(
                title=title,
                exams_file=exams_path,
                rooms_file=rooms_path,
                faculties_file=faculties_path,
                start_date=start_date,
                num_days=num_days
            )
            # 4. Update classroom slots
            update_classroom_slots(store.current_scheduler.get_current_dates(), store.current_scheduler.rooms_df)

            # 5. Создание черновика сессии
            session.query(ExamSessionDraft).update({'is_active': False})  # Деактивируем предыдущие черновики
            new_draft = ExamSessionDraft(
                title=title,
                start_date=datetime.strptime(start_date, '%Y-%m-%d').date(),
                days=num_days,
                exams_data=store.current_scheduler.exams_df.to_json(orient='records'),
                rooms_data=store.current_scheduler.rooms_df.to_json(orient='records'),
                faculties_data=store.current_scheduler.faculties_df.to_json(orient='records'),
                is_active=True
            )
            session.add(new_draft)
            session.commit()

        # 5. Возвращаем данные для управления предметами
        return jsonify({
            'status': 'subject_management',
            'subjects': store.current_scheduler.get_unique_subjects(),
            'dates': store.current_scheduler.get_current_dates(),
            'message': 'Управление предметами перед генерацией'
        })

    except Exception as e:
        logging.error(f"Ошибка инициализации: {traceback.format_exc()}")
        return jsonify({
            'status': 'error',
            'message': f'Ошибка инициализации: {str(e)}'
        }), 500

@session_bp.route('/api/manage_dates', methods=['POST'])
@admin_required("admin")
def manage_dates():
    import services.scheduler_store as store

    # Проверка инициализации планировщика
    if store.current_scheduler is None:
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

            store.current_scheduler.remove_date(date_to_remove)
            update_classroom_slots(store.current_scheduler.get_current_dates(), store.current_scheduler.rooms_df)
            logging.info(f'Дата {date_to_remove} удалена')
            return jsonify({
                'status': 'success',
                'message': f'Дата {date_to_remove} удалена',
                'dates': store.current_scheduler.get_current_dates()
            })

        elif action == 'add_custom':
            custom_date = request.json.get('custom_date')
            if not custom_date:
                return jsonify({
                    'status': 'error',
                    'message': 'Не указана дата для добавления'
                }), 400

            store.current_scheduler.add_custom_date(custom_date)
            update_classroom_slots(store.current_scheduler.get_current_dates(), store.current_scheduler.rooms_df)
            logging.info(f'Дата {custom_date} добавлена')
            return jsonify({
                'status': 'success',
                'message': f'Дата {custom_date} добавлена',
                'dates': store.current_scheduler.get_current_dates()
            })

        elif action == 'restore':
            store.current_scheduler.restore_default_dates()
            update_classroom_slots(store.current_scheduler.get_current_dates(), store.current_scheduler.rooms_df)
            logging.info('Исходные даты восстановлены')
            return jsonify({
            'status': 'success',
            'message': 'Исходные даты восстановлены',
            'dates': store.current_scheduler.get_current_dates()
        })

    except Exception as e:
        logging.error(f"Ошибка управления датами: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Ошибка управления датами: {str(e)}'
        }), 500

@session_bp.route('/api/set_admin_status_draft', methods=['POST'])
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

@session_bp.route('/api/check_all_drafts', methods=['GET'])
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

@session_bp.route('/api/admin_statuses', methods=['GET'])
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

@session_bp.route('/api/sessions', methods=['GET'])
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

@session_bp.route('/api/sessions/<int:session_id>/data', methods=['GET'])
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

@session_bp.route('/api/sessions/<int:session_id>', methods=['GET'])
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

@session_bp.route('/api/sessions/<int:session_id>/activate', methods=['POST'])
@admin_required("admin")
def activate_session(session_id):
    db_session = Session()
    try:
        session = db_session.query(ExamSession).get(session_id)
        if not session:
            return jsonify({"error": "Session not found"}), 404

        import services.scheduler_store as store

        # Загружаем данные сессии
        store.current_scheduler = ExamScheduler(session_data=session)

        # Дополнительная проверка seat_assignments
        if not hasattr(store.current_scheduler, 'seat_assignments') or not store.current_scheduler.seat_assignments:
            if session.seat_assignments:
                store.current_scheduler.seat_assignments = session.seat_assignments
                logging.info("Loaded seat_assignments directly from session")
            else:
                logging.warning("No seat_assignments in session data")

        db_session.query(ExamSession).update({"is_active": False})
        session.is_active = True
        db_session.commit()

        return jsonify({
            "status": "success",
            "seat_assignments_loaded": bool(hasattr(store.current_scheduler, 'seat_assignments') and
                                            store.current_scheduler.seat_assignments)
        })

    except Exception as e:
        db_session.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db_session.close()

@session_bp.route('/api/sessions/<int:session_id>', methods=['DELETE'])
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

@session_bp.route('/api/delete_draft/<int:draft_id>', methods=['DELETE'])
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

@session_bp.route('/api/drafts', methods=['GET'])
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

@session_bp.route('/api/drafts/<int:draft_id>', methods=['GET'])
@admin_required("admin")
def get_draft_details_by_id(draft_id):
    db_session = Session(bind=engine)
    try:
        draft = db_session.query(ExamSessionDraft).get(draft_id)
        if draft:
            return jsonify(draft.to_dict()), 200
        else:
            return jsonify({"error": "Черновик не найден"}), 404
    except Exception as e:
        logging.error(f"Ошибка при получении черновика {draft_id}: {str(e)}")
        return jsonify({"error": str(e)}), 500
    finally:
        db_session.close()

@session_bp.route('/api/drafts/<int:draft_id>', methods=['DELETE'])
@admin_required("admin")
def delete_draft_by_id_api(draft_id):
    db_session = Session(bind=engine)
    try:
        draft = db_session.query(ExamSessionDraft).get(draft_id)
        if not draft:
            return jsonify({"error": "Черновик не найден"}), 404

        # Удаляем связанные статусы администраторов
        db_session.query(AdminStatusDraft).filter_by(session_id=draft_id).delete()
        db_session.delete(draft)
        db_session.commit()
        return jsonify({"message": "Черновик успешно удалён"}), 200
    except Exception as e:
        db_session.rollback()
        logging.error(f"Ошибка при удалении черновика {draft_id}: {str(e)}")
        return jsonify({"error": str(e)}), 500
    finally:
        db_session.close()