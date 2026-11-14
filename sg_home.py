"""StudyGenie index (home) page.

After login every person lands here first, sees only what their role can do, and then
goes into the dashboard by pressing a card. Not shown when login is switched off.

Used from studygenie_ui.py:

    import sg_home
    sg_home.gate(st, USER, allow, AUTH_ON, AUTH_DB, sg_auth)   # stops the script while on the home page
"""
from __future__ import annotations

import html
import sqlite3
from datetime import date

# (icon, title, what it does, tab label to open, permissions - any one of them is enough)
HOME_CARDS = {
    "admin": [
        ("👑", "Users & roles", "Create, delete and change roles of accounts, and see the login history.", "👑 Users", ("manage_users",)),
        ("🎓", "Student planners", "Pick a student in the sidebar to view and fix their subjects, topics and exams.", "🏠 Dashboard", ("manage_users",)),
        ("📅", "Exam schedule", "Every student's exams with college and class.", "🏠 Dashboard", ("manage_users",)),
        ("➕", "Add data", "Add or correct subjects, topics and exams for the selected student.", "➕ Add data", ("add_subject", "add_topic", "add_exam")),
        ("🛠️", "Manage", "Edit or delete wrong data.", "🛠️ Manage", ("edit_topic", "delete_topic", "delete_subject")),
        ("🔴", "Live view", "Watch the selected planner update as data changes.", "🔴 Live view", ("view",)),
    ],
    "user": [
        ("🏠", "My dashboard", "Progress, next exam, countdown and Ask StudyGenie.", "🏠 Dashboard", ("view",)),
        ("➕", "Add data", "Subjects, topics, exams and daily study hours.", "➕ Add data", ("add_subject", "add_topic", "add_exam")),
        ("🗓️", "Make a plan", "Build your day-by-day schedule. Skipped topics are finished first.", "🗓️ Plan", ("plan",)),
        ("✅", "Today", "Tick off or skip today's study blocks.", "✅ Today", ("mark_session",)),
        ("🔍", "Why this order", "See the scores behind your plan.", "🔍 Why this order", ("plan",)),
        ("🛠️", "Manage", "Edit or delete your subjects, topics and exams.", "🛠️ Manage", ("edit_topic", "delete_topic", "delete_subject")),
    ],
}


def home_stats(user, allow, auth_db, sg_auth) -> dict:
    """A few small read-only numbers for the index page. Never raises."""
    out: dict = {}
    try:
        if allow("manage_users"):
            c = sg_auth.connect(auth_db)
            try:
                out["Accounts"] = len(sg_auth.list_users(c))
                out["Students"] = len(sg_auth.student_profiles(c))
            finally:
                c.close()
        else:
            p = sg_auth.user_db_path(user["username"])
            if p.exists():
                con = sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True)
                try:
                    now = date.today().isoformat()
                    out["Pending topics"] = con.execute(
                        "SELECT COUNT(*) FROM topics WHERE status='pending'").fetchone()[0]
                    out["Blocks today"] = con.execute(
                        "SELECT COUNT(*) FROM sessions WHERE session_date=? AND status='planned'", (now,)).fetchone()[0]
                    nxt = con.execute(
                        "SELECT title, exam_date FROM exams WHERE exam_date>=? ORDER BY exam_date LIMIT 1", (now,)).fetchone()
                    out["Next exam"] = f"{nxt[0]} · {nxt[1]}" if nxt else "none set"
                finally:
                    con.close()
    except (sqlite3.Error, OSError):
        pass
    return out


def render(st, user, allow, auth_db, sg_auth) -> None:
    """Draw the index page."""
    first = (user.get("full_name") or user["username"]).split()[0]
    is_admin = user["role"] == "admin"
    st.markdown(
        f'<div class="hero-row"><div><div class="sec" style="font-size:2rem;margin:0">Welcome, {html.escape(first)}'
        f'<span class="role {user["role"]}">{user["role"]}</span></div>'
        f'<small style="color:#b9c0ea">{"Administrator home" if is_admin else "Student home"} · choose where to go</small>'
        f"</div></div>",
        unsafe_allow_html=True,
    )
    stats = home_stats(user, allow, auth_db, sg_auth)
    if stats:
        for col, (k, v) in zip(st.columns(len(stats)), stats.items(), strict=True):
            col.metric(k, v)
    st.markdown("&nbsp;")
    cards = [c for c in HOME_CARDS.get(user["role"], HOME_CARDS["user"]) if allow(*c[4])]
    for row in range(0, len(cards), 3):
        for col, (icon, title, text, tab, _need) in zip(st.columns(3), cards[row:row + 3], strict=False):
            with col.container(border=True):
                st.markdown(f"### {icon} {title}")
                st.caption(text)
                if st.button("Open", key=f"home_{title}", width="stretch"):
                    st.session_state["page"] = "app"
                    st.session_state["open_tab"] = tab
                    st.rerun()


def gate(st, user, allow, auth_on, auth_db, sg_auth) -> None:
    """Call once, right after login and permissions are known.

    On the home page it draws the index and stops the script; once a card is opened
    (page == "app") it returns so the dashboard below runs, with a back button in the sidebar.
    """
    if not auth_on:
        return
    if st.session_state.get("page", "home") == "app":
        if st.sidebar.button("← Back to home", key="sg_home_btn"):
            st.session_state["page"] = "home"
            st.rerun()
        return
    render(st, user, allow, auth_db, sg_auth)
    st.stop()  # the dashboard only runs after a card is opened
