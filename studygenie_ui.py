"""StudyGenie UI (Streamlit). Reuses the rule-based engine in studygenie.py."""

import contextlib
import html
import io
import os
import sqlite3
import time
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace as NS

import altair as alt
import pandas as pd
import streamlit as st

import sg_ai
import studygenie as sg
from sg_core import (
    SCORERS,
    DuplicateError,
    StudyGenieError,
    clean_name,
    explain,
    setup_logging,
    streak,
    track_status,
)

if not hasattr(st, "fragment"):
    st.error("This app needs a newer Streamlit. Run: pip install -U streamlit")
    st.stop()

st.set_page_config(page_title="StudyGenie", page_icon="📚", layout="wide")
PAL = ["#F4A63A", "#2FA58A", "#8FA3E8", "#E4573D", "#B084F5", "#4CC9F0", "#F78FB3", "#9BD36A"]
STATUS_COLOR = {"on track": "#2FA58A", "slightly behind": "#F4A63A", "behind": "#E4573D"}

st.markdown(
    """<style>
.stApp{background:radial-gradient(circle at 15% 0%,#2B2566 0%,#14163A 55%)}
.hero{padding:22px 28px;border-radius:20px;background:linear-gradient(120deg,#4C3FA3,#2B2566 60%,#F4A63A22);
 border:1px solid #ffffff22;animation:fade .8s ease}
.hero h1{margin:0;font-size:2.4rem;background:linear-gradient(90deg,#fff,#F4A63A,#fff);background-size:200%;
 -webkit-background-clip:text;color:transparent;animation:shine 5s linear infinite}
.hero p{margin:4px 0 0;color:#cfd4f5}
.kpi{padding:18px;border-radius:16px;background:#232857;border:1px solid #ffffff1a;transition:.25s;animation:fade .6s ease}
.kpi:hover{transform:translateY(-5px);box-shadow:0 10px 28px #0008;border-color:#F4A63A}
.kpi b{display:block;font-size:2rem;color:#F4A63A}.kpi span{color:#b9c0ea;font-size:.9rem}
.bar{height:12px;border-radius:8px;background:#ffffff18;overflow:hidden;margin:4px 0 14px}
.bar i{display:block;height:100%;border-radius:8px;animation:grow 1.2s ease}
.pill{padding:2px 10px;border-radius:999px;font-size:.75rem;color:#14163A;font-weight:700;margin-left:8px}
.exam{padding:14px 16px;border-radius:14px;background:#232857;margin-bottom:10px;border-left:0;transition:.25s;
 display:flex;justify-content:space-between;align-items:center}
.exam:hover{transform:translateX(6px);background:#2d3370}
.days{font-size:1.6rem;font-weight:800}
.sess{padding:12px 16px;border-radius:14px;background:#232857;margin-bottom:6px;animation:fade .5s ease}
.ring{width:190px;height:190px;border-radius:50%;display:flex;align-items:center;justify-content:center;
 margin:8px auto;box-shadow:0 0 30px #F4A63A33}
.ring-in{width:150px;height:150px;border-radius:50%;background:#14163A;display:flex;flex-direction:column;
 align-items:center;justify-content:center;font-size:2.2rem;font-weight:800;color:#F4A63A}
.ring-in small{font-size:.75rem;font-weight:400;color:#b9c0ea;text-align:center;padding:0 12px}
.live{display:inline-block;width:10px;height:10px;border-radius:50%;background:#2FA58A;margin-right:8px;animation:pulse 1.4s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 #2FA58Aaa}100%{box-shadow:0 0 0 12px #2FA58A00}}
@keyframes shine{to{background-position:200% 0}}
@keyframes fade{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
@keyframes grow{from{width:0!important}}
[data-testid="stExpander"]{border-radius:14px;border:1px solid #ffffff1a;transition:.25s;background:#1d2150}
[data-testid="stExpander"]:hover{border-color:#F4A63A;box-shadow:0 0 22px #F4A63A33}
.stButton>button{border-radius:12px;transition:.2s}.stButton>button:hover{transform:scale(1.05)}
.hero-row{display:flex;justify-content:space-between;align-items:center;gap:18px;flex-wrap:wrap}
.next{padding:10px 16px;border-radius:14px;background:#14163A99;border:1px solid #F4A63A66;color:#fff;text-align:right}
.next b{display:block;font-size:1.15rem;color:#F4A63A}.next span{font-size:.8rem;color:#cfd4f5}
.role{display:inline-block;padding:2px 12px;border-radius:999px;font-size:.78rem;font-weight:700;margin-left:10px;
 vertical-align:middle;background:#8FA3E8;color:#14163A}.role.admin{background:#F4A63A}
.sec{margin:22px 0 6px;font-size:1.35rem;font-weight:700}.sec small{font-weight:400;color:#b9c0ea;margin-left:8px;font-size:.85rem}
</style>""",
    unsafe_allow_html=True,
)

