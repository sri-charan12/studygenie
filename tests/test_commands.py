"""Tests for the engine commands in studygenie.py (each test gets a fresh database)."""

import json
import sqlite3
from datetime import date, timedelta

import pytest
from helpers import count, day, ns, plan, rows, seed

import sg_core as core
import studygenie as sg


def run_cli(db, *argv):
    sg.main(["--db", str(db), *argv])


# -------------------------------------------------------------- data entry
def test_data_entry_saves_everything(conn):
    seed(conn)
    assert [r["name"] for r in rows(conn, "SELECT name FROM subjects ORDER BY name")] == ["History", "Maths"]
    assert count(conn, "topics") == 3
    assert count(conn, "exams") == 2
    assert conn.execute("SELECT est_minutes FROM topics WHERE name = 'Calculus'").fetchone()[0] == 150
    assert int(sg.get_setting(conn, "daily_minutes")) == 120


def test_unknown_subject_is_reported(conn):
    with pytest.raises(core.NotFoundError):
        sg.cmd_add_topic(conn, ns(subject="Nope", name="X", difficulty=3))


def test_engine_rejects_empty_names(conn):
    with pytest.raises(core.ValidationError):
        sg.cmd_add_subject(conn, ns(name="   ", difficulty=3))
    assert count(conn, "subjects") == 0


@pytest.mark.parametrize(
    "argv",
    [
        ["add-subject", "  ", "3"],
        ["add-subject", "Maths", "9"],
        ["add-subject", "Maths", "0"],
        ["set-hours", "21"],
        ["set-hours", "0"],
        ["add-exam", "Maths", "Mid", "31-12-2026"],
        ["plan", "0"],
    ],
)
def test_command_line_rejects_bad_input(argv):
    with pytest.raises(SystemExit):
        sg.build_parser().parse_args(argv)


def test_names_are_cleaned_before_saving(conn):
    sg.cmd_add_subject(conn, ns(name="  Data   Structures ", difficulty=3))
    assert rows(conn, "SELECT name FROM subjects") == [{"name": "Data Structures"}]


# ---------------------------------------------------------------- planning
def test_plan_covers_every_topic(conn):
    seed(conn)
    plan(conn)
    assert conn.execute("SELECT SUM(minutes) FROM sessions").fetchone()[0] == 300


def test_plan_never_schedules_on_or_after_the_exam(conn):
    seed(conn)
    plan(conn)
    late = conn.execute("""SELECT COUNT(*) FROM sessions x JOIN topics t ON t.id = x.topic_id
           JOIN exams e ON e.subject_id = t.subject_id WHERE x.session_date >= e.exam_date""").fetchone()[0]
    assert late == 0


def test_plan_respects_the_daily_hours(conn):
    seed(conn)
    plan(conn)
    per_day = [r[0] for r in conn.execute("SELECT SUM(minutes) FROM sessions GROUP BY session_date")]
    assert max(per_day) <= 120


def test_plan_skips_rest_days(conn):
    seed(conn)
    rest = (date.today() + timedelta(days=1)).weekday()
    sg.cmd_set_rest_days(conn, ns(days=[sg.WEEKDAYS[rest]]))
    plan(conn)
    weekdays = {date.fromisoformat(r[0]).weekday() for r in conn.execute("SELECT session_date FROM sessions")}
    assert rest not in weekdays


def test_rest_days_round_trip(conn):
    sg.cmd_set_rest_days(conn, ns(days=["sat", "sun"]))
    assert sg.get_rest_days(conn) == frozenset({5, 6})
    sg.cmd_set_rest_days(conn, ns(days=["none"]))
    assert sg.get_rest_days(conn) == frozenset()


def test_replanning_gives_the_same_plan(conn):
    seed(conn)
    sql = "SELECT topic_id, session_date, minutes FROM sessions ORDER BY id"
    plan(conn)
    first = rows(conn, sql)
    plan(conn)
    assert rows(conn, sql) == first


@pytest.mark.parametrize("rule", sorted(core.SCORERS))
def test_every_scoring_rule_produces_a_plan(conn, rule):
    seed(conn)
    plan(conn, scorer=rule)
    assert count(conn, "sessions") > 0


def test_planning_without_topics_says_so(conn, capsys):
    plan(conn)
    assert "No pending topics" in capsys.readouterr().out
    assert count(conn, "sessions") == 0


