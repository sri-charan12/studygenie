"""Role tests for the dashboard: each student has a private planner, the admin looks after all of them."""

from pathlib import Path

import pytest
from helpers import day, ns, seed

import sg_auth
import studygenie as sg

testing = pytest.importorskip("streamlit.testing.v1")
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def open_as(tmp_path, monkeypatch):
    """Start the dashboard with login on, already signed in. ravi has Maths and History, meena has Chemistry."""
    data, auth = tmp_path / "data", tmp_path / "auth.db"
    data.mkdir()
    with sg.open_db(str(sg_auth.user_db_path("ravi", data))) as conn:
        seed(conn)
    with sg.open_db(str(sg_auth.user_db_path("meena", data))) as conn:
        sg.cmd_add_subject(conn, ns(name="Chemistry", difficulty=3))
        sg.cmd_add_exam(conn, ns(subject="Chemistry", title="Viva", date=day(4)))
    people = sg_auth.connect(str(auth))
    sg_auth.create_user(people, "asha", "Admin@pass1", "admin", "Asha Rao", "asha@example.com")
    sg_auth.create_user(people, "ravi", "Student@pass1", "user", "Ravi K", "ravi@example.com", "CBIT", "B.Tech CSE")
    sg_auth.create_user(people, "meena", "Student@pass1", "user", "Meena S", "meena@example.com", "VIT", "B.Sc Chemistry")
    people.close()
    monkeypatch.setenv("STUDYGENIE_AUTH", "1")
    monkeypatch.setenv("STUDYGENIE_AUTH_DB", str(auth))
    monkeypatch.setenv("STUDYGENIE_DATA_DIR", str(data))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def build(role):
        who = {"admin": ("asha", "Asha Rao"), "user": ("ravi", "Ravi K")}[role]
        app = testing.AppTest.from_file(str(ROOT / "studygenie_ui.py"), default_timeout=60)
        app.session_state["user"] = {"username": who[0], "full_name": who[1], "role": role}
        return app.run()

    return build


def labels(app):
    return [t.label for t in app.tabs]


def texts(app):
    return [m.value for m in app.markdown]


def test_not_signed_in_shows_the_login_page_and_no_dashboard(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDYGENIE_AUTH", "1")
    monkeypatch.setenv("STUDYGENIE_AUTH_DB", str(tmp_path / "auth.db"))
    monkeypatch.setenv("STUDYGENIE_DATA_DIR", str(tmp_path / "data"))
    app = testing.AppTest.from_file(str(ROOT / "studygenie_ui.py"), default_timeout=60).run()
    assert not app.exception
    assert "🏠 Dashboard" not in labels(app)


def test_student_gets_every_tab_except_users(open_as):
    app = open_as("user")
    assert not app.exception
    assert labels(app) == ["🏠 Dashboard", "➕ Add data", "🛠️ Manage", "🗓️ Plan", "✅ Today",
                           "🔍 Why this order", "🔴 Live view"]


def test_admin_can_fix_data_but_does_not_plan_or_study(open_as):
    app = open_as("admin")
    assert not app.exception
    assert labels(app) == ["🏠 Dashboard", "➕ Add data", "🛠️ Manage", "🔴 Live view", "👑 Users"]


def test_a_student_sees_only_their_own_planner(open_as):
    page = " ".join(texts(open_as("user")))
    assert "Maths" in page
    assert "Chemistry" not in page


def test_each_student_planner_is_a_separate_file(open_as, tmp_path):
    open_as("user")
    files = sorted(p.name for p in (tmp_path / "data").glob("*.db"))
    assert files == ["meena.db", "ravi.db"]


def test_admin_overview_lists_every_students_exams(open_as):
    cells = " ".join(frame.value.to_string() for frame in open_as("admin").dataframe)
    assert "Maths" in cells and "Chemistry" in cells
    assert "CBIT" in cells and "VIT" in cells
    assert "Calculus" in cells and "World War 2" in cells  # topics of ravi
    assert "(no topics yet)" in cells  # meena has a subject but no topics


def test_only_admin_sees_the_all_students_schedule(open_as):
    assert any("Exam schedule" in t for t in texts(open_as("admin")))
    assert not any("Exam schedule" in t for t in texts(open_as("user")))


def test_admin_with_no_students_gets_a_message(tmp_path, monkeypatch):
    auth = tmp_path / "auth.db"
    people = sg_auth.connect(str(auth))
    sg_auth.create_user(people, "asha", "Admin@pass1", "admin", "Asha Rao", "asha@example.com")
    people.close()
    monkeypatch.setenv("STUDYGENIE_AUTH", "1")
    monkeypatch.setenv("STUDYGENIE_AUTH_DB", str(auth))
    monkeypatch.setenv("STUDYGENIE_DATA_DIR", str(tmp_path / "data"))
    app = testing.AppTest.from_file(str(ROOT / "studygenie_ui.py"), default_timeout=60)
    app.session_state["user"] = {"username": "asha", "full_name": "Asha Rao", "role": "admin"}
    app.run()
    assert not app.exception
    assert any("No students have signed up" in i.value for i in app.info)


def test_ask_studygenie_is_a_student_feature(open_as):
    assert any("Ask StudyGenie" in t for t in texts(open_as("user")))
    assert not any("Ask StudyGenie" in t for t in texts(open_as("admin")))


def test_duplicates_are_checked_inside_the_signed_in_students_planner_only(open_as):
    app = open_as("user")  # ravi has Maths; meena has Chemistry
    app.text_input(key="sn").set_value("maths").run()
    assert any("Duplicate found: subject 'maths'" in w.value for w in app.warning)
    app.text_input(key="sn").set_value("Chemistry").run()  # meena's subject, not ravi's
    assert not any("Duplicate found" in w.value for w in app.warning)