AUTH_ON = os.environ.get("STUDYGENIE_AUTH", "1").strip().lower() in ("1", "true", "yes", "on")  # login is ON unless STUDYGENIE_AUTH=0
AUTH_DB = os.environ.get("STUDYGENIE_AUTH_DB", "studygenie_auth.db")
USER = None
PERMS: set[str] = set()
if AUTH_ON:  # sign-up / login first; set STUDYGENIE_AUTH=0 to skip it (tests, quick demos)
    import sg_auth

    USER = sg_auth.streamlit_gate(AUTH_DB)  # sign-up / login page; stops here until someone is logged in
    _auth = sg_auth.connect(AUTH_DB)
    try:
        PERMS = sg_auth.permissions_for(_auth, USER["role"])  # read from the role_permissions table
    finally:
        _auth.close()


def allow(*permissions):
    """True if the signed-in person may do ANY of these. With login switched off, everything is allowed."""
    return USER is None or any(p in PERMS for p in permissions)


# which permission each engine command needs (checked again in run(), so hiding a button is not the only guard)
NEEDS = {
    "cmd_plan": "plan",
    "cmd_add_subject": "add_subject",
    "cmd_add_topic": "add_topic",
    "cmd_add_exam": "add_exam",
    "cmd_edit_topic": "edit_topic",
    "cmd_edit_exam": "edit_exam",
    "cmd_delete_subject": "delete_subject",
    "cmd_delete_topic": "delete_topic",
    "cmd_delete_exam": "delete_exam",
    "cmd_set_hours": "set_hours",
    "cmd_set_rest_days": "set_rest_days",
    "cmd_done": "mark_session",
    "cmd_skip": "mark_session",
}

default_db = os.environ.get("STUDYGENIE_DB") or (
    "studygenie_final.db" if Path("studygenie_final.db").exists() else "studygenie.db"
)
VIEW_LABEL = ""
if not AUTH_ON:  # single-user mode: one planner file, chosen here
    DB = st.sidebar.text_input("Database file", default_db)
elif allow("manage_users"):  # admin: look at (and fix) one student's private planner at a time
    _c = sg_auth.connect(AUTH_DB)
    try:
        STUDENTS = sg_auth.student_profiles(_c)
    finally:
        _c.close()
    if not STUDENTS:
        st.info("No students have signed up yet. Each student creates their own account on the sign-up page "
                "and gets a private planner.")
        sg_auth.render_admin_panel(AUTH_DB)
        st.stop()
    _names = {u: f"{n or u} · {c or 'no college'} · {k or 'no class'}" for u, n, c, k in STUDENTS}
    VIEWING = st.sidebar.selectbox("Student's planner", list(_names), format_func=_names.get, key="viewing_student")
    VIEW_LABEL = _names[VIEWING]
    STUDENT_NAMES = sorted({n or u for u, n, c, k in STUDENTS})
    DB = str(sg_auth.user_db_path(VIEWING))
else:  # student: always their own planner, nobody else's
    DB = str(sg_auth.user_db_path(USER["username"]))
    st.sidebar.caption(f"Your private planner: {Path(DB).name}")  # duplicates are checked in this file only
Path(DB).parent.mkdir(parents=True, exist_ok=True)
setup_logging(Path(DB).with_suffix(".log"))
st.sidebar.caption("Rule-based planner: fixed formula, no AI. Same input gives the same plan.")
if allow("manage_users"):
    st.sidebar.markdown("**Watching this file**")
    st.sidebar.code(str(Path(DB).resolve()), language="text")
    st.sidebar.caption("Adding data from a terminal? Use the same file:")
    st.sidebar.code(f'python studygenie.py --db "{Path(DB).resolve()}" menu', language="bash")


def fingerprint(path):
    """Changes whenever any program (CLI, DB Browser, this UI) writes to the database file."""
    try:
        info = Path(path).stat()
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size)


@st.fragment(run_every=1.5)
def sync():
    """Rerun the whole app when the database file changes, so every tab stays current."""
    now = fingerprint(DB)
    last = st.session_state.get("fp")
    st.session_state["fp"] = now
    st.markdown(
        f'<span class="live"></span><b>LIVE</b> · synced {datetime.now():%H:%M:%S}',
        unsafe_allow_html=True,
    )
    if last is not None and now != last:
        st.rerun()


with st.sidebar:
    sync()


def q(sql, params=()):
    conn = sg.connect(DB)
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


CLEAR_AFTER = {"cmd_add_subject": ("sn",), "cmd_add_topic": ("tn",), "cmd_add_exam": ("et",)}


