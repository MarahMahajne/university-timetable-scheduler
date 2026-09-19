from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import webbrowser
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
    send_file,
)
from flask_babel import Babel, gettext as _
from werkzeug.utils import secure_filename


app = Flask(__name__)
# Fixed on purpose: this app is a local, single-user tool, and the language
# choice is meant to behave like any other saved preference — pick it once
# via the switcher and it stays picked across restarts, rather than
# resetting to the default every time the app is relaunched. A fixed key
# keeps the session cookie (and therefore the language choice) valid across
# runs. If this app is ever exposed beyond localhost, generate this from an
# environment variable instead of hardcoding it.
app.secret_key = "local-timetable-development-key"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = Path(__file__).resolve().parent
BACKEND_DIR = PROJECT_ROOT / "timetable_backend"
TIMETABLE_FILE = BACKEND_DIR / "timetable.json"
CONFIG_FILE = BACKEND_DIR / "schedule_config.json"
CONTENT_TRANSLATIONS_FILE = FRONTEND_DIR / "course_translations.json"
COURSES_FILE = BACKEND_DIR / "courses.json"
COURSES_EXCEL_FILE = BACKEND_DIR / "courses.xlsx"
SYSTEM_SNAPSHOT_FILE = BACKEND_DIR / "system_snapshot.json"
UNRESOLVED_FILE = BACKEND_DIR / "unresolved_requirements.json"
SEMESTER_PLAN_FILE = BACKEND_DIR / "semester_plan.xlsx"
CURRICULUM_PLAN_FILE = BACKEND_DIR / "curriculum_plan.json"

# The natural-language interpreter lives beside the solver. Import it only
# after BACKEND_DIR is known so the frontend and backend remain separate apps.
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
from ai_constraint_interpreter import claude_is_configured, interpret_with_claude

# ---------------------------------------------------------------------------
# Internationalization (Flask-Babel)
# ---------------------------------------------------------------------------
# The UI text lives in Hebrew source strings (wrapped in `_()` in the
# templates/Python below); an English catalog under translations/en supplies
# the English text. A Hebrew catalog is included too for completeness, even
# though missing entries would simply fall back to the Hebrew msgid.

app.config["LANGUAGES"] = {"he": "עברית", "en": "English"}
app.config["BABEL_DEFAULT_LOCALE"] = "he"
app.config["BABEL_TRANSLATION_DIRECTORIES"] = "translations"


def select_locale() -> str:
    return session.get("lang", app.config["BABEL_DEFAULT_LOCALE"])


babel = Babel(app, locale_selector=select_locale)


@app.context_processor
def inject_i18n():
    current_lang = select_locale()
    return {
        "current_lang": current_lang,
        "text_direction": "rtl" if current_lang == "he" else "ltr",
        "languages": app.config["LANGUAGES"],
        "day_label": lambda day_key: day_label_for(day_key, current_lang),
    }


@app.route("/language/<lang_code>")
def set_language(lang_code: str):
    if lang_code in app.config["LANGUAGES"]:
        session["lang"] = lang_code
    return redirect(request.referrer or url_for("setup_page"))


ALL_DAYS = [
    {"key": "Sunday", "hebrew": "ראשון"},
    {"key": "Monday", "hebrew": "שני"},
    {"key": "Tuesday", "hebrew": "שלישי"},
    {"key": "Wednesday", "hebrew": "רביעי"},
    {"key": "Thursday", "hebrew": "חמישי"},
    {"key": "Friday", "hebrew": "שישי"},
    {"key": "Saturday", "hebrew": "שבת"},
]
DAY_LOOKUP = {day["key"]: day for day in ALL_DAYS}

DEFAULT_CONFIG = {
    "degree_years": 3,
    "semesters_per_year": 2,
    "allow_semester_b_start": False,
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


def day_label_for(day_key: str, locale: str) -> str:
    """English day names are already the dict keys, so English needs no
    lookup; Hebrew pulls the matching label from ALL_DAYS."""
    day = DAY_LOOKUP.get(day_key)
    if not day:
        return day_key
    return day_key if locale == "en" else day["hebrew"]


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


def load_content_translations() -> dict:
    return load_json(CONTENT_TRANSLATIONS_FILE, {})


def load_config() -> dict:
    config = load_json(CONFIG_FILE, DEFAULT_CONFIG)
    config.setdefault("degree_years", DEFAULT_CONFIG["degree_years"])
    config.setdefault(
        "semesters_per_year",
        DEFAULT_CONFIG["semesters_per_year"],
    )
    config.setdefault("allow_semester_b_start", DEFAULT_CONFIG["allow_semester_b_start"])
    config.setdefault("days", {})
    config.setdefault("ai_constraints", [])
    config.setdefault("ai_chat", [])
    config.setdefault("lecturer_availability", {})

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
        raise ValueError(_("The number of years in the degree must be between 1 and 10."))

    if semesters_per_year < 1 or semesters_per_year > 4:
        raise ValueError(_("The number of semesters per year must be between 1 and 4."))

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
                _(
                    "On %(day)s the start time must be before the end time.",
                    day=day_label_for(day["key"], select_locale()),
                )
            )

        if start % 5 != 0 or end % 5 != 0:
            raise ValueError(_("Hours must be in 5-minute increments."))

    if enabled_count == 0:
        raise ValueError(_("You must select at least one teaching day."))