def test_replanning_keeps_completed_history(conn):
    seed(conn)
    plan(conn)
    first = conn.execute("SELECT id, topic_id FROM sessions ORDER BY id").fetchone()
    sg.cmd_done(conn, ns(session_id=first["id"], minutes=None))
    plan(conn)
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE status = 'completed'").fetchone()[0] == 1
    total = conn.execute(
        "SELECT SUM(minutes) FROM sessions WHERE topic_id = ?", (first["topic_id"],)
    ).fetchone()[0]
    assert (
        total
        == conn.execute("SELECT est_minutes FROM topics WHERE id = ?", (first["topic_id"],)).fetchone()[0]
    )


def test_old_planned_blocks_become_skipped_on_replan(conn):
    seed(conn)
    plan(conn)
    sid = conn.execute("SELECT id FROM sessions ORDER BY id").fetchone()[0]
    conn.execute("UPDATE sessions SET session_date = ? WHERE id = ?", (day(-1), sid))
    plan(conn)
    assert conn.execute("SELECT status FROM sessions WHERE id = ?", (sid,)).fetchone()[0] == "skipped"


# --------------------------------------------------------------- tracking
def test_done_records_actual_minutes(conn):
    seed(conn)
    plan(conn)
    sid = conn.execute("SELECT id FROM sessions ORDER BY id").fetchone()[0]
    sg.cmd_done(conn, ns(session_id=sid, minutes=20))
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
    assert (row["status"], row["minutes"]) == ("completed", 20)
    assert row["completed_at"]


def test_topic_is_done_once_fully_covered(conn):
    seed(conn)
    plan(conn)
    ids = [
        r[0]
        for r in conn.execute(
            "SELECT x.id FROM sessions x JOIN topics t ON t.id = x.topic_id WHERE t.name = 'World War 2'"
        )
    ]
    for sid in ids:
        sg.cmd_done(conn, ns(session_id=sid, minutes=None))
    assert conn.execute("SELECT status FROM topics WHERE name = 'World War 2'").fetchone()[0] == "done"


def test_skip_marks_the_session(conn):
    seed(conn)
    plan(conn)
    sid = conn.execute("SELECT id FROM sessions ORDER BY id").fetchone()[0]
    sg.cmd_skip(conn, ns(session_id=sid))
    assert conn.execute("SELECT status FROM sessions WHERE id = ?", (sid,)).fetchone()[0] == "skipped"


def test_unknown_session_is_reported(conn):
    with pytest.raises(core.NotFoundError):
        sg.cmd_done(conn, ns(session_id=999, minutes=None))
    with pytest.raises(core.NotFoundError):
        sg.cmd_skip(conn, ns(session_id=999))


# --------------------------------------------------------- edit and delete
def test_deleting_a_subject_removes_everything_under_it(conn):
    seed(conn)
    plan(conn)
    sg.cmd_delete_subject(conn, ns(name="Maths"))
    assert rows(conn, "SELECT name FROM subjects") == [{"name": "History"}]
    assert count(conn, "topics") == 1
    assert count(conn, "exams") == 1
    assert conn.execute("SELECT SUM(minutes) FROM sessions").fetchone()[0] == 60


def test_deleting_a_topic_removes_its_sessions(conn):
    seed(conn)
    plan(conn)
    sg.cmd_delete_topic(conn, ns(subject="Maths", name="Calculus"))
    assert count(conn, "topics") == 2
    assert conn.execute("SELECT SUM(minutes) FROM sessions").fetchone()[0] == 150


def test_editing_a_topic_updates_difficulty_and_estimate(conn):
    seed(conn)
    sg.cmd_edit_topic(conn, ns(subject="Maths", name="Algebra", difficulty=5))
    row = conn.execute("SELECT difficulty, est_minutes FROM topics WHERE name = 'Algebra'").fetchone()
    assert (row[0], row[1]) == (5, 150)


def test_moving_and_deleting_an_exam(conn):
    seed(conn)
    exam_id = conn.execute("SELECT id FROM exams ORDER BY id").fetchone()[0]
    sg.cmd_edit_exam(conn, ns(exam_id=exam_id, date=day(20)))
    assert conn.execute("SELECT exam_date FROM exams WHERE id = ?", (exam_id,)).fetchone()[0] == day(20)
    sg.cmd_delete_exam(conn, ns(exam_id=exam_id))
    assert count(conn, "exams") == 1


