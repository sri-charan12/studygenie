"""Pytest setup: make the project importable and provide a fresh database per test."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import studygenie as sg  # noqa: E402


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


@pytest.fixture
def conn(db_path):
    """An empty StudyGenie database that is closed after the test."""
    with sg.open_db(str(db_path)) as connection:
        yield connection
