import tempfile
from datetime import datetime
import traceback
from flask import Flask, jsonify, send_file
import logging
from flask_cors import CORS
from xx import ExamScheduler  # импортируем ваш класс из файла xx.py
import numpy as np
import pandas as pd
import os


from flask import request
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


app = Flask(__name__)
CORS(app)

# Глобальная переменная для хранения планировщика
current_scheduler = None


@app.route('/api/generate-schedule', methods=['POST'])
def generate_schedule():
    global current_scheduler

    try:
        # Получаем файлы из запроса
        exams_file = request.files['exams']
        rooms_file = request.files['rooms']
        faculties_file = request.files['faculties']

        # Получаем параметры
        start_date = request.form['start_date']
        num_days = int(request.form.get('num_days', 14))

        # Сохраняем файлы во временную директорию
        with tempfile.TemporaryDirectory() as temp_dir:
            exams_path = os.path.join(temp_dir, exams_file.filename)
            rooms_path = os.path.join(temp_dir, rooms_file.filename)
            faculties_path = os.path.join(temp_dir, faculties_file.filename)

            exams_file.save(exams_path)
            rooms_file.save(rooms_path)
            faculties_file.save(faculties_path)

            # Инициализируем и запускаем планировщик
            current_scheduler = ExamScheduler(
                exams_file=exams_path,
                rooms_file=rooms_path,
                faculties_file=faculties_path,
                start_date=start_date,
                num_days=num_days
            )
            current_scheduler.run_scheduling_process()

        return jsonify({
            'status': 'success',
            'message': 'Расписание успешно сгенерировано',
            'stats': {
                'total_exams': len(current_scheduler.exam_groups),
                'scheduled': len(current_scheduler.schedule_df),
                'failed': len(current_scheduler.exam_groups) - len(current_scheduler.schedule_df)
            }
        }), 200

    except Exception as e:
        logging.error(f"Ошибка генерации: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Ошибка генерации расписания: {str(e)}'
        }), 500


# Модифицированные эндпоинты
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

@app.route('/schedule/student/<student_id>')
def get_student_schedule(student_id):
    student_schedule = scheduler.get_student_sections(student_id)
    return jsonify(student_schedule.to_dict('records'))

@app.route('/schedule/export')
def export_schedule():
    output_file = "general_schedule.xlsx"
    scheduler.export_schedule(output_file)
    return send_file(output_file, as_attachment=True)

@app.route('/schedule/student/<student_id>/export')
def export_student_schedule(student_id):
    output_file = f"student_{student_id}_schedule.xlsx"
    scheduler.export_student_schedule_to_excel(student_id, output_file)
    return send_file(output_file, as_attachment=True)

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
    section_info = scheduler.get_section_info(section_id)
    # Преобразуем numpy типы в стандартные типы Python
    section_info = convert_numpy_types(section_info)
    return jsonify(section_info)  # Возвращаем словарь как JSON

@app.route('/section/<section_id>/export')
def export_section_info(section_id):
    output_file = f"section_{section_id}_info.xlsx"
    section_info = scheduler.get_section_info(section_id)  # Получаем информацию о секции
    scheduler.export_section_info_to_excel(section_info, output_file)  # Экспортируем в Excel
    return send_file(output_file, as_attachment=True)  # Отправляем файл пользователю

@app.route('/subjects', methods=['GET'])
def get_subjects():
    try:
        subjects = scheduler.get_unique_subjects()
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
        subject_groups = scheduler.exam_groups[scheduler.exam_groups['Subject'] == subject]

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
def delete_subject(subject):
    try:
        subject_groups = scheduler.exam_groups[scheduler.exam_groups['Subject'] == subject]

        if subject_groups.empty:
            return jsonify({
                'success': False,
                'error': f'Предмет {subject} не найден'
            }), 404

        sections_to_delete = subject_groups['Section'].tolist()
        scheduler._delete_sections(sections_to_delete)

        scheduler.create_schedule()

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

@app.route('/subjects/<subject>/sections/<section>', methods=['DELETE'])
def delete_section(subject, section):
    try:
        subject_groups = scheduler.exam_groups[scheduler.exam_groups['Subject'] == subject]
        if section not in subject_groups['Section'].values:
            return jsonify({
                'success': False,
                'error': f'Секция {section} не найдена для предмета {subject}'
            }), 404

        scheduler._delete_sections([section])

        scheduler.create_schedule()

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

@app.route('/available-rooms/<day>/<time_slot>', methods=['GET'])
def get_available_rooms(day, time_slot):

    try:
        # Проверяем, существует ли расписание
        if not hasattr(scheduler, 'room_availability'):
            return jsonify({
                'success': False,
                'error': 'Расписание не создано. Сначала создайте расписание.'
            }), 400

        logging.info(f"Logging time slot and day: {day}, {time_slot}")

        available_rooms = scheduler.find_available_rooms(day, time_slot)

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

        scheduler.edit_schedule_entry(
            section=section,
            room=data.get('room'),
            date=data.get('date'),
            time_slot=data.get('time_slot'),
            proctor=data.get('proctor')
        )

        return jsonify({
            'success': True,
            'message': f'Запись для секции {section} успешно обновлена',
            'updated_schedule': scheduler.schedule_df.to_dict('records')
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

        all_proctors = scheduler.get_all_proctors()

        if scheduler.schedule_df is None or not {'Date', 'Time_Slot', 'Proctor'}.issubset(
                scheduler.schedule_df.columns):
            return jsonify({'success': False, 'error': 'Данные расписания отсутствуют или некорректны'}), 500

        scheduler.schedule_df['Date'] = scheduler.schedule_df['Date'].astype(str)
        scheduler.schedule_df['Time_Slot'] = scheduler.schedule_df['Time_Slot'].astype(str)

        busy_proctors = set(
            scheduler.schedule_df.loc[
                (scheduler.schedule_df['Date'] == date) &
                (scheduler.schedule_df['Time_Slot'] == time_slot),
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


if __name__ == '__main__':
    app.run(debug=True, port=5000)