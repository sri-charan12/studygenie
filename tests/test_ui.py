"""UI tests: the Streamlit app is driven with simulated clicks and typing."""

from pathlib import Path

import pytest
from helpers import seed

import studygenie as sg

testing = pytest.importorskip("streamlit.testing.v1")
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def app(tmp_path, monkeypatch):
    db = tmp_path / "ui.db"
    with sg.open_db(str(db)) as conn:
        seed(conn)
    monkeypatch.setenv("STUDYGENIE_DB", str(db))
    monkeypatch.setenv("STUDYGENIE_AUTH", "0")  # these tests check the planner screens, not the login
    return testing.AppTest.from_file(str(ROOT / "studygenie_ui.py"), default_timeout=60).run()


def button(app, label):
    return next(b for b in app.button if b.label == label)


def warnings(app):
    return [w.value for w in app.warning]


def test_app_starts_with_all_tabs_and_no_errors(app):
    assert not app.exception
    assert [t.label for t in app.tabs] == [
        "🏠 Dashboard",
        "➕ Add data",
        "🛠️ Manage",
        "🗓️ Plan",
        "✅ Today",
        "🔍 Why this order",
        "🔴 Live view",
    ]


def test_typing_a_duplicate_subject_shows_a_caution_and_blocks_the_button(app):
    app.text_input(key="sn").set_value("  maths ").run()
    assert any("Duplicate found: subject 'maths'" in w for w in warnings(app))
    assert button(app, "Add subject").disabled


def test_new_subject_has_no_caution_and_can_be_added(app):
    app.text_input(key="sn").set_value("Physics").run()
    assert not any("Duplicate found" in w for w in warnings(app))
    button(app, "Add subject").click().run()
    assert any("Added subject 'Physics'" in t.value for t in app.toast)
    assert not app.exception


def test_typing_a_duplicate_topic_shows_a_caution(app):
    app.selectbox(key="ts").set_value("Maths").run()
    app.text_input(key="tn").set_value("calculus").run()
    assert any("Duplicate found: topic 'calculus'" in w for w in warnings(app))
    assert button(app, "Add topic").disabled


def test_typing_a_duplicate_exam_shows_a_caution(app):
    app.selectbox(key="es").set_value("Maths").run()
    app.text_input(key="et").set_value("midterm").run()
    assert any("Duplicate found: exam 'midterm'" in w for w in warnings(app))
    assert button(app, "Add exam").disabled


def test_same_topic_name_in_another_subject_has_no_caution(app):
    app.selectbox(key="ts").set_value("History").run()
    app.text_input(key="tn").set_value("Calculus").run()
    assert not any("Duplicate found" in w for w in warnings(app))
    assert not button(app, "Add topic").disabled


def test_generate_plan_then_mark_a_block_done(app):
    button(app, "✨ Generate plan").click().run()
    assert any("Plan created" in t.value for t in app.toast)
    button(app, "Done").click().run()
    assert any("completed" in t.value for t in app.toast)
    assert not app.exception


def test_deleting_a_subject_needs_confirmation(app):
    delete = next(b for b in app.button if b.label == "Delete subject")
    assert delete.disabled


def test_the_name_box_is_emptied_after_adding_so_no_false_duplicate_warning(app):
    app.text_input(key="sn").set_value("Physics").run()
    button(app, "Add subject").click().run()
    assert app.text_input(key="sn").value == ""
    assert not any("Duplicate found" in w for w in warnings(app))