def run(fn, celebrate=False, **kw):
    need = NEEDS.get(fn.__name__)
    if need and not allow(need):
        st.session_state["flash"] = (f"Your role is not allowed to do this (needs '{need}').", False, "🔒")
        st.rerun()
    out = io.StringIO()
    try:
        with sg.open_db(DB) as conn, contextlib.redirect_stdout(out):
            fn(conn, NS(db=DB, **kw))
        flash = ((out.getvalue().strip().splitlines() or ["Done"])[0], celebrate, "✅")
    except DuplicateError as exc:
        flash = (str(exc), False, "🚫")
    except StudyGenieError as exc:
        flash = (str(exc), False, "⚠️")
    st.session_state["flash"] = flash
    if flash[2] == "✅":  # saved: empty the typing box, otherwise it warns "Duplicate found" about what was just saved
        st.session_state["_clear"] = CLEAR_AFTER.get(fn.__name__, ())
    st.session_state["fp"] = fingerprint(DB)  # our own write: no extra refresh needed
    st.rerun()


for _key in st.session_state.pop("_clear", ()):  # must happen before the text boxes are drawn
    st.session_state[_key] = ""

if "flash" in st.session_state:
    msg, party, icon = st.session_state.pop("flash")
    st.toast(msg, icon=icon)
    if party:
        st.balloons()

today_iso = date.today().isoformat()
hero = st.container()
st.write("")
TAB_DEFS = [  # key, label, shown when the role has any of these permissions (None = everyone)
    ("dash", "🏠 Dashboard", None),
    ("add", "➕ Add data", ("add_subject", "add_topic", "add_exam", "set_hours", "set_rest_days")),
    ("manage", "🛠️ Manage", ("edit_topic", "edit_exam", "delete_topic", "delete_exam", "delete_subject")),
    ("plan", "🗓️ Plan", ("plan",)),
    ("today", "✅ Today", ("mark_session",)),
    ("why", "🔍 Why this order", ("plan",)),
    ("live", "🔴 Live view", ("view",)),
]
shown = [(k, label) for k, label, need in TAB_DEFS if need is None or allow(*need)]
if AUTH_ON and allow("manage_users"):
    shown.append(("users", "👑 Users"))
T = dict(zip([k for k, _ in shown], st.tabs([label for _, label in shown]), strict=True))

prog = q(
    """SELECT s.name AS subject, COUNT(t.id) AS topics, COALESCE(SUM(t.status='done'),0) AS done,
  COALESCE((SELECT SUM(x.minutes) FROM sessions x JOIN topics tt ON tt.id=x.topic_id
            WHERE tt.subject_id=s.id AND x.status='completed'),0) AS studied,
  COALESCE((SELECT SUM(tt.est_minutes) FROM topics tt WHERE tt.subject_id=s.id),0) AS total,
  COALESCE((SELECT SUM(x.minutes) FROM sessions x JOIN topics tt ON tt.id=x.topic_id
            WHERE tt.subject_id=s.id AND x.session_date < ?),0) AS due
  FROM subjects s LEFT JOIN topics t ON t.subject_id=s.id GROUP BY s.id ORDER BY s.name""",
    (today_iso,),
)
exams = q(
    """SELECT s.name AS subject, e.title, e.exam_date FROM exams e JOIN subjects s ON s.id=e.subject_id
             WHERE e.exam_date >= ? ORDER BY e.exam_date""",
    (today_iso,),
)
exams["days"] = [(date.fromisoformat(d) - date.today()).days for d in exams["exam_date"]]
study_days = {
    date.fromisoformat(d)
    for d in q(
        "SELECT DISTINCT date(completed_at) AS d FROM sessions WHERE status='completed' AND completed_at IS NOT NULL"
    )["d"]
}
counts = q(
    """SELECT
  (SELECT COUNT(*) FROM sessions WHERE session_date < ? AND status IN ('planned','skipped')) AS missed,
  (SELECT COUNT(*) FROM sessions WHERE session_date < ? AND status = 'planned') AS stale,
  (SELECT COALESCE(SUM(minutes),0) FROM sessions WHERE session_date = ?) AS goal,
  (SELECT COALESCE(SUM(minutes),0) FROM sessions WHERE session_date = ? AND status='completed') AS goal_done""",
    (today_iso, today_iso, today_iso, today_iso),
).iloc[0]


def _next_exam_html():
    if exams.empty:
        return "<b>No upcoming exams</b><span>Add one to start planning</span>"
    r = exams.iloc[0]
    when = "today" if r["days"] == 0 else f"in {r['days']} day(s)"
    return f"<b>{html.escape(r['subject'])} · {html.escape(r['title'])}</b><span>{when} · {r['exam_date']}</span>"


