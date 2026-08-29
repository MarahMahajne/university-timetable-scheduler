from __future__ import annotations

import argparse
import json
from pathlib import Path

from excel_reader import load_courses
from output import (
    save_courses_to_excel,
    save_courses_to_json,
    save_semester_plan_to_excel,
    save_timetable_to_excel,
    save_timetable_to_json,
    save_unresolved_requirements,
)
from scheduler import create_timetable, get_requirement_report


DEFAULT_EXCEL_FILE = "courses.xlsx"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate calculated course data, a prerequisite/parallel-aware "
            "semester plan, and a weekly timetable."
        )
    )

    parser.add_argument(
        "excel_file",
        nargs="?",
        default=DEFAULT_EXCEL_FILE,
        help="Path to the input Excel file.",
    )

    parser.add_argument(
        "--time-limit",
        type=int,
        default=60,
        help="Solver time limit in seconds.",
    )

    parser.add_argument(
        "--config",
        default=None,
        help="Optional JSON file containing department teaching days/hours.",
    )

    return parser.parse_args()


def load_schedule_config(config_path: str | None) -> dict | None:
    if not config_path:
        return None

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Schedule configuration not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def main() -> None:
    args = parse_arguments()
    excel_path = Path(args.excel_file)
    availability = load_schedule_config(args.config)

    courses = load_courses(excel_path)

    courses_json_path = save_courses_to_json(courses, "courses.json")
    calculated_excel_path = save_courses_to_excel(
        courses,
        "courses_calculated.xlsx",
    )

    unresolved = get_requirement_report(courses)
    unresolved_path = save_unresolved_requirements(
        unresolved,
        "unresolved_requirements.json",
    )

    timetable = create_timetable(
        courses,
        time_limit_seconds=args.time_limit,
        availability=availability,
    )

    timetable_json_path = save_timetable_to_json(
        timetable,
        "timetable.json",
    )

    timetable_excel_path = save_timetable_to_excel(
        timetable,
        "timetable.xlsx",
    )

    semester_plan_path = save_semester_plan_to_excel(
        timetable,
        "semester_plan.xlsx",
    )

    overflow_courses = {
        item.course_id
        for item in timetable
        if item.semester_status == "OVERFLOW"
    }

    degree_years = (
        int(availability.get("degree_years", 3))
        if availability
        else 3
    )
    semesters_per_year = (
        int(availability.get("semesters_per_year", 2))
        if availability
        else 2
    )
    expected_semesters = degree_years * semesters_per_year

    print(f"Loaded {len(courses)} courses.")
    print(f"Degree years: {degree_years}")
    print(f"Semesters per year: {semesters_per_year}")
    print(f"Expected degree semesters: {expected_semesters}")
    print(f"Unresolved prerequisite/parallel references: {len(unresolved)}")
    print(
        f"Courses placed after semester {expected_semesters}: "
        f"{len(overflow_courses)}"
    )
    print()
    print(f"Created: {courses_json_path.resolve()}")
    print(f"Created: {calculated_excel_path.resolve()}")
    print(f"Created: {unresolved_path.resolve()}")
    print(f"Created: {semester_plan_path.resolve()}")
    print(f"Created: {timetable_json_path.resolve()}")
    print(f"Created: {timetable_excel_path.resolve()}")


if __name__ == "__main__":
    main()
