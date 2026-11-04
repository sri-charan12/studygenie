"""Small helpers shared by the test files."""

from datetime import date, timedelta
from types import SimpleNamespace

import studygenie as sg


def ns(**kwargs):
    """Build the 'args' object that the command functions expect."""
    return SimpleNamespace(**kwargs)


def day(offset=0):
    """ISO date, `offset` days from today."""
    return (date.today() + timedelta(days=offset)).isoformat()


def rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params)]


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def seed(conn, maths_exam_in=6, history_exam_in=9):
    """Two subjects, three topics (300 min in total), two exams, 2 hours per day."""
    sg.cmd_add_subject(conn, ns(name="Maths", difficulty=4))
    sg.cmd_add_subject(conn, ns(name="History", difficulty=2))
    sg.cmd_add_topic(conn, ns(subject="Maths", name="Calculus", difficulty=5))
    sg.cmd_add_topic(conn, ns(subject="Maths", name="Algebra", difficulty=3))
    sg.cmd_add_topic(conn, ns(subject="History", name="World War 2", difficulty=2))
    sg.cmd_add_exam(conn, ns(subject="Maths", title="Midterm", date=day(maths_exam_in)))
    sg.cmd_add_exam(conn, ns(subject="History", title="Midterm", date=day(history_exam_in)))
    sg.cmd_set_hours(conn, ns(hours=2))


def plan(conn, days=14, scorer="rule"):
    sg.cmd_plan(conn, ns(days=days, scorer=scorer))