with hero:
    if USER:
        first = (USER.get("full_name") or USER["username"]).split()[0]
        sub_line = f'Welcome back, {html.escape(first)}<span class="role {USER["role"]}">{USER["role"]}</span>'
        if VIEW_LABEL:
            sub_line += f" &nbsp;·&nbsp; Viewing {html.escape(VIEW_LABEL)}"
    else:
        sub_line = "What to study, when, and for how long. Planned by a transparent scoring formula."
    st.markdown(
        f'<div class="hero"><div class="hero-row"><div><h1>📚 StudyGenie</h1><p>{sub_line}</p></div>'
        f'<div class="next">{_next_exam_html()}</div></div></div>',
        unsafe_allow_html=True,
    )

if "dash" in T:
    with T["dash"]:
        for r in exams[exams["days"] <= 3].itertuples():
            when = "today" if r.days == 0 else f"in {r.days} day(s)"
            st.warning(f"⏰ {r.subject}: {r.title} is {when} ({r.exam_date})")
        if counts["stale"] and allow("plan"):
            c1, c2 = st.columns([5, 1])
            c1.warning(
                f"{int(counts['stale'])} block(s) from earlier days were never done. Re-plan to reschedule them."
            )
            if c2.button("Re-plan now", key="replan_missed"):
                run(sg.cmd_plan, celebrate=True, days=14, scorer="rule")
        studied, total = int(prog["studied"].sum()), int(prog["total"].sum())
        pct = round(100 * studied / total) if total else 0
        nxt = f"{exams.iloc[0]['days']} days" if len(exams) else "none"
        goal_pct = round(100 * counts["goal_done"] / counts["goal"]) if counts["goal"] else 0
        row1 = [
            (len(prog), "Subjects"),
            (f"{int(prog['done'].sum())}/{int(prog['topics'].sum())}", "Topics done"),
            (f"{pct}%", f"Studied ({studied} of {total} min)"),
            (nxt, "Until next exam"),
        ]
        row2 = [
            (f"🔥 {streak(study_days, date.today())}", "Day streak"),
            (int(counts["missed"]), "Missed blocks so far"),
            (f"{goal_pct}%", f"Today's goal ({int(counts['goal_done'])} of {int(counts['goal'])} min)"),
        ]
        for cols, row in ((st.columns(4), row1), (st.columns(3), row2)):
            for col, (v, label) in zip(cols, row, strict=True):
                col.markdown(f'<div class="kpi"><b>{v}</b><span>{label}</span></div>', unsafe_allow_html=True)
            st.write("")
        a, b = st.columns([3, 2])
        with a:
            st.subheader("Progress by subject")
            report = []
            for i, r in enumerate(prog.itertuples()):
                p = round(100 * r.studied / r.total) if r.total else 0
                status = track_status(int(r.due), int(r.studied))
                report.append(
                    {
                        "subject": r.subject,
                        "studied_min": r.studied,
                        "total_min": r.total,
                        "progress_pct": p,
                        "status": status,
                    }
                )
                st.markdown(
                    f"<b>{html.escape(r.subject)}</b>"
                    f'<span class="pill" style="background:{STATUS_COLOR[status]}">{status}</span>'
                    f'<span style="float:right;color:#b9c0ea">{r.studied}/{r.total} min · {p}%</span>'
                    f'<div class="bar"><i style="width:{max(p, 2)}%;background:{PAL[i % 8]}"></i></div>',
                    unsafe_allow_html=True,
                )
            if report:
                st.download_button(
                    "⬇ Download progress report (CSV)",
                    pd.DataFrame(report).to_csv(index=False),
                    "studygenie_progress.csv",
                    "text/csv",
                )
        with b:
            st.subheader("Exam countdown")
            for r in exams.itertuples():
                c = "#E4573D" if r.days <= 7 else "#F4A63A" if r.days <= 14 else "#2FA58A"
                st.markdown(
                    f'<div class="exam"><div><b>{html.escape(r.subject)}</b><br>'
                    f'<span style="color:#b9c0ea">{html.escape(r.title)} · {r.exam_date}</span></div>'
                    f'<div class="days" style="color:{c}">{r.days}<small>d</small></div></div>',
                    unsafe_allow_html=True,
                )
            if exams.empty:
                st.info("No upcoming exams. Add one in the Add data tab." if "add" in T else "No upcoming exams.")

        if AUTH_ON and allow("manage_users"):  # admin view: every student's exams, finished or coming up
            overview = []
            for uname, full, college, cls in STUDENTS:
                path = sg_auth.user_db_path(uname)
                if not path.exists():
                    continue
                con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
                try:
                    for subject, title, exam_date in con.execute(
                        "SELECT s.name, e.title, e.exam_date FROM exams e JOIN subjects s ON s.id=e.subject_id"
                    ):
                        overview.append((full or uname, college, cls, subject, title, exam_date))
                except sqlite3.Error:
                    pass  # a student whose planner is still empty has no tables yet
                finally:
                    con.close()
            overview.sort(key=lambda r: r[5])
            finished = sum(1 for r in overview if date.fromisoformat(r[5]) < date.today())
            st.markdown(
                f'<div class="sec">Exam schedule · all students<small>{len(overview) - finished} coming up · '
                f"{finished} finished</small></div>",
                unsafe_allow_html=True,
            )
            if not overview:
                st.info("No student has added an exam yet.")
            else:
                def _when(d):
                    g = (date.fromisoformat(d) - date.today()).days
                    if g < 0:
                        return f"✅ finished {-g} day(s) ago"
                    return "🔴 today" if g == 0 else f"{'🟠' if g <= 7 else '🟢'} in {g} day(s)"

                st.dataframe(
                    pd.DataFrame(
                        {
                            "Student": [r[0] for r in overview],
                            "College": [r[1] for r in overview],
                            "Class": [r[2] for r in overview],
                            "Date": [date.fromisoformat(r[5]).strftime("%a %d %b %Y") for r in overview],
                            "Subject": [r[3] for r in overview],
                            "Exam": [r[4] for r in overview],
                            "When": [_when(r[5]) for r in overview],
                        }
                    ),
                    hide_index=True,
                    width="stretch",
                )
            def _from_every_student(sql):
                """Run one read-only query on each student's planner file, tagged with who it belongs to."""
                found = []
                for uname, full, college, cls in STUDENTS:
                    path = sg_auth.user_db_path(uname)
                    if not path.exists():
                        continue
                    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
                    try:
                        found += [(uname, full or uname, college, cls, *row) for row in con.execute(sql)]
                    except sqlite3.Error:
                        pass  # an empty planner has no tables yet
                    finally:
                        con.close()
                return found

            study = _from_every_student(
                "SELECT s.name, t.name, t.difficulty, t.status FROM topics t JOIN subjects s ON s.id=t.subject_id"
            ) + [
                (*r, "(no topics yet)", None, "")
                for r in _from_every_student("SELECT name FROM subjects WHERE id NOT IN (SELECT subject_id FROM topics)")
            ]
            study.sort(key=lambda r: (r[1].lower(), r[4].lower(), r[5].lower()))
            st.markdown(
                f'<div class="sec">Subjects and topics · all students<small>{len({r[4] for r in study})} subject(s) '
                f"across {len({r[0] for r in study})} student(s)</small></div>",
                unsafe_allow_html=True,
            )
            who = st.selectbox("Show", ["All students", *STUDENT_NAMES], key="topic_filter")
            shown = [r for r in study if who == "All students" or r[1] == who]
            if not shown:
                st.info("No subjects or topics yet.")
            else:
                st.dataframe(
                    pd.DataFrame(
                        {
                            "Student": [r[1] for r in shown],
                            "College": [r[2] for r in shown],
                            "Class": [r[3] for r in shown],
                            "Subject": [r[4] for r in shown],
                            "Topic": [r[5] for r in shown],
                            "Difficulty": [r[6] for r in shown],
                            "Status": [r[7] for r in shown],
                        }
                    ),
                    hide_index=True,
                    width="stretch",
                )
            st.caption(f"{len(STUDENTS)} student(s). Pick a student in the sidebar to view or fix their planner; "
                       "manage accounts in the Users tab.")

        if USER and not allow("plan"):
            st.caption("Admin view of one student's private planner: you can fix their data, but they plan, study "
                       "and ask the AI coach themselves.")

        if allow("ai_ask"):
            st.markdown(
                '<div class="sec">💬 Ask StudyGenie<small>Questions about your own plan and exams</small></div>',
                unsafe_allow_html=True,
            )
            with st.container(border=True):
                sg_ai.render_ai_tab(DB, USER, compact=True, allowed=True)


