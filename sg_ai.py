"""
Ask StudyGenie: an AI study coach that answers questions about the student's own plan.

How it works
    1. build_context() reads the planner database (read-only) and turns the subjects, topics,
       exams and sessions into plain text. The login tables and anything that looks like a
       password, hash or email are never included.
    2. ask() sends that text plus the question to the Anthropic (Claude) API.
    3. render_ai_tab() is the chat page for Streamlit.

The API key is never written in code. Put it once in .streamlit/secrets.toml:
    ANTHROPIC_API_KEY = "your-key-here"
or set it in PowerShell:
    setx ANTHROPIC_API_KEY "your-key-here"        (then open a new terminal)
The model can be changed with the STUDYGENIE_MODEL environment variable.
"""
import os
import sqlite3
from pathlib import Path

DEFAULT_MODEL = os.environ.get("STUDYGENIE_MODEL", "claude-haiku-5-5")
SKIP_TABLES = {"users", "roles", "permissions", "role_permissions", "login_log"}
SKIP_COLUMNS = ("password", "hash", "salt", "email", "token", "secret")
MAX_CONTEXT_CHARS = 12_000

SYSTEM = (
    "You are the study coach inside StudyGenie, a rule-based study planner. "
    "Answer using the student's data below. Be short, practical and friendly. "
    "If the data does not contain the answer, say so instead of guessing. Never invent subjects, topics or dates. "
    "StudyGenie plans with fixed rules and no AI: a topic scores higher when its exam is closer, scores lower when "
    "less time is left, is halved if already planned the same day, and a topic's time is its difficulty x 30 minutes. "
    "Explain these rules if asked."
)

SUGGESTIONS = [
    "What should I study today?",
    "Which exam is closest and how do I prepare?",
    "Explain how my plan was made",
    "Am I behind on anything?",
]


def build_context(planner_db, max_rows=40):
    """Plain-text copy of the planner data. Opened read-only, so nothing can be changed."""
    path = Path(planner_db)
    if not path.exists():
        return "(no planner data found)"
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    parts = []
    try:
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for table in names:
            if table.lower() in SKIP_TABLES:
                continue
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
            keep = [i for i, c in enumerate(cols) if not any(bad in c.lower() for bad in SKIP_COLUMNS)]
            rows = con.execute(f'SELECT * FROM "{table}" LIMIT {int(max_rows)}').fetchall()
            total = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            parts.append(f"TABLE {table} ({total} rows)")
            parts.append(" | ".join(cols[i] for i in keep))
            parts += [" | ".join(str(r[i]) for i in keep) for r in rows]
            if total > len(rows):
                parts.append(f"... {total - len(rows)} more rows not shown")
            parts.append("")
    finally:
        con.close()
    return "\n".join(parts)[:MAX_CONTEXT_CHARS]


def ask(question, history, context, api_key, model=None):
    """Send the question to the Anthropic API and return the answer text."""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    messages = [{"role": "user" if m["role"] == "user" else "assistant", "content": m["content"]}
                for m in history[-8:]]
    messages.append({"role": "user", "content": question})
    reply = client.messages.create(
        model=model or DEFAULT_MODEL,
        max_tokens=2000,
        system=SYSTEM + "\n\nSTUDENT DATA\n" + context,
        messages=messages,
    )
    return "".join(block.text for block in reply.content if getattr(block, "type", "") == "text").strip()


def error_code(err):
    """HTTP status of an API error."""
    for attr in ("status_code", "code"):
        value = getattr(err, attr, None)
        if isinstance(value, int):
            return value
    return None


def key_rejected(err):
    """True when the service says the API key itself is wrong (so the pasted key should be forgotten)."""
    low = str(getattr(err, "message", "") or err).lower()
    return ("api key not valid" in low or "api_key_invalid" in low or "invalid x-api-key" in low
            or "invalid api key" in low or error_code(err) == 401)


