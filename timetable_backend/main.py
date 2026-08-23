from __future__ import annotations

import argparse
from pathlib import Path

from excel_reader import load_courses
from output import (
    save_courses_to_excel,
    save_courses_to_json,
    save_timetable_to_excel,
    save_timetable_to_json,
)
from scheduler import create_timetable


DEFAULT_EXCEL_FILE = "courses.xlsx"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate calculated course data and a university timetable."
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
        default=30,
        help="Solver time limit in seconds.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    excel_path = Path(args.excel_file)

    # 1. Read the original Excel file and calculate lecture/tutorial hours
    courses = load_courses(excel_path)

    # 2. Save the parsed/calculated course information
    courses_json_path = save_courses_to_json(
        courses,
        "courses.json",
    )

    calculated_excel_path = save_courses_to_excel(
        courses,
        "courses_calculated.xlsx",
    )

    # 3. Run the scheduler
    timetable = create_timetable(
        courses,
        time_limit_seconds=args.time_limit,
    )

    # 4. Save timetable outputs
    timetable_json_path = save_timetable_to_json(
        timetable,
        "timetable.json",
    )

    timetable_excel_path = save_timetable_to_excel(
        timetable,
        "timetable.xlsx",
    )

    print(f"Loaded {len(courses)} courses.")
    print(f"Created: {courses_json_path.resolve()}")
    print(f"Created: {calculated_excel_path.resolve()}")
    print(f"Created: {timetable_json_path.resolve()}")
    print(f"Created: {timetable_excel_path.resolve()}")


if __name__ == "__main__":
    main()
