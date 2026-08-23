TIMETABLE PROJECT V4 - LECTURE + TUTORIAL SPLIT

WHAT CHANGED
------------
The project now calculates lecture/tutorial hours directly from the original
Excel input using credits (נ"ז) and weekly hours (ש"ש).

Inferred equations:
    L + T = weekly_hours
    2L + T = credits

Therefore:
    L = credits - weekly_hours
    T = 2*weekly_hours - credits

The calculated values are written to courses.json.

The scheduler now creates separate events:
    Lecture
    Tutorial (only when tutorial_hours > 0)

Every event is scheduled exactly once.

CURRENT HARD CONSTRAINTS
------------------------
1. Every lecture/tutorial event must receive a time.
2. The same lecturer cannot teach overlapping events.
3. Events for the same study year cannot overlap.
4. Both required/elective courses are included; no course is discarded based on type.

IMPORTANT DATA VALIDATION
-------------------------
Some rows in the original Excel do not perfectly fit the inferred relationship.
Those courses receive a calculation_status warning in courses.json.

For a row that mathematically produces a negative duration, the code keeps the
application runnable by temporarily scheduling weekly_hours as one lecture and
0 tutorial, while marking the course REVIEW_REQUIRED.

HOW TO RUN
----------
1. Put the original Excel file next to main.py with this name:
   דרישות קדם (+מרצה, סמסטר ושנה, נז) - מדעי המחשב copy(1).xlsx

2. Install dependencies:
   pip install -r requirements.txt

3. Run:
   python main.py

OUTPUT
------
courses.json
    Includes lecture_hours, tutorial_hours and calculation_status.

timetable.json
    Contains separate Lecture/Tutorial events.

timetable.xlsx
    Human-readable event list including the calculated lecture/tutorial hours.