def is_duplicate(text, existing):
    """True when the typed name matches an existing one (ignoring case and extra spaces)."""
    name = clean_name(text).lower()
    return bool(name) and name in {clean_name(e).lower() for e in existing}


if "add" in T:
    with T["add"]:
        subjects = q("SELECT name FROM subjects ORDER BY name")["name"].tolist()
        a, b = st.columns(2)
        with a:
            st.subheader("Subject")
            n = st.text_input("Subject name", key="sn")
            d = st.slider("Difficulty", 1, 5, 3, key="sd")
            dup_subject = is_duplicate(n, subjects)
            if dup_subject:
                st.warning(f"⚠️ Duplicate found: subject '{clean_name(n)}' already exists")
            if st.button("Add subject", disabled=dup_subject):
                run(sg.cmd_add_subject, name=n, difficulty=d)
            st.subheader("Study time")
            h = st.number_input("Hours per day", 0.5, 16.0, 3.0, 0.5)
            if st.button("Save hours"):
                run(sg.cmd_set_hours, hours=h)
            with sg.open_db(DB) as conn:
                current_rest = [sg.WEEKDAYS[i] for i in sorted(sg.get_rest_days(conn))]
            rest = st.multiselect("Rest days (no study planned)", sg.WEEKDAYS, default=current_rest)
            if st.button("Save rest days"):
                run(sg.cmd_set_rest_days, days=rest or ["none"])
        with b:
            st.subheader("Topic")
            ts = st.selectbox("Subject", subjects, key="ts") if subjects else None
            tn = st.text_input("Topic name", key="tn")
            td = st.slider("Topic difficulty", 1, 5, 3, key="td")
            topic_names = (
                q(
                    "SELECT t.name FROM topics t JOIN subjects s ON s.id=t.subject_id WHERE s.name = ?",
                    (ts,),
                )["name"].tolist()
                if ts
                else []
            )
            dup_topic = is_duplicate(tn, topic_names)
            if dup_topic:
                st.warning(f"⚠️ Duplicate found: topic '{clean_name(tn)}' already exists in {ts}")
            if st.button("Add topic", disabled=dup_topic or not ts):
                run(sg.cmd_add_topic, subject=ts, name=tn, difficulty=td)
            st.subheader("Exam")
            es = st.selectbox("Subject", subjects, key="es") if subjects else None
            et = st.text_input("Exam title", key="et")
            ed = st.date_input("Exam date", min_value=date.today())
            exam_titles = (
                q(
                    "SELECT e.title FROM exams e JOIN subjects s ON s.id=e.subject_id WHERE s.name = ?",
                    (es,),
                )["title"].tolist()
                if es
                else []
            )
            dup_exam = is_duplicate(et, exam_titles)
            if dup_exam:
                st.warning(
                    f"⚠️ Duplicate found: exam '{clean_name(et)}' already exists for {es}. "
                    "Use the Manage tab to move its date."
                )
            if st.button("Add exam", disabled=dup_exam or not es):
                run(sg.cmd_add_exam, subject=es, title=et, date=ed.isoformat())

