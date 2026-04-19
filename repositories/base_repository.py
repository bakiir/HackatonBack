from typing import Type, TypeVar, Generic, List, Optional
from sqlalchemy.orm import Session
from create_db import Base

T = TypeVar("T", bound=Base)

class BaseRepository(Generic[T]):
    def __init__(self, model: Type[T], session: Session):
        self.model = model
        self.session = session

    def get_by_id(self, id: int) -> Optional[T]:
        return self.session.query(self.model).filter(self.model.id == id).first()

    def get_all(self) -> List[T]:
        return self.session.query(self.model).all()

    def add(self, entity: T) -> T:
        self.session.add(entity)
        self.session.commit()
        return entity

    def delete(self, entity: T) -> None:
        self.session.delete(entity)
        self.session.commit()

    def update(self) -> None:
        self.session.commit()
