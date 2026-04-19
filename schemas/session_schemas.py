from pydantic import BaseModel, Field
from datetime import date
from typing import Optional, Dict, Any

class SessionCreateSchema(BaseModel):
    title: str = Field(..., min_length=1, max_length=150)
    start_date: date
    num_days: int = Field(..., gt=0)
    exams_file: Optional[str] = None
    rooms_file: Optional[str] = None
    faculties_file: Optional[str] = None

class DraftCreateSchema(BaseModel):
    title: str
    start_date: date
    days: int
    faculties_data: Optional[Dict[str, Any]] = None
    exams_data: Optional[Dict[str, Any]] = None
    rooms_data: Optional[Dict[str, Any]] = None

class AdminStatusUpdateSchema(BaseModel):
    status: str = Field(..., pattern="^(in_progress|done)$")
