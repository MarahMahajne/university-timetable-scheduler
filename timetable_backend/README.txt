TIMETABLE BACKEND - FIXED VERSION

RUN:
    pip install -r requirements.txt
    python main.py

INPUT:
    courses.xlsx

IMPORTANT FIX:
Prerequisite parsing now splits only on a plus sign surrounded by spaces.
This prevents the course name "מבוא למדעי המחשב בשפת ++C" from being broken into separate strings.

RULES:
- prerequisite must be in an earlier semester
- parallel requirement may be earlier or in the same semester
- lecture/tutorial are scheduled separately
- same lecturer cannot overlap
- every course receives a place in its semester
- semesters 7-8 are allowed only as overflow when prerequisites cannot fit in semesters 1-6

OUTPUTS:
    courses.json
    courses_calculated.xlsx
    unresolved_requirements.json
    semester_plan.xlsx
    timetable.json
    timetable.xlsx
