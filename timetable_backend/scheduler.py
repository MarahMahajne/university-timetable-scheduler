from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ortools.sat.python import cp_model

from models import Course


DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"]
DAY_NAMES_HE = {
    "Sunday": "ראשון",
    "Monday": "שני",
    "Tuesday": "שלישי",
    "Wednesday": "רביעי",
    "Thursday": "חמישי",
}

START_HOUR = 8
END_HOUR = 20
SLOT_MINUTES = 30
SLOTS_PER_DAY = (END_HOUR - START_HOUR) * 60 // SLOT_MINUTES


@dataclass
class CourseEvent:
    course_index: int
    event_type: str
    duration_hours: float


@dataclass
class ScheduledCourse:
    course_id: int
    name: str
    lecturer: str
    year: int
    course_type: str
    credits: float
    weekly_hours: float
    lecture_hours: float
    tutorial_hours: float
    calculation_status: str
    event_type: str
    event_duration: float
    day: str
    day_hebrew: str
    start: str
    end: str

    def to_dict(self) -> dict:
        return {
            "course_id": self.course_id,
            "name": self.name,
            "lecturer": self.lecturer,
            "year": self.year,
            "course_type": self.course_type,
            "credits": self.credits,
            "weekly_hours": self.weekly_hours,
            "lecture_hours": self.lecture_hours,
            "tutorial_hours": self.tutorial_hours,
            "calculation_status": self.calculation_status,
            "event_type": self.event_type,
            "event_duration": self.event_duration,
            "day": self.day,
            "day_hebrew": self.day_hebrew,
            "start": self.start,
            "end": self.end,
        }


def _duration_slots(hours: float, label: str) -> int:
    slots = round(hours * 60 / SLOT_MINUTES)

    if slots <= 0:
        raise ValueError(f"{label} has invalid duration: {hours}")

    represented_hours = slots * SLOT_MINUTES / 60

    if abs(represented_hours - hours) > 1e-9:
        raise ValueError(
            f"{label} duration={hours} is incompatible with "
            f"{SLOT_MINUTES}-minute slots."
        )

    return slots


def _slot_to_time(slot: int) -> str:
    base = datetime(2000, 1, 1, START_HOUR, 0)
    result = base + timedelta(minutes=slot * SLOT_MINUTES)
    return result.strftime("%H:%M")


def _build_events(courses: list[Course]) -> list[CourseEvent]:
    """Create one lecture event and, when needed, one tutorial event per course."""
    events: list[CourseEvent] = []

    for course_index, course in enumerate(courses):
        if course.lecture_hours > 0:
            events.append(
                CourseEvent(
                    course_index=course_index,
                    event_type="Lecture",
                    duration_hours=course.lecture_hours,
                )
            )

        if course.tutorial_hours > 0:
            events.append(
                CourseEvent(
                    course_index=course_index,
                    event_type="Tutorial",
                    duration_hours=course.tutorial_hours,
                )
            )

        if course.lecture_hours <= 0 and course.tutorial_hours <= 0:
            raise ValueError(
                f"Course {course.course_id} has no schedulable lecture/tutorial hours."
            )

    return events


