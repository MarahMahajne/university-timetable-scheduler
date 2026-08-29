from __future__ import annotations

import re
from dataclasses import dataclass

from ortools.sat.python import cp_model

from models import Course


ALL_DAYS = [
    "Sunday",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
]

DAY_NAMES_HE = {
    "Sunday": "ראשון",
    "Monday": "שני",
    "Tuesday": "שלישי",
    "Wednesday": "רביעי",
    "Thursday": "חמישי",
    "Friday": "שישי",
    "Saturday": "שבת",
}

SLOT_MINUTES = 30
DEFAULT_DEGREE_YEARS = 3
DEFAULT_SEMESTERS_PER_YEAR = 2
OVERFLOW_SEMESTERS = 2

DEFAULT_AVAILABILITY = {
    "days": {
        day: {
            "enabled": day in {"Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"},
            "start": "08:00",
            "end": "20:00",
        }
        for day in ALL_DAYS
    }
}


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
    prerequisites: str | None
    parallel_requirement: str | None
    semester: int
    semester_name: str
    semester_status: str
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
            "prerequisites": self.prerequisites,
            "parallel_requirement": self.parallel_requirement,
            "semester": self.semester,
            "semester_name": self.semester_name,
            "semester_status": self.semester_status,
            "event_type": self.event_type,
            "event_duration": self.event_duration,
            "day": self.day,
            "day_hebrew": self.day_hebrew,
            "start": self.start,
            "end": self.end,
        }


COURSE_ALIASES = {
    "לינארית 1": "אלגברה לינארית 1",
    "לינארית 2": "אלגברה לינארית 2",
}


def _normalize_course_name(name: str) -> str:
    name = str(name).replace("\u00a0", " ")
    name = re.sub(r"\s+", " ", name).strip()
    name = name.rstrip(".")
    return COURSE_ALIASES.get(name, name)


def _requirement_parts(text: str | None) -> list[str]:
    """Split Excel prerequisite/parallel cells without breaking C++."""
    if text is None:
        return []

    text = str(text).replace("\u00a0", " ").strip()

    if not text or text in {"6", "7"}:
        return []

    text = text.replace(" וגם ", " + ")
    raw_parts = re.split(r"\s+\+\s+|\s*/\s*", text)

    parts: list[str] = []
    seen: set[str] = set()

    for raw in raw_parts:
        part = _normalize_course_name(raw)
        if part and part not in seen:
            parts.append(part)
            seen.add(part)

    return parts


def _build_name_index(courses: list[Course]) -> dict[str, int]:
    return {
        _normalize_course_name(course.name): index
        for index, course in enumerate(courses)
    }


def _resolve_requirements(
    courses: list[Course],
) -> tuple[dict[int, list[int]], dict[int, list[int]], list[dict]]:
    """
    Resolve BOTH prerequisite and parallel-condition fields from the Excel.

    prerequisite:
        prerequisite semester < course semester

    parallel condition:
        parallel-course semester <= course semester
    """
    name_index = _build_name_index(courses)
    prerequisite_map: dict[int, list[int]] = {}
    parallel_map: dict[int, list[int]] = {}
    unresolved: list[dict] = []

    for course_index, course in enumerate(courses):
        prerequisite_map[course_index] = []
        parallel_map[course_index] = []

        for requirement_name in _requirement_parts(course.prerequisites):
            resolved = name_index.get(requirement_name)
            if resolved is None:
                unresolved.append({
                    "course_id": course.course_id,
                    "course": course.name,
                    "requirement_type": "prerequisite",
                    "unresolved_name": requirement_name,
                })
            elif resolved != course_index:
                prerequisite_map[course_index].append(resolved)

        for requirement_name in _requirement_parts(course.parallel_requirement):
            resolved = name_index.get(requirement_name)
            if resolved is None:
                unresolved.append({
                    "course_id": course.course_id,
                    "course": course.name,
                    "requirement_type": "parallel",
                    "unresolved_name": requirement_name,
                })
            elif resolved != course_index:
                parallel_map[course_index].append(resolved)

    return prerequisite_map, parallel_map, unresolved


