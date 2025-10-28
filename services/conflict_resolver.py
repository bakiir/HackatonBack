import pandas as pd
import logging
from collections import defaultdict
from create_db import Session, ResolvedConflict  # Import Session and ResolvedConflict

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def resolve_day_conflicts(scheduler, session_id):
    """
    Tries to resolve student exam conflicts where a student has more than one exam on the same day.
    It attempts to move one of the conflicting exams to a different group of the same subject on a different day.

    :param scheduler: An instance of the ExamScheduler class.
    :param session_id: The ID of the current exam session.
    :return: A list of dictionaries detailing the successful changes.
    """
    changes_made = []
    session = Session()  # Create a new session

    try:
        conflicts_df = pd.read_excel("student_day_conflicts_after_optimization.xlsx")
        conflicted_student_ids = conflicts_df['Student_ID'].unique()
        logging.info(f"Loaded {len(conflicted_student_ids)} students with day conflicts.")
    except FileNotFoundError:
        logging.error("Conflict file 'student_day_conflicts_after_optimization.xlsx' not found.")
        return {"error": "Conflict file not found."}

    # Get all sections and their schedules once to avoid repeated lookups
    all_sections_schedule = scheduler.schedule_df
    all_exam_groups = scheduler.exam_groups
    room_capacities = scheduler.room_capacities

    for student_id in conflicted_student_ids:
        student_id = str(student_id)
        logging.info(f"--- Processing student: {student_id} ---")

        student_schedule_df = scheduler.get_student_sections(student_id)
        if student_schedule_df.empty:
            logging.warning(f"Could not retrieve schedule for student {student_id}. Skipping.")
            continue

        exams_by_day = defaultdict(list)
        for _, exam in student_schedule_df.iterrows():
            exams_by_day[exam['Date']].append(exam.to_dict())

        for conflict_date, exams in exams_by_day.items():
            if len(exams) > 1:
                logging.info(f"Conflict found for student {student_id} on {conflict_date} with {len(exams)} exams.")

                # Try to move one of the conflicting exams
                for exam_to_move in exams:
                    original_section = exam_to_move['Section']
                    subject_to_move = exam_to_move['Subject']
                    instructor_to_match = exam_to_move['Instructor']

                    logging.info(
                        f"Attempting to move '{subject_to_move}' (section: {original_section}) for instructor '{instructor_to_match}'")

                    # Find other sections for the same subject and instructor
                    alternative_sections = all_exam_groups[
                        (all_exam_groups['Subject'] == subject_to_move) &
                        (all_exam_groups['Instructor'] == instructor_to_match) &
                        (all_exam_groups['Section'] != original_section)
                        ]

                    move_successful = False  # Initialize here to avoid UnboundLocalError

                    if alternative_sections.empty:
                        logging.warning(f"No alternative sections found for subject '{subject_to_move}'.")
                        continue

                    for _, alt_section_row in alternative_sections.iterrows():
                        alt_section_id = alt_section_row['Section']

                        alt_schedule = all_sections_schedule[all_sections_schedule['Section'] == alt_section_id]
                        if alt_schedule.empty:
                            continue

                        alt_schedule_info = alt_schedule.iloc[0]
                        new_date = alt_schedule_info['Date']

                        # 1. Check if the new date is different and not another conflict day for the student
                        if new_date == conflict_date or new_date in exams_by_day:
                            continue

                        # 2. Check for available space
                        room_name = alt_schedule_info['Room']
                        # Clean room name if it has capacity in it e.g. "101(25)"
                        room_name = str(room_name)
                        if '(' in room_name:
                            room_name = room_name.split('(')[0].strip()

                        capacity = room_capacities.get(room_name, 0)
                        current_students = alt_schedule_info['Students_Count']

                        if current_students < capacity:
                            # This is a valid move
                            logging.info(f"Found valid move for student {student_id}:")
                            logging.info(f"  From: Section {original_section} ({subject_to_move}) on {conflict_date}")
                            logging.info(f"  To:   Section {alt_section_id} ({subject_to_move}) on {new_date}")

                            # Update scheduler's in-memory data
                            # a) Update exams_df (student's enrollment)
                            scheduler.exams_df.loc[
                                (scheduler.exams_df['fake_id'] == student_id) &
                                (scheduler.exams_df['Section'] == original_section), 'Section'
                            ] = alt_section_id

                            # b) Update schedule_df (student counts)
                            scheduler.schedule_df.loc[
                                scheduler.schedule_df['Section'] == original_section, 'Students_Count'] -= 1
                            scheduler.schedule_df.loc[
                                scheduler.schedule_df['Section'] == alt_section_id, 'Students_Count'] += 1

                            changes_made.append({
                                "student": student_id,
                                "switched": {
                                    "subject": subject_to_move,
                                    "from": original_section,
                                    "to": alt_section_id
                                }
                            })

                            resolved_conflict = ResolvedConflict(
                                session_id=session_id,
                                student_id=student_id,
                                subject=subject_to_move,
                                original_section=original_section,
                                new_section=alt_section_id
                            )
                            session.add(resolved_conflict)

                            move_successful = True
                            break  # Break out of the alternative sections loop

                    if move_successful:
                        break  # Break out of the exams_to_move loop

                if move_successful:
                    break  # Break out of the conflict_date loop

    logging.info(f"Conflict resolution finished. Total changes made: {len(changes_made)}")
    session.commit()
    session.close()
    return changes_made