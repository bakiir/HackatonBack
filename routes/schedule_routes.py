from io import BytesIO

from flask import Blueprint, jsonify, send_file
from create_db import ExamSession, engine
from sqlalchemy.orm import sessionmaker
import logging
import traceback
import pandas as pd
import numpy as np

from users_db import get_resolved_conflicts_by_session_id
from services.jwt_service import admin_required

from services.exam_scheduler import ExamScheduler
import services.scheduler_store as store
from services.check_student_conflicts import get_student_conflicts

Session = sessionmaker(bind=engine)
session = Session()

schedule_bp = Blueprint('schedule_bp', __name__)

from services.scheduler_core.utils import handle_nan_values, group_consecutive_slots, role_to_faculty
from datetime import datetime


@schedule_bp.route('/schedule')
def get_schedule():
    if not store.current_scheduler:
        return jsonify({'error': 'Расписание не сгенерировано'}), 400

    schedule_data = store.current_scheduler.schedule_df.replace({np.nan: None}).to_dict('records')
    grouped_schedule = group_consecutive_slots(schedule_data)
    return jsonify(grouped_schedule)

@schedule_bp.route('/schedule/stats')
def get_schedule_stats():
    if not store.current_scheduler:
        return jsonify({'error': 'Расписание не сгенерировано'}), 400

    total = len(store.current_scheduler.exam_groups)
    scheduled = len(store.current_scheduler.schedule_df)

    return jsonify({
        'total_exams': total,
        'scheduled': scheduled,
        'failed': total - scheduled
    })

@schedule_bp.route('/api/schedule/status', methods=['GET'])
def get_scheduling_status():
    """
    Возвращает статистику по последнему запуску генерации расписания,
    включая количество запланированных, общее количество и список незапланированных экзаменов.
    """
    if not store.current_scheduler:
        return jsonify({'error': 'Планировщик не инициализирован или расписание не создано'}), 400

    # Общее количество экзаменов, которые должны были быть запланированы
    try:
        total_to_schedule = len(store.current_scheduler.exam_groups[store.current_scheduler.exam_groups['has_exam'] == True])
    except (AttributeError, KeyError):
        total_to_schedule = 0

    # Количество успешно запланированных экзаменов
    try:
        successfully_scheduled = len(store.current_scheduler.schedule_df)
    except (AttributeError, TypeError):
        successfully_scheduled = 0

    # Список незапланированных секций
    failed_sections_list = []
    if hasattr(store.current_scheduler, 'failed_sections') and store.current_scheduler.failed_sections:
        for failed in store.current_scheduler.failed_sections:
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

@schedule_bp.route('/schedule/export')
def export_schedule():
    output_file = "general_schedule.xlsx"
    store.current_scheduler.export_schedule(output_file)
    return send_file(output_file, as_attachment=True)

