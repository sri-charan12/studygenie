"""Core building blocks for StudyGenie: errors, models, scoring rules and helpers."""

from __future__ import annotations

import functools
import logging
import time
from abc import ABC, abstractmethod
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import groupby
from pathlib import Path
from types import TracebackType
from typing import Any, ClassVar, TypeVar, cast

BLOCK_MINUTES = 45  # length of one study block
MINUTES_PER_LEVEL = 30  # estimated effort = difficulty * 30 min

log = logging.getLogger("studygenie")
F = TypeVar("F", bound=Callable[..., Any])


# ---------------------------------------------------------------- exceptions
class StudyGenieError(Exception):
    """Base class for every error StudyGenie raises on purpose."""


class NotFoundError(StudyGenieError):
    """A subject, topic or session does not exist."""


class ValidationError(StudyGenieError):
    """The input is not acceptable."""


class DuplicateError(ValidationError):
    """The same subject, topic or exam already exists."""


def clean_name(value: str) -> str:
    """Trim a name and collapse repeated spaces, so 'Java  Full  Stack ' equals 'Java Full Stack'."""
    return " ".join(value.split())


# ------------------------------------------------------- logging and helpers
def setup_logging(path: str | Path) -> None:
    """Send StudyGenie log lines to a file (safe to call again with a new path)."""
    logger = logging.getLogger("studygenie")
    for handler in list(logger.handlers):
        if isinstance(handler, logging.FileHandler):
            logger.removeHandler(handler)
            handler.close()
    try:
        handler = logging.FileHandler(Path(path), encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def timed(fn: F) -> F:
    """Decorator: log how long the wrapped function took."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            log.info("%s took %.2f ms", fn.__name__, (time.perf_counter() - start) * 1000)

    return cast(F, wrapper)


class Timer:
    """Context manager that measures how long its block takes."""

    ms: float = 0.0

    def __enter__(self) -> Timer:
        self._start = time.perf_counter()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.ms = (time.perf_counter() - self._start) * 1000


@functools.cache
def estimate_minutes(difficulty: int) -> int:
    """Estimated effort for a topic of this difficulty (cached)."""
    return difficulty * MINUTES_PER_LEVEL


def iter_days(start: date, n: int) -> Iterator[date]:
    """Generator: yield n consecutive days, one at a time."""
    for offset in range(n):
        yield start + timedelta(days=offset)


def make_limiter(total: int) -> Callable[[int], int]:
    """Closure: hand out minutes from a daily budget until it runs out."""
    left = total

    def take(want: int) -> int:
        nonlocal left
        got = min(want, left)
        left -= got
        return got

    return take


# -------------------------------------------------------------------- models
@dataclass(slots=True)
class Item:
    """A topic that still needs study time (mutable: `remaining` shrinks)."""

    id: int
    name: str
    subject: str
    est: int
    remaining: int
    weight: float
    last_day: date

    @property
    def need(self) -> float:
        return self.remaining / self.est

    def days_left(self, day: date) -> int:
        return (self.last_day - day).days + 1


@dataclass(frozen=True, slots=True)
class Block:
    """One planned study block (immutable, hashable)."""

    day: date
    topic_id: int
    topic: str
    subject: str
    minutes: int

    def __str__(self) -> str:
        return f"{self.day}  {self.subject} / {self.topic}  {self.minutes} min"

    def __lt__(self, other: Block) -> bool:
        return (self.day, self.topic_id) < (other.day, other.topic_id)


# ------------------------------------------------------------ scoring rules
class Scorer(ABC):
    """Abstract scoring rule. Subclasses decide how urgent a topic is."""

    key: ClassVar[str] = "base"

    @abstractmethod
    def score(self, item: Item, day: date, allocated: dict[int, int]) -> float:
        """Higher score = scheduled first."""

    def __repr__(self) -> str:
        return f"{type(self).__name__}(key={self.key!r})"


class RuleBasedScorer(Scorer):
    """score = weight x urgency x (0.3 + need) x variety"""

    key = "rule"

    def score(self, item: Item, day: date, allocated: dict[int, int]) -> float:
        urgency = 1 + 5 / item.days_left(day)
        variety = 0.5 if item.id in allocated else 1.0
        return item.weight * urgency * (0.3 + item.need) * variety


class UrgencyOnlyScorer(Scorer):
    """Only the exam distance matters."""

    key = "urgency"

    def score(self, item: Item, day: date, allocated: dict[int, int]) -> float:
        return 1 + 5 / item.days_left(day)


class ExamWeekScorer(RuleBasedScorer):
    """The rule-based score, boosted by 1.5x in the last 3 days before an exam."""

    key = "exam-week"

    def score(self, item: Item, day: date, allocated: dict[int, int]) -> float:
        base = super().score(item, day, allocated)
        return base * 1.5 if item.days_left(day) <= 3 else base


SCORERS: dict[str, type[Scorer]] = {
    cls.key: cls for cls in (RuleBasedScorer, UrgencyOnlyScorer, ExamWeekScorer)
}


# ------------------------------------------------------------------- planner
class Planner:
    """Greedy scheduler. It has-a Scorer (composition), so rules can be swapped."""

    def __init__(self, scorer: Scorer, block_minutes: int = BLOCK_MINUTES) -> None:
        self.scorer = scorer
        self.block_minutes = block_minutes  # goes through the property setter

    @property
    def block_minutes(self) -> int:
        return self._block_minutes

    @block_minutes.setter
    def block_minutes(self, value: int) -> None:
        if value < 5:
            raise ValidationError("block length must be at least 5 minutes")
        self._block_minutes = value

    @timed
    def build(
        self,
        items: list[Item],
        start: date,
        days: int,
        daily: int,
        rest_days: frozenset[int] = frozenset(),
    ) -> list[Block]:
        """Fill each day with the highest-scoring blocks until its time is used."""
        by_id = {i.id: i for i in items}
        blocks: list[Block] = []
        for day in iter_days(start, days):
            if day.weekday() in rest_days:  # 0 = Monday ... 6 = Sunday
                continue
            take = make_limiter(daily)
            alloc: dict[int, int] = defaultdict(int)
            key = functools.partial(self.scorer.score, day=day, allocated=alloc)
            while True:
                cands = [i for i in items if i.remaining > 0 and day <= i.last_day]
                if not cands:
                    break
                best = max(cands, key=key)
                got = take(min(self.block_minutes, best.remaining))
                if got == 0:
                    break
                alloc[best.id] += got
                best.remaining -= got
            blocks.extend(
                Block(day, tid, by_id[tid].name, by_id[tid].subject, mins) for tid, mins in alloc.items()
            )
        return blocks


def summarize(blocks: list[Block]) -> tuple[dict[date, int], Counter[str]]:
    """Minutes per day (sorted + groupby) and per subject (Counter)."""
    per_day = {
        day: sum(b.minutes for b in group) for day, group in groupby(sorted(blocks), key=lambda b: b.day)
    }
    per_subject: Counter[str] = Counter()
    for b in blocks:
        per_subject[b.subject] += b.minutes
    return per_day, per_subject


# ------------------------------------------------------- insights for the UI
def streak(days: set[date], today: date) -> int:
    """Consecutive study days ending today (or yesterday, if today has no study yet)."""
    day = today if today in days else today - timedelta(days=1)
    count = 0
    while day in days:
        count += 1
        day -= timedelta(days=1)
    return count


def track_status(due: int, studied: int) -> str:
    """Compare minutes studied with the minutes that were due before today."""
    if due == 0:
        return "on track"
    ratio = studied / due
    if ratio >= 0.9:
        return "on track"
    if ratio >= 0.6:
        return "slightly behind"
    return "behind"


def explain(items: list[Item], day: date, scorer: Scorer | None = None) -> list[dict[str, Any]]:
    """Show every factor behind each topic's score for the first block of a day."""
    scorer = scorer or RuleBasedScorer()
    rows = [
        {
            "subject": i.subject,
            "topic": i.name,
            "weight": i.weight,
            "days_left": i.days_left(day),
            "urgency": round(1 + 5 / i.days_left(day), 2),
            "need": round(i.need, 2),
            "score": round(scorer.score(i, day, {}), 2),
        }
        for i in items
        if i.remaining > 0 and day <= i.last_day
    ]
    return sorted(rows, key=lambda r: r["score"], reverse=True)
