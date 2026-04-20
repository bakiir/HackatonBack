from datetime import datetime

def group_consecutive_slots(slots):
    if not slots:
        return []

    # Define the key for grouping exams that are the same event
    def get_exam_key(slot):
        return (
            str(slot.get('Date')),
            str(slot.get('Subject')),
            str(slot.get('Instructor')),
            str(slot.get('Room')),
            str(slot.get('EduProgram')),
            str(slot.get('Section')),
            str(slot.get('Students_Count')),
            str(slot.get('pinned')),
            str(slot.get('two_rooms_needed'))
        )

    # Sort slots by the grouping key and then by time
    try:
        slots.sort(key=lambda s: (get_exam_key(s), datetime.strptime(s.get('Time_Slot', '00:00-00:00').split('-')[0], '%H:%M')))
    except (ValueError, IndexError):
        # If time format is unexpected, fall back to string sort for time
        slots.sort(key=lambda s: (get_exam_key(s), s.get('Time_Slot', '')))


    merged_slots = []
    i = 0
    while i < len(slots):
        # Start of a potential group
        group = [slots[i]]
        j = i + 1
        while j < len(slots):
            # Check if the next slot is part of the same exam event
            if get_exam_key(slots[j]) == get_exam_key(slots[i]):
                try:
                    # Check if the time slots are consecutive
                    prev_end_time_str = group[-1]['Time_Slot'].split('-')[1]
                    curr_start_time_str = slots[j]['Time_Slot'].split('-')[0]
                    
                    prev_end_time = datetime.strptime(prev_end_time_str, '%H:%M')
                    curr_start_time = datetime.strptime(curr_start_time_str, '%H:%M')

                    # If they are consecutive
                    if prev_end_time == curr_start_time:
                        group.append(slots[j])
                        j += 1
                    else:
                        # Time is not consecutive, so break the group
                        break
                except (ValueError, IndexError):
                    # Time format is wrong, break the group
                    break
            else:
                # Different exam, break the group
                break
        
        # If the group has more than one slot, merge them
        if len(group) > 1:
            first_slot = group[0]
            last_slot = group[-1]
            
            start_time = first_slot['Time_Slot'].split('-')[0]
            end_time = last_slot['Time_Slot'].split('-')[1]
            
            merged_slot = first_slot.copy()
            merged_slot['Time_Slot'] = f"{start_time}-{end_time}"
            
            # Per user request, use the last slot's Base_Time_Slot and seat_info
            if 'Base_Time_Slot' in last_slot:
                merged_slot['Base_Time_Slot'] = last_slot['Base_Time_Slot']
            if 'seat_info' in last_slot:
                merged_slot['seat_info'] = last_slot.get('seat_info') 
            
            merged_slots.append(merged_slot)
        else:
            # Single slot, just add it
            merged_slots.append(group[0])
            
        # Move index to the next un-processed slot
        i = j
    return merged_slots


def normalize_room(room_str):
    """
    Нормализует строку с аудиториями (сортирует при перечислении через запятую).
    Например: '201,101' -> '101,201'
    """
    room_str = str(room_str)
    if ',' in room_str:
        return ','.join(sorted([r.strip() for r in room_str.split(',')]))
    return room_str


def handle_nan_values(obj):
    import math
    import numpy as np
    import pandas as pd
    if isinstance(obj, (float, np.float64, np.float32)) and (math.isnan(obj) or np.isnan(obj)):
        return None
    if isinstance(obj, (np.int64, np.int32)):
        return int(obj)
    if isinstance(obj, (np.float64, np.float32)):
        return float(obj)
    elif isinstance(obj, dict):
        return {key: handle_nan_values(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [handle_nan_values(item) for item in obj]
    elif isinstance(obj, pd.DataFrame):
        return obj.replace({np.nan: None}).to_dict('records')
    else:
        return obj


allowed_roles = {"admin-sdt", "admin-gum", "admin-spigu", "admin-sem", "admin"}
role_to_faculty = {
    "admin-sdt": "Школа цифровых технологий",
    "admin-sem": "Школа экономики и менеджмента",
    "admin-gum": "Гуманитарная школа",
    "admin-spigu": "Школа права и государственного управления"
}

def get_exam_type(exam_info):
    """Определяет тип экзамена на основе его атрибутов."""
    has_exam = exam_info.get('has_exam', False)
    proctor_needed = exam_info.get('proctor_needed', False)
    two_rooms_needed = exam_info.get('two_rooms_needed', False)

    if has_exam and proctor_needed and two_rooms_needed:
        return "written"
    elif not has_exam and not proctor_needed and not two_rooms_needed:
        return "summative"
    elif has_exam and not proctor_needed and not two_rooms_needed:
        return "defense"
    return "unknown"

def check_overlap(slot1, slot2):
    """Проверяет пересечение двух временных слотов (Оптимизировано: конвертация в минуты)."""
    if not slot1 or not slot2 or slot1 == 'N/A' or slot2 == 'N/A':
        return False
    try:
        s1_str, e1_str = slot1.split('-')
        s2_str, e2_str = slot2.split('-')
        
        # Конвертируем HH:MM в минуты от начала суток. Это супер-быстро и спасает от ошибки незначащего нуля (9:00 vs 09:00).
        def to_mins(t_str):
            h, m = t_str.strip().split(':')
            return int(h) * 60 + int(m)
            
        start1, end1 = to_mins(s1_str), to_mins(e1_str)
        start2, end2 = to_mins(s2_str), to_mins(e2_str)
        
        return max(start1, start2) < min(end1, end2)
    except Exception:
        return False