def config_from_form() -> dict:
    previous = load_config()
    config = {
        "degree_years": int(request.form.get("degree_years", 3)),
        "semesters_per_year": int(
            request.form.get("semesters_per_year", 2)
        ),
        "allow_semester_b_start": request.form.get("allow_semester_b_start") == "on",
        "days": {},
        # Keep natural-language requirements when the department changes
        # general availability settings.
        "ai_constraints": previous.get("ai_constraints", []),
        "ai_chat": previous.get("ai_chat", []),
        "lecturer_availability": previous.get("lecturer_availability", {}),
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
        return False, _("The solver exceeded the 3-minute time limit.")

    if result.returncode != 0:
        error_text = (result.stderr or result.stdout).strip()
        return False, error_text[-3000:] if error_text else _("Unknown error.")

    return True, result.stdout.strip()


def active_days_from_config(config: dict) -> list[dict]:
    return [
        day
        for day in ALL_DAYS
        if config["days"].get(day["key"], {}).get("enabled", False)
    ]


def semester_display_name(academic_year: int, semester_in_year: int) -> str:
    return _(
        "Year %(year)s - Semester %(semester)s",
        year=academic_year,
        semester=semester_in_year,
    )


def localize_event(event: dict, locale: str, content_translations: dict) -> dict:
    localized = dict(event)

    if locale == "en":
        overlay = content_translations.get(str(event["course_id"]))
        if overlay:
            localized["name"] = overlay.get("name", event["name"])
            localized["lecturer"] = overlay.get("lecturer", event["lecturer"])
            if event.get("prerequisites"):
                localized["prerequisites"] = overlay.get(
                    "prerequisites", event["prerequisites"]
                )
            if event.get("parallel_requirement"):
                localized["parallel_requirement"] = overlay.get(
                    "parallel_requirement", event["parallel_requirement"]
                )

    localized["event_type_label"] = (
        _("Tutorial") if event.get("event_type") == "Tutorial" else _("Lecture")
    )
    localized["day_label"] = day_label_for(event["day"], locale)
    return localized


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
    """Department dashboard: AI requests, constraints and course data."""
    config = load_config()
    catalog = load_course_catalog()
    locale = select_locale()
    if locale == "en":
        core_constraints = [
            {"title": "Prerequisites", "description": "A prerequisite course must be scheduled in an earlier semester than the dependent course.", "hard": True},
            {"title": "Parallel requirements", "description": "A parallel requirement must be scheduled in the same semester or an earlier semester.", "hard": True},
            {"title": "Every event is scheduled", "description": "Every required lecture and tutorial is placed exactly once in the semester selected for its course.", "hard": True},
            {"title": "Lecturer conflicts", "description": "The same lecturer cannot teach two events at the same time.", "hard": True},
            {"title": "Required/general course conflicts", "description": "Required (core) and general courses cannot overlap any other course. General courses follow the same overlap rule as required courses.", "hard": True},
            {"title": "Elective, seminar and project overlap", "description": "Electives and seminars may overlap each other with a soft penalty. Projects may overlap only other projects. Every other category combination is forbidden.", "hard": False},
            {"title": "Department and lecturer availability", "description": "Every event must fit inside the department hours and, when configured, inside that lecturer’s own working days and hours.", "hard": True},
            {"title": "Strategic cohort-A curriculum", "description": "An explicit original semester from Excel is fixed. Otherwise cohort A is placed strategically inside the catalog study year so gateway courses can move to shared semesters with cohort B, while flexible non-blocking courses are preferred early for A and may be completed by B in the continuation semester.", "hard": True},
            {"title": "Comfort-first semester and day balance", "description": "The solver strongly avoids empty or overloaded semesters, minimizes the heaviest semester, then smooths the load across teaching days. It searches for a comfortable timetable, not merely the first feasible one.", "hard": False},
            {"title": "Preferred break between classes", "description": "A 5–30 minute break between consecutive classes is preferred. Back-to-back classes and breaks longer than 30 minutes receive a soft penalty. There is no lecture/tutorial ordering or closeness rule.", "hard": False},
        ]
    else:
        core_constraints = [
            {"title": "קורסי קדם", "description": "קורס קדם חייב להיקבע בסמסטר מוקדם יותר מהקורס שתלוי בו.", "hard": True},
            {"title": "דרישות מקבילות", "description": "קורס שמוגדר כדרישה מקבילה חייב להיקבע באותו סמסטר או בסמסטר מוקדם יותר.", "hard": True},
            {"title": "שיבוץ כל המפגשים", "description": "כל הרצאה וכל תרגול נדרשים משובצים פעם אחת בדיוק בסמסטר שנבחר עבור הקורס.", "hard": True},
            {"title": "מניעת חפיפות למרצה", "description": "אותו מרצה לא יכול ללמד שני מפגשים באותה שעה.", "hard": True},
            {"title": "מניעת חפיפות חובה וכללי", "description": "קורס חובה וקורס כללי אינם יכולים לחפוף לשום קורס אחר. מבחינת חפיפות, קורס כללי מתנהג בדיוק כמו קורס חובה.", "hard": True},
            {"title": "חפיפות בחירה, סמינרים ופרויקטים", "description": "בחירה עם בחירה, בחירה עם סמינר, וסמינר עם סמינר יכולים לחפוף עם קנס רך. פרויקט יכול לחפוף רק לפרויקט אחר. כל שילוב אחר אסור.", "hard": False},
            {"title": "זמינות המחלקה והמרצים", "description": "כל מפגש חייב להיכנס בשעות הפעילות של המחלקה, ואם הוגדרו שעות אישיות למרצה — גם בתוך ימי ושעות העבודה של אותו מרצה.", "hard": True},
            {"title": "תכנון אסטרטגי של מחזור א׳", "description": "אם הוגדר סמסטר מקורי ב-Excel הוא נשמר בדיוק. אחרת הקורס נשאר בתוך שנת הלימוד שלו, אבל הסמסטר נבחר אסטרטגית: קורסי שער/קדם מרכזיים מועדפים בסמסטר שבו A ו-B יכולים ללמוד יחד, וקורסים גמישים ולא-חוסמים מועדפים מוקדם עבור A ויכולים להידחות לסמסטר ההמשך של B.", "hard": True},
            {"title": "איזון נוחות בין סמסטרים וימים", "description": "הפותר נותן עדיפות חזקה למניעת סמסטרים ריקים או עמוסים מאוד, ממזער את העומס בסמסטר הכבד ביותר ורק אחר כך מאזן את העומס בין ימי הלימוד. המטרה היא מערכת נוחה ולא רק פתרון אפשרי.", "hard": False},
            {"title": "הפסקה מועדפת בין שיעורים", "description": "מועדפת הפסקה של 5–30 דקות בין שיעורים רצופים. שיעורים ללא הפסקה והפסקות ארוכות מ־30 דקות מקבלים קנס רך. אין שום אילוץ על הסדר או הקרבה בין הרצאה לתרגול.", "hard": False},
        ]
    if config.get("allow_semester_b_start", False):
        if locale == "en":
            core_constraints.append({
                "title": "Fixed cohort A and flexible semester-B intake",
                "description": "Cohort A is frozen after the curriculum-planning stage. Cohort B starts one semester later and is the flexible cohort. Year 4 Semester 1 belongs only to B's planned path.",
                "hard": True,
            })
            core_constraints.append({
                "title": "Maximize A+B sharing and minimize B-only repeats",
                "description": "For every B course, the solver first tries to join an existing A offering. B-only repeats carry a dominant penalty and are created only when academic progression prevents sharing. A flexible non-blocking course that A takes in Year 1 Semester 1 is completed by B in the final continuation semester rather than copied automatically into Semester 2.",
                "hard": False,
            })
            core_constraints.append({
                "title": "B progression and deferred flexible courses",
                "description": "Prerequisite courses that B cannot share are repeated early enough to keep B progressing. Flexible non-blocking first-semester courses are deferred to B's final semester. Cohort A is not moved to accommodate B.",
                "hard": False,
            })
        else:
            core_constraints.append({
                "title": "מחזור א׳ קבוע ומחזור ב׳ גמיש",
                "description": "מחזור א׳ ננעל אחרי שלב תכנון הסמסטרים. מחזור ב׳ מתחיל סמסטר אחד מאוחר יותר והוא המחזור הגמיש. שנה 4 סמסטר א׳ שייכת רק למסלול המתוכנן של מחזור ב׳.",
                "hard": True,
            })
            core_constraints.append({
                "title": "מקסום A+B ומזעור פתיחות B בלבד",
                "description": "לכל קורס של B הפותר מנסה קודם להצטרף לפתיחה קיימת של A. פתיחה B בלבד מקבלת קנס דומיננטי ונוצרת רק אם קורסי הקדם/המקבילה לא מאפשרים שיתוף. קורס גמיש ולא חוסם ש-A לומד בשנה 1 סמסטר א׳ נדחה עבור B לסמסטר ההמשך האחרון ולא משוכפל אוטומטית בסמסטר ב׳.",
                "hard": False,
            })
            core_constraints.append({
                "title": "התקדמות B ודחיית קורסים גמישים",
                "description": "קורס קדם ש-B אינו יכול לשתף נפתח עבורו מוקדם מספיק כדי לא לעכב את התואר. קורס גמיש ולא חוסם מהסמסטר הראשון נדחה לסמסטר האחרון של B. מחזור A אינו מוזז כדי להתאים את עצמו ל-B.",
                "hard": False,
            })

    return render_template(
        "setup.html",
        all_days=ALL_DAYS,
        config=config,
        courses=catalog,
        ai_constraints=config.get("ai_constraints", []),
        ai_chat=config.get("ai_chat", []),
        ai_pending=config.get("ai_pending"),
        claude_configured=claude_is_configured(),
        core_constraints=core_constraints,
        courses_excel_exists=COURSES_EXCEL_FILE.exists(),
    )


@app.route("/days", methods=["GET"])
def day_adjustment_page():
    """Separate page for degree structure and department days/hours."""
    return render_template(
        "day_adjustment.html",
        all_days=ALL_DAYS,
        config=load_config(),
    )


@app.route("/days/save", methods=["POST"])
def save_day_adjustments():
    try:
        config = config_from_form()
        save_config(config)
        flash(_("Department days and hours were saved."), "success")
        return redirect(url_for("setup_page"))
    except Exception as error:
        flash(str(error), "error")
        return redirect(url_for("day_adjustment_page"))


@app.route("/lecturers", methods=["GET"])
def lecturer_availability_page():
    config = load_config()
    lecturers = lecturer_names()
    selected = (request.args.get("lecturer") or "").strip()
    if selected not in lecturers:
        selected = lecturers[0] if lecturers else ""

    overrides = config.get("lecturer_availability", {})
    selected_values = overrides.get(selected, {}) if selected else {}
    effective = {}
    for day in ALL_DAYS:
        key = day["key"]
        dept = config["days"][key]
        rule = selected_values.get(key)
        if rule is None:
            effective[key] = {
                "enabled": bool(dept.get("enabled", False)),
                "start": dept.get("start", "08:00"),
                "end": dept.get("end", "20:00"),
            }
        else:
            effective[key] = {
                "enabled": bool(rule.get("enabled", False)),
                "start": rule.get("start", dept.get("start", "08:00")),
                "end": rule.get("end", dept.get("end", "20:00")),
            }

    return render_template(
        "lecturer_availability.html",
        all_days=ALL_DAYS,
        config=config,
        lecturers=lecturers,
        selected_lecturer=selected,
        lecturer_values=effective,
        saved_overrides=overrides,
    )


@app.route("/lecturers/save", methods=["POST"])
def save_lecturer_availability():
    config = load_config()
    lecturer = (request.form.get("lecturer") or "").strip()
    if lecturer not in lecturer_names():
        flash(_("Choose a lecturer from the current course catalog."), "error")
        return redirect(url_for("lecturer_availability_page"))

    values = {}
    for day in ALL_DAYS:
        key = day["key"]
        values[key] = {
            "enabled": request.form.get(f"lecturer_enabled_{key}") == "on",
            "start": request.form.get(f"lecturer_start_{key}", config["days"][key]["start"]),
            "end": request.form.get(f"lecturer_end_{key}", config["days"][key]["end"]),
        }

    try:
        validate_lecturer_availability(lecturer, values, config["days"])
        config.setdefault("lecturer_availability", {})[lecturer] = values
        save_config(config)
        flash(_("Lecturer availability was saved. Rebuild the timetable to apply it."), "success")
    except Exception as error:
        flash(str(error), "error")
    return redirect(url_for("lecturer_availability_page", lecturer=lecturer))


@app.route("/lecturers/reset", methods=["POST"])
def reset_lecturer_availability():
    lecturer = (request.form.get("lecturer") or "").strip()
    config = load_config()
    config.setdefault("lecturer_availability", {}).pop(lecturer, None)
    save_config(config)
    flash(_("Lecturer availability was reset to the department defaults."), "success")
    return redirect(url_for("lecturer_availability_page", lecturer=lecturer))


@app.route("/generate", methods=["POST"])
def generate_timetable():
    """Save the user input, run the solver, then show the generated calendar."""
    try:
        config = config_from_form()
        save_config(config)

        success, details = run_backend()
        if not success:
            details_lower = details.lower()
            solver_failure = (
                "no feasible timetable" in details_lower
                or "infeasible" in details_lower
                or "time limit" in details_lower
                or "3-minute" in details_lower
                or "3 minute" in details_lower
                or "semester-b" in details_lower
                or "cohort b" in details_lower
                or "continuation semester" in details_lower
                or "repeated offering" in details_lower
            )
            if solver_failure:
                message = _(
                    "We couldn't build a timetable that satisfies all the hard "
                    "constraints, or the solver ran out of time. Please check the "
                    "number of semesters, prerequisite/parallel courses, and the "
                    "active days and hours."
                )
            else:
                message = _(
                    "The timetable calculation reached a system/export error after "
                    "or during generation. This does not necessarily mean that the "
                    "hard constraints are impossible."
                )
            flash(message + "\n\n" + details, "error")
            return redirect(url_for("setup_page"))

        try:
            save_system_snapshot()
        except Exception as snapshot_error:
            # Snapshot export is diagnostic only; never fail a valid timetable because of it.
            print(f"Warning: could not save system snapshot: {snapshot_error}", file=sys.stderr)
        flash(_("The timetable was built successfully by the solver."), "success")
        return redirect(url_for("calendar_page"))

    except Exception as error:
        flash(str(error), "error")
        return redirect(url_for("setup_page"))


def load_course_catalog() -> list[dict]:
    return load_json(COURSES_FILE, [])


def lecturer_names() -> list[str]:
    """Return unique lecturer names currently present in the course catalog."""
    return sorted({
        str(course.get("lecturer", "")).strip()
        for course in load_course_catalog()
        if str(course.get("lecturer", "")).strip()
    })


def validate_lecturer_availability(lecturer: str, values: dict, department_days: dict) -> None:
    if not lecturer.strip():
        raise ValueError(_("Choose a lecturer."))

    for day in ALL_DAYS:
        day_key = day["key"]
        rule = values.get(day_key, {})
        if not rule.get("enabled", False):
            continue
        if not department_days.get(day_key, {}).get("enabled", False):
            raise ValueError(
                _("%(lecturer)s cannot be enabled on %(day)s because the department is closed that day.",
                  lecturer=lecturer, day=day_label_for(day_key, select_locale()))
            )
        start = time_to_minutes(rule.get("start", "08:00"))
        end = time_to_minutes(rule.get("end", "20:00"))
        if start >= end:
            raise ValueError(_("For %(lecturer)s on %(day)s, the start time must be before the end time.",
                               lecturer=lecturer, day=day_label_for(day_key, select_locale())))
        if start % 5 != 0 or end % 5 != 0:
            raise ValueError(_("Hours must be in 5-minute increments."))

        dept = department_days[day_key]
        dept_start = time_to_minutes(dept["start"])
        dept_end = time_to_minutes(dept["end"])
        if start < dept_start or end > dept_end:
            raise ValueError(
                _("%(lecturer)s availability on %(day)s must stay inside department hours (%(start)s–%(end)s).",
                  lecturer=lecturer, day=day_label_for(day_key, select_locale()),
                  start=dept["start"], end=dept["end"])
            )


@app.route("/assistant/apply", methods=["POST"])
def apply_ai_requirement():
    """Chat with Claude only. Do NOT run the solver from a chat message."""
    user_text = (request.form.get("assistant_text") or "").strip()
    if not user_text:
        flash(_("Write a scheduling request first."), "error")
        return redirect(url_for("setup_page") + "#ai-assistant")

    config = load_config()
    catalog = load_course_catalog()
    if not catalog:
        flash(_("Add or upload courses before adding AI requirements."), "error")
        return redirect(url_for("setup_page") + "#ai-assistant")

    try:
        chat = config.setdefault("ai_chat", [])
        result = interpret_with_claude(
            user_text,
            catalog,
            active_rules=list(config.get("ai_constraints", [])),
            semesters_per_year=int(config.get("semesters_per_year", 2)),
            recent_chat=chat,
        )
        chat.append({"role": "user", "text": user_text})

        if result.get("needs_clarification"):
            message = result.get("clarification_question") or result.get("assistant_message")
            chat.append({"role": "assistant", "text": message, "status": "question"})
            config.pop("ai_pending", None)
        else:
            message = result.get("assistant_message") or _("I understood the request. Review it and apply it when you are ready.")
            pending = {
                "rules": result.get("rules", []),
                "data_actions": result.get("data_actions", []),
                "remove_rule_ids": result.get("remove_rule_ids", []),
                "clear_all": bool(result.get("clear_all")),
                "message": message,
            }
            config["ai_pending"] = pending
            chat.append({"role": "assistant", "text": message, "status": "proposal"})

        save_config(config)
        return redirect(url_for("setup_page") + "#ai-assistant")
    except Exception as error:
        config.setdefault("ai_chat", []).append({"role": "user", "text": user_text})
        config["ai_chat"].append({"role": "assistant", "text": str(error), "status": "error"})
        save_config(config)
        flash(str(error), "error")
        return redirect(url_for("setup_page") + "#ai-assistant")


def _refresh_course_catalog_only() -> None:
    """Refresh displayed course data without invoking the timetable solver."""
    from excel_reader import load_courses
    from output import save_courses_to_json, save_courses_to_excel, save_unresolved_requirements
    from scheduler import get_requirement_report
    courses = load_courses(COURSES_EXCEL_FILE)
    save_courses_to_json(courses, str(COURSES_FILE))
    save_courses_to_excel(courses, str(BACKEND_DIR / "courses_calculated.xlsx"))
    save_unresolved_requirements(get_requirement_report(courses), str(BACKEND_DIR / "unresolved_requirements.json"))


def _apply_claude_data_actions(actions: list[dict]) -> list[str]:
    """Apply confirmed Claude data edits. No solver is run here."""
    from openpyxl import load_workbook
    if not actions:
        return []
    if not COURSES_EXCEL_FILE.exists():
        raise ValueError("Upload the course Excel file once before adding courses through Claude.")
    wb = load_workbook(COURSES_EXCEL_FILE)
    if "גיליון2" not in wb.sheetnames:
        raise ValueError('The workbook must contain a sheet named "גיליון2".')
    ws = wb["גיליון2"]
    headers = [cell.value for cell in ws[1]]
    for optional_header in ["קטגוריית קורס", "סמסטר מקורי", "גמישות"]:
        if optional_header not in headers:
            ws.cell(row=1, column=len(headers)+1, value=optional_header)
            headers.append(optional_header)
    wanted = ["מס.שעור", "שם השיעור", "תיאור ד.קדם", "תיאור ד.מקבילה", "שנת לימודים", "שם המרצה", "נ.זיכוי", 'ש"ש', "סוג שיעור"]
    missing = [h for h in wanted if h not in headers]
    if missing:
        raise ValueError("Missing required Excel columns: " + ", ".join(missing))
    existing = load_course_catalog()
    used_ids = {int(c.get("course_id")) for c in existing if c.get("course_id") is not None}
    added = []
    for action in actions:
        if action.get("action") != "add_course":
            raise ValueError(f"Unsupported Claude data action: {action.get('action')}")
        required = ["name", "year", "lecturer", "credits", "weekly_hours", "course_type"]
        absent = [k for k in required if action.get(k) in (None, "")]
        if absent:
            raise ValueError("Claude is missing required course details: " + ", ".join(absent))
        requested_id = action.get("course_id")
        if requested_id in (None, ""):
            course_id = (max(used_ids) + 1) if used_ids else 100001
        else:
            course_id = int(requested_id)
        if course_id in used_ids:
            raise ValueError(f"Course ID {course_id} already exists.")
        used_ids.add(course_id)
        values = {
            "מס.שעור": course_id,
            "שם השיעור": str(action["name"]).strip(),
            "תיאור ד.קדם": action.get("prerequisites") or None,
            "תיאור ד.מקבילה": action.get("parallel_requirement") or None,
            "שנת לימודים": int(action["year"]),
            "שם המרצה": str(action["lecturer"]).strip(),
            "נ.זיכוי": float(action["credits"]),
            'ש"ש': float(action["weekly_hours"]),
            "סוג שיעור": str(action["course_type"]).strip(),
            "קטגוריית קורס": str(action.get("course_category") or "חובה").strip(),
            "סמסטר מקורי": action.get("original_semester"),
            "גמישות": str(action.get("flexibility") or "auto").strip(),
        }
        ws.append([values.get(h) for h in headers])
        added.append(f"{values['שם השיעור']} ({course_id})")
    wb.save(COURSES_EXCEL_FILE)
    _refresh_course_catalog_only()
    return added


@app.route("/assistant/confirm", methods=["POST"])
def confirm_ai_requirement():
    """Apply Claude's latest proposal, then invoke the solver exactly once."""
    config = load_config()
    pending = config.get("ai_pending")
    if not pending:
        flash(_("There is no Claude proposal waiting to be applied."), "error")
        return redirect(url_for("setup_page") + "#ai-assistant")

    data_actions = list(pending.get("data_actions", []))
    if data_actions and not pending.get("rules") and not pending.get("remove_rule_ids") and not pending.get("clear_all"):
        try:
            added = _apply_claude_data_actions(data_actions)
            config.pop("ai_pending", None)
            config.setdefault("ai_chat", []).append({
                "role": "assistant",
                "text": _("The course data was added successfully. The solver was not run. You can keep editing data or build the timetable when ready."),
                "status": "applied",
            })
            save_config(config)
            flash(_("Course data added successfully. The solver was not run."), "success")
        except Exception as error:
            config.setdefault("ai_chat", []).append({"role": "assistant", "text": str(error), "status": "error"})
            save_config(config)
            flash(str(error), "error")
        return redirect(url_for("setup_page") + "#ai-assistant")

    previous_rules = list(config.get("ai_constraints", []))
    rules = [] if pending.get("clear_all") else list(previous_rules)
    remove_ids = {str(v) for v in pending.get("remove_rule_ids", [])}
    if remove_ids:
        rules = [r for r in rules if str(r.get("id")) not in remove_ids]
    rules.extend(pending.get("rules", []))
    config["ai_constraints"] = rules
    save_config(config)

    success, details = run_backend()
    if not success:
        config["ai_constraints"] = previous_rules
        config.setdefault("ai_chat", []).append({
            "role": "assistant",
            "text": _("I understood the requirement, but the solver could not build a timetable with it. I did not apply the change. You can continue the conversation and adjust the request."),
            "status": "error",
        })
        save_config(config)
        flash(_("The solver could not apply Claude's proposed requirement. The previous timetable rules were restored."), "error")
        return redirect(url_for("setup_page") + "#ai-assistant")

    config.pop("ai_pending", None)
    config.setdefault("ai_chat", []).append({
        "role": "assistant",
        "text": _("The requirement was applied successfully and the timetable was rebuilt."),
        "status": "applied",
    })
    save_config(config)
    flash(_("Claude's requirement was applied successfully."), "success")
    return redirect(url_for("setup_page") + "#ai-assistant")


@app.route("/assistant/remove/<rule_id>", methods=["POST"])
def remove_ai_requirement(rule_id: str):
    config = load_config()
    before = list(config.get("ai_constraints", []))
    after = [rule for rule in before if str(rule.get("id")) != rule_id]
    config["ai_constraints"] = after
    if len(after) == len(before):
        flash(_("That AI requirement was already removed."), "error")
        return redirect(url_for("setup_page") + "#ai-assistant")

    config.setdefault("ai_chat", []).append({
        "role": "assistant",
        "text": _("Removed one scheduling requirement and rebuilt the timetable."),
        "status": "applied",
    })
    save_config(config)
    success, details = run_backend()
    if not success:
        flash(details, "error")
    else:
        flash(_("Requirement removed and timetable rebuilt."), "success")
    return redirect(url_for("setup_page") + "#ai-assistant")


@app.route("/assistant/clear", methods=["POST"])
def clear_ai_requirements():
    config = load_config()
    config["ai_constraints"] = []
    config.setdefault("ai_chat", []).append({
        "role": "assistant",
        "text": _("Cleared all AI scheduling requirements."),
        "status": "applied",
    })
    save_config(config)
    success, details = run_backend()
    if not success:
        flash(details, "error")
    else:
        flash(_("All AI requirements were cleared and the timetable was rebuilt."), "success")
    return redirect(url_for("setup_page") + "#ai-assistant")



def _refresh_after_course_change() -> tuple[bool, str]:
    """Rebuild derived course JSON/timetable after the course source changes."""
    return run_backend()


@app.route("/courses/upload", methods=["POST"])
def upload_courses_excel():
    uploaded = request.files.get("courses_excel")
    if not uploaded or not uploaded.filename:
        flash(_("Choose an Excel file first."), "error")
        return redirect(url_for("setup_page") + "#course-data")

    filename = secure_filename(uploaded.filename)
    if not filename.lower().endswith((".xlsx", ".xlsm")):
        flash(_("Please upload an .xlsx or .xlsm Excel file."), "error")
        return redirect(url_for("setup_page") + "#course-data")

    temp_path = BACKEND_DIR / "_uploaded_courses.xlsx"
    backup_path = BACKEND_DIR / "courses.backup.xlsx"
    try:
        uploaded.save(temp_path)
        # Validate against the exact schema expected by the existing backend.
        from excel_reader import load_courses
        loaded = load_courses(temp_path)
        if not loaded:
            raise ValueError("The uploaded workbook does not contain any usable courses.")

        if COURSES_EXCEL_FILE.exists():
            shutil.copy2(COURSES_EXCEL_FILE, backup_path)
        shutil.move(str(temp_path), str(COURSES_EXCEL_FILE))

        success, details = _refresh_after_course_change()
        if not success:
            if backup_path.exists():
                shutil.copy2(backup_path, COURSES_EXCEL_FILE)
                _refresh_after_course_change()
            raise ValueError(details)

        flash(_("Course Excel file uploaded successfully and the timetable was rebuilt."), "success")
    except Exception as error:
        if temp_path.exists():
            temp_path.unlink()
        flash(str(error), "error")
    return redirect(url_for("setup_page") + "#course-data")


@app.route("/courses/add", methods=["POST"])
def add_course_manually():
    try:
        from openpyxl import load_workbook
        if not COURSES_EXCEL_FILE.exists():
            raise ValueError("Upload the course Excel file once before adding courses manually.")

        course_id = int(request.form.get("course_id", "").strip())
        name = (request.form.get("course_name") or "").strip()
        year = int(request.form.get("course_year", "").strip())
        lecturer = (request.form.get("lecturer") or "").strip()
        credits = float(request.form.get("credits", "").strip())
        weekly_hours = float(request.form.get("weekly_hours", "").strip())
        course_type = (request.form.get("course_type") or "").strip()
        course_category = (request.form.get("course_category") or "חובה").strip()
        original_semester_raw = (request.form.get("original_semester") or "").strip()
        original_semester = int(original_semester_raw) if original_semester_raw else None
        flexibility = (request.form.get("flexibility") or "auto").strip()
        prerequisites = (request.form.get("prerequisites") or "").strip() or None
        parallel = (request.form.get("parallel_requirement") or "").strip() or None

        if not name or not lecturer or not course_type or course_category not in {"חובה", "בחירה", "סמינר", "פרויקט", "כללי"}:
            raise ValueError("Course name, lecturer and course type are required.")
        if flexibility not in {"fixed", "flexible", "auto"}:
            raise ValueError("Flexibility must be fixed, flexible or auto.")
        if year < 1:
            raise ValueError("Study year must be at least 1.")
        if credits <= 0 or weekly_hours <= 0:
            raise ValueError("Credits and weekly hours must be greater than 0.")

        existing_ids = {int(c.get("course_id")) for c in load_course_catalog() if c.get("course_id") is not None}
        if course_id in existing_ids:
            raise ValueError(f"Course ID {course_id} already exists.")

        wb = load_workbook(COURSES_EXCEL_FILE)
        if "גיליון2" not in wb.sheetnames:
            raise ValueError('The workbook must contain a sheet named "גיליון2".')
        ws = wb["גיליון2"]
        headers = [cell.value for cell in ws[1]]
        wanted = ["מס.שעור", "שם השיעור", "תיאור ד.קדם", "תיאור ד.מקבילה", "שנת לימודים", "שם המרצה", "נ.זיכוי", 'ש"ש', "סוג שיעור"]
        for optional_header in ["קטגוריית קורס", "סמסטר מקורי", "גמישות"]:
            if optional_header not in headers:
                ws.cell(row=1, column=len(headers)+1, value=optional_header)
                headers.append(optional_header)
        missing = [h for h in wanted if h not in headers]
        if missing:
            raise ValueError("Missing required Excel columns: " + ", ".join(missing))
        values = {
            "מס.שעור": course_id,
            "שם השיעור": name,
            "תיאור ד.קדם": prerequisites,
            "תיאור ד.מקבילה": parallel,
            "שנת לימודים": year,
            "שם המרצה": lecturer,
            "נ.זיכוי": credits,
            'ש"ש': weekly_hours,
            "סוג שיעור": course_type,
            "קטגוריית קורס": course_category,
            "סמסטר מקורי": original_semester,
            "גמישות": flexibility,
        }
        ws.append([values.get(h) for h in headers])
        wb.save(COURSES_EXCEL_FILE)

        success, details = _refresh_after_course_change()
        if not success:
            # Remove the just-added row if it made the source invalid/unsatisfiable.
            wb = load_workbook(COURSES_EXCEL_FILE)
            ws = wb["גיליון2"]
            ws.delete_rows(ws.max_row, 1)
            wb.save(COURSES_EXCEL_FILE)
            _refresh_after_course_change()
            raise ValueError(details)

        flash(_("Course added successfully and the timetable was rebuilt."), "success")
    except Exception as error:
        flash(str(error), "error")
    return redirect(url_for("setup_page") + "#manual-course")




def _event_overlap(a: dict, b: dict) -> bool:
    if a.get("day") != b.get("day"):
        return False
    return max(time_to_minutes(a["start"]), time_to_minutes(b["start"])) < min(
        time_to_minutes(a["end"]), time_to_minutes(b["end"])
    )


def build_system_snapshot() -> dict:
    """Build a complete, human-shareable JSON snapshot of the current system.

    The file is intentionally redundant: it contains raw inputs, generated
    events and derived diagnostics so a bug can be understood without needing
    screenshots or the original Excel workbook.
    """
    config = load_config()
    courses = load_course_catalog()
    timetable = load_timetable()
    unresolved = load_json(UNRESOLVED_FILE, [])
    curriculum_plan = load_json(CURRICULUM_PLAN_FILE, {})

    semesters_per_year = int(config.get("semesters_per_year", 2))
    degree_years = int(config.get("degree_years", 3))
    base_semesters = degree_years * semesters_per_year
    total_semesters = base_semesters + (1 if config.get("allow_semester_b_start") else 0)

    # One row per course/semester/cohort, independent of lecture/tutorial count.
    offerings = {}
    for event in timetable:
        key = (str(event.get("course_id")), int(event.get("semester", 0)), str(event.get("cohort_label", "A")))
        if key not in offerings:
            offerings[key] = {
                "course_id": event.get("course_id"),
                "name": event.get("name"),
                "semester": event.get("semester"),
                "semester_name": event.get("semester_name"),
                "cohort": event.get("cohort_label", "A"),
                "category": event.get("course_category"),
                "lecturer": event.get("lecturer"),
                "prerequisites": event.get("prerequisites"),
                "parallel_requirement": event.get("parallel_requirement"),
                "events": [],
            }
        offerings[key]["events"].append({
            "type": event.get("event_type"),
            "day": event.get("day"),
            "start": event.get("start"),
            "end": event.get("end"),
        })

    semester_summary = []
    for semester in range(1, total_semesters + 1):
        events = [e for e in timetable if int(e.get("semester", 0)) == semester]
        unique_offerings = {
            (str(e.get("course_id")), str(e.get("cohort_label", "A"))) for e in events
        }
        cohort_counts = Counter(str(e.get("cohort_label", "A")) for e in events)
        category_courses = defaultdict(set)
        for e in events:
            category_courses[str(e.get("course_category", "unknown"))].add(
                (str(e.get("course_id")), str(e.get("cohort_label", "A")))
            )
        semester_summary.append({
            "semester": semester,
            "academic_year": ((semester - 1) // semesters_per_year) + 1,
            "semester_in_year": ((semester - 1) % semesters_per_year) + 1,
            "event_count": len(events),
            "offering_count": len(unique_offerings),
            "cohort_event_counts": dict(cohort_counts),
            "category_offering_counts": {k: len(v) for k, v in category_courses.items()},
        })

    # Course-level cohort behavior makes repeated/shared offerings easy to inspect.
    course_paths = {}
    for course in courses:
        cid = str(course.get("course_id"))
        related = [o for o in offerings.values() if str(o.get("course_id")) == cid]
        course_paths[cid] = {
            "course_id": course.get("course_id"),
            "name": course.get("name"),
            "catalog_year": course.get("year"),
            "category": course.get("course_category"),
            "prerequisites": course.get("prerequisites"),
            "parallel_requirement": course.get("parallel_requirement"),
            "offerings": sorted(related, key=lambda x: (int(x["semester"]), x["cohort"])),
        }

    overlaps = []
    for semester in range(1, total_semesters + 1):
        events = [e for e in timetable if int(e.get("semester", 0)) == semester]
        for i, a in enumerate(events):
            for b in events[i + 1:]:
                if not _event_overlap(a, b):
                    continue
                overlaps.append({
                    "semester": semester,
                    "day": a.get("day"),
                    "event_1": {
                        "course_id": a.get("course_id"), "name": a.get("name"),
                        "category": a.get("course_category"), "cohort": a.get("cohort_label"),
                        "lecturer": a.get("lecturer"), "start": a.get("start"), "end": a.get("end"),
                    },
                    "event_2": {
                        "course_id": b.get("course_id"), "name": b.get("name"),
                        "category": b.get("course_category"), "cohort": b.get("cohort_label"),
                        "lecturer": b.get("lecturer"), "start": b.get("start"), "end": b.get("end"),
                    },
                })

    # Explicitly document what this build is supposed to optimize.
    solver_policy = {
        "hard_constraints": [
            "Prerequisites must be completed before dependent courses for each cohort.",
            "Parallel requirements must be in the same or an earlier semester for each cohort.",
            "Every required lecture/tutorial of each course offering is scheduled once.",
            "A lecturer cannot teach overlapping events.",
            "Required/core and general courses cannot overlap any other course for the same cohort.",
            "Projects may overlap projects only; project/elective and project/seminar overlaps are forbidden.",
            "Events must fit department and lecturer availability.",
            "Explicit original-semester values are fixed. Otherwise cohort A is strategically placed inside each course's catalog year to maximize future A+B sharing and reduce repeated gateway offerings.",
            "If semester-B intake is enabled, cohort B starts one semester later and has one continuation semester.",
        ],
        "soft_objectives": [
            "Joint curriculum strategy: keep flexible/non-blocking material in A's first semester when useful, move gateway courses to semesters where A and B can share them, maximize A+B offerings, and minimize B-only repeats.",
            "Courses marked flexible and non-blocking may be deferred for B, especially first-semester general courses which may move to B's continuation semester. Non-flexible required/core courses are strongly preferred before the continuation semester; Semester 10 is a last resort for them.",
            "Only elective/elective, elective/seminar, and seminar/seminar overlaps are soft-allowed. Project/project is allowed without a student-overlap penalty; every other combination is hard-forbidden.",
            "Avoid empty or extremely overloaded semesters and balance teaching load across active days.",
            "Prefer 5-30 minute breaks; penalize zero-minute and long gaps.",
        ],
        "current_numeric_weights_detected_in_solver": {
            "repeat_course_penalty": 400000,
            "strategic_foundation_repeat_penalty": 20000,
            "core_continuation_penalty": 1500000,
            "core_delay_penalty_per_semester": 20000,
            "semester_1_underfill_penalty_per_course": 120000,
            "elective_seminar_overlap_penalty": 6,
            "back_to_back_penalty": 30,
            "long_gap_slot_penalty": 5,
            "prerequisite_wait_penalty": 25,
        },
    }

    snapshot = {
        "snapshot_version": 2,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "purpose": "Complete diagnostic snapshot for reviewing timetable behavior and solver decisions.",
        "configuration": config,
        "curriculum_plan": curriculum_plan,
        "solver_policy": solver_policy,
        "course_catalog": courses,
        "generated_timetable_events": timetable,
        "course_offerings": sorted(offerings.values(), key=lambda x: (int(x["semester"]), str(x["course_id"]), x["cohort"])),
        "course_paths_by_id": course_paths,
        "semester_summary": semester_summary,
        "overlaps_detected": overlaps,
        "unresolved_requirements": unresolved,
        "diagnostic_counts": {
            "catalog_courses": len(courses),
            "timetable_events": len(timetable),
            "course_offerings": len(offerings),
            "overlap_pairs": len(overlaps),
            "ai_constraints": len(config.get("ai_constraints", [])),
            "semester_b_enabled": bool(config.get("allow_semester_b_start")),
            "configured_semesters": total_semesters,
        },
    }
    return snapshot


def save_system_snapshot() -> Path:
    snapshot = build_system_snapshot()
    with SYSTEM_SNAPSHOT_FILE.open("w", encoding="utf-8") as file:
        json.dump(snapshot, file, ensure_ascii=False, indent=2)
    return SYSTEM_SNAPSHOT_FILE


@app.route("/export/system-json", methods=["GET"])
def export_system_json():
    path = save_system_snapshot()
    return send_file(
        path,
        as_attachment=True,
        download_name="timetable-system-snapshot.json",
        mimetype="application/json",
    )


@app.route("/calendar", methods=["GET"])
def calendar_page():
    """Second page: display only the timetable created from the selected input."""
    locale = select_locale()
    content_translations = load_content_translations()

    timetable = load_timetable()
    config = load_config()
    active_days = active_days_from_config(config)
    timetable, slot_labels = enrich_for_calendar(timetable, config)
    timetable = [
        localize_event(event, locale, content_translations) for event in timetable
    ]

    semesters_per_year = int(config.get("semesters_per_year", 2))
    degree_years = int(config.get("degree_years", 3))
    base_degree_semesters = degree_years * semesters_per_year
    total_configured_semesters = base_degree_semesters + (1 if config.get("allow_semester_b_start", False) else 0)

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

        display_name = semester_display_name(academic_year, semester_in_year)
        semesters.append((semester_number, display_name))

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
        ai_constraints=config.get("ai_constraints", []),
        ai_chat=config.get("ai_chat", []),
        claude_configured=claude_is_configured(),
    )


def open_browser() -> None:
    webbrowser.open_new("http://127.0.0.1:5000/")


if __name__ == "__main__":
    threading.Timer(1.0, open_browser).start()
    app.run(host="127.0.0.1", port=5000, debug=False)
