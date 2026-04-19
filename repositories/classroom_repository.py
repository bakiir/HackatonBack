from typing import List
from sqlalchemy.orm import Session
from datetime import date, datetime
from create_db import ClassroomSlot, RoomExclusion
from .base_repository import BaseRepository

class ClassroomRepository(BaseRepository[ClassroomSlot]):
    def __init__(self, session: Session):
        super().__init__(ClassroomSlot, session)

    def get_slots_by_room(self, room_number: str) -> List[ClassroomSlot]:
        return self.session.query(ClassroomSlot).filter(ClassroomSlot.classroom_number == room_number).all()

    def get_booked_slots(self) -> List[ClassroomSlot]:
        return self.session.query(ClassroomSlot).filter(ClassroomSlot.is_booked == True).all()

    def clear_all_slots(self) -> None:
        self.session.query(ClassroomSlot).delete()
        self.session.commit()

class RoomExclusionRepository(BaseRepository[RoomExclusion]):
    def __init__(self, session: Session):
        super().__init__(RoomExclusion, session)

    def get_exclusions_for_date(self, check_date: date) -> List[RoomExclusion]:
        return self.session.query(RoomExclusion).filter(RoomExclusion.exclusion_date == check_date).all()

    def is_room_excluded(self, room_number: str, check_date: date, start: datetime, end: datetime) -> bool:
        overlap = self.session.query(RoomExclusion).filter(
            RoomExclusion.room_number == room_number,
            RoomExclusion.exclusion_date == check_date,
            RoomExclusion.start_time < end,
            RoomExclusion.end_time > start
        ).first()
        return overlap is not None
