import logging
from collections import defaultdict, Counter
import pandas as pd
from create_db import Session, ResolvedConflict
from services.check_student_conflicts import get_student_conflicts
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename='student_conflicts_log.txt',
    filemode='a'
)

def resolve_it_lab_conflicts(scheduler):
    """
    Finds exams that require an IT lab but are in a regular room,
    and swaps them with exams that are in an IT lab but don't require one.
    """
    logging.info("Starting IT lab conflict resolution.")
    schedule_df = scheduler.schedule_df
    exam_groups = scheduler.exam_groups
    room_types = scheduler.room_types

    # Merge schedule with exam group properties
    full_schedule = pd.merge(schedule_df, exam_groups[['Section', 'classroom_type']], on='Section', how='left')

    # Identify misplaced IT groups
    misplaced_it_groups = full_schedule[
        (full_schedule['classroom_type'] == 'it_lab') &
        (full_schedule['Room'].apply(lambda r: room_types.get(str(r), 'regular') != 'it_lab'))
    ]

    if misplaced_it_groups.empty:
        logging.info("No misplaced IT lab groups found. No swaps needed.")
        return

    logging.info(f"Found {len(misplaced_it_groups)} misplaced IT groups to resolve.")

    for index, misplaced_exam in misplaced_it_groups.iterrows():
        # Find potential swap candidates
        candidates = full_schedule[
            (full_schedule['Date'] == misplaced_exam['Date']) &
            (full_schedule['Time_Slot'] == misplaced_exam['Time_Slot']) &
            (full_schedule['classroom_type'] != 'it_lab') &
            (full_schedule['Room'].apply(lambda r: room_types.get(str(r), 'regular') == 'it_lab'))
        ]

        if not candidates.empty:
            # Select the first candidate
            candidate_exam = candidates.iloc[0]
            
            # Get original indices from the main schedule_df
            misplaced_original_idx = misplaced_exam.name
            candidate_original_idx = candidate_exam.name

            # Swap rooms
            original_room = scheduler.schedule_df.at[misplaced_original_idx, 'Room']
            candidate_room = scheduler.schedule_df.at[candidate_original_idx, 'Room']
            
            scheduler.schedule_df.at[misplaced_original_idx, 'Room'] = candidate_room
            scheduler.schedule_df.at[candidate_original_idx, 'Room'] = original_room

            logging.info(f"SWAP SUCCESSFUL: Section {misplaced_exam['Section']} (needs IT lab) moved to room {candidate_room}. "
                         f"Section {candidate_exam['Section']} moved to room {original_room}.")
        else:
            logging.warning(f"SWAP FAILED: No suitable swap candidate found for section {misplaced_exam['Section']} "
                            f"at {misplaced_exam['Date']} {misplaced_exam['Time_Slot']}.")


def resolve_conflicts_by_moving_groups(scheduler, session_id, max_moves=15):
    """
    Разрешает конфликты путем перемещения целых экзаменационных групп (секций) в другие слоты.
    Цель - минимизировать количество перемещений, воздействуя на группы, а не на отдельных студентов.
    """
    changes_made = []
    session = Session()
    
    try:
        # Получаем полный список всех уникальных дат экзаменов
        available_dates = scheduler.schedule_df['Date'].unique()

        for move_attempt in range(max_moves):
            # 1. Получаем текущие конфликты
            conflicts = get_student_conflicts(scheduler)
            if not conflicts:
                logging.info("Конфликты не найдены. Разрешение завершено.")
                break

            logging.info(f"Попытка {move_attempt + 1}/{max_moves}. Найдено конфликтов: {len(conflicts)}")

            # 2. Агрегируем конфликты по секциям, чтобы найти самые проблемные
            section_conflict_counts = Counter()
            for conflict in conflicts:
                for exam in conflict['exams']:
                    section_conflict_counts[exam['Section']] += 1
            
            if not section_conflict_counts:
                logging.info("Не удалось определить проблемные секции. Завершение.")
                break

            # 3. Выбираем самую проблемную секцию для перемещения
            section_to_move, _ = section_conflict_counts.most_common(1)[0]
            
            original_schedule_info = scheduler.schedule_df[scheduler.schedule_df['Section'] == section_to_move].iloc[0]
            original_date = original_schedule_info['Date']
            subject = original_schedule_info['Subject']
            students_in_section = scheduler.get_students_in_section(section_to_move)
            
            logging.info(f"--- Попытка переместить секцию '{section_to_move}' ({subject}) с {len(students_in_section)} студентами ---")

            best_move = None
            min_new_conflicts = len(conflicts)

            # 4. Ищем лучший новый слот (дату) для этой секции
            for new_date in available_dates:
                if new_date == original_date:
                    continue

                # 5. Проверяем, не создаст ли перемещение новые конфликты для студентов этой секции
                new_conflicts_count = 0
                for student_id in students_in_section:
                    # Получаем экзамены студента, исключая текущий перемещаемый
                    student_exams = scheduler.get_student_exams(student_id)
                    other_exams_on_new_date = [
                        exam for exam in student_exams 
                        if exam['Section'] != section_to_move and exam['Date'] == new_date
                    ]
                    if other_exams_on_new_date:
                        new_conflicts_count += 1
                
                # Если этот ход не создает новых конфликтов, он является хорошим кандидатом
                if new_conflicts_count == 0:
                    # В этом упрощенном примере мы выбираем первый же подходящий слот.
                    # В более сложной реализации можно было бы оценивать все и выбирать лучший.
                    best_move = new_date
                    break
            
            # 6. Если найден подходящий слот, выполняем перемещение
            if best_move is not None:
                new_date = best_move
                logging.info(f"Найдено перемещение для секции {section_to_move} на дату {new_date}")

                # Обновляем расписание в памяти
                scheduler.schedule_df.loc[scheduler.schedule_df['Section'] == section_to_move, 'Date'] = new_date
                
                # Логируем изменение
                change_info = {
                    "type": "group_move",
                    "section": section_to_move,
                    "subject": subject,
                    "student_count": len(students_in_section),
                    "from_date": original_date,
                    "to_date": new_date
                }
                changes_made.append(change_info)

                # Здесь можно было бы добавить запись в БД, если требуется
                # Например, создать новую таблицу для логов перемещения групп

            else:
                logging.warning(f"Не удалось найти подходящий слот для секции {section_to_move}. Пропускаем.")
                # Чтобы избежать зацикливания на одной и той же секции, можно добавить логику ее пропуска
                # в следующих итерациях, но для простоты пока опустим это.
                break # Прерываем, если не можем найти ход

    finally:
        # В данном примере мы не сохраняем изменения в БД, но можно добавить
        # session.commit()
        session.close()

    logging.info(f"Разрешение конфликтов завершено. Всего перемещено групп: {len(changes_made)}")
    return changes_made


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