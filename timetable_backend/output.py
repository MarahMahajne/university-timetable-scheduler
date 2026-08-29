from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from models import Course

if TYPE_CHECKING:
    from scheduler import ScheduledCourse


def save_courses_to_json(
    courses: list[Course],
    output_path: str | Path = "courses.json",
) -> Path:
    output_path = Path(output_path)

    with output_path.open("w", encoding="utf-8") as file:
        json.dump(
            [course.to_dict() for course in courses],
            file,
            ensure_ascii=False,
            indent=4,
        )

    return output_path


def save_courses_to_excel(
    courses: list[Course],
    output_path: str | Path = "courses_calculated.xlsx",
) -> Path:
    output_path = Path(output_path)

    rows = []

    for course in courses:
        rows.append({
            "מס. שיעור": course.course_id,
            "שם השיעור": course.name,
            "דרישות קדם": course.prerequisites,
            "דרישות מקבילות": course.parallel_requirement,
            "שנת לימודים": course.year,
            "שם המרצה": course.lecturer,
            'נ"ז': course.credits,
            'ש"ש': course.weekly_hours,
            "סוג שיעור": course.course_type,
            "שעות הרצאה מחושבות": course.lecture_hours,
            "שעות תרגול מחושבות": course.tutorial_hours,
            "סטטוס חישוב": course.calculation_status,
        })

    dataframe = pd.DataFrame(rows)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        dataframe.to_excel(writer, sheet_name="קורסים מחושבים", index=False)

        worksheet = writer.sheets["קורסים מחושבים"]
        worksheet.sheet_view.rightToLeft = True
        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions

        widths = {
            "A": 14, "B": 40, "C": 48, "D": 32, "E": 12, "F": 25,
            "G": 10, "H": 10, "I": 20, "J": 22, "K": 22, "L": 34,
        }

        for column, width in widths.items():
            worksheet.column_dimensions[column].width = width

        for cell in worksheet[1]:
            cell.font = cell.font.copy(bold=True)

    return output_path


def save_unresolved_requirements(
    unresolved: list[dict],
    output_path: str | Path = "unresolved_requirements.json",
) -> Path:
    output_path = Path(output_path)

    with output_path.open("w", encoding="utf-8") as file:
        json.dump(
            unresolved,
            file,
            ensure_ascii=False,
            indent=4,
        )

    return output_path


def save_timetable_to_json(
    timetable: list["ScheduledCourse"],
    output_path: str | Path = "timetable.json",
) -> Path:
    output_path = Path(output_path)

    with output_path.open("w", encoding="utf-8") as file:
        json.dump(
            [item.to_dict() for item in timetable],
            file,
            ensure_ascii=False,
            indent=4,
        )

    return output_path


def save_timetable_to_excel(
    timetable: list["ScheduledCourse"],
    output_path: str | Path = "timetable.xlsx",
) -> Path:
    output_path = Path(output_path)

    rows = [item.to_dict() for item in timetable]
    dataframe = pd.DataFrame(rows)

    preferred_columns = [
        "semester",
        "semester_name",
        "semester_status",
        "day_hebrew",
        "start",
        "end",
        "name",
        "event_type",
        "event_duration",
        "lecturer",
        "course_id",
        "year",
        "credits",
        "weekly_hours",
        "prerequisites",
        "parallel_requirement",
        "course_type",
        "calculation_status",
    ]

    dataframe = dataframe[preferred_columns]

    dataframe.columns = [
        "סמסטר",
        "שם הסמסטר",
        "סטטוס סמסטר",
        "יום",
        "התחלה",
        "סיום",
        "שם הקורס",
        "סוג מפגש",
        "משך המפגש",
        "מרצה",
        "מספר קורס",
        "שנה מקורית",
        'נ"ז',
        'ש"ש',
        "דרישות קדם",
        "דרישות מקבילות",
        "סוג שיעור",
        "סטטוס חישוב שעות",
    ]

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        dataframe.to_excel(writer, sheet_name="מערכת שעות", index=False)

        worksheet = writer.sheets["מערכת שעות"]
        worksheet.sheet_view.rightToLeft = True
        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions

        for cell in worksheet[1]:
            cell.font = cell.font.copy(bold=True)

        widths = {
            "A": 10, "B": 25, "C": 18, "D": 10, "E": 10, "F": 10,
            "G": 42, "H": 14, "I": 12, "J": 25, "K": 14, "L": 12,
            "M": 10, "N": 10, "O": 45, "P": 35, "Q": 20, "R": 35,
        }

        for column, width in widths.items():
            worksheet.column_dimensions[column].width = width

    return output_path


def save_semester_plan_to_excel(
    timetable: list["ScheduledCourse"],
    output_path: str | Path = "semester_plan.xlsx",
) -> Path:
    """
    Save one row per COURSE (not per lecture/tutorial event),
    showing which semester the solver selected.
    """
    output_path = Path(output_path)

    seen = set()
    rows = []

    for item in timetable:
        if item.course_id in seen:
            continue

        seen.add(item.course_id)

        rows.append({
            "סמסטר": item.semester,
            "שם הסמסטר": item.semester_name,
            "סטטוס": item.semester_status,
            "מספר קורס": item.course_id,
            "שם הקורס": item.name,
            "שנה מקורית": item.year,
            "מרצה": item.lecturer,
            "דרישות קדם": item.prerequisites,
            "דרישות מקבילות": item.parallel_requirement,
            'נ"ז': item.credits,
            'ש"ש': item.weekly_hours,
            "שעות הרצאה": item.lecture_hours,
            "שעות תרגול": item.tutorial_hours,
        })

    dataframe = pd.DataFrame(rows).sort_values(
        ["סמסטר", "שם הקורס"],
        kind="stable",
    )

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        dataframe.to_excel(writer, sheet_name="תכנית לפי סמסטר", index=False)

        worksheet = writer.sheets["תכנית לפי סמסטר"]
        worksheet.sheet_view.rightToLeft = True
        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions

        for cell in worksheet[1]:
            cell.font = cell.font.copy(bold=True)

        widths = {
            "A": 10, "B": 25, "C": 18, "D": 14, "E": 42, "F": 12,
            "G": 25, "H": 45, "I": 35, "J": 10, "K": 10, "L": 15, "M": 15,
        }

        for column, width in widths.items():
            worksheet.column_dimensions[column].width = width

    return output_path
