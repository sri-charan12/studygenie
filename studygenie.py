#!/usr/bin/env python3

import argparse
import csv
import json
import os
import sqlite3
import sys
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
    StudyGenieError,
    Timer,
    ValidationError,
    clean_name,
    estimate_minutes,
    log,
    setup_logging,
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
    conn.commit()
    return conn


def level(value):
    v = int(value)
    if not 1 <= v <= 5:
        raise argparse.ArgumentTypeError("difficulty must be between 1 and 5")
    return v


def valid_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD") from None


def nonempty(value):
    v = clean_name(value)
    if not v:
        raise argparse.ArgumentTypeError("name cannot be empty")
    return v


def hours_value(value):
    v = float(value)
    if not 0.5 <= v <= 16:
        raise argparse.ArgumentTypeError("hours must be between 0.5 and 16")
    return v


def positive_int(value):
    v = int(value)
    if v < 1:
        raise argparse.ArgumentTypeError("must be a positive number")
    return v


def get_subject(conn, name):
    name = clean_name(name)
    row = conn.execute("SELECT * FROM subjects WHERE name = ?", (name,)).fetchone()
    if not row:
        raise NotFoundError(f"subject '{name}' not found. Add it with: add-subject \"{name}\" 3")
    return row


def get_setting(conn, key):
    return conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()[0]


def table(headers, rows):
    if not rows:
        print("(nothing to show)")
        return
    widths = [max(len(str(h)), *(len(str(r[i])) for r in rows)) for i, h in enumerate(headers)]
    line = "  ".join(str(h).ljust(w) for h, w in zip(headers, widths, strict=True))
    print(line)
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(str(c).ljust(w) for c, w in zip(r, widths, strict=True)))


# ---------------------------------------------------------------- commands
def cmd_init(conn, args):
    print(f"Database ready at: {args.db}")


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


def cmd_list(conn, args):
    if args.what == "subjects":
        rows = conn.execute(
            "SELECT s.id, s.name, s.difficulty, COUNT(t.id) FROM subjects s "
            "LEFT JOIN topics t ON t.subject_id = s.id GROUP BY s.id ORDER BY s.name"
        ).fetchall()
        table(["ID", "SUBJECT", "DIFFICULTY", "TOPICS"], rows)
    elif args.what == "topics":
        rows = conn.execute(
            "SELECT t.id, s.name, t.name, t.difficulty, t.est_minutes, t.status "
            "FROM topics t JOIN subjects s ON s.id = t.subject_id ORDER BY s.name, t.id"
        ).fetchall()
        table(["ID", "SUBJECT", "TOPIC", "DIFF", "EST_MIN", "STATUS"], rows)
    elif args.what == "exams":
        rows = conn.execute(
            "SELECT e.id, s.name, e.title, e.exam_date FROM exams e "
            "JOIN subjects s ON s.id = e.subject_id ORDER BY e.exam_date"
        ).fetchall()
        table(["ID", "SUBJECT", "EXAM", "DATE"], rows)
    else:
        rows = conn.execute(
            "SELECT x.id, x.session_date, s.name, t.name, x.minutes, x.status "
            "FROM sessions x JOIN topics t ON t.id = x.topic_id "
            "JOIN subjects s ON s.id = t.subject_id ORDER BY x.session_date, x.id"
        ).fetchall()
        table(["ID", "DATE", "SUBJECT", "TOPIC", "MIN", "STATUS"], rows)


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
                          WHERE x.topic_id = t.id AND x.status = 'completed'), 0) AS done_min
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
            )
        )
    return items


def cmd_set_rest_days(conn, args):
    days = sorted({WEEKDAYS.index(d) for d in args.days if d != "none"})
    conn.execute("INSERT OR REPLACE INTO settings VALUES ('rest_days', ?)", (",".join(map(str, days)),))
    conn.commit()
    names = ", ".join(WEEKDAYS[d] for d in days) or "none"
    log.info("rest days set to %s", names)
    print(f"Rest days set to: {names}. Run 'plan' to apply.")


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
    print(f"Topic '{args.name}' is now difficulty {args.difficulty} (~{est} min). Run 'plan' to apply.")


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
    print(f"Exam {args.exam_id} moved to {args.date}. Run 'plan' to apply.")


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

    if not items:
        conn.commit()
        print("No pending topics to plan. Add topics with: add-topic <subject> <n> [difficulty]")
        return

    with Timer() as timer:
        blocks = Planner(scorer).build(items, today, args.days, daily, rest)
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
        print("Increase daily hours (set-hours) or plan more days.")
    print("\nSee it with: today   or   list sessions")


def cmd_today(conn, args):
    d = date.today().isoformat()
    rows = conn.execute(
        "SELECT x.id, s.name, t.name, x.minutes, x.status FROM sessions x "
        "JOIN topics t ON t.id = x.topic_id JOIN subjects s ON s.id = t.subject_id "
        "WHERE x.session_date = ? ORDER BY x.id",
        (d,),
    ).fetchall()
    print(f"Study plan for {d}")
    table(["ID", "SUBJECT", "TOPIC", "MIN", "STATUS"], rows)
    if rows:
        print("\nMark finished with: done <ID> [minutes]")


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
    print(f"Session {args.session_id} skipped. Run 'plan' again to reschedule.")