def get_requirement_report(courses: list[Course]) -> list[dict]:
    _, _, unresolved = _resolve_requirements(courses)
    return unresolved


def _time_to_slot(value: str) -> int:
    """Convert HH:MM to an absolute 30-minute slot from midnight."""
    try:
        hour_text, minute_text = value.split(":")
        hour = int(hour_text)
        minute = int(minute_text)
    except (ValueError, AttributeError) as error:
        raise ValueError(f"Invalid time: {value!r}") from error

    if not (0 <= hour <= 23 and minute in {0, 30}):
        raise ValueError(
            f"Time {value!r} must use 30-minute boundaries, e.g. 08:00 or 08:30."
        )

    return (hour * 60 + minute) // SLOT_MINUTES


def _slot_to_time(slot: int) -> str:
    total_minutes = slot * SLOT_MINUTES
    hour = total_minutes // 60
    minute = total_minutes % 60
    return f"{hour:02d}:{minute:02d}"


def normalize_availability(config: dict | None) -> tuple[list[str], dict[str, tuple[int, int]]]:
    """
    Validate the frontend availability configuration.

    Expected shape:
    {
        "days": {
            "Sunday": {"enabled": true, "start": "08:00", "end": "20:00"},
            ...
        }
    }
    """
    config = config or DEFAULT_AVAILABILITY
    raw_days = config.get("days", {})

    active_days: list[str] = []
    windows: dict[str, tuple[int, int]] = {}

    for day in ALL_DAYS:
        values = raw_days.get(day, {})
        if not values.get("enabled", False):
            continue

        start = _time_to_slot(values.get("start", "08:00"))
        end = _time_to_slot(values.get("end", "20:00"))

        if start >= end:
            raise ValueError(
                f"{day}: start time must be earlier than end time."
            )

        active_days.append(day)
        windows[day] = (start, end)

    if not active_days:
        raise ValueError("At least one teaching day must be enabled.")

    return active_days, windows


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


def _build_events(courses: list[Course]) -> list[CourseEvent]:
    events: list[CourseEvent] = []

    for course_index, course in enumerate(courses):
        if course.lecture_hours > 0:
            events.append(CourseEvent(course_index, "Lecture", course.lecture_hours))

        if course.tutorial_hours > 0:
            events.append(CourseEvent(course_index, "Tutorial", course.tutorial_hours))

        if course.lecture_hours <= 0 and course.tutorial_hours <= 0:
            raise ValueError(f"Course {course.course_id} has no schedulable hours.")

    return events


def _minimum_semester_for_year(
    year: int,
    semesters_per_year: int,
) -> int:
    return (year - 1) * semesters_per_year + 1


