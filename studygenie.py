#!/usr/bin/env python3

import csv
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from sg_core import (
    SCORERS,
    DuplicateError,
    Item,
    NotFoundError,
    Planner,
    Timer,
    ValidationError,
    clean_name,
    estimate_minutes,
    log,
    summarize,
)

DB_DEFAULT = os.environ.get("STUDYGENIE_DB", "studygenie.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS subjects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    difficulty  INTEGER NOT NULL CHECK (difficulty BETWEEN 1 AND 5),
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS topics (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id   INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    name         TEXT NOT NULL COLLATE NOCASE,
    difficulty   INTEGER NOT NULL CHECK (difficulty BETWEEN 1 AND 5),
    est_minutes  INTEGER NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','done')),
    UNIQUE (subject_id, name)
);

CREATE TABLE IF NOT EXISTS exams (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id  INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    exam_date   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id      INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    session_date  TEXT NOT NULL,
    minutes       INTEGER NOT NULL,
    status        TEXT NOT NULL DEFAULT 'planned'
                  CHECK (status IN ('planned','completed','skipped')),
    completed_at  TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions(session_date);
CREATE INDEX IF NOT EXISTS idx_topics_subject ON topics(subject_id);
"""


# ----------------------------------------------------------------- helpers
def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR IGNORE INTO settings VALUES ('daily_minutes', '180')")
    conn.execute("INSERT OR IGNORE INTO settings VALUES ('catch_up_first', '1')")
    conn.commit()
    return conn


def valid_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise ValidationError("date must be YYYY-MM-DD") from None


def get_subject(conn, name):
    name = clean_name(name)
    row = conn.execute("SELECT * FROM subjects WHERE name = ?", (name,)).fetchone()
    if not row:
        raise NotFoundError(f"subject '{name}' not found. Add it with: add-subject \"{name}\" 3")
    return row


def get_setting(conn, key):
    return conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()[0]


def get_setting_or(conn, key, default):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def skipped_backlog(conn):
    """[(subject, topic, minutes still owed)] for topics with skipped sessions not yet made up."""
    return [(i.subject, i.name, i.overdue) for i in load_items(conn, date.today(), 14) if i.overdue > 0]


# ---------------------------------------------------------------- commands


def require_name(value):
    name = clean_name(value)
    if not name:
        raise ValidationError("name cannot be empty")
    return name


def cmd_add_subject(conn, args):
    name = require_name(args.name)
    if conn.execute("SELECT 1 FROM subjects WHERE name = ?", (name,)).fetchone():
        raise DuplicateError(f"Duplicate found: subject '{name}' already exists")
    try:
        conn.execute("INSERT INTO subjects (name, difficulty) VALUES (?, ?)", (name, args.difficulty))
    except sqlite3.IntegrityError as exc:
        raise ValidationError(f"could not save subject '{name}': {exc}") from exc
    conn.commit()
    log.info("added subject %s", name)
    print(f"Added subject '{name}' (difficulty {args.difficulty}/5)")


def cmd_add_topic(conn, args):
    name = require_name(args.name)
    subj = get_subject(conn, args.subject)
    if conn.execute("SELECT 1 FROM topics WHERE subject_id = ? AND name = ?", (subj["id"], name)).fetchone():
        raise DuplicateError(f"Duplicate found: topic '{name}' already exists in {subj['name']}")
    diff = args.difficulty or subj["difficulty"]
    try:
        conn.execute(
            "INSERT INTO topics (subject_id, name, difficulty, est_minutes) VALUES (?,?,?,?)",
            (subj["id"], name, diff, estimate_minutes(diff)),
        )
    except sqlite3.IntegrityError as exc:
        raise ValidationError(f"could not save topic '{name}': {exc}") from exc
    conn.commit()
    log.info("added topic %s", name)
    print(f"Added topic '{name}' to {subj['name']} (difficulty {diff}, ~{estimate_minutes(diff)} min)")


def find_exam(conn, subject_id, title):
    """An exam counts as a duplicate when subject and title match (ignoring case and spacing)."""
    return conn.execute(
        "SELECT id, exam_date FROM exams WHERE subject_id = ? AND title = ? COLLATE NOCASE",
        (subject_id, clean_name(title)),
    ).fetchone()


def cmd_add_exam(conn, args):
    title = require_name(args.title)
    subj = get_subject(conn, args.subject)
    found = find_exam(conn, subj["id"], title)
    if found:
        raise DuplicateError(
            f"Duplicate found: exam '{title}' already exists for {subj['name']} "
            f"(on {found['exam_date']}). Use edit-exam to change its date."
        )
    conn.execute(
        "INSERT INTO exams (subject_id, title, exam_date) VALUES (?,?,?)", (subj["id"], title, args.date)
    )
    conn.commit()
    log.info("added exam %s", title)
    print(f"Added exam '{title}' for {subj['name']} on {args.date}")


def cmd_set_hours(conn, args):
    minutes = int(args.hours * 60)
    conn.execute("INSERT OR REPLACE INTO settings VALUES ('daily_minutes', ?)", (str(minutes),))
    conn.commit()
    print(f"Daily study time set to {args.hours} h ({minutes} min)")


WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def get_rest_days(conn) -> frozenset[int]:
    """Weekdays (0 = Monday) on which no study is planned."""
    row = conn.execute("SELECT value FROM settings WHERE key = 'rest_days'").fetchone()
    return frozenset(int(x) for x in row[0].split(",") if x) if row else frozenset()


def load_items(conn, today: date, days: int) -> list[Item]:
    """Pending topics with their remaining minutes and last allowed study day."""
    rows = conn.execute(
        """
        SELECT t.id, t.name, t.difficulty AS tdiff, t.est_minutes,
               s.name AS sname, s.difficulty AS sdiff,
               (SELECT MIN(exam_date) FROM exams e
                 WHERE e.subject_id = s.id AND e.exam_date >= ?) AS exam_date,
               COALESCE((SELECT SUM(minutes) FROM sessions x
                          WHERE x.topic_id = t.id AND x.status = 'completed'), 0) AS done_min,
               COALESCE((SELECT SUM(minutes) FROM sessions x
                          WHERE x.topic_id = t.id AND x.status = 'skipped'), 0) AS skipped_min,
               COALESCE((SELECT SUM(minutes) FROM sessions x
                          WHERE x.topic_id = t.id AND x.status = 'completed'
                            AND x.session_date >= (SELECT MIN(session_date) FROM sessions y
                                                    WHERE y.topic_id = t.id AND y.status = 'skipped')), 0) AS madeup_min
        FROM topics t JOIN subjects s ON s.id = t.subject_id
        WHERE t.status = 'pending'""",
        (today.isoformat(),),
    ).fetchall()
    horizon_end = today + timedelta(days=days - 1)
    items = []
    for r in rows:
        remaining = max(r["est_minutes"] - r["done_min"], 0)
        if remaining == 0:
            continue
        if r["exam_date"]:
            last_day = max(date.fromisoformat(r["exam_date"]) - timedelta(days=1), today)
        else:
            last_day = horizon_end
        items.append(
            Item(
                r["id"],
                r["name"],
                r["sname"],
                r["est_minutes"],
                remaining,
                (r["sdiff"] + r["tdiff"]) / 2,
                min(last_day, horizon_end),
                min(max(r["skipped_min"] - r["madeup_min"], 0), remaining),
            )
        )
    return items


def cmd_set_rest_days(conn, args):
    days = sorted({WEEKDAYS.index(d) for d in args.days if d != "none"})
    conn.execute("INSERT OR REPLACE INTO settings VALUES ('rest_days', ?)", (",".join(map(str, days)),))
    conn.commit()
    names = ", ".join(WEEKDAYS[d] for d in days) or "none"
    log.info("rest days set to %s", names)
    print(f"Rest days set to: {names}. Generate the plan again to apply.")


def get_topic(conn, subject, name):
    subj = get_subject(conn, subject)
    row = conn.execute(
        "SELECT * FROM topics WHERE subject_id = ? AND name = ?", (subj["id"], name)
    ).fetchone()
    if not row:
        raise NotFoundError(f"topic '{name}' not found in {subj['name']}")
    return row


def cmd_delete_subject(conn, args):
    subj = get_subject(conn, args.name)
    conn.execute("DELETE FROM subjects WHERE id = ?", (subj["id"],))
    conn.commit()
    log.info("deleted subject %s", subj["name"])
    print(f"Deleted subject '{subj['name']}' with its topics, exams and sessions")


def cmd_delete_topic(conn, args):
    topic = get_topic(conn, args.subject, args.name)
    conn.execute("DELETE FROM topics WHERE id = ?", (topic["id"],))
    conn.commit()
    log.info("deleted topic %s", args.name)
    print(f"Deleted topic '{args.name}' and its sessions")


def cmd_edit_topic(conn, args):
    topic = get_topic(conn, args.subject, args.name)
    est = estimate_minutes(args.difficulty)
    done = conn.execute(
        "SELECT COALESCE(SUM(minutes), 0) FROM sessions WHERE topic_id = ? AND status = 'completed'",
        (topic["id"],),
    ).fetchone()[0]
    status = "done" if done >= est else "pending"
    conn.execute(
        "UPDATE topics SET difficulty = ?, est_minutes = ?, status = ? WHERE id = ?",
        (args.difficulty, est, status, topic["id"]),
    )
    conn.commit()
    log.info("edited topic %s: difficulty %s", args.name, args.difficulty)
    print(f"Topic '{args.name}' is now difficulty {args.difficulty} (~{est} min). Generate the plan again to apply.")


def cmd_delete_exam(conn, args):
    cur = conn.execute("DELETE FROM exams WHERE id = ?", (args.exam_id,))
    conn.commit()
    if cur.rowcount == 0:
        raise NotFoundError(f"exam {args.exam_id} not found")
    log.info("deleted exam %s", args.exam_id)
    print(f"Deleted exam {args.exam_id}")


def cmd_edit_exam(conn, args):
    cur = conn.execute("UPDATE exams SET exam_date = ? WHERE id = ?", (args.date, args.exam_id))
    conn.commit()
    if cur.rowcount == 0:
        raise NotFoundError(f"exam {args.exam_id} not found")
    log.info("exam %s moved to %s", args.exam_id, args.date)
    print(f"Exam {args.exam_id} moved to {args.date}. Generate the plan again to apply.")


def cmd_plan(conn, args):
    """Score topics with the chosen rule and fill each day's study time."""
    today = date.today()
    daily = int(get_setting(conn, "daily_minutes"))
    scorer = SCORERS[getattr(args, "scorer", "rule")]()
    rest = get_rest_days(conn)

    # Planned blocks from earlier days were never done: mark them skipped.
    conn.execute(
        "UPDATE sessions SET status = 'skipped' WHERE status = 'planned' AND session_date < ?",
        (today.isoformat(),),
    )
    # Re-planning wipes only future planned sessions; history is kept.
    conn.execute("DELETE FROM sessions WHERE status = 'planned' AND session_date >= ?", (today.isoformat(),))

    items = load_items(conn, today, args.days)
    catch_up = get_setting_or(conn, "catch_up_first", "1") == "1"
    backlog = [i for i in items if i.overdue > 0]
    if catch_up and backlog:
        print("Catch-up first: you skipped these, so the plan finishes them before any new topic:")
        for i in backlog:
            print(f"  - {i.subject} / {i.name}: {i.overdue} min")
        print()

    if not items:
        conn.commit()
        print("No pending topics to plan. Add topics on the Add data tab.")
        return

    with Timer() as timer:
        blocks = Planner(scorer, catch_up=catch_up).build(items, today, args.days, daily, rest)
    conn.executemany(
        "INSERT INTO sessions (topic_id, session_date, minutes) VALUES (?,?,?)",
        [(b.topic_id, b.day.isoformat(), b.minutes) for b in blocks],
    )
    conn.commit()

    total = sum(b.minutes for b in blocks)
    per_day, per_subject = summarize(blocks)
    log.info("plan created: %s min over %s day(s) with %r", total, len(per_day), scorer)
    print(f"Plan created for {args.days} day(s): {total} min scheduled " f"({daily} min/day available).")
    print(f"Rule: {scorer!r} - built in {timer.ms:.1f} ms")
    if rest:
        print("Rest days: " + ", ".join(WEEKDAYS[d] for d in sorted(rest)))
    if per_subject:
        print("\nMinutes per subject:")
        for name, mins in per_subject.most_common():
            print(f"  {name}: {mins}")
    unplanned = [i for i in items if i.remaining > 0]
    if unplanned:
        print("\nWarning - not enough time for:")
        for i in unplanned:
            print(f"  - {i.subject} / {i.name}: {i.remaining} min left unscheduled")
        print("Increase the daily hours or plan more days.")
    

def cmd_done(conn, args):
    s = conn.execute("SELECT * FROM sessions WHERE id = ?", (args.session_id,)).fetchone()
    if not s:
        raise NotFoundError(f"session {args.session_id} not found")
    minutes = args.minutes or s["minutes"]
    conn.execute(
        "UPDATE sessions SET status='completed', minutes=?, completed_at=? WHERE id=?",
        (minutes, datetime.now().isoformat(timespec="seconds"), s["id"]),
    )
    t = conn.execute(
        """SELECT t.est_minutes,
        COALESCE((SELECT SUM(minutes) FROM sessions WHERE topic_id=t.id AND status='completed'),0) AS done
        FROM topics t WHERE t.id = ?""",
        (s["topic_id"],),
    ).fetchone()
    if t["done"] >= t["est_minutes"]:
        conn.execute("UPDATE topics SET status='done' WHERE id=?", (s["topic_id"],))
        print("Topic fully covered - marked as done!")
    conn.commit()
    log.info("session %s completed (%s min)", s["id"], minutes)
    print(f"Session {s['id']} completed ({minutes} min). Nice work!")


def cmd_skip(conn, args):
    cur = conn.execute("UPDATE sessions SET status='skipped' WHERE id=?", (args.session_id,))
    conn.commit()
    if cur.rowcount == 0:
        raise NotFoundError(f"session {args.session_id} not found")
    log.info("session %s skipped", args.session_id)
    print(f"Session {args.session_id} skipped. Generate the plan again: this topic will be finished before any new one starts.")


def cmd_export_excel(conn, args):
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    q = {
        "subjects": "SELECT id, name, difficulty FROM subjects ORDER BY id",
        "topics": "SELECT t.id, s.name AS subject, t.name, t.difficulty, t.est_minutes, t.status "
        "FROM topics t JOIN subjects s ON s.id=t.subject_id ORDER BY t.id",
        "exams": "SELECT e.id, s.name AS subject, e.title, e.exam_date FROM exams e "
        "JOIN subjects s ON s.id=e.subject_id ORDER BY e.id",
        "sessions": "SELECT x.id, x.session_date, s.name AS subject, t.name AS topic, x.minutes, x.status "
        "FROM sessions x JOIN topics t ON t.id=x.topic_id JOIN subjects s ON s.id=t.subject_id "
        "ORDER BY x.id",
        "settings": "SELECT key, value FROM settings",
    }
    for name, sql in q.items():
        ws = wb.create_sheet(name)
        cur = conn.execute(sql)
        ws.append([c[0] for c in cur.description])
        for row in cur:
            ws.append(list(row))
    wb.save(args.file)
    print(f"Exported to {args.file}")


def cmd_import_excel(conn, args):
    from openpyxl import load_workbook

    wb = load_workbook(args.file, data_only=True)
    added = {"subjects": 0, "topics": 0, "exams": 0}
    duplicates = 0
    for r in list(wb["subjects"].iter_rows(min_row=2, values_only=True)):
        if r[1] and clean_name(str(r[1])):
            cur = conn.execute(
                "INSERT OR IGNORE INTO subjects (name, difficulty) VALUES (?,?)",
                (clean_name(str(r[1])), int(r[2])),
            )
            added["subjects"] += cur.rowcount
            duplicates += 1 - cur.rowcount
    for r in list(wb["topics"].iter_rows(min_row=2, values_only=True)):
        if not (r[1] and r[2]):
            continue
        subj = get_subject(conn, str(r[1]))
        diff = int(r[3])
        est = int(r[4]) if r[4] else estimate_minutes(diff)
        cur = conn.execute(
            "INSERT OR IGNORE INTO topics (subject_id, name, difficulty, est_minutes) VALUES (?,?,?,?)",
            (subj["id"], clean_name(str(r[2])), diff, est),
        )
        added["topics"] += cur.rowcount
        duplicates += 1 - cur.rowcount
    for r in list(wb["exams"].iter_rows(min_row=2, values_only=True)):
        if not (r[1] and r[2] and r[3]):
            continue
        subj = get_subject(conn, str(r[1]))
        d = r[3].date().isoformat() if hasattr(r[3], "date") else valid_date(str(r[3]))
        if find_exam(conn, subj["id"], str(r[2])):
            duplicates += 1
            continue
        conn.execute(
            "INSERT INTO exams (subject_id, title, exam_date) VALUES (?,?,?)",
            (subj["id"], clean_name(str(r[2])), d),
        )
        added["exams"] += 1
    conn.commit()
    log.info("import-excel: %s, %s duplicate(s) skipped", added, duplicates)
    print(
        f"Imported {added['subjects']} subjects, {added['topics']} topics, {added['exams']} exams "
        f"from {args.file}"
    )
    if duplicates:
        print(f"Caution: Duplicate found - {duplicates} row(s) already existed and were skipped")


def export_format(path: str) -> str:
    """Pick the export format from the file suffix (structural pattern matching)."""
    match Path(path).suffix.lower():
        case ".json":
            return "json"
        case ".csv":
            return "csv"
        case ".xlsx":
            return "xlsx"
        case other:
            raise ValidationError(f"unsupported file type '{other}' (use .json, .csv or .xlsx)")


def cmd_export(conn, args):
    """Save data as JSON (all tables), CSV (sessions) or Excel, chosen by file suffix."""
    path = Path(args.file)
    match export_format(args.file):
        case "json":
            tables = ("subjects", "topics", "exams", "sessions", "settings")
            data = {t: [dict(r) for r in conn.execute(f"SELECT * FROM {t}")] for t in tables}
            with path.open("w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
        case "csv":
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT x.session_date AS date, s.name AS subject, t.name AS topic, x.minutes, x.status "
                    "FROM sessions x JOIN topics t ON t.id=x.topic_id JOIN subjects s ON s.id=t.subject_id "
                    "ORDER BY x.session_date, x.id"
                )
            ]
            with path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=["date", "subject", "topic", "minutes", "status"])
                writer.writeheader()
                writer.writerows(rows)
        case "xlsx":
            cmd_export_excel(conn, args)
            return
    log.info("exported %s", path.name)
    print(f"Exported to {path}")


# ------------------------------------------------------------------- parser


def error_label(exc: Exception) -> str:
    return "Caution" if isinstance(exc, DuplicateError) else "Error"


@contextmanager
def open_db(path: str) -> Iterator[sqlite3.Connection]:
    """Open the database, roll back if something fails, always close it."""
    conn = connect(path)
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()