def friendly_error(err):
    name = type(err).__name__
    code = error_code(err)
    detail = str(getattr(err, "message", "") or err)
    if isinstance(err, ImportError):
        return "The AI package is missing. Run:  python -m pip install anthropic"
    if key_rejected(err) or code == 403:
        return "The API key was not accepted. Create a new key in the Anthropic console and try again."
    if code == 429:
        return ("Rate limit reached (too many requests). "
                f"Wait a minute and try again. ({detail[:120]})")
    if code == 404:
        return "That model name was not found. Set STUDYGENIE_MODEL to a model your key can use."
    if code in (500, 502, 503, 504, 529) or name in ("ServerError", "InternalServerError"):
        return "The AI service is busy. Wait a moment and try again."
    if name in ("ConnectError", "ConnectTimeout", "ReadTimeout", "TimeoutException",
                "APIConnectionError", "APITimeoutError"):
        return "Could not reach the AI service. Check your internet connection."
    if code == 400 and "credit balance" in detail.lower():
        return "The Anthropic account has no credit left. Add credits in the Anthropic console and try again."
    if code == 400:
        return f"The AI service rejected the request: {detail[:200]}"
    return f"Something went wrong: {name}"


def shared_key(st):
    """One key for every user, set once by whoever runs the app: .streamlit/secrets.toml
    containing  ANTHROPIC_API_KEY = "your-key-here"  . Returns "" when it is not set."""
    try:
        return str(st.secrets.get("ANTHROPIC_API_KEY", "") or "")
    except Exception:  # noqa: BLE001 - no secrets file is normal
        return ""


def render_ai_tab(planner_db, user=None, compact=False, allowed=None):
    """The 'Ask AI' chat. compact=True drops the big title so it fits inside the dashboard.
    allowed=True/False overrides the shared login state (the dashboard passes the signed-in role's permission)."""
    import streamlit as st
    if allowed is None:
        import sg_auth
        allowed = sg_auth.can("ai_ask")
    if not allowed:
        st.info("Your role does not have access to the AI coach.")
        return

    if not compact:
        st.markdown('<div class="sg-hero" style="font-size:2rem">Ask StudyGenie</div>'
                    '<div class="sg-sub">Your study coach. Ask about your plan, exams and what to do next.</div>',
                    unsafe_allow_html=True)

    key = os.environ.get("ANTHROPIC_API_KEY") or shared_key(st) or st.session_state.get("api_key", "")
    if not key:
        with st.container(border=True):
            st.markdown("**Add your API key to start**")
            typed = st.text_input("Anthropic API key", type="password", key="api_key_box",
                                  help="Kept only in this browser session. It is never saved to a file.")
            if typed:
                st.session_state["api_key"] = typed
                st.rerun()
        return

    st.caption("To answer, your subjects, topics, exams and sessions are sent to the AI service. "
               "Accounts and passwords are never sent.")
    chat_key = f"chat:{Path(planner_db).name}"  # a separate conversation for each student
    chat = st.session_state.setdefault(chat_key, [])
    pending = None
    if not chat:
        cols = st.columns(len(SUGGESTIONS))
        for col, text in zip(cols, SUGGESTIONS):
            if col.button(text, key=f"sg_{text}", width="stretch"):
                pending = text
    for m in chat:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    question = st.chat_input("Ask about your plan...") or pending
    if question:
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                try:
                    answer, failed = ask(question, chat, build_context(planner_db), key), False
                except Exception as err:  # noqa: BLE001 - shown to the student in plain words
                    answer, failed = friendly_error(err), True
                    if key_rejected(err):
                        st.session_state.pop("api_key", None)
            (st.error if failed else st.markdown)(answer)
        if not failed:  # an error message is not part of the conversation
            chat += [{"role": "user", "content": question}, {"role": "assistant", "content": answer}]

    if chat and st.button("Clear chat", key="sg_clear"):
        st.session_state[chat_key] = []
        st.rerun()