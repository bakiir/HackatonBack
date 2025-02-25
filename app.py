from flask import Flask, jsonify, send_file
import logging
from flask_cors import CORS
from xx import ExamScheduler  # импортируем ваш класс из файла xx.py
import numpy as np
from flask import request


app = Flask(__name__)
CORS(app)  # Разрешаем CORS для работы с фронтендом

# Инициализация планировщика при запуске сервера
scheduler = ExamScheduler(
    exams_file=r"C:\Users\User\Downloads\FakedNarxozData (2).xlsx",
    rooms_file=r"C:\Users\User\Downloads\auditoriums.xlsx",
    faculties_file=r"C:\Users\User\Documents\Faculties.xlsx",
    start_date='2024-01-15',
    num_days=14
)

# Создание расписания при запуске
scheduler.run_scheduling_process()

def handle_nan_values(obj):
    """
    Обрабатывает NaN значения в объекте, заменяя их на None для JSON-сериализации
    """
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

@app.route('/schedule')
def get_schedule():
    """Получение общего расписания"""
    schedule_data = scheduler.schedule_df.replace({np.nan: None}).to_dict('records')
    return jsonify(schedule_data)

@app.route('/schedule/stats')
def get_schedule_stats(successful_exa1ms=None):
    """Получение статистики по расписанию"""
    try:
        total_exams = len(scheduler.exam_groups)  # Общее количество экзаменов
        successful_exams = len(scheduler.schedule_df)  # Успешно запланированные экзамены
        failed_exams = total_exams - successful_exa1ms  # Неудачные попытки

        return jsonify({
            'success': True,
            'total_exams': total_exams,
            'successful_exams': successful_exams,
            'failed_exams': failed_exams
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/proctors', methods=['GET'])
def get_proctors():
    """Получение полного списка всех доступных прокторов"""
    try:
        unique_proctors = scheduler.get_all_proctors()

        return jsonify({
            'success': True,
            'count': len(unique_proctors),
            'proctors': unique_proctors
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/schedule/edit/<string:section>', methods=['POST'])
def edit_schedule(section):
    """Редактирование записи в расписании по Section."""
    logging.info(f"Получен запрос на редактирование секции: {section}")

    try:
        # Получаем данные из JSON
        data = request.json
        if not data:
            logging.warning("Нет данных для обновления.")
            return jsonify({
                'success': False,
                'error': 'No data provided'
            }), 400

        # Проверяем, что хотя бы одно поле передано
        if not any([data.get('room'), data.get('date'), data.get('time_slot'), data.get('proctor')]):
            logging.warning("Все поля для обновления пусты.")
            return jsonify({
                'success': False,
                'error': 'At least one field (room, date, time_slot, proctor) must be provided'
            }), 400

        # Редактируем запись в DataFrame
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

@app.route('/schedule/student/<student_id>')
def get_student_schedule(student_id):
    """Получение расписания конкретного студента"""
    student_schedule = scheduler.get_student_sections(student_id)
    return jsonify(student_schedule.to_dict('records'))

@app.route('/schedule/export')
def export_schedule():
    """Экспорт общего расписания"""
    output_file = "general_schedule.xlsx"
    scheduler.export_schedule(output_file)
    return send_file(output_file, as_attachment=True)

@app.route('/schedule/student/<student_id>/export')
def export_student_schedule(student_id):
    """Экспорт расписания студента"""
    output_file = f"student_{student_id}_schedule.xlsx"
    scheduler.export_student_schedule_to_excel(student_id, output_file)
    return send_file(output_file, as_attachment=True)

def convert_numpy_types(obj):
    """
    Рекурсивно преобразует numpy типы в стандартные типы Python.
    """
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
    """Получение списка всех уникальных предметов"""
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
    """Получение всех групп для конкретного предмета"""
    try:
        # Получаем группы по предмету
        subject_groups = scheduler.exam_groups[scheduler.exam_groups['Subject'] == subject]

        if subject_groups.empty:
            return jsonify({
                'success': False,
                'error': f'Группы для предмета {subject} не найдены'
            }), 404

        # Группируем данные по образовательным программам
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
    """Удаление всех групп конкретного предмета"""
    try:
        # Находим все группы с указанным предметом
        subject_groups = scheduler.exam_groups[scheduler.exam_groups['Subject'] == subject]

        if subject_groups.empty:
            return jsonify({
                'success': False,
                'error': f'Предмет {subject} не найден'
            }), 404

        # Удаляем все секции этого предмета
        sections_to_delete = subject_groups['Section'].tolist()
        scheduler._delete_sections(sections_to_delete)

        # Пересоздаем расписание
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
    """Удаление конкретной секции предмета"""
    try:
        # Проверяем существование секции и соответствие предмету
        subject_groups = scheduler.exam_groups[scheduler.exam_groups['Subject'] == subject]
        if section not in subject_groups['Section'].values:
            return jsonify({
                'success': False,
                'error': f'Секция {section} не найдена для предмета {subject}'
            }), 404

        # Удаляем конкретную секцию
        scheduler._delete_sections([section])

        # Пересоздаем расписание
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
    """
    Получение списка свободных аудиторий на указанный день и временной слот.

    Args:
        day (str): Дата в формате 'YYYY-MM-DD'.
        time_slot (str): Временной слот (например, '08:00-11:00').

    Returns:
        JSON: Список свободных аудиторий или сообщение об ошибке.
    """
    try:
        # Проверяем, существует ли расписание
        if not hasattr(scheduler, 'room_availability'):
            return jsonify({
                'success': False,
                'error': 'Расписание не создано. Сначала создайте расписание.'
            }), 400

        # Получаем список свободных аудиторий
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

if __name__ == '__main__':
    app.run(debug=True, port=5000)