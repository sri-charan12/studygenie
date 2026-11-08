"""Test cases for the AI coach. No API key or internet needed: the AI service is replaced by a fake."""
import sqlite3
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sg_ai  # noqa: E402


@pytest.fixture
def planner_db(tmp_path):
    path = tmp_path / "planner.db"
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE subjects(id INTEGER PRIMARY KEY, name TEXT, difficulty INT);
        CREATE TABLE exams(id INTEGER PRIMARY KEY, title TEXT, exam_date TEXT);
        CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT);
        CREATE TABLE students(id INTEGER PRIMARY KEY, name TEXT, email TEXT, password TEXT);
        INSERT INTO subjects VALUES (1,'Maths',4),(2,'History',2);
        INSERT INTO exams VALUES (1,'Midterm','2026-11-10');
        INSERT INTO users VALUES (1,'asha','SECRET-HASH-VALUE');
        INSERT INTO students VALUES (1,'Ravi','ravi@example.com','plain-secret');
    """)
    c.commit()
    c.close()
    return path


@pytest.fixture
<<<<<<< HEAD
def fake_anthropic(monkeypatch):
    calls = []

    class Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            content = [types.SimpleNamespace(type="text", text="  Study Maths first.  ")]
            return types.SimpleNamespace(content=content)
=======
def fake_gemini(monkeypatch):
    calls = []

    class Models:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            return types.SimpleNamespace(text="  Study Maths first.  ")
>>>>>>> 56917d7ad2e0548d676f020bd028ced300768960

    class Client:
        def __init__(self, api_key):
            calls.append({"api_key": api_key})
<<<<<<< HEAD
            self.messages = Messages()

    anthropic = types.ModuleType("anthropic")
    anthropic.Anthropic = Client
    monkeypatch.setitem(sys.modules, "anthropic", anthropic)
=======
            self.models = Models()

    google = types.ModuleType("google")
    genai = types.SimpleNamespace(Client=Client)
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
>>>>>>> 56917d7ad2e0548d676f020bd028ced300768960
    return calls


def test_context_contains_the_planner_data(planner_db):
    text = sg_ai.build_context(planner_db)
    assert "Maths" in text and "Midterm" in text and "2026-11-10" in text


def test_context_never_contains_login_tables_or_secrets(planner_db):
    text = sg_ai.build_context(planner_db)
    assert "SECRET-HASH-VALUE" not in text and "asha" not in text
    assert "plain-secret" not in text and "ravi@example.com" not in text


def test_context_is_limited_in_size_and_rows(tmp_path):
    path = tmp_path / "big.db"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE topics(id INTEGER PRIMARY KEY, name TEXT)")
    c.executemany("INSERT INTO topics(name) VALUES (?)", [(f"topic-{i}",) for i in range(500)])
    c.commit()
    c.close()
    text = sg_ai.build_context(path, max_rows=10)
    assert "topic-9" in text and "topic-10" not in text
    assert "490 more rows not shown" in text
    assert len(text) <= sg_ai.MAX_CONTEXT_CHARS


def test_context_is_read_only_and_does_not_change_the_database(planner_db):
    before = Path(planner_db).read_bytes()
    sg_ai.build_context(planner_db)
    assert Path(planner_db).read_bytes() == before


def test_a_missing_database_gives_a_clear_message(tmp_path):
    assert "no planner data" in sg_ai.build_context(tmp_path / "nothing.db")


<<<<<<< HEAD
def test_ask_sends_the_question_data_and_key_and_returns_the_answer(fake_anthropic, planner_db):
    answer = sg_ai.ask("What first?", [], sg_ai.build_context(planner_db), "test-key")
    assert answer == "Study Maths first."
    assert fake_anthropic[0] == {"api_key": "test-key"}
    sent = fake_anthropic[1]
    assert "Maths" in sent["system"]
    assert sent["messages"][-1] == {"role": "user", "content": "What first?"}
    assert sent["max_tokens"] <= 2000


def test_ask_keeps_only_the_last_few_messages_of_history(fake_anthropic):
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(20)]
    sg_ai.ask("next?", history, "ctx", "k")
    messages = fake_anthropic[1]["messages"]
    assert len(messages) == 9  # 8 history + the new question
    assert {message["role"] for message in messages} == {"user", "assistant"}
=======
def test_ask_sends_the_question_data_and_key_and_returns_the_answer(fake_gemini, planner_db):
    answer = sg_ai.ask("What first?", [], sg_ai.build_context(planner_db), "test-key")
    assert answer == "Study Maths first."
    assert fake_gemini[0] == {"api_key": "test-key"}
    sent = fake_gemini[1]
    assert "Maths" in sent["config"]["system_instruction"]
    assert sent["contents"][-1] == {"role": "user", "parts": [{"text": "What first?"}]}
    assert sent["config"]["max_output_tokens"] <= 2000


def test_ask_keeps_only_the_last_few_messages_of_history(fake_gemini):
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(20)]
    sg_ai.ask("next?", history, "ctx", "k")
    contents = fake_gemini[1]["contents"]
    assert len(contents) == 9  # 8 history + the new question
    assert {c["role"] for c in contents} == {"user", "model"}  # Gemini calls the assistant "model"
>>>>>>> 56917d7ad2e0548d676f020bd028ced300768960


def error(name, code=None, message=""):
    return type(name, (Exception,), {"code": code})(message)


@pytest.mark.parametrize("err,word", [
    (error("ClientError", 400, "API key not valid. Please pass a valid API key."), "key"),
    (error("ClientError", 403, "permission denied"), "key"),
    (error("ClientError", 429, "RESOURCE_EXHAUSTED"), "limit"),
    (error("ClientError", 404, "model not found"), "model"),
    (error("ServerError", 503, "overloaded"), "busy"),
    (error("ConnectError"), "internet"),
])
def test_errors_are_explained_in_plain_words(err, word):
    assert word in sg_ai.friendly_error(err).lower()


def test_a_wrong_key_is_recognised_so_it_can_be_forgotten():
    assert sg_ai.key_rejected(error("ClientError", 400, "API key not valid"))
    assert not sg_ai.key_rejected(error("ClientError", 429, "RESOURCE_EXHAUSTED"))