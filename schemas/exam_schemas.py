from pydantic import BaseModel, Field, validator
from typing import List, Optional

class DurationUpdateSchema(BaseModel):
    section_id: str = Field(..., example="CS101-01")
    duration: int = Field(..., example=120)

    @validator('duration')
    def validate_duration(cls, v):
        allowed_durations = [60, 90, 120, 150, 180]
        if v not in allowed_durations:
            raise ValueError(f"Duration must be one of {allowed_durations}")
        return v

class ExamRequirementSchema(BaseModel):
    section_id: str
    has_exam: Optional[bool] = None
    has_proctor: Optional[bool] = None
    two_rooms_needed: Optional[bool] = None
    classroom_type: Optional[str] = Field(None, pattern="^(regular|it_lab)$")

class BatchExamUpdateSchema(BaseModel):
    exams: List[ExamRequirementSchema]
