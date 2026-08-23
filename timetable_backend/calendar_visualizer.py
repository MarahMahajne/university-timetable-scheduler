from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


DAYS = [
    ("Sunday", "ראשון"),
    ("Monday", "שני"),
    ("Tuesday", "שלישי"),
    ("Wednesday", "רביעי"),
    ("Thursday", "חמישי"),
]

START_HOUR = 8
END_HOUR = 20


def time_to_float(value: str) -> float:
    """Convert HH:MM into a decimal hour, for example 09:30 -> 9.5."""
    hour, minute = map(int, value.split(":"))
    return hour + minute / 60


def load_timetable(json_path: str | Path) -> list[dict]:
    json_path = Path(json_path)

    if not json_path.exists():
        raise FileNotFoundError(f"Timetable file not found: {json_path}")

    with json_path.open("r", encoding="utf-8") as file:
        timetable = json.load(file)

    if not isinstance(timetable, list):
        raise ValueError("The timetable JSON must contain a list of courses.")

    return timetable


def validate_timetable(timetable: list[dict]) -> list[str]:
    """
    Validate the important hard constraints.

    Returns a list of error messages. An empty list means that all checks passed.
    """
    errors: list[str] = []

    def overlaps(first: dict, second: dict) -> bool:
        return (
            first["day"] == second["day"]
            and time_to_float(first["start"]) < time_to_float(second["end"])
            and time_to_float(second["start"]) < time_to_float(first["end"])
        )

    course_ids = [course["course_id"] for course in timetable]

    if len(course_ids) != len(set(course_ids)):
        errors.append("The timetable contains duplicate course IDs.")

    for course in timetable:
        actual_duration = (
            time_to_float(course["end"]) - time_to_float(course["start"])
        )

        if abs(actual_duration - float(course["weekly_hours"])) > 0.001:
            errors.append(
                f'Course {course["course_id"]}: duration does not match weekly_hours.'
            )

        if (
            time_to_float(course["start"]) < START_HOUR
            or time_to_float(course["end"]) > END_HOUR
        ):
            errors.append(
                f'Course {course["course_id"]}: outside the allowed hours.'
            )

    for index, first in enumerate(timetable):
        for second in timetable[index + 1:]:
            if not overlaps(first, second):
                continue

            if first["lecturer"] == second["lecturer"]:
                errors.append(
                    "Lecturer conflict: "
                    f'{first["lecturer"]} teaches '
                    f'{first["name"]} and {second["name"]} at the same time.'
                )

            if first["year"] == second["year"]:
                errors.append(
                    "Year conflict: "
                    f'Year {first["year"]} has '
                    f'{first["name"]} and {second["name"]} at the same time.'
                )

    return errors


def draw_year_calendar(
    timetable: list[dict],
    year: int,
    output_path: str | Path,
) -> Path:
    """Create one human-readable weekly calendar image for a study year."""
    output_path = Path(output_path)
    year_courses = [course for course in timetable if course["year"] == year]

    figure, axis = plt.subplots(figsize=(15, 10))

    axis.set_xlim(0, len(DAYS))
    axis.set_ylim(END_HOUR, START_HOUR)

    axis.set_xticks(
        [index + 0.5 for index in range(len(DAYS))],
        [hebrew_name for _, hebrew_name in DAYS],
    )
    axis.set_yticks(range(START_HOUR, END_HOUR + 1))
    axis.set_ylabel("Time")
    axis.set_title(f"Weekly Timetable - Year {year}")

    axis.grid(True)

    day_positions = {
        english_name: index
        for index, (english_name, _) in enumerate(DAYS)
    }

    for course in year_courses:
        day_x = day_positions[course["day"]]
        start = time_to_float(course["start"])
        end = time_to_float(course["end"])
        duration = end - start

        rectangle = Rectangle(
            (day_x + 0.05, start),
            0.9,
            duration,
            alpha=0.65,
        )
        axis.add_patch(rectangle)

        course_text = (
            f'{course["name"]}\n'
            f'{course["start"]}-{course["end"]}\n'
            f'{course["lecturer"]}'
        )

        axis.text(
            day_x + 0.5,
            start + duration / 2,
            course_text,
            ha="center",
            va="center",
            fontsize=8,
            wrap=True,
        )

    figure.tight_layout()
    figure.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(figure)

    return output_path


def create_all_calendars(
    json_path: str | Path = "timetable.json",
    output_folder: str | Path = "calendar_images",
) -> None:
    timetable = load_timetable(json_path)
    errors = validate_timetable(timetable)

    if errors:
        print("The timetable has validation errors:")

        for error in errors:
            print(f"- {error}")

        raise RuntimeError("Calendar images were not created.")

    print("Validation passed:")
    print("- No lecturer conflicts")
    print("- No same-year conflicts")
    print("- Course durations are correct")
    print("- All courses are inside the allowed hours")

    output_folder = Path(output_folder)
    output_folder.mkdir(parents=True, exist_ok=True)

    years = sorted({int(course["year"]) for course in timetable})

    for year in years:
        output_path = output_folder / f"timetable_year_{year}.png"
        draw_year_calendar(timetable, year, output_path)
        print(f"Created: {output_path.resolve()}")


if __name__ == "__main__":
    create_all_calendars()
