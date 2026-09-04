from __future__ import annotations

import json
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, url_for


app = Flask(__name__)
app.secret_key = "local-timetable-development-key"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "timetable_backend"
TIMETABLE_FILE = BACKEND_DIR / "timetable.json"
CONFIG_FILE = BACKEND_DIR / "schedule_config.json"

ALL_DAYS = [
    {"key": "Sunday", "hebrew": "ראשון"},
    {"key": "Monday", "hebrew": "שני"},
    {"key": "Tuesday", "hebrew": "שלישי"},
    {"key": "Wednesday", "hebrew": "רביעי"},
    {"key": "Thursday", "hebrew": "חמישי"},
    {"key": "Friday", "hebrew": "שישי"},
    {"key": "Saturday", "hebrew": "שבת"},
]

DEFAULT_CONFIG = {
    "degree_years": 3,
    "semesters_per_year": 2,
    "days": {
        day["key"]: {
            "enabled": day["key"] in {
                "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"
            },
            "start": "08:00",
            "end": "20:00",
        }
        for day in ALL_DAYS
    },
}


def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_config(config: dict) -> None:
    with CONFIG_FILE.open("w", encoding="utf-8") as file:
        json.dump(config, file, ensure_ascii=False, indent=4)


def load_timetable() -> list[dict]:
    return load_json(TIMETABLE_FILE, [])


def load_config() -> dict:
    config = load_json(CONFIG_FILE, DEFAULT_CONFIG)
    config.setdefault("degree_years", DEFAULT_CONFIG["degree_years"])
    config.setdefault(
        "semesters_per_year",
        DEFAULT_CONFIG["semesters_per_year"],
    )
    config.setdefault("days", {})

    for day in ALL_DAYS:
        config["days"].setdefault(
            day["key"],
            DEFAULT_CONFIG["days"][day["key"]].copy(),
        )

    return config


def time_to_minutes(value: str) -> int:
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def validate_form_config(config: dict) -> None:
    degree_years = int(config["degree_years"])
    semesters_per_year = int(config["semesters_per_year"])

    if degree_years < 1 or degree_years > 10:
        raise ValueError("מספר שנות התואר חייב להיות בין 1 ל־10.")

    if semesters_per_year < 1 or semesters_per_year > 4:
        raise ValueError("מספר הסמסטרים בשנה חייב להיות בין 1 ל־4.")

    enabled_count = 0

    for day in ALL_DAYS:
        values = config["days"][day["key"]]

        if not values["enabled"]:
            continue

        enabled_count += 1
        start = time_to_minutes(values["start"])
        end = time_to_minutes(values["end"])

        if start >= end:
            raise ValueError(
                f'ביום {day["hebrew"]} שעת ההתחלה חייבת להיות לפני שעת הסיום.'
            )

        if start % 15 != 0 or end % 15 != 0:
            raise ValueError("השעות חייבות להיות בקפיצות של 15 דקות.")

    if enabled_count == 0:
        raise ValueError("יש לבחור לפחות יום לימודים אחד.")



def config_from_form() -> dict:
    config = {
        "degree_years": int(request.form.get("degree_years", 3)),
        "semesters_per_year": int(
            request.form.get("semesters_per_year", 2)
        ),
        "days": {},
    }

    for day in ALL_DAYS:
        key = day["key"]
        enabled = request.form.get(f"enabled_{key}") == "on"
        start = request.form.get(f"start_{key}", "08:00")
        end = request.form.get(f"end_{key}", "20:00")

        config["days"][key] = {
            "enabled": enabled,
            "start": start,
            "end": end,
        }

    validate_form_config(config)
    return config



