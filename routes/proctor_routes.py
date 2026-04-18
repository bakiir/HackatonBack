from flask import Blueprint, jsonify, request
from create_db import ExamSession, engine, ExamSessionDraft, AdminStatusDraft
from sqlalchemy.orm import sessionmaker
import logging
import traceback
from datetime import datetime
import pandas as pd
import tempfile
import os

from users_db import get_or_create_admin_status
from services.jwt_service import admin_required
from flask_jwt_extended import jwt_required, get_jwt

import services.scheduler_store as store

Session = sessionmaker(bind=engine)
session = Session()

proctor_bp = Blueprint('proctor_bp', __name__)

from services.scheduler_core.utils import role_to_faculty

@proctor_bp.route('/api/proctors/assign', methods=['POST'])
@admin_required("admin")
def assign_proctors():
    import services.scheduler_store as store

    if store.current_scheduler is None:
        return jsonify({'error': 'Планировщик не инициализирован'}), 400

    session = Session()
    try:
        proctors_file = request.files['proctors']

        with tempfile.TemporaryDirectory() as temp_dir:
            proctors_path = os.path.join(temp_dir, 'proctors.xlsx')
            proctors_file.save(proctors_path)

            store.current_scheduler.assign_proctors(proctors_path)

        # Update the session
        active_session = session.query(ExamSession).filter_by(is_active=True).first()
        if active_session:
            active_session.schedule_data = store.current_scheduler.schedule_df.to_json(orient='records')
            session.commit()
            logging.info("Сессия успешно обновлена с данными о прокторах.")

        return jsonify({'message': 'Прокторы успешно назначены'}), 200

    except Exception as e:
        session.rollback()
        logging.error(f"Ошибка назначения прокторов: {str(e)}")
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()

@proctor_bp.route('/api/proctors/save_excel', methods=['GET'])
def save_proctor_list_excel():
    if store.current_scheduler is None:
        return jsonify({'error': 'Планировщик не инициализирован'}), 400

    try:
        # Логируем содержимое section_proctors для отладки
        logging.info(f"section_proctors: {store.current_scheduler.section_proctors}")

        # Генерация DataFrame с назначениями прокторов
        section_ids = list(store.current_scheduler.section_proctors.keys())
        proctors_data = list(store.current_scheduler.section_proctors.values())

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

@proctor_bp.route('/available-proctors/<string:date>/<path:time_slot>', methods=['GET'])
def get_available_proctors(date, time_slot):
    try:
        try:
            datetime.strptime(date, '%d-%m-%Y')
        except ValueError:
            return jsonify({'success': False, 'error': 'Некорректный формат даты. Ожидается DD-MM-YYYY'}), 400

        logging.info(f"Запрос доступных прокторов: дата={date}, слот={time_slot}")

        all_proctors = store.current_scheduler.get_all_proctors()

        if store.current_scheduler.schedule_df is None or not {'Date', 'Time_Slot', 'Proctor'}.issubset(
                store.current_scheduler.schedule_df.columns):
            return jsonify({'success': False, 'error': 'Данные расписания отсутствуют или некорректны'}), 500

        store.current_scheduler.schedule_df['Date'] = store.current_scheduler.schedule_df['Date'].astype(str)
        store.current_scheduler.schedule_df['Time_Slot'] = store.current_scheduler.schedule_df['Time_Slot'].astype(str)

        busy_proctors = set(
            store.current_scheduler.schedule_df.loc[
                (store.current_scheduler.schedule_df['Date'] == date) &
                (store.current_scheduler.schedule_df['Time_Slot'] == time_slot),
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

@proctor_bp.route('/api/update_proctor_status', methods=['POST'])
@jwt_required()
def update_proctor_status():
    import services.scheduler_store as store
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
            proctor_needed = exam.get('has_proctor', exam.get('proctor_needed', False))
            if section_id not in store.current_scheduler.exam_groups['Section'].values:
                logging.warning(f"Секция {section_id} не найдена в exam_groups")
                continue
            store.current_scheduler.exam_groups.loc[
                store.current_scheduler.exam_groups['Section'] == section_id, 'proctor_needed'
            ] = proctor_needed
            logging.info(f"Обновлён proctor_needed={proctor_needed} для секции {section_id}")

        # Синхронизируем exam_groups с exams_data в базе
        active_draft.exams_data = store.current_scheduler.exam_groups.to_json(orient='records')

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