from flask import Flask, jsonify, send_file
from flask_cors import CORS
from xx import ExamScheduler  # импортируем ваш класс из файла xx.py
import numpy as np

app = Flask(__name__)
CORS(app)  # Разрешаем CORS для работы с фронтендом

# Инициализация планировщика при запуске сервера
scheduler = ExamScheduler(
    exams_file=r"C:\Users\User\Downloads\FakedNarxozData (2).xlsx",
    rooms_file=r"C:\Users\User\Downloads\auditoriums.xlsx",
    start_date='2024-01-15',
    num_days=14
)

# Создание расписания при запуске
scheduler.create_schedule()

@app.route('/schedule')
def get_schedule():
    """Получение общего расписания"""
    return jsonify(scheduler.schedule_df.to_dict('records'))

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


if __name__ == '__main__':
    app.run(debug=True, port=5000)