def run_backend() -> tuple[bool, str]:
    command = [
        sys.executable,
        "main.py",
        "--config",
        CONFIG_FILE.name,
    ]

    try:
        result = subprocess.run(
            command,
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return False, "ה־Solver עבר את מגבלת הזמן של 3 דקות."

    if result.returncode != 0:
        error_text = (result.stderr or result.stdout).strip()
        return False, error_text[-3000:] if error_text else "שגיאה לא ידועה."

    return True, result.stdout.strip()


def active_days_from_config(config: dict) -> list[dict]:
    return [
        day
        for day in ALL_DAYS
        if config["days"].get(day["key"], {}).get("enabled", False)
    ]


def enrich_for_calendar(
    timetable: list[dict], config: dict
) -> tuple[list[dict], list[str]]:
    active_days = active_days_from_config(config)
    active_keys = [day["key"] for day in active_days]

    if not active_days:
        return timetable, []

    starts = [
        time_to_minutes(config["days"][day["key"]]["start"])
        for day in active_days
    ]
    ends = [
        time_to_minutes(config["days"][day["key"]]["end"])
        for day in active_days
    ]

    display_start = min(starts)
    display_end = max(ends)

    slot_labels = []
    minute = display_start
    while minute < display_end:
        slot_labels.append(f"{minute // 60:02d}:{minute % 60:02d}")
        minute += 15

    day_columns = {key: index + 2 for index, key in enumerate(active_keys)}

    enriched = []
    for item in timetable:
        copied = dict(item)
        if item["day"] in day_columns:
            start = time_to_minutes(item["start"])
            end = time_to_minutes(item["end"])
            copied["grid_column"] = day_columns[item["day"]]
            copied["grid_row"] = ((start - display_start) // 15) + 2
            copied["grid_span"] = max(1, (end - start) // 15)
        enriched.append(copied)

    return enriched, slot_labels


@app.route("/", methods=["GET"])
def setup_page():
    """First page: choose department teaching days and allowed hours."""
    return render_template(
        "setup.html",
        all_days=ALL_DAYS,
        config=load_config(),
    )


@app.route("/generate", methods=["POST"])
def generate_timetable():
    """Save the user input, run the solver, then show the generated calendar."""
    try:
        config = config_from_form()
        save_config(config)

        success, details = run_backend()
        if not success:
            flash(
                "לא הצלחנו ליצור מערכת שעומדת בכל האילוצים הקשיחים. "
                "יש לבדוק את מספר הסמסטרים, קורסי הקדם/המקבילה, "
                "הימים ושעות הפעילות.\n\n" + details,
                "error",
            )
            return redirect(url_for("setup_page"))

        flash("המערכת נבנתה בהצלחה על ידי ה־Solver.", "success")
        return redirect(url_for("calendar_page"))

    except Exception as error:
        flash(str(error), "error")
        return redirect(url_for("setup_page"))


@app.route("/calendar", methods=["GET"])
def calendar_page():
    """Second page: display only the timetable created from the selected input."""
    timetable = load_timetable()
    config = load_config()
    active_days = active_days_from_config(config)
    timetable, slot_labels = enrich_for_calendar(timetable, config)

    semesters_per_year = int(config.get("semesters_per_year", 2))
    degree_years = int(config.get("degree_years", 3))
    total_configured_semesters = degree_years * semesters_per_year

    # HARD degree structure:
    # always display exactly the semesters chosen by the user, even if a
    # solver/data error ever produced an empty semester.
    semesters = []

    for semester_number in range(1, total_configured_semesters + 1):
        academic_year = (
            (semester_number - 1) // semesters_per_year
        ) + 1
        semester_in_year = (
            (semester_number - 1) % semesters_per_year
        ) + 1

        semesters.append((
            semester_number,
            f"שנה {academic_year} - סמסטר {semester_in_year}",
        ))

    unique_courses = len({item["course_id"] for item in timetable})

    # Data for the "all semesters" overview.
    # The degree overview is organized by YEAR.  Inside every year we show
    # one compact weekly timetable for each semester, preserving the same
    # day/time layout as the normal single-semester view.
    semester_groups = []

    for semester_number, semester_name in semesters:
        semester_events = [
            item
            for item in timetable
            if int(item["semester"]) == semester_number
        ]

        academic_year = (
            (semester_number - 1) // semesters_per_year
        ) + 1
        semester_in_year = (
            (semester_number - 1) % semesters_per_year
        ) + 1

        semester_groups.append({
            "number": semester_number,
            "name": semester_name,
            "year": academic_year,
            "semester_in_year": semester_in_year,
            "status": (
                "OVERFLOW"
                if any(
                    event.get("semester_status") == "OVERFLOW"
                    for event in semester_events
                )
                else "ON_PLAN"
            ),
            "events": semester_events,
            "course_count": len({
                event["course_id"] for event in semester_events
            }),
        })

    year_groups = []

    maximum_year = max(
        [degree_years]
        + [group["year"] for group in semester_groups]
    )

    for academic_year in range(1, maximum_year + 1):
        year_semesters = [
            group
            for group in semester_groups
            if group["year"] == academic_year
        ]

        if year_semesters:
            year_groups.append({
                "year": academic_year,
                "semesters": year_semesters,
            })

    return render_template(
        "calendar.html",
        timetable=timetable,
        days=active_days,
        config=config,
        semesters=semesters,
        unique_courses=unique_courses,
        slot_labels=slot_labels,
        semester_groups=semester_groups,
        year_groups=year_groups,
        semesters_per_year=semesters_per_year,
    )


def open_browser() -> None:
    webbrowser.open_new("http://127.0.0.1:5000/")


if __name__ == "__main__":
    threading.Timer(1.0, open_browser).start()
    app.run(host="127.0.0.1", port=5000, debug=False)
