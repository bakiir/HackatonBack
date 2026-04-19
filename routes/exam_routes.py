from flask import Blueprint, jsonify, request, send_file
from create_db import engine, ExamSession, ExamSessionDraft, AdminStatusDraft, RoomExclusion, ClassroomSlot, update_classroom_slots
from sqlalchemy.orm import sessionmaker
import logging
import traceback
import pandas as pd
import bcrypt

from users_db import User, get_resolved_conflicts_by_session_id
from services.jwt_service import admin_required
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt

import services.scheduler_store as store
from services.scheduler_core.utils import handle_nan_values, role_to_faculty
from repositories.exam_repository import ExamRepository, ExamDraftRepository
from services.exam_service import ExamService

Session = sessionmaker(bind=engine)
db_session = Session()

# Initialize Repository and Service
exam_repo = ExamRepository(db_session)
draft_repo = ExamDraftRepository(db_session)
exam_service = ExamService(exam_repo, draft_repo)

exam_bp = Blueprint('exam_bp', __name__)

@exam_bp.route('/api/manage', methods=['POST'])
@jwt_required()
def handle_management():
    logging.info(f"Запрос API /manage с данными: {request.json}")
    claims = get_jwt()
    user_role = claims.get('role')
    current_user = get_jwt_identity()
    
    if store.current_scheduler is None:
        return jsonify({"error": "Планировщик не инициализирован"}), 500

    try:
        data = request.json
        action = data.get('action')

        if action == 'get_subjects':
            requested_faculty = data.get('faculty')
            faculty = requested_faculty
            if user_role in role_to_faculty:
                faculty = role_to_faculty[user_role]
            
            subjects = exam_service.get_subjects_by_faculty(store.current_scheduler, faculty, user_role)
            return jsonify({
                "status": "success",
                "subjects": subjects,
                "user": current_user,
                "role": user_role,
                "used_faculty": faculty
            })

        elif action == 'delete_subject':
            subject = data['subject']
            exam_service.delete_subject(store.current_scheduler, subject, user_role)
            return jsonify({
                'status': 'success',
                'message': f'Предмет {subject} удален',
                'remaining_subjects': store.current_scheduler.get_unique_subjects()
            })

        elif action == 'delete_section':
            section = data['section']
            exam_service.delete_section(store.current_scheduler, section, user_role)
            return jsonify({
                'status': 'success',
                'message': f'Секция {section} удалена',
                'remaining_subjects': store.current_scheduler.get_unique_subjects()
            })

        elif action == 'generate':
            if user_role != "admin":
                return jsonify({"error": "Доступ запрещён! Только admin может генерировать расписание"}), 403

            sanitized_schedule, analysis_report = exam_service.generate_schedule(store.current_scheduler)

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
        logging.error(f"Management error: {traceback.format_exc()}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@exam_bp.route('/api/get-subjects-by-faculty/', defaults={'faculty': None})
@exam_bp.route('/api/get-subjects-by-faculty/<faculty>', methods=['GET'])
@jwt_required()
def get_subjects_by_faculty_route(faculty):
    if store.current_scheduler is None:
        return jsonify({"error": "Планировщик не инициализирован"}), 500

    try:
        claims = get_jwt()
        user_role = claims.get('role')
        if user_role in role_to_faculty:
            faculty = role_to_faculty[user_role]

        subjects = exam_service.get_subjects_by_faculty(store.current_scheduler, faculty, user_role)
        return jsonify({
            "subjects": subjects,
            "role": user_role,
            "used_faculty": faculty
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@exam_bp.route('/section/<section_id>')
def get_section_info(section_id):
    section_info = store.current_scheduler.get_section_info(section_id)
    return jsonify(handle_nan_values(section_info))

@exam_bp.route('/section/<section_id>/export')
def export_section_info(section_id):
    output_file = f"section_{section_id}_info.xlsx"
    section_info = store.current_scheduler.get_section_info(section_id)
    store.current_scheduler.export_section_info_to_excel(section_info, output_file)
    return send_file(output_file, as_attachment=True)

@exam_bp.route('/subjects', methods=['GET'])
def get_subjects():
    try:
        subjects = store.current_scheduler.get_unique_subjects()
        return jsonify({'success': True, 'subjects': subjects})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@exam_bp.route('/subjects/<subject>/groups', methods=['GET'])
def get_subject_groups(subject):
    try:
        grouped_data = exam_service.get_subject_groups(store.current_scheduler, subject)
        if not grouped_data:
            return jsonify({'success': False, 'error': f'Группы для предмета {subject} не найдены'}), 404

        return jsonify({'success': True, 'subject': subject, 'groups': grouped_data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@exam_bp.route('/api/update_exam_durations', methods=['POST'])
@jwt_required()
def handle_update_durations():
    claims = get_jwt()
    user_role = claims.get('role')
    try:
        data = request.json
        if not data or 'exams' not in data:
            return jsonify({'status': 'error', 'message': 'Не предоставлены данные'}), 400

        exam_service.update_exam_durations(store.current_scheduler, data['exams'], user_role)
        return jsonify({'status': 'success', 'message': 'Длительности успешно обновлены'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@exam_bp.route('/api/exams/batch-update', methods=['POST'])
@jwt_required()
def batch_update_exams():
    claims = get_jwt()
    user_role = claims.get('role')
    try:
        data = request.json
        if not data or 'exams' not in data:
            return jsonify({'status': 'error', 'message': 'Не предоставлены данные'}), 400

        exam_service.batch_update_exams(store.current_scheduler, data['exams'], user_role)
        return jsonify({'status': 'success', 'message': 'Экзамены успешно обновлены'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@exam_bp.route('/schedule/student/<student_id>/export')
def export_student_schedule(student_id):
    output_file = f"student_{student_id}_schedule.xlsx"
    store.current_scheduler.export_student_schedule_to_excel(student_id, output_file)
    return send_file(output_file, as_attachment=True)

@exam_bp.route('/api/upload-students', methods=['POST'])
@admin_required("admin")
def upload_students():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    file = request.files['file']
    try:
        df = pd.read_excel(file)
        results = {'created': [], 'errors': []}
        session = Session()
        for idx, row in df.iterrows():
            try:
                if session.query(User).filter_by(email=str(row['fake_id'])).first():
                    continue
                new_user = User(
                    email=str(row['fake_id']),
                    password=bcrypt.hashpw(row['fake_name'].encode('utf-8'), bcrypt.gensalt()).decode('utf-8'),
                    full_name=row['fake_name'],
                    role='student'
                )
                session.add(new_user)
                session.commit()
                results['created'].append(row['fake_id'])
            except Exception as e:
                session.rollback()
                results['errors'].append(str(e))
        return jsonify({'message': 'Success', 'results': results}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()

@exam_bp.route('/schedule/edit/<string:section>', methods=['POST'])
def edit_schedule(section):
    try:
        data = request.json
        store.current_scheduler.edit_schedule_entry(
            section=section,
            room=data.get('room'),
            date=data.get('date'),
            time_slot=data.get('time_slot'),
            proctor=data.get('proctor')
        )
        return jsonify({'success': True, 'updated_schedule': store.current_scheduler.schedule_df.to_dict('records')})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500