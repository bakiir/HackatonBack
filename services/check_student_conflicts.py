import logging
from collections import defaultdict
import pandas as pd
from datetime import datetime

# Настройка логирования (в консоль и файл)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[
    logging.StreamHandler(),
    logging.FileHandler('student_conflicts_log.txt')
])


def check_all_students_conflicts(scheduler):
    """
    Проверяет всех студентов на конфликты (>1 экзамена в день в одном временном слоте).
    Формат вывода: <Student_ID>, Дата <YYYY-MM-DD>: <N> экзаменов (>1 в слоте), Предметы: <Subject1>, <Subject2>, ...

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
    conflict_details = []  # Для сохранения в Excel

    logging.info(f"Проверка конфликтов (>1 экзамена в день в одном слоте) для {total_students} студентов...")

    for student_id in all_students:
        try:
            student_schedule = scheduler.get_student_sections(student_id)

            if student_schedule.empty:
                logging.warning(f"Для студента {student_id} нет расписания. Пропуск.")
                continue

            # Группируем по дате и временному слоту
            exams_by_date_slot = defaultdict(list)
            for _, exam in student_schedule.iterrows():
                date = exam['Date']
                time_slot = exam['Time_Slot'].strip()
                subject = exam['Subject'].strip()
                exams_by_date_slot[(date, time_slot)].append(subject)

            # Проверяем конфликты (>1 экзамена в одном слоте)
            for (date, time_slot), subjects in exams_by_date_slot.items():
                if len(subjects) > 1:  # Конфликт, если >1 экзамена в одном слоте
                    conflict_students += 1
                    subjects_str = ", ".join(subjects)
                    logging.warning(
                        f"{student_id}, Дата {date}: {len(subjects)} экзаменов (>1 в слоте {time_slot}), Предметы: {subjects_str}")

                    # Добавляем в детали для Excel
                    conflict_details.append({
                        'Student_ID': student_id,
                        'Conflict_Date': date,
                        'Time_Slot': time_slot,
                        'Exam_Count': len(subjects),
                        'Subjects': subjects_str
                    })

        except Exception as e:
            logging.error(f"Ошибка при проверке студента {student_id}: {str(e)}")

    # Итоговый лог
    logging.info(f"Проверка завершена. Студентов с конфликтами (>1 экзамена в слоте): "
                 f"{conflict_students} из {total_students} ({conflict_students / total_students * 100:.2f}%)")

    # Сохранение в Excel, если есть конфликты
    if conflict_details:
        conflict_df = pd.DataFrame(conflict_details)
        output_file = "student_conflicts_after_optimization.xlsx"
        try:
            conflict_df.to_excel(output_file, index=False)
            logging.info(f"Конфликтные студенты сохранены в файл: {output_file}")
        except Exception as e:
            logging.error(f"Ошибка при сохранении в Excel: {str(e)}")
    else:
        logging.info("Конфликтных студентов нет, Excel не создан.")
