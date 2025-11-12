import logging
from collections import defaultdict
import pandas as pd
from create_db import Session, ResolvedConflict
from services.check_student_conflicts import get_student_conflicts

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def resolve_conflicts_by_moving_student(scheduler, session_id):
    """
    Пытается разрешить конфликты, перемещая студента в другую секцию того же предмета в другой день.
    :param scheduler: Экземпляр ExamScheduler.
    :param session_id: ID текущей сессии экзаменов.
    :return: Список словарей с информацией о внесенных изменениях.
    """
    changes_made = []
    session = Session()

    try:
        # 1. Получаем конфликты напрямую из функции
        conflicts = get_student_conflicts(scheduler)
        if not conflicts:
            logging.info("Студенческих конфликтов для разрешения не найдено.")
            return []

        logging.info(f"Найдено {len(conflicts)} студенто-дней с конфликтами для обработки.")

        # Кэшируем данные для производительности
        all_sections_schedule = scheduler.schedule_df
        all_exam_groups = scheduler.exam_groups
        room_capacities = scheduler.room_capacities
        
        # Собираем все конфликтные дни для каждого студента
        student_conflict_dates = defaultdict(set)
        for c in conflicts:
            student_conflict_dates[c['student']].add(c['date'])

        for conflict in conflicts:
            student_id = conflict['student']
            conflict_date = conflict['date']
            conflicting_exams = conflict['exams']

            logging.info(f"--- Обработка студента: {student_id} в день {conflict_date} ---")

            # Сортируем экзамены, чтобы сначала пытаться переместить экзамен с меньшим числом студентов
            conflicting_exams.sort(key=lambda x: x['Students_Count'])

            move_successful_for_day = False
            for exam_to_move in conflicting_exams:
                original_section = exam_to_move['Section']
                subject_to_move = exam_to_move['Subject']

                logging.info(f"Попытка переместить экзамен '{subject_to_move}' (секция: {original_section})")

                # Ищем альтернативные секции того же предмета
                alternative_sections = all_exam_groups[
                    (all_exam_groups['Subject'] == subject_to_move) &
                    (all_exam_groups['Section'] != original_section)
                ]

                if alternative_sections.empty:
                    logging.warning(f"Нет альтернативных секций для предмета '{subject_to_move}'.")
                    continue

                for _, alt_section_row in alternative_sections.iterrows():
                    alt_section_id = alt_section_row['Section']
                    alt_schedule = all_sections_schedule[all_sections_schedule['Section'] == alt_section_id]

                    if alt_schedule.empty:
                        continue

                    alt_schedule_info = alt_schedule.iloc[0]
                    new_date = alt_schedule_info['Date']

                    # Проверка 1: Новый день не должен быть днем исходного конфликта
                    if new_date == conflict_date:
                        continue
                    
                    # Проверка 2: Новый день не должен быть другим конфликтным днем для этого студента
                    if new_date in student_conflict_dates.get(student_id, set()):
                        continue

                    # Проверка 3: Наличие свободного места
                    room_name = str(alt_schedule_info['Room'])
                    capacity = sum(room_capacities.get(r.strip(), 0) for r in room_name.split(','))
                    current_students = alt_schedule_info['Students_Count']

                    if current_students < capacity:
                        logging.info(f"Найдено валидное перемещение для студента {student_id}:")
                        logging.info(f"  Из: Секция {original_section} ({subject_to_move}) в день {conflict_date}")
                        logging.info(f"  В:  Секция {alt_section_id} ({subject_to_move}) в день {new_date}")

                        # Обновляем данные в памяти планировщика
                        scheduler.exams_df.loc[
                            (scheduler.exams_df['fake_id'] == student_id) &
                            (scheduler.exams_df['Section'] == original_section), 'Section'
                        ] = alt_section_id

                        scheduler.schedule_df.loc[scheduler.schedule_df['Section'] == original_section, 'Students_Count'] -= 1
                        scheduler.schedule_df.loc[scheduler.schedule_df['Section'] == alt_section_id, 'Students_Count'] += 1

                        # Записываем изменения для отчета и БД
                        changes_made.append({
                            "student": student_id,
                            "subject": subject_to_move,
                            "from_section": original_section,
                            "to_section": alt_section_id,
                            "from_date": conflict_date,
                            "to_date": new_date
                        })

                        resolved_conflict = ResolvedConflict(
                            session_id=session_id,
                            student_id=student_id,
                            subject=subject_to_move,
                            original_section=original_section,
                            new_section=alt_section_id
                        )
                        session.add(resolved_conflict)
                        
                        # Обновляем информацию о конфликтах для студента, чтобы не попасть в тот же день снова
                        student_conflict_dates[student_id].discard(conflict_date)

                        move_successful_for_day = True
                        break  # Переходим к следующему конфликту (студент-день)
                
                if move_successful_for_day:
                    break # Переходим к следующему конфликту (студент-день)
    
    finally:
        session.commit()
        session.close()

    logging.info(f"Разрешение конфликтов завершено. Всего внесено изменений: {len(changes_made)}")
    return changes_made