"""Unit tests for the scoring rules, planner and helpers in sg_core.py."""

import inspect
from dataclasses import FrozenInstanceError
from datetime import date, timedelta

import pytest

import sg_core as core

TODAY = date(2026, 10, 26)  # a Monday; fixed so the tests do not depend on the real clock


def item(id=1, name="Calculus", subject="Maths", est=60, remaining=60, weight=4.0, days=2):
    """An Item whose exam is `days` days away (days_left(TODAY) == days)."""
    return core.Item(id, name, subject, est, remaining, weight, TODAY + timedelta(days=days - 1))


def build(items, days=5, daily=90, rest=frozenset(), scorer=None):
    planner = core.Planner(scorer or core.RuleBasedScorer())
    return planner.build(items, TODAY, days, daily, rest)


# ----------------------------------------------------------------- helpers
def test_estimate_minutes_is_difficulty_times_30():
    assert core.estimate_minutes(3) == 90
    assert core.estimate_minutes(5) == 150


def test_iter_days_is_a_lazy_generator():
    days = core.iter_days(TODAY, 3)
    assert inspect.isgenerator(days)
    assert next(days) == TODAY
    assert list(days) == [TODAY + timedelta(days=1), TODAY + timedelta(days=2)]


def test_limiter_hands_out_only_the_budget():
    take = core.make_limiter(90)
    assert [take(45), take(45), take(45)] == [45, 45, 0]


def test_limiter_gives_what_is_left():
    take = core.make_limiter(50)
    assert [take(45), take(45)] == [45, 5]


@pytest.mark.parametrize(
    ("typed", "expected"),
    [("  Java   Full  Stack ", "Java Full Stack"), ("Maths", "Maths"), ("   ", "")],
)
def test_clean_name(typed, expected):
    assert core.clean_name(typed) == expected


# ----------------------------------------------------------------- scorers
def test_rule_based_score_matches_the_formula():
    # weight 4 x urgency (1 + 5/2 = 3.5) x (0.3 + need 1.0) x variety 1.0
    assert core.RuleBasedScorer().score(item(), TODAY, {}) == pytest.approx(4 * 3.5 * 1.3)


def test_variety_penalty_halves_a_topic_already_planned_today():
    scorer = core.RuleBasedScorer()
    fresh = scorer.score(item(), TODAY, {})
    again = scorer.score(item(), TODAY, {1: 45})
    assert again == pytest.approx(fresh / 2)


def test_topic_with_less_remaining_scores_lower():
    scorer = core.RuleBasedScorer()
    full = item(est=60, remaining=60)
    half = item(est=60, remaining=30)
    assert scorer.score(half, TODAY, {}) < scorer.score(full, TODAY, {})


def test_closer_exam_scores_higher():
    scorer = core.RuleBasedScorer()
    assert scorer.score(item(days=2), TODAY, {}) > scorer.score(item(days=10), TODAY, {})


def test_urgency_only_scorer():
    assert core.UrgencyOnlyScorer().score(item(days=2), TODAY, {}) == pytest.approx(3.5)


def test_exam_week_scorer_boosts_the_last_three_days_only():
    rule, boosted = core.RuleBasedScorer(), core.ExamWeekScorer()
    near, far = item(days=3), item(days=5)
    assert boosted.score(near, TODAY, {}) == pytest.approx(rule.score(near, TODAY, {}) * 1.5)
    assert boosted.score(far, TODAY, {}) == pytest.approx(rule.score(far, TODAY, {}))


def test_scorer_is_abstract():
    with pytest.raises(TypeError):
        core.Scorer()  # type: ignore[abstract]


def test_scorer_registry_has_three_rules():
    assert set(core.SCORERS) == {"rule", "urgency", "exam-week"}
    assert all(issubclass(cls, core.Scorer) for cls in core.SCORERS.values())


# ----------------------------------------------------------------- planner
def test_daily_budget_is_never_exceeded():
    blocks = build([item(est=600, remaining=600, days=5)], days=5, daily=100)
    per_day, _ = core.summarize(blocks)
    assert per_day and all(minutes <= 100 for minutes in per_day.values())


def test_chunks_of_one_topic_on_one_day_merge_into_one_block():
    # 135 minutes = three 45-minute chunks, reported as a single block for that day
    blocks = build([item(est=135, remaining=135, days=5)], days=5, daily=200)
    assert [(b.day, b.minutes) for b in blocks] == [(TODAY, 135)]


def test_nothing_is_scheduled_after_the_last_allowed_day():
    topic = item(est=600, remaining=600, days=2)  # may only be studied today and tomorrow
    blocks = build([topic], days=10, daily=90)
    assert blocks and all(b.day <= topic.last_day for b in blocks)


def test_everything_is_scheduled_when_there_is_enough_time():
    items = [item(1, est=90, remaining=90, days=5), item(2, "Optics", "Physics", 60, 60, 2.0, 5)]
    blocks = build(items, days=5, daily=90)
    assert sum(b.minutes for b in blocks) == 150
    assert all(i.remaining == 0 for i in items)