def test_missing_things_are_reported(conn):
    seed(conn)
    with pytest.raises(core.NotFoundError):
        sg.cmd_delete_exam(conn, ns(exam_id=999))
    with pytest.raises(core.NotFoundError):
        sg.cmd_edit_exam(conn, ns(exam_id=999, date=day(3)))
    with pytest.raises(core.NotFoundError):
        sg.cmd_delete_topic(conn, ns(subject="Maths", name="Nope"))
    with pytest.raises(core.NotFoundError):
        sg.cmd_delete_subject(conn, ns(name="Nope"))


# ------------------------------------------------------------ export, import
def test_export_json_contains_every_table(conn, tmp_path):
    seed(conn)
    plan(conn)
    target = tmp_path / "data.json"
    sg.cmd_export(conn, ns(file=str(target)))
    data = json.loads(target.read_text(encoding="utf-8"))
    assert set(data) == {"subjects", "topics", "exams", "sessions", "settings"}
    assert len(data["subjects"]) == 2


def test_export_csv_has_a_header_and_all_sessions(conn, tmp_path):
    seed(conn)
    plan(conn)
    target = tmp_path / "plan.csv"
    sg.cmd_export(conn, ns(file=str(target)))
    lines = target.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "date,subject,topic,minutes,status"
    assert len(lines) - 1 == count(conn, "sessions")


def test_export_rejects_unknown_file_types(conn, tmp_path):
    with pytest.raises(core.ValidationError):
        sg.cmd_export(conn, ns(file=str(tmp_path / "plan.txt")))


def test_excel_export_and_import_round_trip(conn, tmp_path, db_path):
    pytest.importorskip("openpyxl")
    seed(conn)
    target = tmp_path / "backup.xlsx"
    sg.cmd_export(conn, ns(file=str(target)))
    with sg.open_db(str(tmp_path / "other.db")) as other:
        sg.cmd_import_excel(other, ns(file=str(target)))
        assert (count(other, "subjects"), count(other, "topics"), count(other, "exams")) == (2, 3, 2)


def test_shipped_sample_data_imports(conn):
    pytest.importorskip("openpyxl")
    sample = sg.Path(__file__).resolve().parent.parent / "studygenie_data.xlsx"
    if not sample.exists():
        pytest.skip("sample workbook not found")
    sg.cmd_import_excel(conn, ns(file=str(sample)))
<<<<<<< HEAD
    assert (count(conn, "subjects"), count(conn, "topics"), count(conn, "exams")) == (4, 10, 4)
=======
    assert (count(conn, "subjects"), count(conn, "topics"), count(conn, "exams")) == (5, 17, 5)
>>>>>>> 56917d7ad2e0548d676f020bd028ced300768960


# ---------------------------------------------------------- database access
def test_open_db_closes_the_connection(db_path):
    with sg.open_db(str(db_path)) as connection:
        connection.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_open_db_rolls_back_when_something_fails(db_path):
    with pytest.raises(RuntimeError), sg.open_db(str(db_path)) as connection:
        connection.execute("INSERT INTO subjects (name, difficulty) VALUES ('Temp', 3)")
        raise RuntimeError("boom")
    with sg.open_db(str(db_path)) as connection:
        assert count(connection, "subjects") == 0


def test_schema_blocks_out_of_range_difficulty(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO subjects (name, difficulty) VALUES ('X', 9)")


# -------------------------------------------------------------- command line
def test_cli_adds_and_lists(db_path, capsys):
    run_cli(db_path, "add-subject", "Maths", "3")
    run_cli(db_path, "list", "subjects")
    assert "Maths" in capsys.readouterr().out


def test_cli_errors_exit_with_code_1(db_path, capsys):
    with pytest.raises(SystemExit) as stop:
        run_cli(db_path, "add-topic", "Nope", "X")
    assert stop.value.code == 1
    assert "Error:" in capsys.readouterr().err


def test_cli_plan_prints_a_summary(db_path, capsys):
    run_cli(db_path, "add-subject", "Maths", "3")
    run_cli(db_path, "add-topic", "Maths", "Algebra", "3")
    run_cli(db_path, "add-exam", "Maths", "Midterm", day(5))
    run_cli(db_path, "plan", "7", "--scorer", "exam-week")
    out = capsys.readouterr().out
    assert "Plan created for 7 day(s)" in out
    assert "ExamWeekScorer" in out