@schedule_bp.route('/api/export/all-student-schedules')
@admin_required("admin")
def export_all_student_schedules():
    import services.scheduler_store as store
    if not store.current_scheduler:
        return jsonify({'error': 'Планировщик не инициализирован'}), 400

    try:
        # 1. Get the data as a DataFrame
        student_schedules_df = store.current_scheduler.get_all_student_schedules_data()

        if student_schedules_df.empty:
            return jsonify({'message': 'Нет данных для экспорта.'}), 404

        # 2. Create an in-memory Excel file
        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            student_schedules_df.to_excel(writer, index=False, sheet_name='Расписание студентов')
            # Auto-adjust columns width
            for column in student_schedules_df:
                column_length = max(student_schedules_df[column].astype(str).map(len).max(), len(column))
                col_idx = student_schedules_df.columns.get_loc(column)
                writer.sheets['Расписание студентов'].set_column(col_idx, col_idx, column_length + 1)

        output.seek(0)

        # 3. Send the file
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='student_schedules.xlsx'
        )

    except Exception as e:
        logging.error(f"Ошибка при экспорте расписания студентов: {traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500

@schedule_bp.route('/api/report/conflicts', methods=['GET'])
def get_conflict_report():
    import services.scheduler_store as store
    if not store.current_scheduler:
        return jsonify({'error': 'Планировщик не инициализирован'}), 400

    # Scheduled stats
    total = len(store.current_scheduler.exam_groups[store.current_scheduler.exam_groups['has_exam'] == True])
    scheduled_count = len(store.current_scheduler.schedule_df)
    scheduled_str = f"{scheduled_count}/{total}"

    # Non-scheduled subjects
    non_scheduled_subjects = []
    if hasattr(store.current_scheduler, 'failed_sections'):
        for failed in store.current_scheduler.failed_sections:
            # Extract relevant info from the 'group' series
            group_info = failed.get('group', pd.Series())
            non_scheduled_subjects.append({
                'section': failed.get('section'),
                'subject': group_info.get('Subject'),
                'instructor': group_info.get('Instructor'),
                'num_students': failed.get('num_students'),
            })

    # Conflicts
    conflicts = get_student_conflicts(store.current_scheduler)

    # Группируем последовательные слоты для каждого конфликта
    for conflict in conflicts:
        if 'exams' in conflict and isinstance(conflict['exams'], list):
            conflict['exams'] = group_consecutive_slots(conflict['exams'])

    report = {
        "scheduled": scheduled_str,
        "non_scheduled_subjects": non_scheduled_subjects,
        "conflicts": conflicts
    }

    return jsonify(handle_nan_values(report))

@schedule_bp.route('/api/resolve-conflicts-by-group', methods=['POST'])
@admin_required("admin")
def resolve_conflicts_by_group_api():
    db_session = Session()
    try:
        active_session = db_session.query(ExamSession).filter_by(is_active=True).first()
        if not active_session:
            return jsonify({"error": "Активная сессия не найдена"}), 404

        # Инициализируем планировщик с данными из активной сессии
        scheduler = ExamScheduler(session_data=active_session)
        
        # Импортируем и вызываем новую функцию
        from services.conflict_resolver import resolve_conflicts_by_moving_groups
        changes = resolve_conflicts_by_moving_groups(scheduler, active_session.id)

        if changes:
            # Если были внесены изменения, обновляем данные сессии в БД
            active_session.schedule_data = scheduler.schedule_df.to_json(orient='records')
            
            # Перераспределяем места после изменения дат
            scheduler.assign_seats()
            active_session.seat_assignments = scheduler.seat_assignments

            db_session.commit()
            logging.info(f"Успешно перемещено {len(changes)} групп. Изменения сохранены в сессии {active_session.id}.")

            # Обновляем глобальный планировщик, чтобы изменения были видны сразу
            import services.scheduler_store as store
            store.current_scheduler = scheduler

        return jsonify({
            "message": f"Обработка конфликтов завершена. Перемещено групп: {len(changes)}.",
            "changes": changes
        }), 200

    except Exception as e:
        db_session.rollback()
        logging.error(f"Ошибка при разрешении конфликтов: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500
    finally:
        db_session.close()


@schedule_bp.route('/schedule/student/<string:student_id>')
def get_student_schedule(student_id):
    try:
        if not store.current_scheduler:
            return jsonify({"error": "Планировщик не инициализирован"}), 500

        # Диагностика: логируем первые 5 ключей из seat_assignments
        if hasattr(store.current_scheduler, 'seat_assignments'):
            sample_keys = list(store.current_scheduler.seat_assignments.keys())[:5]
            logging.info(f"Sample seat assignment keys: {sample_keys}")
        else:
            logging.error("No seat_assignments in scheduler!")

        student_schedule = store.current_scheduler.get_student_sections(student_id)

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
                seat_info = (store.current_scheduler.seat_assignments.get(exact_key) or
                             store.current_scheduler.seat_assignments.get(clean_key) or
                             next((v for k, v in store.current_scheduler.seat_assignments.items()
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


@schedule_bp.route('/api/debug/all_sections', methods=['GET'])
def get_all_sections_debug():
    """
    Диагностический эндпоинт для получения списка всех секций,
    известных планировщику в данный момент.
    """
    
    if not store.current_scheduler:
        return jsonify({'error': 'Планировщик не инициализирован'}), 400
    
    try:
        # Убедимся, что exam_groups на месте
        if not hasattr(store.current_scheduler, 'exam_groups') or store.current_scheduler.exam_groups.empty:
             store.current_scheduler._prepare_data() # Попытка переподготовить данные, если они пусты

        all_sections = store.current_scheduler.get_all_section_names()
        return jsonify({
            'total_sections': len(all_sections),
            'sections': all_sections
        })
    except Exception as e:
        logging.error(f"Ошибка в /api/debug/all_sections: {str(e)}")
        return jsonify({'error': str(e)}), 500

@schedule_bp.route('/api/resolved_conflicts', methods=['GET'])
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