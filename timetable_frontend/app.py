from __future__ import annotations

import json
import threading
import webbrowser
from pathlib import Path

from flask import Flask, render_template


app = Flask(__name__)
TIMETABLE_FILE = Path(__file__).with_name("timetable.json")


def load_timetable() -> list[dict]:
    if not TIMETABLE_FILE.exists():
        return []

    with TIMETABLE_FILE.open("r", encoding="utf-8") as file:
        return json.load(file)


@app.route("/")
def calendar_page():
    timetable = load_timetable()

    days = [
        {"key": "Sunday", "hebrew": "ראשון"},
        {"key": "Monday", "hebrew": "שני"},
        {"key": "Tuesday", "hebrew": "שלישי"},
        {"key": "Wednesday", "hebrew": "רביעי"},
        {"key": "Thursday", "hebrew": "חמישי"},
    ]

    years = sorted({int(course["year"]) for course in timetable})

    return render_template(
        "calendar.html",
        timetable=timetable,
        days=days,
        years=years,
    )


def open_browser() -> None:
    webbrowser.open_new("http://127.0.0.1:5000/")


if __name__ == "__main__":
    # Open the calendar page automatically after the local server starts.
    threading.Timer(1.0, open_browser).start()
    app.run(host="127.0.0.1", port=5000, debug=False)
