from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
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
    config.setdefault("days", {})
    config.setdefault("ai_constraints", [])
    config.setdefault("ai_chat", [])

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

        if start % 15 != 0 or end % 15 != 0:
            raise ValueError(_("Hours must be in 15-minute increments."))

    if enabled_count == 0:
        raise ValueError(_("You must select at least one teaching day."))



def config_from_form() -> dict:
    previous = load_config()
    config = {
        "degree_years": int(request.form.get("degree_years", 3)),
        "semesters_per_year": int(
            request.form.get("semesters_per_year", 2)
        ),
        "days": {},
        # Keep natural-language requirements when the department changes
        # general availability settings.
        "ai_constraints": previous.get("ai_constraints", []),
        "ai_chat": previous.get("ai_chat", []),
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
            {"title": "Every event is scheduled", "description": "Every lecture and tutorial is placed exactly once in the semester selected for its course.", "hard": True},
            {"title": "Lecturer conflicts", "description": "The same lecturer cannot teach two events at the same time.", "hard": True},
            {"title": "Student timetable conflicts", "description": "Courses in the same semester cannot overlap, so students can attend every scheduled course.", "hard": True},
            {"title": "Department availability", "description": "Every event must fit completely inside an enabled teaching day and its allowed hours.", "hard": True},
            {"title": "Study-year placement", "description": "The solver strongly prefers courses to stay inside the study year listed in the course file.", "hard": False},
            {"title": "Balanced semesters", "description": "Weekly teaching load is balanced across the semesters of each study year instead of packing earlier semesters.", "hard": False},
            {"title": "Lecture/tutorial order", "description": "There is no rule forcing a lecture to occur before its tutorial and no rule forcing them to be close together.", "hard": False},
        ]
    else:
        core_constraints = [
            {"title": "קורסי קדם", "description": "קורס קדם חייב להיקבע בסמסטר מוקדם יותר מהקורס שתלוי בו.", "hard": True},
            {"title": "דרישות מקבילות", "description": "קורס שמוגדר כדרישה מקבילה חייב להיקבע באותו סמסטר או בסמסטר מוקדם יותר.", "hard": True},
            {"title": "שיבוץ כל המפגשים", "description": "כל הרצאה וכל תרגול משובצים פעם אחת בדיוק בסמסטר שנבחר עבור הקורס.", "hard": True},
            {"title": "מניעת חפיפות למרצה", "description": "אותו מרצה לא יכול ללמד שני מפגשים באותה שעה.", "hard": True},
            {"title": "מניעת חפיפות לסטודנטים", "description": "קורסים באותו סמסטר אינם יכולים לחפוף, כך שהסטודנטים יוכלו להשתתף בכל הקורסים.", "hard": True},
            {"title": "ימי ושעות פעילות המחלקה", "description": "כל מפגש חייב להיכנס במלואו בתוך יום לימודים פעיל ובטווח השעות שהוגדר עבורו.", "hard": True},
            {"title": "שמירה על שנת הלימוד", "description": "הפותר מעדיף מאוד להשאיר כל קורס בשנת הלימוד שמופיעה בקובץ הקורסים.", "hard": False},
            {"title": "איזון בין סמסטרים", "description": "העומס השבועי מחולק בצורה מאוזנת בין הסמסטרים של כל שנת לימוד במקום לדחוס קורסים לסמסטרים מוקדמים.", "hard": False},
            {"title": "סדר הרצאה ותרגול", "description": "אין אילוץ שמכריח הרצאה להיות לפני התרגול ואין אילוץ שמכריח אותם להיות קרובים זה לזה.", "hard": False},
        ]
    return render_template(
        "setup.html",
        all_days=ALL_DAYS,
        config=config,
        courses=catalog,
        ai_constraints=config.get("ai_constraints", []),
        ai_chat=config.get("ai_chat", []),
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


@app.route("/generate", methods=["POST"])
def generate_timetable():
    """Save the user input, run the solver, then show the generated calendar."""
    try:
        config = config_from_form()
        save_config(config)

        success, details = run_backend()
        if not success:
            flash(
                _(
                    "We couldn't build a timetable that satisfies all the hard "
                    "constraints. Please check the number of semesters, the "
                    "prerequisite/parallel courses, and the active days and hours."
                )
                + "\n\n" + details,
                "error",
            )
            return redirect(url_for("setup_page"))

        flash(_("The timetable was built successfully by the solver."), "success")
        return redirect(url_for("calendar_page"))

    except Exception as error:
        flash(str(error), "error")
        return redirect(url_for("setup_page"))


def load_course_catalog() -> list[dict]:
    return load_json(COURSES_FILE, [])


@app.route("/assistant/apply", methods=["POST"])
def apply_ai_requirement():
    """Interpret unrestricted department text with Claude and re-run the solver."""
    user_text = (request.form.get("assistant_text") or "").strip()
    if not user_text:
        flash(_("Write a scheduling request first."), "error")
        return redirect(url_for("setup_page"))

    config = load_config()
    catalog = load_course_catalog()
    if not catalog:
        flash(_("Add or upload courses before adding AI requirements."), "error")
        return redirect(url_for("setup_page") + "#ai-assistant")

    try:
        chat = config.setdefault("ai_chat", [])
        previous_rules = list(config.get("ai_constraints", []))
        result = interpret_with_claude(
            user_text,
            catalog,
            active_rules=previous_rules,
            semesters_per_year=int(config.get("semesters_per_year", 2)),
            recent_chat=chat,
        )

        chat.append({"role": "user", "text": user_text})

        if result.get("needs_clarification"):
            message = result.get("clarification_question") or result.get("assistant_message")
            chat.append({"role": "assistant", "text": message, "status": "question"})
            save_config(config)
            flash(message, "success")
            return redirect(url_for("setup_page") + "#ai-assistant")

        rules = list(config.get("ai_constraints", []))
        if result.get("clear_all"):
            rules = []

        remove_ids = {str(v) for v in result.get("remove_rule_ids", [])}
        if remove_ids:
            rules = [rule for rule in rules if str(rule.get("id")) not in remove_ids]

        rules.extend(result.get("rules", []))
        config["ai_constraints"] = rules

        assistant_message = result.get("assistant_message") or _("I understood the request and added it to the solver.")
        chat.append({
            "role": "assistant",
            "text": assistant_message,
            "status": "applied",
            "rule_ids": [rule.get("id") for rule in result.get("rules", [])],
        })
        save_config(config)

        success, details = run_backend()
        if not success:
            # Roll back the new interpretation so the timetable and the list of
            # active rules always describe the same solver state.
            config["ai_constraints"] = previous_rules
            chat.append({
                "role": "assistant",
                "text": _("I understood that request, but it conflicts with the current hard constraints, so I did not apply it."),
                "status": "error",
            })
            save_config(config)
            flash(
                _("Claude understood the request, but the solver could not satisfy it together with the existing hard constraints. The new rule was not applied.")
                + "\n\n" + details,
                "error",
            )
            return redirect(url_for("setup_page") + "#ai-assistant")

        flash(assistant_message, "success")
        return redirect(url_for("setup_page") + "#ai-assistant")

    except Exception as error:
        config.setdefault("ai_chat", []).append({"role": "user", "text": user_text})
        config["ai_chat"].append({"role": "assistant", "text": str(error), "status": "error"})
        save_config(config)
        flash(str(error), "error")
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
        prerequisites = (request.form.get("prerequisites") or "").strip() or None
        parallel = (request.form.get("parallel_requirement") or "").strip() or None

        if not name or not lecturer or not course_type:
            raise ValueError("Course name, lecturer and course type are required.")
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
            semester_display_name(academic_year, semester_in_year),
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
        ai_constraints=config.get("ai_constraints", []),
        ai_chat=config.get("ai_chat", []),
        claude_configured=claude_is_configured(),
    )


def open_browser() -> None:
    webbrowser.open_new("http://127.0.0.1:5000/")


if __name__ == "__main__":
    threading.Timer(1.0, open_browser).start()
    app.run(host="127.0.0.1", port=5000, debug=False)