def create_timetable(
    courses: list[Course],
    time_limit_seconds: int = 30,
) -> list[ScheduledCourse]:
    """
    Build a timetable where lecture and tutorial are separate events.

    Hard constraints:
    1. Every lecture/tutorial event is scheduled exactly once.
    2. The same lecturer cannot teach overlapping events.
    3. Events belonging to the same study year cannot overlap.

    Current assumptions:
    - The lecturer listed for the course teaches both its lecture and tutorial.
    - A course has at most one lecture event and one tutorial event per week.
    - A course's lecture/tutorial durations are calculated from credits and ש"ש.
    """
    if not courses:
        raise ValueError("No courses were loaded.")

    events = _build_events(courses)
    model = cp_model.CpModel()

    durations = [
        _duration_slots(
            event.duration_hours,
            f"Course {courses[event.course_index].course_id} {event.event_type}",
        )
        for event in events
    ]

    # x[(event, day, start_slot)] = 1 when the event starts there.
    x = {}

    for event_index, duration in enumerate(durations):
        latest_start = SLOTS_PER_DAY - duration

        if latest_start < 0:
            event = events[event_index]
            course = courses[event.course_index]
            raise ValueError(
                f"Course {course.course_id} {event.event_type} is longer than one day."
            )

        possible_starts = []

        for day_index in range(len(DAYS)):
            for start_slot in range(latest_start + 1):
                variable = model.NewBoolVar(
                    f"x_e{event_index}_d{day_index}_s{start_slot}"
                )
                x[(event_index, day_index, start_slot)] = variable
                possible_starts.append(variable)

        model.AddExactlyOne(possible_starts)

    # Lecturer conflicts.
    for lecturer in sorted({course.lecturer for course in courses}):
        lecturer_events = [
            event_index
            for event_index, event in enumerate(events)
            if courses[event.course_index].lecturer == lecturer
        ]

        for day_index in range(len(DAYS)):
            for occupied_slot in range(SLOTS_PER_DAY):
                overlapping = []

                for event_index in lecturer_events:
                    duration = durations[event_index]

                    for start_slot in range(SLOTS_PER_DAY - duration + 1):
                        if start_slot <= occupied_slot < start_slot + duration:
                            overlapping.append(
                                x[(event_index, day_index, start_slot)]
                            )

                if overlapping:
                    model.Add(sum(overlapping) <= 1)

    # Study-year conflicts: every course/event must have a place in the timetable,
    # whether the source calls it required or elective.
    for year in sorted({course.year for course in courses}):
        year_events = [
            event_index
            for event_index, event in enumerate(events)
            if courses[event.course_index].year == year
        ]

        for day_index in range(len(DAYS)):
            for occupied_slot in range(SLOTS_PER_DAY):
                overlapping = []

                for event_index in year_events:
                    duration = durations[event_index]

                    for start_slot in range(SLOTS_PER_DAY - duration + 1):
                        if start_slot <= occupied_slot < start_slot + duration:
                            overlapping.append(
                                x[(event_index, day_index, start_slot)]
                            )

                if overlapping:
                    model.Add(sum(overlapping) <= 1)

    # Soft preference: earlier days/hours.
    objective_terms = []

    for (_, day_index, start_slot), variable in x.items():
        cost = day_index * SLOTS_PER_DAY + start_slot
        objective_terms.append(variable * cost)

    model.Minimize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    solver.parameters.num_search_workers = 8

    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError(
            "No feasible timetable was found. The constraints may be too strict."
        )

    timetable: list[ScheduledCourse] = []

    for event_index, event in enumerate(events):
        course = courses[event.course_index]
        duration = durations[event_index]
        selected = None

        for day_index in range(len(DAYS)):
            for start_slot in range(SLOTS_PER_DAY - duration + 1):
                if solver.Value(x[(event_index, day_index, start_slot)]) == 1:
                    selected = (day_index, start_slot)
                    break

            if selected is not None:
                break

        if selected is None:
            raise RuntimeError(
                f"Course {course.course_id} {event.event_type} was not scheduled."
            )

        day_index, start_slot = selected
        day = DAYS[day_index]

        timetable.append(
            ScheduledCourse(
                course_id=course.course_id,
                name=course.name,
                lecturer=course.lecturer,
                year=course.year,
                course_type=course.course_type,
                credits=course.credits,
                weekly_hours=course.weekly_hours,
                lecture_hours=course.lecture_hours,
                tutorial_hours=course.tutorial_hours,
                calculation_status=course.calculation_status,
                event_type=event.event_type,
                event_duration=event.duration_hours,
                day=day,
                day_hebrew=DAY_NAMES_HE[day],
                start=_slot_to_time(start_slot),
                end=_slot_to_time(start_slot + duration),
            )
        )

    timetable.sort(
        key=lambda item: (
            DAYS.index(item.day),
            item.start,
            item.year,
            item.course_id,
            item.event_type,
        )
    )

    return timetable
