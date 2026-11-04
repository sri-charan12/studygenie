StudyGenie – Rule-Based Student Study Planner

## Summary
StudyGenie is a Python command-line app that helps students decide **what to study, when, and for how long**.
You enter subjects, topics, exam dates and how many hours you can study per day. A rule-based scheduling engine
(a fixed formula, no AI or training data) scores every topic and builds a day-by-day plan. You then tick off sessions and track progress.
Everything is stored in a local **SQLite** database – no server or internet needed.

## How the rule-based planner works
For each study block it scores every pending topic:

`score = weight × urgency × (0.3 + remaining/estimated) × variety`

- **weight** – average of subject and topic difficulty (harder = more time)
- **urgency** – rises sharply as the subject's exam gets closer
- **remaining** – topics you've already studied get lower priority
- **variety** – penalty if the topic is already scheduled that day, so days are mixed

The highest score wins each 45-minute block until the day's time is full. Topics are never scheduled after their exam.
If time is too short, the planner warns you which topics didn't fit.

## SQLite tables
| Table | Purpose |
|---|---|
| `subjects` | name, difficulty (1–5) |
| `topics` | belongs to a subject; difficulty, estimated minutes, status (pending/done) |
| `exams` | belongs to a subject; title, date |
| `sessions` | planned/completed/skipped study blocks (topic, date, minutes) |
| `settings` | key/value (daily study minutes) |

Tables are created automatically on first run (or with `init`).

## Plan of work (project phases)
1. **Database design** – the 5 tables above, with foreign keys and CHECK constraints.
2. **CLI layer** – `argparse` sub-commands with positional arguments.
3. **Data entry commands** – add-subject, add-topic, add-exam, set-hours.
4. **Planning engine** – `plan` scores topics and writes sessions.
5. **Tracking** – today, done, skip, progress.
6. **User interface** – Streamlit app (`studygenie_ui.py`) with Dashboard, Add data, Plan and Today tabs.
7. **Future ideas** – spaced-repetition revision sessions, CSV/PDF export, delete/edit commands, reminders.

## Usage
Requires Python 3.10+. The command line uses only the standard library (Excel import/export needs `openpyxl`).

```bash
python studygenie.py init
python studygenie.py add-subject Maths 4            # NAME [DIFFICULTY 1-5]
python studygenie.py add-subject History 2
python studygenie.py add-topic Maths Calculus 5     # SUBJECT NAME [DIFFICULTY]
python studygenie.py add-topic Maths Algebra
python studygenie.py add-topic History "World War 2"
python studygenie.py add-exam Maths Midterm 2026-10-20   # SUBJECT TITLE YYYY-MM-DD
python studygenie.py set-hours 2                    # HOURS per day
python studygenie.py plan 7                         # DAYS (default 7)
python studygenie.py today
python studygenie.py done 1 40                      # SESSION_ID [MINUTES]
python studygenie.py skip 2                         # SESSION_ID
python studygenie.py progress
python studygenie.py list topics                    # subjects|topics|exams|sessions
```

Use `-h` on any command, e.g. `python studygenie.py plan -h`.
Use `--db other.db` (before the command) or the `STUDYGENIE_DB` env variable to choose another database file.

## Dashboard, login and Ask AI
```bash
pip install -r requirements.txt
python -m streamlit run studygenie_ui.py
```
The first screen is sign-up / login (accounts are stored in their own SQLite file, `studygenie_auth.db`).
The first account created becomes the admin; later sign-ups are students. For a quick demo run
`python sg_auth.py seed-demo` once (admin/Admin@123 and student/Student@123).
Set `STUDYGENIE_AUTH=0` to skip the login.

Every student has a **private planner**: their own file `data/<username>.db` (folder set with `STUDYGENIE_DATA_DIR`).
Students from different colleges and classes never see each other's subjects, topics, exams or plan, and the AI coach
only reads the signed-in student's own file. Students add their college and class when they sign up.

| | Student | Admin |
|---|---|---|
| Job | Everything on their own planner: add and manage subjects, topics, exams, hours; plan; study; Ask AI | Maintain user accounts and roles; pick any student in the sidebar to view and fix that student's planner; see every student's exams |
| Tabs | Dashboard, Add data, Manage, Plan, Today, Why this order, Live view | Dashboard, Add data, Manage, Live view, Users |
| Dashboard | Next exam, progress, exam countdown, Ask StudyGenie | Selected student's progress, plus the exam schedule of all students with college and class |

To give an existing planner file to a student, copy it: `copy studygenie_final.db data\ravi.db`
(or load sample data: `python studygenie.py --db data\ravi.db import-excel studygenie_data.xlsx`).
Deleting a user keeps their planner file; remove it by hand if it is no longer needed.

Ask StudyGenie (AI coach) sits on the dashboard. It needs an Anthropic API key, either in the
`ANTHROPIC_API_KEY` environment variable or pasted into the page. Your subjects, topics, exams and
sessions are sent to the AI service; accounts and passwords never are.
The UI calls the same functions as the command line, so both always agree.

## Daily workflow
`plan 7` → `today` → study → `done <id>` → `progress`. If you fall behind, run `plan` again –
it rebuilds the future schedule and keeps your history.

## Setup: where to put things
```
studygenie/                 <- any folder you like
├── studygenie.py           <- engine and command line
├── sg_core.py              <- scoring rules and planner
├── sg_auth.py              <- login, roles, permissions
├── sg_ai.py                <- Ask AI coach
├── studygenie_ui.py        <- the dashboard (run this)
├── studygenie_data.xlsx    <- example data (optional)
├── tests/                  <- run with: python -m pytest
├── studygenie_final.db     <- sample planner (single-user mode / template to copy)
├── data/                   <- one private planner per student (created automatically)
└── studygenie_auth.db      <- accounts (created on first login)
```
Open a terminal in that folder (in VS Code: Terminal > New Terminal) and run the commands below.

## Viewing the tables
- `python studygenie.py tables` – all tables, columns and row counts
- `python studygenie.py list subjects|topics|exams|sessions` – table contents
- GUI: open `studygenie.db` in **DB Browser for SQLite** (free) or the VS Code "SQLite Viewer" extension
- Sqlite shell: `sqlite3 studygenie.db` then `.tables`, `.schema`, `SELECT * FROM topics;`

## Excel
```bash
pip install openpyxl
python studygenie.py import-excel studygenie_data.xlsx   # Excel -> SQLite
python studygenie.py export-excel backup.xlsx            # SQLite -> Excel
```
Sheets: subjects, topics, exams, sessions, settings (topics/exams refer to subjects by name).