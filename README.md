# University Timetable Scheduler - V8

## Flow

1. Run the frontend.
2. The first page asks for the department's teaching days and allowed hours.
3. Clicking **Build Timetable** saves those values to `timetable_backend/schedule_config.json`.
4. The frontend runs the backend solver automatically.
5. The solver reads from Excel:
   - prerequisites (`תיאור ד.קדם`)
   - parallel conditions (`תיאור ד.מקבילה`)
   - lecturer, year, credits, weekly hours, course type
6. The generated timetable opens on `/calendar`.

## Important constraints

- Prerequisite: `semester(prerequisite) < semester(course)`
- Parallel condition: `semester(parallel) <= semester(course)`
- Same lecturer cannot overlap.
- Events may only be placed on enabled department days and inside the allowed hours.
- Lecture/tutorial durations are derived from credits and weekly hours.

## Run

From the project root:

```powershell
pip install -r requirements.txt
python timetable_frontend\app.py
```

The browser opens on the input page first.


## V9 inputs

Before generating the timetable, the frontend now asks for:

- Number of years in the degree.
- Number of semesters in each year.
- Department teaching days.
- Allowed teaching start/end time for every enabled day.

The solver derives the normal number of semesters as:

`degree_years * semesters_per_year`

Prerequisites must be in an earlier semester, while parallel requirements
may be in the same semester or an earlier semester.
