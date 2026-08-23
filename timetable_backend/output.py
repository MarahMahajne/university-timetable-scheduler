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
    """Save all course data, including calculated lecture/tutorial hours, to JSON."""
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
    """
    Create a new Excel file with the original course information plus:
    - calculated lecture hours
    - calculated tutorial hours
    - calculation status
    """
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
            "סטטוס חישוב": getattr(course, "calculation_status", "OK"),
        })

    dataframe = pd.DataFrame(rows)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        dataframe.to_excel(
            writer,
            sheet_name="קורסים מחושבים",
            index=False,
        )

        worksheet = writer.sheets["קורסים מחושבים"]

        # Hebrew / right-to-left display
        worksheet.sheet_view.rightToLeft = True

        # Keep headers visible while scrolling
        worksheet.freeze_panes = "A2"

        # Add filters
        worksheet.auto_filter.ref = worksheet.dimensions

        # Adjust column widths
        widths = {
            "A": 14,
            "B": 38,
            "C": 45,
            "D": 30,
            "E": 12,
            "F": 24,
            "G": 10,
            "H": 10,
            "I": 18,
            "J": 22,
            "K": 22,
            "L": 28,
        }

        for column, width in widths.items():
            worksheet.column_dimensions[column].width = width

        # Bold header row
        for cell in worksheet[1]:
            cell.font = cell.font.copy(bold=True)

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

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        dataframe.to_excel(writer, sheet_name="מערכת שעות", index=False)

        worksheet = writer.sheets["מערכת שעות"]
        worksheet.sheet_view.rightToLeft = True
        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions

    return output_path