if "manage" in T:
    with T["manage"]:
        st.caption(
            "Change difficulty, move exam dates or delete things. Press Generate plan afterwards to apply."
        )
        all_subjects = q("SELECT id, name, difficulty FROM subjects ORDER BY name")
        if all_subjects.empty:
            st.info("No subjects yet.")
        for subj in all_subjects.itertuples():
            with st.expander(f"{subj.name} · difficulty {subj.difficulty}"):
                st.markdown("**Topics**")
                for t in q(
                    "SELECT id, name, difficulty, status FROM topics WHERE subject_id=? ORDER BY id", (subj.id,)
                ).itertuples():
                    c1, c2, c3, c4 = st.columns([4, 2, 1, 1])
                    c1.write(f"{t.name} · {t.status}")
                    nd = c2.selectbox(
                        "Difficulty",
                        [1, 2, 3, 4, 5],
                        index=t.difficulty - 1,
                        key=f"td{t.id}",
                        label_visibility="collapsed",
                    )
                    if c3.button("Save", key=f"ts{t.id}"):
                        run(sg.cmd_edit_topic, subject=subj.name, name=t.name, difficulty=nd)
                    if c4.button("Delete", key=f"tx{t.id}"):
                        run(sg.cmd_delete_topic, subject=subj.name, name=t.name)
                st.markdown("**Exams**")
                for e in q(
                    "SELECT id, title, exam_date FROM exams WHERE subject_id=? ORDER BY exam_date", (subj.id,)
                ).itertuples():
                    c1, c2, c3, c4 = st.columns([4, 2, 1, 1])
                    c1.write(e.title)
                    nd = c2.date_input(
                        "Date",
                        value=date.fromisoformat(e.exam_date),
                        key=f"ed{e.id}",
                        label_visibility="collapsed",
                    )
                    if c3.button("Move", key=f"em{e.id}"):
                        run(sg.cmd_edit_exam, exam_id=e.id, date=nd.isoformat())
                    if c4.button("Delete", key=f"ex{e.id}"):
                        run(sg.cmd_delete_exam, exam_id=e.id)
                st.divider()
                sure = st.checkbox(
                    "Yes, delete this subject with all its topics, exams and sessions", key=f"cf{subj.id}"
                )
                if st.button("Delete subject", key=f"ds{subj.id}", disabled=not sure):
                    run(sg.cmd_delete_subject, name=subj.name)

