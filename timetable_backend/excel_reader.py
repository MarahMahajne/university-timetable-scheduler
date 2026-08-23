from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from models import Course


SHEET_NAME = "גיליון2"

REQUIRED_COLUMNS = {
    "מס.שעור",
    "שם השיעור",
    "תיאור ד.קדם",
    "תיאור ד.מקבילה",
    "שנת לימודים",
    "שם המרצה",
    "נ.זיכוי",
    'ש"ש',
    "סוג שיעור",
}


def clean_text(value) -> str | None:
    if pd.isna(value):
        return None

    text = str(value).replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def normalize_lecturer_name(value) -> str:
    name = clean_text(value)

    if not name:
        raise ValueError("Missing lecturer name")

    return name.replace('ד״ר', 'ד"ר')


def calculate_lecture_tutorial_hours(
    credits: float,
    weekly_hours: float,
    course_type: str,
) -> tuple[float, float, str]:
    """
    Derive lecture/tutorial hours from credits and weekly hours.

    From the dataset we inferred:
        L + T = weekly_hours
        2L + T = credits

    Therefore:
        L = credits - weekly_hours
        T = 2*weekly_hours - credits

    If the source data makes either value negative, the row is marked for
    review. We keep the project runnable by falling back to one lecture block
    of weekly_hours and zero tutorial hours for scheduling only.
    """
    lecture_hours = round(credits - weekly_hours, 2)
    tutorial_hours = round((2 * weekly_hours) - credits, 2)

    if lecture_hours < 0 or tutorial_hours < 0:
        return (
            float(weekly_hours),
            0.0,
            "REVIEW_REQUIRED: credits/weekly_hours produce a negative duration",
        )

    status = "OK"

    if course_type == "שעור" and tutorial_hours > 0:
        status = "REVIEW: source says lecture only but formula gives tutorial hours"
    elif course_type == "שעור ותרגול" and tutorial_hours == 0:
        status = "REVIEW: source says lecture+tutorial but formula gives 0 tutorial"

    return lecture_hours, tutorial_hours, status


def load_courses(excel_path: str | Path) -> list[Course]:
    excel_path = Path(excel_path)

    if not excel_path.exists():
        raise FileNotFoundError(f"Excel file not found: {excel_path}")

    dataframe = pd.read_excel(excel_path, sheet_name=SHEET_NAME)
    dataframe.columns = [str(column).strip() for column in dataframe.columns]

    missing_columns = REQUIRED_COLUMNS.difference(dataframe.columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"Missing required Excel columns: {missing}")

    courses: list[Course] = []

    for excel_row, row in dataframe.iterrows():
        row_number = excel_row + 2

        essential_values = [
            row["מס.שעור"],
            row["שם השיעור"],
            row["שנת לימודים"],
            row["שם המרצה"],
            row["נ.זיכוי"],
            row['ש"ש'],
            row["סוג שיעור"],
        ]

        if any(pd.isna(value) for value in essential_values):
            print(f"Skipping Excel row {row_number}: missing essential information.")
            continue

        credits = float(row["נ.זיכוי"])
        weekly_hours = float(row['ש"ש'])
        course_type = clean_text(row["סוג שיעור"]) or ""

        lecture_hours, tutorial_hours, calculation_status = (
            calculate_lecture_tutorial_hours(
                credits=credits,
                weekly_hours=weekly_hours,
                course_type=course_type,
            )
        )

        courses.append(
            Course(
                course_id=int(row["מס.שעור"]),
                name=clean_text(row["שם השיעור"]) or "",
                prerequisites=clean_text(row["תיאור ד.קדם"]),
                parallel_requirement=clean_text(row["תיאור ד.מקבילה"]),
                year=int(row["שנת לימודים"]),
                lecturer=normalize_lecturer_name(row["שם המרצה"]),
                credits=credits,
                weekly_hours=weekly_hours,
                course_type=course_type,
                lecture_hours=lecture_hours,
                tutorial_hours=tutorial_hours,
                calculation_status=calculation_status,
            )
        )

    return courses