def test_not_enough_time_leaves_minutes_unscheduled():
    topic = item(est=600, remaining=600, days=10)
    blocks = build([topic], days=2, daily=90)
    assert sum(b.minutes for b in blocks) == 180
    assert topic.remaining == 420


def test_same_input_gives_the_same_plan():
    first = build([item(1, est=150, remaining=150, days=4), item(2, "Optics", "Physics", 90, 90, 2.0, 5)])
    second = build([item(1, est=150, remaining=150, days=4), item(2, "Optics", "Physics", 90, 90, 2.0, 5)])
    assert first == second


def test_rest_days_get_no_blocks():
    tuesday = 1
    blocks = build([item(est=600, remaining=600, days=5)], days=5, daily=90, rest=frozenset({tuesday}))
    assert blocks
    assert all(b.day.weekday() != tuesday for b in blocks)


def test_variety_mixes_topics_on_one_day():
    items = [item(1, "A", "S1", 90, 90, 3.0, 5), item(2, "B", "S2", 90, 90, 3.0, 5)]
    blocks = build(items, days=1, daily=90)
    assert {b.topic for b in blocks} == {"A", "B"}


def test_swapping_the_scorer_changes_the_first_choice():
    def pair():
        return [
            item(1, "Calculus", "Maths", 90, 90, 4.5, days=5),
            item(2, "Optics", "Physics", 60, 60, 2.0, days=2),
        ]

    first = {key: build(pair(), days=1, daily=45, scorer=cls())[0].topic for key, cls in core.SCORERS.items()}
    assert first == {"rule": "Calculus", "urgency": "Optics", "exam-week": "Optics"}


def test_block_length_must_be_at_least_five_minutes():
    planner = core.Planner(core.RuleBasedScorer())
    with pytest.raises(core.ValidationError):
        planner.block_minutes = 4
    planner.block_minutes = 5
    assert planner.block_minutes == 5


# ------------------------------------------------------------------ models
def test_block_is_immutable_hashable_and_sortable():
    early = core.Block(TODAY, 1, "A", "S", 45)
    late = core.Block(TODAY + timedelta(days=1), 2, "B", "S", 45)
    with pytest.raises(FrozenInstanceError):
        early.minutes = 1  # type: ignore[misc]
    assert early == core.Block(TODAY, 1, "A", "S", 45)
    assert len({early, core.Block(TODAY, 1, "A", "S", 45), late}) == 2
    assert sorted([late, early]) == [early, late]


def test_summarize_totals_per_day_and_per_subject():
    blocks = [
        core.Block(TODAY, 1, "A", "Maths", 45),
        core.Block(TODAY, 2, "B", "History", 30),
        core.Block(TODAY + timedelta(days=1), 1, "A", "Maths", 45),
    ]
    per_day, per_subject = core.summarize(blocks)
    assert per_day == {TODAY: 75, TODAY + timedelta(days=1): 45}
    assert per_subject == {"Maths": 90, "History": 30}


# --------------------------------------------------------------- insights
def test_streak_counts_consecutive_days():
    days = {TODAY, TODAY - timedelta(days=1), TODAY - timedelta(days=2)}
    assert core.streak(days, TODAY) == 3


def test_streak_stops_at_a_gap():
    assert core.streak({TODAY, TODAY - timedelta(days=2)}, TODAY) == 1


def test_streak_survives_until_today_is_over():
    assert core.streak({TODAY - timedelta(days=1), TODAY - timedelta(days=2)}, TODAY) == 2


def test_streak_is_zero_without_study():
    assert core.streak(set(), TODAY) == 0
    assert core.streak({TODAY - timedelta(days=5)}, TODAY) == 0


@pytest.mark.parametrize(
    ("due", "studied", "expected"),
    [
        (0, 0, "on track"),
        (100, 100, "on track"),
        (100, 90, "on track"),
        (100, 70, "slightly behind"),
        (100, 60, "slightly behind"),
        (100, 59, "behind"),
        (100, 0, "behind"),
    ],
)
def test_track_status(due, studied, expected):
    assert core.track_status(due, studied) == expected


def test_explain_ranks_topics_by_score_and_skips_finished_ones():
    expired = core.Item(9, "Old", "Maths", 60, 60, 4.0, TODAY - timedelta(days=1))
    done = core.Item(8, "Done", "Maths", 60, 0, 4.0, TODAY + timedelta(days=3))
    rows = core.explain([item(1, "Near", days=2), item(2, "Far", days=9), expired, done], TODAY)
    assert [r["topic"] for r in rows] == ["Near", "Far"]
    assert rows[0]["score"] > rows[1]["score"]
    assert {"subject", "topic", "weight", "days_left", "urgency", "need", "score"} <= set(rows[0])