def _semester_name(
    semester: int,
    semesters_per_year: int,
    expected_last_semester: int,
) -> str:
    if semester > expected_last_semester:
        return f"סמסטר {semester} - חריגה מהתכנית"

    year = ((semester - 1) // semesters_per_year) + 1
    semester_in_year = ((semester - 1) % semesters_per_year) + 1
    return f"שנה {year} - סמסטר {semester_in_year}"


def create_timetable(
    courses: list[Course],
    time_limit_seconds: int = 60,
    availability: dict | None = None,
) -> list[ScheduledCourse]:
    """
    Build a semester-aware timetable using the department's selected days/hours.

    Hard constraints:
      * prerequisite semester < course semester
      * parallel-condition semester <= course semester
      * every lecture/tutorial is scheduled once
      * same lecturer cannot overlap
      * courses in the same curriculum semester cannot overlap
      * every event must fit entirely inside one enabled department time window
    """
    if not courses:
        raise ValueError("No courses were loaded.")

    active_days, day_windows = normalize_availability(availability)
    day_rank = {day: index for index, day in enumerate(active_days)}

    availability = availability or {}
    degree_years = int(
        availability.get("degree_years", DEFAULT_DEGREE_YEARS)
    )
    semesters_per_year = int(
        availability.get(
            "semesters_per_year",
            DEFAULT_SEMESTERS_PER_YEAR,
        )
    )

    if degree_years < 1:
        raise ValueError("degree_years must be at least 1.")

    if semesters_per_year < 1:
        raise ValueError("semesters_per_year must be at least 1.")

    max_excel_year = max(course.year for course in courses)

    if max_excel_year > degree_years:
        raise ValueError(
            f"The Excel contains a course from study year {max_excel_year}, "
            f"but the selected degree has only {degree_years} years."
        )

    expected_last_semester = degree_years * semesters_per_year
    max_semester = expected_last_semester + OVERFLOW_SEMESTERS

    events = _build_events(courses)
    prerequisite_map, parallel_map, unresolved = _resolve_requirements(courses)

    if unresolved:
        print(
            f"WARNING: {len(unresolved)} prerequisite/parallel references "
            "could not be matched. See unresolved_requirements.json."
        )

    model = cp_model.CpModel()

    # 1. Course -> semester variables.
    semester_choice: dict[tuple[int, int], cp_model.IntVar] = {}

    for course_index, course in enumerate(courses):
        min_semester = _minimum_semester_for_year(course.year, semesters_per_year)
        allowed = []

        for semester in range(min_semester, max_semester + 1):
            variable = model.NewBoolVar(f"semester_c{course_index}_s{semester}")
            semester_choice[(course_index, semester)] = variable
            allowed.append(variable)

        model.AddExactlyOne(allowed)

    def semester_expression(course_index: int):
        course = courses[course_index]
        min_semester = _minimum_semester_for_year(course.year, semesters_per_year)
        return sum(
            semester * semester_choice[(course_index, semester)]
            for semester in range(min_semester, max_semester + 1)
        )

    # 2. Prerequisites AND parallel conditions from the Excel.
    for course_index, prerequisite_indexes in prerequisite_map.items():
        for prerequisite_index in prerequisite_indexes:
            model.Add(
                semester_expression(prerequisite_index)
                < semester_expression(course_index)
            )

    for course_index, parallel_indexes in parallel_map.items():
        for parallel_index in parallel_indexes:
            model.Add(
                semester_expression(parallel_index)
                <= semester_expression(course_index)
            )

    # 3. Event placement variables.
    durations = [
        _duration_slots(
            event.duration_hours,
            f"Course {courses[event.course_index].course_id} {event.event_type}",
        )
        for event in events
    ]

    # x[(event, semester, day, absolute_start_slot)] = 1
    x: dict[tuple[int, int, str, int], cp_model.IntVar] = {}

    for event_index, event in enumerate(events):
        course = courses[event.course_index]
        duration = durations[event_index]
        min_semester = _minimum_semester_for_year(course.year, semesters_per_year)

        event_has_any_placement = False

        for semester in range(min_semester, max_semester + 1):
            placements_in_semester = []

            for day in active_days:
                day_start, day_end = day_windows[day]
                latest_start = day_end - duration

                if latest_start < day_start:
                    continue

                event_has_any_placement = True

                for start_slot in range(day_start, latest_start + 1):
                    variable = model.NewBoolVar(
                        f"x_e{event_index}_sem{semester}_{day}_t{start_slot}"
                    )
                    x[(event_index, semester, day, start_slot)] = variable
                    placements_in_semester.append(variable)

            if not placements_in_semester:
                # If a course chooses this semester, it would be impossible to place.
                model.Add(semester_choice[(event.course_index, semester)] == 0)
            else:
                model.Add(
                    sum(placements_in_semester)
                    == semester_choice[(event.course_index, semester)]
                )

        if not event_has_any_placement:
            raise ValueError(
                f"{course.name} {event.event_type} ({event.duration_hours} hours) "
                "does not fit inside any enabled teaching-day window."
            )

    # Utility: variables that cover one occupied absolute slot.
    def covering_variables(
        event_indexes: list[int], semester: int, day: str, occupied_slot: int
    ) -> list[cp_model.IntVar]:
        result = []

        for event_index in event_indexes:
            event = events[event_index]
            course = courses[event.course_index]
            if semester < _minimum_semester_for_year(course.year, semesters_per_year):
                continue

            duration = durations[event_index]
            day_start, day_end = day_windows[day]
            latest_start = day_end - duration

            for start_slot in range(day_start, latest_start + 1):
                variable = x.get((event_index, semester, day, start_slot))
                if variable is not None and start_slot <= occupied_slot < start_slot + duration:
                    result.append(variable)

        return result

    # 4a. Same lecturer cannot overlap.
    all_event_indexes = list(range(len(events)))

    for lecturer in sorted({course.lecturer for course in courses}):
        lecturer_events = [
            event_index
            for event_index, event in enumerate(events)
            if courses[event.course_index].lecturer == lecturer
        ]

        for semester in range(1, max_semester + 1):
            for day in active_days:
                day_start, day_end = day_windows[day]
                for occupied_slot in range(day_start, day_end):
                    overlapping = covering_variables(
                        lecturer_events, semester, day, occupied_slot
                    )
                    if overlapping:
                        model.Add(sum(overlapping) <= 1)

    # 4b. All courses in the same semester, including electives, get a place.
    for semester in range(1, max_semester + 1):
        for day in active_days:
            day_start, day_end = day_windows[day]
            for occupied_slot in range(day_start, day_end):
                overlapping = covering_variables(
                    all_event_indexes, semester, day, occupied_slot
                )
                if overlapping:
                    model.Add(sum(overlapping) <= 1)

    # 5. Objective: prefer normal semesters, earlier teaching days, earlier times.
    objective_terms = []

    for course_index, course in enumerate(courses):
        min_semester = _minimum_semester_for_year(course.year, semesters_per_year)

        for semester in range(min_semester, max_semester + 1):
            delay = semester - min_semester
            cost = delay * 500
            if semester > expected_last_semester:
                cost += (semester - expected_last_semester) * 10000

            objective_terms.append(
                semester_choice[(course_index, semester)] * cost
            )

    for (event_index, semester, day, start_slot), variable in x.items():
        day_start, _ = day_windows[day]
        placement_cost = day_rank[day] * 20 + (start_slot - day_start)
        objective_terms.append(variable * placement_cost)

    model.Minimize(sum(objective_terms))

    # 6. Call solver.
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    solver.parameters.num_search_workers = 8

    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError(
            "No feasible timetable was found for the selected department days/hours. "
            "Try enabling another day, extending the hours, or checking prerequisite chains."
        )

    # 7. Read selected semesters.
    course_semesters: dict[int, int] = {}

    for course_index, course in enumerate(courses):
        min_semester = _minimum_semester_for_year(course.year, semesters_per_year)
        selected_semester = None

        for semester in range(min_semester, max_semester + 1):
            if solver.Value(semester_choice[(course_index, semester)]) == 1:
                selected_semester = semester
                break

        if selected_semester is None:
            raise RuntimeError(f"No semester chosen for {course.name}.")

        course_semesters[course_index] = selected_semester

    # 8. Read event placements.
    timetable: list[ScheduledCourse] = []

    for event_index, event in enumerate(events):
        course = courses[event.course_index]
        semester = course_semesters[event.course_index]
        selected: tuple[str, int] | None = None

        for day in active_days:
            day_start, day_end = day_windows[day]
            duration = durations[event_index]
            latest_start = day_end - duration

            for start_slot in range(day_start, latest_start + 1):
                variable = x.get((event_index, semester, day, start_slot))
                if variable is not None and solver.Value(variable) == 1:
                    selected = (day, start_slot)
                    break

            if selected is not None:
                break

        if selected is None:
            raise RuntimeError(f"{course.name} {event.event_type} was not scheduled.")

        day, start_slot = selected
        duration = durations[event_index]

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
                prerequisites=course.prerequisites,
                parallel_requirement=course.parallel_requirement,
                semester=semester,
                semester_name=_semester_name(
                    semester,
                    semesters_per_year,
                    expected_last_semester,
                ),
                semester_status=(
                    "ON_PLAN" if semester <= expected_last_semester else "OVERFLOW"
                ),
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
            item.semester,
            day_rank[item.day],
            item.start,
            item.course_id,
            item.event_type,
        )
    )

    return timetable
