from datetime import datetime
from collections import defaultdict
import logging
import pandas as pd

def check_all_students_conflicts(scheduler):
    """
    Проверяет всех студентов на конфликты (>1 экзамена в день с пересечением по времени).
    Формат вывода: <Student_ID>, Дата <YYYY-MM-DD>: <N> экзаменов с конфликтом, Предметы: <Subject1> vs <Subject2>, ...
    """
    if not scheduler:
        logging.error("Scheduler не инициализирован!")
        return

    all_students = list(scheduler.student_exams.keys())
    total_students = len(all_students)
    conflict_students_set = set()
    conflict_details = []

    logging.info(f"Проверка временных конфликтов для {total_students} студентов...")

    for student_id in all_students:
        if student_id not in scheduler.student_exams:
            continue

        exams = scheduler.student_exams[student_id]
        
        exams_by_date = defaultdict(list)
        for exam in exams:
            if exam.get('Time_Slot') and exam['Time_Slot'] != 'N/A':
                exams_by_date[exam['Date']].append(exam)

        for date, daily_exams in exams_by_date.items():
            if len(daily_exams) > 1:
                # Проверяем каждую пару экзаменов на пересечение
                for i in range(len(daily_exams)):
                    for j in range(i + 1, len(daily_exams)):
                        e1 = daily_exams[i]
                        e2 = daily_exams[j]
                        
                        try:
                            e1_start = datetime.strptime(e1['Time_Slot'].split('-')[0], '%H:%M')
                            e1_end = datetime.strptime(e1['Time_Slot'].split('-')[1], '%H:%M')
                            e2_start = datetime.strptime(e2['Time_Slot'].split('-')[0], '%H:%M')
                            e2_end = datetime.strptime(e2['Time_Slot'].split('-')[1], '%H:%M')

                            # Если интервалы пересекаются
                            if e1_start < e2_end and e2_start < e1_end:
                                conflict_students_set.add(student_id)
                                conflict_info = {
                                    'Student_ID': student_id,
                                    'Conflict_Date': date,
                                    'Exams': f"{e1.get('Subject', 'N/A')} vs {e2.get('Subject', 'N/A')}",
                                    'Time_Slots': f"{e1['Time_Slot']} vs {e2['Time_Slot']}"
                                }
                                conflict_details.append(conflict_info)
                                logging.warning(f"КОНФЛИКТ: Студент {student_id}, Дата {date}, "
                                                f"Пересечение: {e1.get('Subject')} ({e1['Time_Slot']}) и "
                                                f"{e2.get('Subject')} ({e2['Time_Slot']})")
                        except (ValueError, IndexError) as e:
                            logging.error(f"Ошибка парсинга времени для студента {student_id} в дате {date}: {e}")

    conflict_count = len(conflict_students_set)
    logging.info(f"Проверка завершена. Студентов с конфликтами: "
                 f"{conflict_count} из {total_students} ({conflict_count / total_students * 100:.2f}%)")

    if conflict_details:
        conflict_df = pd.DataFrame(conflict_details)
        output_file = "student_conflicts_after_optimization.xlsx"
        try:
            conflict_df.to_excel(output_file, index=False)
            logging.info(f"Детали конфликтов сохранены в файл: {output_file}")
        except Exception as e:
            logging.error(f"Ошибка при сохранении в Excel: {str(e)}")
    else:
        logging.info("Временных конфликтов не найдено.")


def get_student_conflicts(scheduler):
    """
    Проверяет всех студентов на конфликты (>1 экзамена в день с пересечением по времени)
    и возвращает список конфликтов.
    """
    if not scheduler:
        logging.error("Scheduler не инициализирован!")
        return []

    all_students = list(scheduler.student_exams.keys())
    conflict_details = []

    for student_id in all_students:
        if student_id not in scheduler.student_exams:
            continue

        exams = scheduler.student_exams[student_id]
        
        exams_by_date = defaultdict(list)
        for exam in exams:
            if exam.get('Time_Slot') and exam['Time_Slot'] != 'N/A':
                exams_by_date[exam['Date']].append(exam)

        for date, daily_exams in exams_by_date.items():
            if len(daily_exams) > 1:
                # --- DEBUG LOGGING ---
                logging.info(f"Checking conflicts for student {student_id} on date {date} with exams: {daily_exams}")
                # --- END DEBUG LOGGING ---
                for i in range(len(daily_exams)):
                    for j in range(i + 1, len(daily_exams)):
                        e1 = daily_exams[i]
                        e2 = daily_exams[j]
                        
                        try:
                            e1_start = datetime.strptime(e1['Time_Slot'].split('-')[0], '%H:%M')
                            e1_end = datetime.strptime(e1['Time_Slot'].split('-')[1], '%H:%M')
                            e2_start = datetime.strptime(e2['Time_Slot'].split('-')[0], '%H:%M')
                            e2_end = datetime.strptime(e2['Time_Slot'].split('-')[1], '%H:%M')

                            if e1_start < e2_end and e2_start < e1_end:
                                conflict_details.append({
                                    'student': student_id,
                                    'date': date,
                                    'subjects': [f"{e.get('Subject')} ({e.get('Time_Slot')})" for e in daily_exams]
                                })
                                break 
                        except (ValueError, IndexError):
                            continue
                    else:
                        continue
                    break
    return conflict_details