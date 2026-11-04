"""Review demo: five small tests, each with ONE value you can change.

1. Run:      python -m pytest review_demo -v          (all PASS)
2. Change the number or sign marked  # CHANGE ME  in any test.
3. Run again: that test turns FAIL and shows  expected value vs real value.
4. Change it back: PASS again.
"""

import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sg_core as core  # noqa: E402
import studygenie as sg  # noqa: E402

TODAY = date(2026, 10, 26)


def topic(days_left, weight=4.0, minutes=60):
    return core.Item(1, "Calculus", "Maths", minutes, minutes, weight, TODAY + timedelta(days=days_left - 1))


def test_difficulty_to_minutes():
    # Rule: a topic of difficulty 4 needs 4 x 30 = 120 minutes
    assert core.estimate_minutes(4) == 120  # CHANGE ME: try 130


def test_score_formula():
    # weight 4.0 x urgency (1 + 5/2) x (0.3 + need 1.0) x variety 1 = 18.2
    score = core.RuleBasedScorer().score(topic(days_left=2), TODAY, {})
    assert score == pytest.approx(18.2)  # CHANGE ME: try 20


def test_closer_exam_scores_higher():
    scorer = core.RuleBasedScorer()
    near, far = topic(days_left=2), topic(days_left=10)
    assert scorer.score(near, TODAY, {}) > scorer.score(far, TODAY, {})  # CHANGE ME: swap > for <


def test_whole_topic_is_scheduled():
    # 150 minutes, 90 minutes a day, 5 days available: everything fits
    blocks = core.Planner(core.RuleBasedScorer()).build([topic(5, minutes=150)], TODAY, 5, 90)
    assert sum(b.minutes for b in blocks) == 150  # CHANGE ME: try 200


def test_duplicate_subject_is_refused(tmp_path):
    with sg.open_db(str(tmp_path / "demo.db")) as conn:
        sg.cmd_add_subject(conn, SimpleNamespace(name="Maths", difficulty=3))
        with pytest.raises(core.DuplicateError):
            sg.cmd_add_subject(conn, SimpleNamespace(name="  MATHS ", difficulty=4))
        assert conn.execute("SELECT COUNT(*) FROM subjects").fetchone()[0] == 1  # CHANGE ME: try 2
