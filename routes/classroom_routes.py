from flask import Blueprint, jsonify, request
from create_db import engine, ExamSessionDraft, AdminStatusDraft, RoomExclusion, ClassroomSlot
from sqlalchemy.orm import sessionmaker
import logging
import traceback
import json
import math
from datetime import datetime, timedelta


from users_db import get_or_create_admin_status
from services.jwt_service import admin_required
from flask_jwt_extended import jwt_required, get_jwt

import services.scheduler_store as store

Session = sessionmaker(bind=engine)
session = Session()

classroom_bp = Blueprint('classroom_bp', __name__)

role_to_faculty = {
    "admin-sdt": "Школа цифровых технологий",
    "admin-sem": "Школа экономики и менеджмента",
    "admin-gum": "Гуманитарная школа",
    "admin-spigu": "Школа права и государственного управления"
}

@classroom_bp.route('/api/classroom/free-slots-old', methods=['GET'])
def get_free_classroom_slots_old():
    session = Session()
    try:
        classroom_number = request.args.get('classroom_number')
        query = session.query(ClassroomSlot).filter_by(is_booked=False)
        if classroom_number:
            query = query.filter_by(classroom_number=classroom_number)
        free_slots = query.all()
        return jsonify([slot.to_dict() for slot in free_slots]), 200
    except Exception as e:
        logging.error(f"Error getting free slots: {traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()

@classroom_bp.route('/api/free-slots', methods=['GET'])
def get_free_slots():
    session = Session()
    try:
        # 1. Получение параметров
        date_str = request.args.get('date')
        duration_minutes = request.args.get('duration', type=int)
        classroom_number = request.args.get('classroom_number')

        if not all([date_str, duration_minutes, classroom_number]):
            return jsonify({'error': 'Missing required parameters: date, duration, classroom_number'}), 400

        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()

        # 2. Определение временных рамок
        work_start_hour=8
        work_end_hour=19
        work_end_minute=30
        time_step=30
        
        day_start = datetime.combine(target_date, datetime.min.time()).replace(hour=work_start_hour)
        day_end = datetime.combine(target_date, datetime.min.time()).replace(hour=work_end_hour, minute=work_end_minute)
        
        total_duration_minutes = (day_end - day_start).total_seconds() / 60
        num_blocks_in_day = int(total_duration_minutes / time_step)

        # 3. Построение сетки доступности
        availability_grid = [False] * num_blocks_in_day

        booked_slots = session.query(ClassroomSlot).filter(
            ClassroomSlot.classroom_number == classroom_number,
            ClassroomSlot.start_time >= day_start,
            ClassroomSlot.end_time <= day_end,
            ClassroomSlot.is_booked == True
        ).all()

        for slot in booked_slots:
            start_block = int(((slot.start_time - day_start).total_seconds() / 60) / time_step)
            end_block = int(((slot.end_time - day_start).total_seconds() / 60) / time_step)
            for i in range(start_block, end_block):
                if i < num_blocks_in_day:
                    availability_grid[i] = True

        # 4. Поиск свободных непрерывных слотов
        slots_needed = math.ceil(duration_minutes / time_step)
        available_start_times = []

        for start_block in range(num_blocks_in_day - slots_needed + 1):
            if not any(availability_grid[start_block : start_block + slots_needed]):
                slot_time = day_start + timedelta(minutes=start_block * time_step)
                available_start_times.append(slot_time.strftime('%H:%M'))

        return jsonify(available_start_times), 200

    except ValueError:
        return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD.'}), 400
    except Exception as e:
        logging.error(f"Error getting free slots: {traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()

@classroom_bp.route('/api/slots/book-old', methods=['POST'])
# @admin_required("admin")  # Temporarily commented out
def book_classroom_slot_old():
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

@classroom_bp.route('/api/book-slot', methods=['POST'])
def book_slot():
    session = Session()
    try:
        data = request.json
        bookings = data if isinstance(data, list) else [data]
        results = []
        
        all_slots_to_update_for_commit = []

        for booking_data in bookings:
            date_str = booking_data.get('date')
            start_time_str = booking_data.get('start_time')
            duration_minutes = booking_data.get('duration')
            classroom_numbers_str = booking_data.get('classroom_number')
            subject = booking_data.get('subject')
            sections = booking_data.get('sections')

            # 1. Валидация входных данных
            if not all([date_str, start_time_str, duration_minutes, classroom_numbers_str, subject, sections]):
                results.append({'error': 'Missing required parameters', 'booking_data': booking_data})
                continue

            if not isinstance(sections, list):
                results.append({'error': '"sections" must be a list', 'booking_data': booking_data})
                continue

            try:
                target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                start_time = datetime.strptime(start_time_str, '%H:%M').time()
                booking_start_dt = datetime.combine(target_date, start_time)
                booking_end_dt = booking_start_dt + timedelta(minutes=duration_minutes)
            except ValueError as ve:
                results.append({'error': f'Invalid date or time format: {ve}', 'booking_data': booking_data})
                continue

            classroom_list = [room.strip() for room in str(classroom_numbers_str).split(',')]
            
            current_booking_slots = []
            booking_has_error = False

            for room_num in classroom_list:
                if booking_has_error: break

                # 2. Поиск всех слотов, которые нужно забронировать для данной аудитории
                slots_to_book = session.query(ClassroomSlot).filter(
                    ClassroomSlot.classroom_number == room_num,
                    ClassroomSlot.start_time >= booking_start_dt,
                    ClassroomSlot.start_time < booking_end_dt
                ).all()

                # 3. Проверка, что все слоты существуют и свободны
                if not slots_to_book:
                    results.append({'error': f'No slots found for classroom {room_num} in the specified time range', 'sections': sections})
                    booking_has_error = True
                    continue

                required_slots_count = math.ceil(duration_minutes / 30)
                if len(slots_to_book) != required_slots_count:
                    error_message = (
                        f'For classroom {room_num}, the requested duration does not align with available slot boundaries. '
                        f'Required slots: {required_slots_count}, Found slots: {len(slots_to_book)}.'
                    )
                    results.append({'error': error_message, 'sections': sections})
                    booking_has_error = True
                    continue

                for slot in slots_to_book:
                    if slot.is_booked:
                        results.append({'error': f'Slot in classroom {room_num} at {slot.start_time.strftime("%H:%M")} is already booked', 'sections': sections})
                        booking_has_error = True
                        break
                
                if not booking_has_error:
                    current_booking_slots.extend(slots_to_book)

            if not booking_has_error:
                all_slots_to_update_for_commit.extend(current_booking_slots)
                
                booking_info = json.dumps({"subject": subject, "sections": sections}, ensure_ascii=False)
                for slot in current_booking_slots:
                    slot.is_booked = True
                    slot.booked_groups_info = booking_info

                end_time_str = booking_end_dt.strftime("%H:%M")
                results.append({
                    'status': 'success',
                    'message': f'Slots from {start_time_str} to {end_time_str} in classrooms {classroom_numbers_str} successfully prepared for booking.'
                })
                logging.info(
                    f"Slots from {start_time_str} for {duration_minutes} mins in classrooms {classroom_numbers_str} prepared for booking for subject '{subject}'.")

        has_error = any('error' in res for res in results)
        if has_error:
            session.rollback()
            return jsonify({'results': results}), 400
        else:
            session.commit()
            # Update success messages after commit
            for res in results:
                if res.get('status') == 'success':
                    res['message'] = res['message'].replace('prepared for booking', 'successfully booked')
            return jsonify({'results': results}), 200

    except Exception as e:
        session.rollback()
        logging.error(f"Error booking slot: {traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()

@classroom_bp.route('/api/manage-rooms', methods=['POST'])
@admin_required("admin")
def manage_rooms():
    session = Session()
    try:
        data = request.json
        
        # Если на вход подается один объект, оборачиваем его в список для универсальной обработки
        if isinstance(data, dict):
            data = [data]

        results = []
        for item in data:
            action = item.get('action')
            room_number = item.get('room_number')
            date_str = item.get('date')
            start_time_str = item.get('start_time')
            end_time_str = item.get('end_time')
            reason = item.get('reason')

            if not all([action, room_number, date_str, start_time_str, end_time_str]):
                results.append({'error': 'Missing required parameters for an item', 'item': item})
                continue

            try:
                exclusion_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                start_time_obj = datetime.strptime(start_time_str, '%H:%M').time()
                end_time_obj = datetime.strptime(end_time_str, '%H:%M').time()

                start_datetime = datetime.combine(exclusion_date, start_time_obj)
                end_datetime = datetime.combine(exclusion_date, end_time_obj)
            except ValueError as e:
                results.append({'error': f'Invalid date or time format: {e}', 'item': item})
                continue

            if action == 'block':
                new_exclusion = RoomExclusion(
                    room_number=str(room_number),
                    exclusion_date=exclusion_date,
                    start_time=start_datetime,
                    end_time=end_datetime,
                    reason=reason
                )
                session.add(new_exclusion)
                results.append({'status': 'success', 'message': f'Room {room_number} scheduled for blocking.'})

            elif action == 'unblock':
                exclusion_to_delete = session.query(RoomExclusion).filter_by(
                    room_number=str(room_number),
                    exclusion_date=exclusion_date,
                    start_time=start_datetime,
                    end_time=end_datetime
                ).first()

                if exclusion_to_delete:
                    session.delete(exclusion_to_delete)
                    results.append({'status': 'success', 'message': f'Block on room {room_number} scheduled for removal.'})
                else:
                    results.append({'error': 'Exclusion record not found.', 'item': item})
            
            else:
                results.append({'error': 'Invalid action. Use "block" or "unblock".', 'item': item})
        
        # Проверяем, есть ли ошибки
        has_errors = any('error' in r for r in results)
        if has_errors:
            session.rollback()
            return jsonify({'results': results}), 400
        else:
            session.commit()
            return jsonify({'results': results}), 200

    except Exception as e:
        session.rollback()
        logging.error(f"Error in manage_rooms: {traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()

@classroom_bp.route('/available-rooms/<day>/<time_slot>', methods=['GET'])
def get_available_rooms(day, time_slot):
    try:
        # Проверяем, существует ли расписание
        if not hasattr(store.current_scheduler, 'room_availability'):
            return jsonify({
                'success': False,
                'error': 'Расписание не создано. Сначала создайте расписание.'
            }), 400

        logging.info(f"Logging time slot and day: {day}, {time_slot}")

        available_rooms = store.current_scheduler.find_available_rooms(day, time_slot)

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

@classroom_bp.route('/api/update_room_requirement', methods=['POST'])
@jwt_required()
def update_room_requirement():
    import services.scheduler_store as store
    claims = get_jwt()
    user_role = claims.get('role')
    session = Session(bind=engine)
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
            # Align with the request field 'two_rooms_needed'
            two_rooms_needed = exam.get('two_rooms_needed', False)
            logging.info(f"Обновлено требование к аудиториям для {section_id}: two_rooms_needed={two_rooms_needed}")
            store.current_scheduler.update_room_requirement(section_id, two_rooms_needed)

        # Update the draft with the latest exam_groups
        active_draft.exams_data = store.current_scheduler.exam_groups.to_json(orient='records')
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

@classroom_bp.route('/api/update_classroom_type', methods=['POST'])
@jwt_required()
def update_classroom_type():
    import services.scheduler_store as store
    claims = get_jwt()
    user_role = claims.get('role')
    session = Session(bind=engine)
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
            classroom_type = exam.get('classroom_type', 'regular')
            logging.info(f"Обновлен тип аудитории для {section_id}: classroom_type={classroom_type}")
            store.current_scheduler.update_classroom_type(section_id, classroom_type)

        # Update the draft with the latest exam_groups
        active_draft.exams_data = store.current_scheduler.exam_groups.to_json(orient='records')
        session.commit()

        return jsonify({
            'status': 'success',
            'message': 'Типы аудиторий успешно обновлены'
        }), 200

    except Exception as e:
        session.rollback()
        logging.error(f"Ошибка при обновлении типов аудиторий: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Ошибка при обновлении типов аудиторий: {str(e)}'
        }), 500
    finally:
        session.close()