if "plan" in T:
    with T["plan"]:
        c1, c2 = st.columns([3, 2])
        days = c1.slider("Days to plan", 1, 30, 14)
        rule = c2.radio(
            "Scoring rule", list(SCORERS), horizontal=True, help="Same Planner, different Scorer class"
        )
        if st.button("✨ Generate plan", type="primary"):
            run(sg.cmd_plan, celebrate=True, days=days, scorer=rule)
        sess = q("""SELECT x.session_date AS date, s.name AS subject, t.name AS topic, x.minutes, x.status
                    FROM sessions x JOIN topics t ON t.id=x.topic_id JOIN subjects s ON s.id=t.subject_id
                    ORDER BY x.session_date, x.id""")
        if sess.empty:
            st.info("No plan yet. Press Generate plan.")
        else:
            order = prog["subject"].tolist()
            colors = alt.Color(
                "subject:N",
                scale=alt.Scale(domain=order, range=PAL[: len(order)]),
                legend=alt.Legend(orient="bottom", title=None),
            )
            st.altair_chart(
                alt.Chart(sess)
                .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
                .encode(
                    x=alt.X("date:O", title=None),
                    y=alt.Y("sum(minutes):Q", title="Minutes"),
                    color=colors,
                    tooltip=["date", "subject", "topic", "minutes"],
                )
                .properties(height=280),
                width="stretch",
            )
            st.markdown("**Heatmap: minutes per subject per day**")
            st.altair_chart(
                alt.Chart(sess)
                .mark_rect(cornerRadius=3)
                .encode(
                    x=alt.X("date:O", title=None),
                    y=alt.Y("subject:N", title=None),
                    color=alt.Color("sum(minutes):Q", scale=alt.Scale(scheme="goldorange"), legend=None),
                    tooltip=["date", "subject", alt.Tooltip("sum(minutes):Q", title="minutes")],
                )
                .properties(height=200),
                width="stretch",
            )
            st.dataframe(sess, hide_index=True, width="stretch")
            st.download_button(
                "⬇ Download plan (CSV)", sess.to_csv(index=False), "studygenie_plan.csv", "text/csv"
            )

if "today" in T:
    with T["today"]:
        st.subheader(f"Today · {date.today():%A, %d %B %Y}")
        today = q(
            """SELECT x.id, s.name AS subject, t.name AS topic, x.minutes, x.status FROM sessions x
                     JOIN topics t ON t.id=x.topic_id JOIN subjects s ON s.id=t.subject_id
                     WHERE x.session_date=? ORDER BY x.id""",
            (today_iso,),
        )
        if today.empty:
            st.info("Nothing planned today (it may be a rest day). Generate a plan first.")
        else:
            dn = int((today["status"] == "completed").sum())
            st.progress(dn / len(today), text=f"{dn} of {len(today)} blocks completed")
        icon = {"planned": "🟡", "completed": "🟢", "skipped": "⚪"}
        for r in today.itertuples():
            c1, c2, c3 = st.columns([6, 1, 1])
            c1.markdown(
                f'<div class="sess">{icon[r.status]} <b>{html.escape(r.subject)}</b> · {html.escape(r.topic)} · {r.minutes} min</div>',
                unsafe_allow_html=True,
            )
            if r.status == "planned":
                if c2.button("Done", key=f"d{r.id}"):
                    run(sg.cmd_done, celebrate=True, session_id=r.id, minutes=None)
                if c3.button("Skip", key=f"s{r.id}"):
                    run(sg.cmd_skip, session_id=r.id)

        st.markdown("### ⏱️ Focus timer")
        planned = today[today["status"] == "planned"].reset_index(drop=True)
        if planned.empty and "timer" not in st.session_state:
            st.caption("No planned block left to time.")
        elif "timer" not in st.session_state:
            labels = [f"{r.subject} · {r.topic} ({r.minutes} min)" for r in planned.itertuples()]
            pick = st.selectbox("Block to study", range(len(labels)), format_func=lambda i: labels[i])
            if st.button("▶ Start timer", type="primary"):
                row = planned.iloc[pick]
                st.session_state["timer"] = {
                    "id": int(row["id"]),
                    "start": time.time(),
                    "minutes": int(row["minutes"]),
                    "label": f"{row['subject']} · {row['topic']}",
                }
                st.rerun()

        @st.fragment(run_every=1 if "timer" in st.session_state else None)
        def focus_timer():
            t = st.session_state.get("timer")
            if not t:
                return
            elapsed = time.time() - t["start"]
            total = t["minutes"] * 60
            left = max(total - elapsed, 0)
            pct = min(100, elapsed / total * 100)
            st.markdown(
                f'<div class="ring" style="background:conic-gradient(#F4A63A {pct}%, #ffffff18 0)">'
                f'<div class="ring-in">{int(left // 60):02d}:{int(left % 60):02d}<small>{html.escape(t["label"])}</small></div></div>',
                unsafe_allow_html=True,
            )
            if left == 0:
                st.success("Time's up! Log the block when you are ready.")
            b1, b2 = st.columns(2)
            if b1.button("✅ Finish and log", key="tfin"):
                studied_min = min(max(1, round(elapsed / 60)), t["minutes"])
                st.session_state.pop("timer")
                run(sg.cmd_done, celebrate=True, session_id=t["id"], minutes=studied_min)
            if b2.button("Cancel", key="tcan"):
                st.session_state.pop("timer")
                st.rerun()

        focus_timer()

