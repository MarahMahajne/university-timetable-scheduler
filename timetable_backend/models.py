from dataclasses import asdict, dataclass
from typing import Optional


@dataclass
class Course:
    course_id: int
    name: str
    prerequisites: Optional[str]
    parallel_requirement: Optional[str]
    year: int
    lecturer: str
    credits: float
    weekly_hours: float
    course_type: str
    lecture_hours: float
    tutorial_hours: float
    calculation_status: str

    def to_dict(self) -> dict:
        return asdict(self)