def cmd_progress(conn, args):
    rows = conn.execute("""
        SELECT s.name,
               COUNT(t.id) AS total,
               SUM(t.status='done') AS done,
               COALESCE((SELECT SUM(x.minutes) FROM sessions x JOIN topics tt ON tt.id=x.topic_id
                          WHERE tt.subject_id=s.id AND x.status='completed'),0) AS mins
        FROM subjects s LEFT JOIN topics t ON t.subject_id = s.id
        GROUP BY s.id ORDER BY s.name""").fetchall()
    out = []
    for r in rows:
        total, done = r["total"], r["done"] or 0
        pct = int(100 * done / total) if total else 0
        bar = "#" * (pct // 10) + "." * (10 - pct // 10)
        out.append([r["name"], f"{done}/{total}", f"[{bar}] {pct}%", f"{r['mins']} min"])
    table(["SUBJECT", "TOPICS", "PROGRESS", "STUDIED"], out)

    exams = conn.execute(
        "SELECT s.name, e.title, e.exam_date FROM exams e JOIN subjects s ON s.id=e.subject_id "
        "WHERE e.exam_date >= ? ORDER BY e.exam_date",
        (date.today().isoformat(),),
    ).fetchall()
    if exams:
        print("\nUpcoming exams")
        table(
            ["SUBJECT", "EXAM", "DATE", "DAYS LEFT"],
            [
                [
                    e["name"],
                    e["title"],
                    e["exam_date"],
                    (date.fromisoformat(e["exam_date"]) - date.today()).days,
                ]
                for e in exams
            ],
        )


def cmd_tables(conn, args):
    rows = []
    for t in ["subjects", "topics", "exams", "sessions", "settings"]:
        n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        cols = ", ".join(c[1] for c in conn.execute(f"PRAGMA table_info({t})"))
        rows.append([t, n, cols])
    table(["TABLE", "ROWS", "COLUMNS"], rows)


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


def cmd_menu(conn, args):
    """Interactive manual data entry. Every entry is committed immediately."""

    class A:  # tiny args holder reused by the existing commands
        pass

    while True:
        print("\n=== StudyGenie menu ===")
        print("1 Add subject   2 Add topic   3 Add exam   4 Set daily hours")
        print("5 Generate plan 6 Today       7 Mark done  8 Progress")
        print("9 Show all tables             0 Exit")
        ch = input("Choose: ").strip()
        a = A()
        try:
            if ch == "1":
                a.name = nonempty(input("Subject name: "))
                a.difficulty = level(input("Difficulty 1-5: ") or 3)
                cmd_add_subject(conn, a)
            elif ch == "2":
                a.subject = input("Subject name: ").strip()
                a.name = nonempty(input("Topic name: "))
                d = input("Difficulty 1-5 (Enter = subject's): ").strip()
                a.difficulty = level(d) if d else None
                cmd_add_topic(conn, a)
            elif ch == "3":
                a.subject = input("Subject name: ").strip()
                a.title = nonempty(input("Exam title: "))
                a.date = valid_date(input("Date YYYY-MM-DD: ").strip())
                cmd_add_exam(conn, a)
            elif ch == "4":
                a.hours = hours_value(input("Hours per day: "))
                cmd_set_hours(conn, a)
            elif ch == "5":
                a.days = positive_int(input("Days to plan (7): ") or 7)
                cmd_plan(conn, a)
            elif ch == "6":
                cmd_today(conn, a)
            elif ch == "7":
                a.session_id = int(input("Session ID: "))
                m = input("Minutes (Enter = planned): ").strip()
                a.minutes = positive_int(m) if m else None
                cmd_done(conn, a)
            elif ch == "8":
                cmd_progress(conn, a)
            elif ch == "9":
                for w in ["subjects", "topics", "exams", "sessions"]:
                    print(f"\n[{w}]")
                    a.what = w
                    cmd_list(conn, a)
            elif ch == "0":
                break
            else:
                print("Invalid choice")
        except StudyGenieError as e:
            print(f"{error_label(e)}: {e}")
        except (ValueError, argparse.ArgumentTypeError) as e:
            print(f"Invalid input: {e}")


def cmd_watch(conn, args):
    """Live view: redraws all tables every few seconds. Run in a second terminal."""
    import time

    class A:
        pass

    try:
        while True:
            os.system("cls" if os.name == "nt" else "clear")
            print(f"LIVE VIEW of {args.db}  (updated {datetime.now():%H:%M:%S}, Ctrl+C to stop)")
            for w in ["subjects", "topics", "exams", "sessions"]:
                print(f"\n[{w}]")
                a = A()
                a.what = w
                cmd_list(conn, a)
            time.sleep(args.seconds)
    except KeyboardInterrupt:
        print("\nStopped.")


# ------------------------------------------------------------------- parser
def build_parser():
    p = argparse.ArgumentParser(
        prog="studygenie", description="StudyGenie - rule-based student study planner"
    )
    p.add_argument("--db", default=DB_DEFAULT, help="SQLite file (default: studygenie.db)")
    sub = p.add_subparsers(dest="command", required=True, metavar="command")

    sub.add_parser("init", help="create the database and tables").set_defaults(func=cmd_init)

    s = sub.add_parser("add-subject", help="add a subject")
    s.add_argument("name", type=nonempty)
    s.add_argument("difficulty", type=level, nargs="?", default=3, help="1-5 (default 3)")
    s.set_defaults(func=cmd_add_subject)

    s = sub.add_parser("add-topic", help="add a topic to a subject")
    s.add_argument("subject")
    s.add_argument("name", type=nonempty)
    s.add_argument("difficulty", type=level, nargs="?", help="1-5 (default: subject's)")
    s.set_defaults(func=cmd_add_topic)

    s = sub.add_parser("add-exam", help="add an exam date")
    s.add_argument("subject")
    s.add_argument("title", type=nonempty)
    s.add_argument("date", type=valid_date, help="YYYY-MM-DD")
    s.set_defaults(func=cmd_add_exam)

    s = sub.add_parser("set-hours", help="set daily study hours")
    s.add_argument("hours", type=hours_value)
    s.set_defaults(func=cmd_set_hours)

    s = sub.add_parser("list", help="list subjects, topics, exams or sessions")
    s.add_argument("what", choices=["subjects", "topics", "exams", "sessions"])
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("plan", help="generate the rule-based study plan")
    s.add_argument("days", type=positive_int, nargs="?", default=7, help="days to plan (default 7)")
    s.add_argument("--scorer", choices=sorted(SCORERS), default="rule", help="scoring rule (default: rule)")
    s.set_defaults(func=cmd_plan)

    sub.add_parser("today", help="show today's sessions").set_defaults(func=cmd_today)

    s = sub.add_parser("done", help="mark a session completed")
    s.add_argument("session_id", type=int)
    s.add_argument("minutes", type=positive_int, nargs="?", help="actual minutes studied")
    s.set_defaults(func=cmd_done)

    s = sub.add_parser("skip", help="skip a session")
    s.add_argument("session_id", type=int)
    s.set_defaults(func=cmd_skip)

    s = sub.add_parser("import-excel", help="load subjects/topics/exams from an .xlsx file")
    s.add_argument("file")
    s.set_defaults(func=cmd_import_excel)
    s = sub.add_parser("export-excel", help="save all tables to an .xlsx file")
    s.add_argument("file")
    s.set_defaults(func=cmd_export_excel)
    s = sub.add_parser("export", help="save data as .json, .csv or .xlsx (chosen by file suffix)")
    s.add_argument("file")
    s.set_defaults(func=cmd_export)
    s = sub.add_parser("set-rest-days", help="weekdays with no study, e.g. set-rest-days sun")
    s.add_argument("days", nargs="+", choices=[*WEEKDAYS, "none"])
    s.set_defaults(func=cmd_set_rest_days)
    s = sub.add_parser("delete-subject", help="delete a subject with its topics, exams and sessions")
    s.add_argument("name")
    s.set_defaults(func=cmd_delete_subject)
    s = sub.add_parser("delete-topic", help="delete a topic and its sessions")
    s.add_argument("subject")
    s.add_argument("name")
    s.set_defaults(func=cmd_delete_topic)
    s = sub.add_parser("edit-topic", help="change a topic's difficulty")
    s.add_argument("subject")
    s.add_argument("name")
    s.add_argument("difficulty", type=level)
    s.set_defaults(func=cmd_edit_topic)
    s = sub.add_parser("delete-exam", help="delete an exam by id (see: list exams)")
    s.add_argument("exam_id", type=int)
    s.set_defaults(func=cmd_delete_exam)
    s = sub.add_parser("edit-exam", help="move an exam to a new date")
    s.add_argument("exam_id", type=int)
    s.add_argument("date", type=valid_date, help="YYYY-MM-DD")
    s.set_defaults(func=cmd_edit_exam)
    sub.add_parser("progress", help="show progress and upcoming exams").set_defaults(func=cmd_progress)
    sub.add_parser("tables", help="show tables, columns and row counts").set_defaults(func=cmd_tables)
    sub.add_parser("menu", help="interactive manual data entry").set_defaults(func=cmd_menu)
    s = sub.add_parser("watch", help="live auto-refreshing view of all tables")
    s.add_argument("seconds", type=float, nargs="?", default=2, help="refresh interval (default 2)")
    s.set_defaults(func=cmd_watch)
    return p


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


def main(argv=None):
    args = build_parser().parse_args(argv)
    setup_logging(Path(args.db).with_suffix(".log"))
    try:
        with open_db(args.db) as conn:
            args.func(conn, args)
    except StudyGenieError as exc:
        print(f"{error_label(exc)}: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
