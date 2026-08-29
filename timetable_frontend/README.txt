RUN THE FULL APPLICATION
========================

From the main timeTabling folder:

    pip install -r requirements.txt
    python timetable_frontend\app.py

The browser opens automatically.

In the page:
1. Select the days when the department can teach.
2. Enter the allowed start/end time for every enabled day.
3. Click "Build timetable".
4. The frontend saves the settings to timetable_backend/schedule_config.json.
5. It runs timetable_backend/main.py automatically.
6. The page reloads and displays the new timetable.

The solver also reads BOTH prerequisite and parallel-condition columns from courses.xlsx.
