import tempfile
import math
import threading
from datetime import datetime, timedelta
import traceback
from io import BytesIO
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
from services.conflict_resolver import resolve_conflicts_by_moving_student, resolve_it_lab_conflicts
import numpy as np
import pandas as pd
import os
from flask import request
from create_db import ExamSession, engine, ExamSessionDraft, AdminStatusDraft, RoomExclusion
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv

load_dotenv() # Загружаем переменные из .env

import json # Added this import

allowed_roles = {"admin-sdt", "admin-gum", "admin-spigu", "admin-sem", "admin"}
role_to_faculty = {
    "admin-sdt": "Школа цифровых технологий",
    "admin-sem": "Школа экономики и менеджмента",
    "admin-gum": "Гуманитарная школа",
    "admin-spigu": "Школа права и государственного управления"
}




# это второй варик если ммикросервисный сделаем, сервис уже готов:)

app = Flask(__name__)

app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY", "fallback-secret-key")
app.config["JWT_TOKEN_LOCATION"] = ["headers"]     # обязательно!
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(minutes=30)

jwt = JWTManager(app)

CORS(app)



from create_db import ExamSession, engine, ExamSessionDraft, AdminStatusDraft, ClassroomSlot, update_classroom_slots


# DEPRECATED: This endpoint is deprecated and will be removed in a future version.
# Use /api/free-slots instead.















Session = sessionmaker(bind=engine)
session = Session()








# Получить список сессий




# Получить детали конкретной сессии


























































def group_consecutive_slots(slots):
    if not slots:
        return []

    # Define the key for grouping exams that are the same event
    def get_exam_key(slot):
        return (
            str(slot.get('Date')),
            slot.get('Subject'),
            slot.get('Instructor'),
            slot.get('Room'),
            slot.get('EduProgram'),
            slot.get('Section'),
            # Do not group by duration, as it's the same for all small slots
            # slot.get('Duration'), 
            slot.get('Students_Count'),
            slot.get('pinned'),
            slot.get('two_rooms_needed')
        )

    # Sort slots by the grouping key and then by time
    try:
        slots.sort(key=lambda s: (get_exam_key(s), datetime.strptime(s.get('Time_Slot', '00:00-00:00').split('-')[0], '%H:%M')))
    except (ValueError, IndexError):
        # If time format is unexpected, fall back to string sort for time
        slots.sort(key=lambda s: (get_exam_key(s), s.get('Time_Slot', '')))


    merged_slots = []
    i = 0
    while i < len(slots):
        # Start of a potential group
        group = [slots[i]]
        j = i + 1
        while j < len(slots):
            # Check if the next slot is part of the same exam event
            if get_exam_key(slots[j]) == get_exam_key(slots[i]):
                try:
                    # Check if the time slots are consecutive
                    prev_end_time_str = group[-1]['Time_Slot'].split('-')[1]
                    curr_start_time_str = slots[j]['Time_Slot'].split('-')[0]
                    
                    prev_end_time = datetime.strptime(prev_end_time_str, '%H:%M')
                    curr_start_time = datetime.strptime(curr_start_time_str, '%H:%M')

                    # If they are consecutive
                    if prev_end_time == curr_start_time:
                        group.append(slots[j])
                        j += 1
                    else:
                        # Time is not consecutive, so break the group
                        break
                except (ValueError, IndexError):
                    # Time format is wrong, break the group
                    break
            else:
                # Different exam, break the group
                break
        
        # If the group has more than one slot, merge them
        if len(group) > 1:
            first_slot = group[0]
            last_slot = group[-1]
            
            start_time = first_slot['Time_Slot'].split('-')[0]
            end_time = last_slot['Time_Slot'].split('-')[1]
            
            merged_slot = first_slot.copy()
            merged_slot['Time_Slot'] = f"{start_time}-{end_time}"
            
            # Per user request, use the last slot's Base_Time_Slot and seat_info
            merged_slot['Base_Time_Slot'] = last_slot['Base_Time_Slot']
            merged_slot['seat_info'] = last_slot.get('seat_info') # Use .get for safety
            
            merged_slots.append(merged_slot)
        else:
            # Single slot, just add it
            merged_slots.append(group[0])
            
        # Move index to the next un-processed slot
        i = j

    return merged_slots


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
        
        grouped_result = group_consecutive_slots(result)
        return jsonify(grouped_result)

    except Exception as e:
        logging.error(f"Ошибка при получении расписания: {traceback.format_exc()}")
        return jsonify({
            "error": "Внутренняя ошибка сервера",
            "details": str(e)
        }), 500




@app.route('/api/update_exam_durations', methods=['POST'])
@jwt_required()
def handle_update_durations():
    
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

            if exam['duration'] not in [60, 90, 120, 150, 180]:
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




# Получить список всех черновиков

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

@app.route('/api/debug/all_sections', methods=['GET'])
def get_all_sections_debug():
    """
    Диагностический эндпоинт для получения списка всех секций,
    известных планировщику в данный момент.
    """
    
    if not current_scheduler:
        return jsonify({'error': 'Планировщик не инициализирован'}), 400
    
    try:
        # Убедимся, что exam_groups на месте
        if not hasattr(current_scheduler, 'exam_groups') or current_scheduler.exam_groups.empty:
             current_scheduler._prepare_data() # Попытка переподготовить данные, если они пусты

        all_sections = current_scheduler.get_all_section_names()
        return jsonify({
            'total_sections': len(all_sections),
            'sections': all_sections
        })
    except Exception as e:
        logging.error(f"Ошибка в /api/debug/all_sections: {str(e)}")
        return jsonify({'error': str(e)}), 500




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

from routes.auth_routes import auth_bp
from routes.classroom_routes import classroom_bp
from routes.proctor_routes import proctor_bp
from routes.session_routes import session_bp
from routes.schedule_routes import schedule_bp
from routes.exam_routes import exam_bp

app.register_blueprint(auth_bp)
app.register_blueprint(classroom_bp)
app.register_blueprint(proctor_bp)
app.register_blueprint(session_bp)
app.register_blueprint(schedule_bp)
app.register_blueprint(exam_bp)

if __name__ == '__main__':
    app.run(debug=True)
