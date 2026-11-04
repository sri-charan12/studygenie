"""Duplicate protection: the same subject, topic or exam must never be saved twice."""

import pytest
from helpers import count, day, ns, seed

import sg_core as core
import studygenie as sg


# ----------------------------------------------------------------- subjects
@pytest.mark.parametrize("typed", ["Maths", "maths", "MATHS", "  Maths  "])
def test_duplicate_subject_is_refused(conn, typed):
    seed(conn)
    with pytest.raises(core.DuplicateError, match="Duplicate found"):
        sg.cmd_add_subject(conn, ns(name=typed, difficulty=3))
    assert count(conn, "subjects") == 2


def test_extra_spaces_inside_a_subject_name_do_not_hide_a_duplicate(conn):
    sg.cmd_add_subject(conn, ns(name="Data  Structures", difficulty=3))
    with pytest.raises(core.DuplicateError):
        sg.cmd_add_subject(conn, ns(name="data structures", difficulty=3))
    assert count(conn, "subjects") == 1


def test_different_subject_names_are_allowed(conn):
    seed(conn)
    sg.cmd_add_subject(conn, ns(name="Physics", difficulty=3))
    assert count(conn, "subjects") == 3


# ------------------------------------------------------------------- topics
@pytest.mark.parametrize("typed", ["Calculus", "calculus", " CALCULUS "])
def test_duplicate_topic_in_the_same_subject_is_refused(conn, typed):
    seed(conn)
    with pytest.raises(core.DuplicateError, match="Duplicate found: topic"):
        sg.cmd_add_topic(conn, ns(subject="Maths", name=typed, difficulty=3))
    assert count(conn, "topics") == 3


def test_subject_name_case_does_not_hide_a_duplicate_topic(conn):
    seed(conn)
    with pytest.raises(core.DuplicateError):
        sg.cmd_add_topic(conn, ns(subject="maths", name="Calculus", difficulty=3))


def test_same_topic_name_in_another_subject_is_allowed(conn):
    seed(conn)
    sg.cmd_add_topic(conn, ns(subject="History", name="Calculus", difficulty=2))
    assert count(conn, "topics") == 4


# -------------------------------------------------------------------- exams
@pytest.mark.parametrize("typed", ["Midterm", "midterm", "  MIDTERM "])
def test_duplicate_exam_is_refused_even_with_another_date(conn, typed):
    seed(conn)
    with pytest.raises(core.DuplicateError, match="Duplicate found: exam"):
        sg.cmd_add_exam(conn, ns(subject="Maths", title=typed, date=day(30)))
    assert count(conn, "exams") == 2


def test_duplicate_exam_message_shows_the_existing_date(conn):
    seed(conn, maths_exam_in=6)
    with pytest.raises(core.DuplicateError) as error:
        sg.cmd_add_exam(conn, ns(subject="Maths", title="Midterm", date=day(30)))
    assert day(6) in str(error.value)


def test_other_exam_titles_and_other_subjects_are_allowed(conn):
    seed(conn)
    sg.cmd_add_exam(conn, ns(subject="Maths", title="Final", date=day(30)))
    sg.cmd_add_exam(conn, ns(subject="History", title="Final", date=day(31)))
    assert count(conn, "exams") == 4


# ---------------------------------------------------------------- the error
def test_duplicate_error_is_a_validation_error():
    assert issubclass(core.DuplicateError, core.ValidationError)
    assert issubclass(core.DuplicateError, core.StudyGenieError)


# ------------------------------------------------------------- command line
def test_cli_shows_a_caution_for_a_duplicate(db_path, capsys):
    sg.main(["--db", str(db_path), "add-subject", "Maths", "3"])
    with pytest.raises(SystemExit) as stop:
        sg.main(["--db", str(db_path), "add-subject", "maths", "4"])
    assert stop.value.code == 1
    assert "Caution: Duplicate found" in capsys.readouterr().err
    with sg.open_db(str(db_path)) as conn:
        assert count(conn, "subjects") == 1


def test_cli_caution_for_duplicate_exam(db_path, capsys):
    sg.main(["--db", str(db_path), "add-subject", "Maths", "3"])
    sg.main(["--db", str(db_path), "add-exam", "Maths", "Midterm", day(5)])
    with pytest.raises(SystemExit):
        sg.main(["--db", str(db_path), "add-exam", "Maths", "midterm", day(9)])
    assert "Caution: Duplicate found: exam" in capsys.readouterr().err


# -------------------------------------------------------------------- Excel
def write_workbook(path, subjects, topics, exams):
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    for name, header, data in (
        ("subjects", ["id", "name", "difficulty"], subjects),
        ("topics", ["id", "subject", "name", "difficulty", "est_minutes"], topics),
        ("exams", ["id", "subject", "title", "exam_date"], exams),
    ):
        ws = wb.create_sheet(name)
        ws.append(header)
        for row in data:
            ws.append(row)
    wb.save(path)


def test_excel_import_skips_duplicates_and_warns(conn, tmp_path, capsys):
    pytest.importorskip("openpyxl")
    book = tmp_path / "data.xlsx"
    write_workbook(
        book,
        subjects=[[1, "Maths", 3], [2, "maths", 4]],  # duplicate inside the file
        topics=[[1, "Maths", "Algebra", 3, 90], [2, "Maths", "algebra", 3, 90]],
        exams=[[1, "Maths", "Midterm", day(5)], [2, "Maths", "midterm", day(8)]],
    )
    sg.cmd_import_excel(conn, ns(file=str(book)))
    assert (count(conn, "subjects"), count(conn, "topics"), count(conn, "exams")) == (1, 1, 1)
    assert "Duplicate found - 3 row(s)" in capsys.readouterr().out


def test_importing_the_same_file_twice_adds_nothing(conn, tmp_path, capsys):
    pytest.importorskip("openpyxl")
    book = tmp_path / "data.xlsx"
    write_workbook(
        book,
        subjects=[[1, "Maths", 3]],
        topics=[[1, "Maths", "Algebra", 3, 90]],
        exams=[[1, "Maths", "Midterm", day(5)]],
    )
    sg.cmd_import_excel(conn, ns(file=str(book)))
    capsys.readouterr()
    sg.cmd_import_excel(conn, ns(file=str(book)))
    out = capsys.readouterr().out
    assert "Imported 0 subjects, 0 topics, 0 exams" in out
    assert "Duplicate found" in out
    assert (count(conn, "subjects"), count(conn, "topics"), count(conn, "exams")) == (1, 1, 1)
