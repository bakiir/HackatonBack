from flask import Blueprint, jsonify, request
from create_db import engine, ExamSessionDraft, AdminStatusDraft, RoomExclusion, update_classroom_slots
from sqlalchemy.orm import sessionmaker
import logging
import traceback
from datetime import datetime

from users_db import get_all_admin_statuses, are_all_admins_ready
from services.jwt_service import admin_required
from flask_jwt_extended import jwt_required, get_jwt

from services.exam_scheduler import ExamScheduler
from repositories.session_repository import SessionRepository, SessionDraftRepository
from services.data_access.session_service import SessionService

Session = sessionmaker(bind=engine)
db_session = Session()

# Initialize Repository and Service
session_repo = SessionRepository(db_session)
draft_repo = SessionDraftRepository(db_session)
session_service = SessionService(session_repo, draft_repo)

session_bp = Blueprint('session_bp', __name__)

@session_bp.route('/api/init', methods=['POST'])
@admin_required("admin")
def handle_initialization():
    import services.scheduler_store as store

    try:
        title = request.form.get('title', 'Сезон без имени')
        exams_file = request.files['exams']
        rooms_file = request.files['rooms']
        faculties_file = request.files['faculties']
        start_date = request.form['start_date']
        num_days = int(request.form.get('num_days', 14))

        # Delegate initialization to service
        scheduler, new_draft = session_service.initialize_scheduler_session(
            title, exams_file, rooms_file, faculties_file, start_date, num_days
        )
        
        # Update global store
        store.current_scheduler = scheduler

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

    try:
        active_draft = draft_repo.session.query(ExamSessionDraft).filter_by(is_active=True).first()
        if not active_draft:
            return jsonify({"error": "Активный черновик сессии не найден"}), 404

        draft_repo.update_admin_status(active_draft.id, user_role, 'ready')
        logging.info(f"Администратор {user_role} установил статус 'ready' для черновика сессии {active_draft.id}")
        return jsonify({"message": f"Статус для {user_role} установлен на 'ready'"}), 200
    except Exception as e:
        logging.error(f"Ошибка при установке статуса: {str(e)}")
        return jsonify({"error": str(e)}), 500

@session_bp.route('/api/check_all_drafts', methods=['GET'])
@admin_required("admin")
def check_all_drafts():
    try:
        active_draft = draft_repo.session.query(ExamSessionDraft).filter_by(is_active=True).first()
        if not active_draft:
            return jsonify({"have_drafts": False}), 200

        all_ready = are_all_admins_ready(draft_repo.session, active_draft.id, model=AdminStatusDraft)
        return jsonify({"have_drafts": all_ready}), 200
    except Exception as e:
        logging.error(f"Ошибка при проверке статусов: {str(e)}")
        return jsonify({"error": str(e)}), 500

@session_bp.route('/api/admin_statuses', methods=['GET'])
@jwt_required()
def admin_statuses():
    try:
        active_session = draft_repo.session.query(ExamSessionDraft).filter_by(is_active=True).first()
        draft_count = draft_repo.session.query(ExamSessionDraft).count()
        has_drafts = draft_count > 0

        if active_session:
            statuses = get_all_admin_statuses(draft_repo.session, active_session.id)
            return jsonify({
                "statuses": [s.to_dict() for s in statuses],
                "has_drafts": has_drafts
            }), 200
        else:
            return jsonify({
                "has_drafts": has_drafts
            }), 200

    except Exception as e:
        logging.error(f"Ошибка при получении статусов: {str(e)}")
        return jsonify({"error": str(e), "has_drafts": False}), 500

@session_bp.route('/api/sessions', methods=['GET'])
@admin_required("admin")
def get_all_sessions():
    try:
        sessions = session_repo.get_all()
        sessions_data = [
            {
                'title': s.title,
                'start_date': s.start_date,
                'days': s.days,
                'created_at': s.created_at,
                'id': s.id
            }
            for s in sessions
        ]
        return jsonify(sessions_data), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@session_bp.route('/api/sessions/<int:session_id>/data', methods=['GET'])
@admin_required("admin")
def get_data_by_id(session_id):
    try:
        data = session_repo.get_by_id(session_id)
        if data:
            return jsonify(data.to_dict()), 200
        return jsonify({"error": "Session not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@session_bp.route('/api/sessions/<int:session_id>', methods=['GET'])
@admin_required("admin")
def get_session_details(session_id):
    try:
        session = session_repo.get_by_id(session_id)
        if session:
            return jsonify(session.to_dict()), 200
        return jsonify({"error": "Session not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@session_bp.route('/api/sessions/<int:session_id>/activate', methods=['POST'])
@admin_required("admin")
def activate_session(session_id):
    try:
        session_obj = session_repo.get_by_id(session_id)
        if not session_obj:
            return jsonify({"error": "Session not found"}), 404

        import services.scheduler_store as store
        store.current_scheduler = ExamScheduler(session_data=session_obj)

        if not hasattr(store.current_scheduler, 'seat_assignments') or not store.current_scheduler.seat_assignments:
            if session_obj.seat_assignments:
                store.current_scheduler.seat_assignments = session_obj.seat_assignments
                logging.info("Loaded seat_assignments directly from session")

        session_repo.set_active_session(session_id)

        return jsonify({
            "status": "success",
            "seat_assignments_loaded": bool(hasattr(store.current_scheduler, 'seat_assignments') and
                                            store.current_scheduler.seat_assignments)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@session_bp.route('/api/sessions/<int:session_id>', methods=['DELETE'])
@admin_required("admin")
def delete_session(session_id):
    try:
        session_obj = session_repo.get_by_id(session_id)
        if session_obj:
            session_repo.delete(session_obj)
            return jsonify({"message": "Сессия удалена"}), 200
        return jsonify({"error": "Session not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@session_bp.route('/api/delete_draft/<int:draft_id>', methods=['DELETE'])
@admin_required("admin")
def delete_draft(draft_id):
    try:
        draft = draft_repo.get_by_id(draft_id)
        if not draft:
            return jsonify({"error": "Черновик не найден"}), 404

        draft_repo.session.query(AdminStatusDraft).filter_by(session_id=draft_id).delete()
        draft_repo.delete(draft)
        return jsonify({"message": "Черновик успешно удалён"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@session_bp.route('/api/drafts', methods=['GET'])
@admin_required("admin")
def get_all_drafts():
    try:
        drafts = draft_repo.get_all()
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

@session_bp.route('/api/drafts/<int:draft_id>', methods=['GET'])
@admin_required("admin")
def get_draft_details_by_id(draft_id):
    try:
        draft = draft_repo.get_by_id(draft_id)
        if draft:
            return jsonify(draft.to_dict()), 200
        return jsonify({"error": "Черновик не найден"}), 404
    except Exception as e:
        logging.error(f"Ошибка при получении черновика {draft_id}: {str(e)}")
        return jsonify({"error": str(e)}), 500

@session_bp.route('/api/drafts/<int:draft_id>', methods=['DELETE'])
@admin_required("admin")
def delete_draft_by_id_api(draft_id):
    try:
        draft = draft_repo.get_by_id(draft_id)
        if not draft:
            return jsonify({"error": "Черновик не найден"}), 404

        draft_repo.session.query(AdminStatusDraft).filter_by(session_id=draft_id).delete()
        draft_repo.delete(draft)
        return jsonify({"message": "Черновик успешно удалён"}), 200
    except Exception as e:
        logging.error(f"Ошибка при удалении черновика {draft_id}: {str(e)}")
        return jsonify({"error": str(e)}), 500