import logging
from collections import defaultdict
import pandas as pd

# Настройка логирования (в консоль и файл)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[
    logging.StreamHandler(),
    logging.FileHandler('student_conflicts_log.txt')
])

def check_all_students_conflicts(scheduler):
    """
    Проверяет всех студентов на конфликты (>1 экзамена в день в одном временном слоте).
    Формат вывода: <Student_ID>, Дата <YYYY-MM-DD>: <N> экзаменов (>1 в слоте), Предметы: <Subject1>, <Subject2>, ...
    Также выводит конфликты по дням для отладки.

    :param scheduler: Экземпляр класса ExamScheduler после оптимизации.
    """
    if not scheduler:
        logging.error("Scheduler не инициализирован!")
        return

    # Получаем всех студентов
    if hasattr(scheduler, 'exams_df') and not scheduler.exams_df.empty:
        all_students = scheduler.exams_df['fake_id'].unique()
    elif hasattr(scheduler, 'student_exams'):
        all_students = list(scheduler.student_exams.keys())
    else:
        logging.error("Нет данных о студентах! Проверьте exams_df или student_exams.")
        return

    total_students = len(all_students)
    conflict_students = 0
    conflict_details = []
    day_conflict_details = []

    logging.info(f"Проверка конфликтов (>1 экзамена в день в одном слоте) для {total_students} студентов...")

    for student_id in all_students:
        try:
            student_schedule = scheduler.get_student_sections(student_id)

            if student_schedule.empty:
                logging.warning(f"Для студента {student_id} нет расписания. Пропуск.")
                continue

            # --- ИЗМЕНЕНИЕ: Исключаем экзамены без реального времени (N/A или пустые) ---
            student_schedule = student_schedule[
                (student_schedule['Time_Slot'].notna()) &
                (student_schedule['Time_Slot'] != 'N/A')
            ].copy()
            # --- КОНЕЦ ИЗМЕНЕНИЯ ---

            # Проверка конфликтов по слотам
            exams_by_date_slot = defaultdict(list)
            for _, exam in student_schedule.iterrows():
                date = exam['Date']
                time_slot = exam['Time_Slot'].strip()
                subject = exam['Subject'].strip()
                exams_by_date_slot[(date, time_slot)].append(subject)

            for (date, time_slot), subjects in exams_by_date_slot.items():
                if len(subjects) > 1:
                    conflict_students += 1
                    subjects_str = ", ".join(subjects)
                    logging.warning(f"{student_id}, Дата {date}: {len(subjects)} экзаменов (>1 в слоте {time_slot}), Предметы: {subjects_str}")

                    conflict_details.append({
                        'Student_ID': student_id,
                        'Conflict_Date': date,
                        'Time_Slot': time_slot,
                        'Exam_Count': len(subjects),
                        'Subjects': subjects_str
                    })

            # Проверка конфликтов по дням (для отладки)
            exams_by_date = defaultdict(list)
            for _, exam in student_schedule.iterrows():
                date = exam['Date']
                subject = exam['Subject'].strip()
                exams_by_date[date].append(subject)
            for date, subjects in exams_by_date.items():
                if len(subjects) > 1:
                    subjects_str = ", ".join(subjects)
                    day_conflict_details.append({
                        'Student_ID': student_id,
                        'Conflict_Date': date,
                        'Exam_Count': len(subjects),
                        'Subjects': subjects_str
                    })
                    break

        except Exception as e:
            logging.error(f"Ошибка при проверке студента {student_id}: {str(e)}")

    # Итоговый лог для конфликтов по слотам
    logging.info(f"Проверка завершена. Студентов с конфликтами (>1 экзамена в слоте): "
                 f"{conflict_students} из {total_students} ({conflict_students / total_students * 100:.2f}%)")

    # Сохранение конфликтов по слотам в Excel
    if conflict_details:
        conflict_df = pd.DataFrame(conflict_details)
        output_file = "student_conflicts_after_optimization.xlsx"
        try:
            conflict_df.to_excel(output_file, index=False)
            logging.info(f"Конфликтные студенты (>1 в слоте) сохранены в файл: {output_file}")
        except Exception as e:
            logging.error(f"Ошибка при сохранении в Excel: {str(e)}")
    else:
        logging.info("Конфликтных студентов (>1 в слоте) нет, Excel не создан.")

    # Лог и Excel для конфликтов по дням (для отладки)
    if day_conflict_details:
        logging.warning(f"Найдено {len(day_conflict_details)} студентов с конфликтами по дням (>1 экзамена в день):")
        for detail in day_conflict_details:
            logging.warning(f"{detail['Student_ID']}, Дата {detail['Conflict_Date']}: {detail['Exam_Count']} экзаменов (>1 в день), Предметы: {detail['Subjects']}")
        day_conflict_df = pd.DataFrame(day_conflict_details)
        day_output_file = "student_day_conflicts_after_optimization.xlsx"
        try:
            day_conflict_df.to_excel(day_output_file, index=False)
            logging.info(f"Конфликтные студенты (>1 в день) сохранены в файл: {day_output_file}")
        except Exception as e:
            logging.error(f"Ошибка при сохранении конфликтов по дням в Excel: {str(e)}")
    else:
        logging.info("Конфликтных студентов по дням (>1 экзамена в день) нет.")


def get_student_conflicts(scheduler):
    """
    Проверяет всех студентов на конфликты (>1 экзамена в день)
    и возвращает список конфликтов.

    :param scheduler: Экземпляр класса ExamScheduler.
    :return: Список словарей с деталями конфликтов.
    """
    if not scheduler:
        logging.error("Scheduler не инициализирован!")
        return []

    if hasattr(scheduler, 'exams_df') and not scheduler.exams_df.empty:
        all_students = scheduler.exams_df['fake_id'].unique()
    elif hasattr(scheduler, 'student_exams'):
        all_students = list(scheduler.student_exams.keys())
    else:
        logging.error("Нет данных о студентах! Проверьте exams_df или student_exams.")
        return []

    conflict_details = []

    for student_id in all_students:
        try:
            student_schedule = scheduler.get_student_sections(student_id)

            if student_schedule.empty:
                continue

            # --- ИЗМЕНЕНИЕ: Исключаем экзамены без реального времени (N/A или пустые) ---
            student_schedule = student_schedule[
                (student_schedule['Time_Slot'].notna()) &
                (student_schedule['Time_Slot'] != 'N/A')
            ].copy()
            # --- КОНЕЦ ИЗМЕНЕНИЯ ---

            exams_by_date = defaultdict(list)
            for _, exam in student_schedule.iterrows():
                date = exam['Date']
                subject = exam['Subject'].strip()
                time_slot = exam['Time_Slot'].strip()
                exams_by_date[date].append(f"{subject} ({time_slot})")

            for date, subjects in exams_by_date.items():
                if len(subjects) > 1:
                    conflict_details.append({
                        'student': student_id,
                        'date': date,
                        'subjects': subjects
                    })
        except Exception as e:
            logging.error(f"Ошибка при проверке студента {student_id}: {str(e)}")

    return conflict_details