if "why" in T:
    with T["why"]:
        st.markdown(
            "The planner is not a black box. For the **first block of today**, each topic's score is "
            "`weight × urgency × (0.3 + need)`, and the highest score goes first (a 14-day horizon is assumed)."
        )
        why_rule = st.radio("Scoring rule", list(SCORERS), horizontal=True, key="why_rule")
        with sg.open_db(DB) as conn:
            items = sg.load_items(conn, date.today(), 14)
        rows = explain(items, date.today(), SCORERS[why_rule]())
        if not rows:
            st.info("No pending topics to explain.")
        else:
            why = pd.DataFrame(rows)
            why.insert(0, "rank", range(1, len(why) + 1))
            st.altair_chart(
                alt.Chart(why.head(10))
                .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4)
                .encode(
                    y=alt.Y("topic:N", sort="-x", title=None),
                    x=alt.X("score:Q", title="Score"),
                    color=alt.Color("subject:N", legend=alt.Legend(orient="bottom", title=None)),
                    tooltip=["rank", "subject", "topic", "weight", "days_left", "urgency", "need", "score"],
                )
                .properties(height=320),
                width="stretch",
            )
            st.dataframe(why, hide_index=True, width="stretch")
            st.caption(
                "weight = average of subject and topic difficulty · urgency = 1 + 5 ÷ days left · need = minutes left ÷ estimate"
            )


def max_ids():
    return q("""SELECT (SELECT COALESCE(MAX(id),0) FROM subjects) AS subjects,
                    (SELECT COALESCE(MAX(id),0) FROM topics) AS topics,
                    (SELECT COALESCE(MAX(id),0) FROM exams) AS exams,
                    (SELECT COALESCE(MAX(id),0) FROM sessions) AS sessions""").iloc[0].to_dict()


if st.session_state.get("base_db") != DB:  # remember what existed when this file was opened
    st.session_state["base"] = max_ids()
    st.session_state["base_db"] = DB


def mark_new(df, key):
    base = st.session_state["base"][key]
    df.insert(0, "new", ["🆕" if i > base else "" for i in df["id"]])
    return df


@st.fragment(run_every=2)
def live():
    st.markdown(
        f'<span class="live"></span><b>LIVE</b> · refreshed {datetime.now():%H:%M:%S} · newest rows first, 🆕 = added since you opened this file',
        unsafe_allow_html=True,
    )
    cnt = q("""SELECT (SELECT COUNT(*) FROM subjects) AS subjects, (SELECT COUNT(*) FROM topics) AS topics,
               (SELECT COUNT(*) FROM exams) AS exams, (SELECT COUNT(*) FROM sessions) AS sessions""").iloc[0]
    for col, key in zip(st.columns(4), ["subjects", "topics", "exams", "sessions"], strict=True):
        col.markdown(
            f'<div class="kpi"><b>{int(cnt[key])}</b><span>{key} rows</span></div>', unsafe_allow_html=True
        )
    st.write("")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**Subjects**")
        df = q("SELECT id, name, difficulty FROM subjects ORDER BY id DESC LIMIT 6")
        st.dataframe(mark_new(df, "subjects"), hide_index=True, width="stretch")
    with c2:
        st.markdown("**Topics**")
        df = q("""SELECT t.id, s.name AS subject, t.name AS topic, t.status FROM topics t
                  JOIN subjects s ON s.id=t.subject_id ORDER BY t.id DESC LIMIT 6""")
        st.dataframe(mark_new(df, "topics"), hide_index=True, width="stretch")
    with c3:
        st.markdown("**Exams**")
        df = q("""SELECT e.id, s.name AS subject, e.title AS exam, e.exam_date AS date FROM exams e
                  JOIN subjects s ON s.id=e.subject_id ORDER BY e.id DESC LIMIT 6""")
        st.dataframe(mark_new(df, "exams"), hide_index=True, width="stretch")
    st.markdown("**Sessions**")
    df = q("""SELECT x.id, x.session_date AS date, s.name AS subject, t.name AS topic, x.minutes, x.status
              FROM sessions x JOIN topics t ON t.id=x.topic_id JOIN subjects s ON s.id=t.subject_id
              ORDER BY x.id DESC LIMIT 8""")
    st.dataframe(mark_new(df, "sessions"), hide_index=True, width="stretch")
    if st.button("Clear 🆕 marks"):
        st.session_state["base"] = max_ids()
    log_file = Path(DB).with_suffix(".log")
    st.markdown("**Activity log**")
    lines = log_file.read_text(encoding="utf-8").splitlines()[-8:] if log_file.exists() else []
    st.code("\n".join(lines) or "No activity yet. Add data or generate a plan.", language="text")


if "live" in T:
    with T["live"]:
        live()

if "users" in T:
    with T["users"]:
        sg_auth.render_admin_panel(AUTH_DB)