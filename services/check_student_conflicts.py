import logging
from collections import defaultdict
from itertools import combinations
from datetime import datetime
import pandas as pd

# Настройка логирования (в консоль и файл)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[
    logging.StreamHandler(),
    logging.FileHandler('student_conflicts_log.txt', mode='w') # 'w' для перезаписи файла при каждом запуске
])

def _check_overlap(time_slot1, time_slot2):
    """Проверяет пересечение двух временных интервалов."""
    try:
        start1_str, end1_str = time_slot1.split('-')
        start2_str, end2_str = time_slot2.split('-')

        start1 = datetime.strptime(start1_str, '%H:%M')
        end1 = datetime.strptime(end1_str, '%H:%M')
        start2 = datetime.strptime(start2_str, '%H:%M')
        end2 = datetime.strptime(end2_str, '%H:%M')

        return max(start1, start2) < min(end1, end2)
    except ValueError:
        # Если формат Time_Slot некорректен, считаем, что конфликта нет
        return False

def check_all_students_conflicts(scheduler):
    """
    Проверяет всех студентов на конфликты (пересечение времени экзаменов в один день).
    Формат вывода: <Student_ID>, Дата <YYYY-MM-DD>: конфликт между <Subject1> (HH:MM-HH:MM) и <Subject2> (HH:MM-HH:MM).
    """
    if not scheduler:
        logging.error("Scheduler не инициализирован!")
        return

    if not hasattr(scheduler, 'exams_df') or scheduler.exams_df.empty:
        logging.error("Нет данных о студентах! Проверьте exams_df.")
        return

    all_students = scheduler.exams_df['fake_id'].unique()
    total_students = len(all_students)
    conflicting_students = set()
    conflict_details = []

    logging.info(f"Проверка конфликтов (пересечение времени) для {total_students} студентов...")

    for student_id in all_students:
        try:
            student_schedule_df = scheduler.get_student_sections(student_id)

            if student_schedule_df.empty:
                continue

            # Исключаем экзамены без реального времени
            student_schedule_df = student_schedule_df[
                (student_schedule_df['Time_Slot'].notna()) &
                (student_schedule_df['Time_Slot'] != 'N/A')
            ].copy()

            # Группируем экзамены по дате
            exams_by_date = student_schedule_df.groupby('Date')

            for date, daily_exams_df in exams_by_date:
                if len(daily_exams_df) > 1:
                    # Проверяем все комбинации пар экзаменов в этот день
                    for exam1, exam2 in combinations(daily_exams_df.to_dict('records'), 2):
                        # Пропускаем сравнение, если это один и тот же предмет
                        if exam1['Subject'] == exam2['Subject']:
                            continue

                        time_slot1 = exam1['Time_Slot']
                        time_slot2 = exam2['Time_Slot']

                        if _check_overlap(time_slot1, time_slot2):
                            conflicting_students.add(student_id)
                            
                            conflict_info = {
                                'Student_ID': student_id,
                                'Conflict_Date': date,
                                'Exam_1': f"{exam1['Subject']} ({time_slot1})",
                                'Exam_2': f"{exam2['Subject']} ({time_slot2})",
                            }
                            conflict_details.append(conflict_info)
                            
                            logging.warning(
                                f"КОНФЛИКТ: Студент {student_id}, Дата {date}: "
                                f"пересечение между '{conflict_info['Exam_1']}' и '{conflict_info['Exam_2']}'"
                            )

        except Exception as e:
            logging.error(f"Ошибка при проверке студента {student_id}: {str(e)}")

    # Итоговый лог
    num_conflict_students = len(conflicting_students)
    percentage = (num_conflict_students / total_students * 100) if total_students > 0 else 0
    logging.info(f"Проверка завершена. Студентов с конфликтами: "
                 f"{num_conflict_students} из {total_students} ({percentage:.2f}%)")

    # Сохранение конфликтов в Excel
    if conflict_details:
        conflict_df = pd.DataFrame(conflict_details)
        output_file = "student_time_conflicts.xlsx"
        try:
            conflict_df.to_excel(output_file, index=False)
            logging.info(f"Детали конфликтов сохранены в файл: {output_file}")
        except Exception as e:
            logging.error(f"Ошибка при сохранении в Excel: {str(e)}")
    else:
        logging.info("Конфликтов не найдено, Excel не создан.")


def get_student_conflicts(scheduler):
    """
    Проверяет всех студентов на конфликты (пересечение времени экзаменов в один день)
    и возвращает список конфликтов.

    :param scheduler: Экземпляр класса ExamScheduler.
    :return: Список словарей с деталями конфликтов.
             Формат: [{'student': student_id, 'date': date, 'exams': [exam_record_1, exam_record_2, ...]}, ...]
    """
    if not scheduler:
        logging.error("Scheduler не инициализирован!")
        return []

    if not hasattr(scheduler, 'exams_df') or scheduler.exams_df.empty:
        logging.error("Нет данных о студентах! Проверьте exams_df или student_exams.")
        return []

    all_students = scheduler.exams_df['fake_id'].unique()
    conflict_details = []
    
    processed_conflicts = set() # Чтобы не дублировать конфликты (студент-день)

    for student_id in all_students:
        try:
            student_schedule_df = scheduler.get_student_sections(student_id)

            if student_schedule_df.empty:
                continue

            student_schedule_df = student_schedule_df[
                (student_schedule_df['Time_Slot'].notna()) &
                (student_schedule_df['Time_Slot'] != 'N/A')
            ].copy()

            exams_by_date = student_schedule_df.groupby('Date')

            for date, daily_exams_df in exams_by_date:
                if len(daily_exams_df) > 1:
                    exam_records = daily_exams_df.to_dict('records')
                    # Проверяем все комбинации пар экзаменов
                    for exam1, exam2 in combinations(exam_records, 2):
                        # Пропускаем сравнение, если это один и тот же предмет
                        if exam1['Subject'] == exam2['Subject']:
                            continue
                            
                        if _check_overlap(exam1['Time_Slot'], exam2['Time_Slot']):
                            # Найден конфликт для этого студента в этот день
                            conflict_key = (student_id, date)
                            if conflict_key not in processed_conflicts:
                                conflict_details.append({
                                    'student': student_id,
                                    'date': date,
                                    'exams': exam_records
                                })
                                processed_conflicts.add(conflict_key)
                            break  # Достаточно одного пересечения, чтобы пометить день конфликтным
        except Exception as e:
            logging.error(f"Ошибка при проверке студента {student_id}: {str(e)}")

    return conflict_details
