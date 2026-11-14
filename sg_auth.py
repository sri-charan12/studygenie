"""
StudyGenie login, sign-up and role permissions (version 2).

Everything is stored in its own SQLite file, studygenie_auth.db, so password hashes never
end up in your planner database, its exports or its tests.

Tables
    roles             admin, user
    permissions       add_exam, plan, ai_ask, ...
    role_permissions  which role may do which thing   (this is where permissions are fetched from)
    users             name, username, email, salted password hash, role_id -> roles
    login_log         every login try, successful or not
    password_resets   one-time codes for 'forgot password' (only a hash of each code is kept)

"""
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from functools import wraps
from pathlib import Path

AUTH_DB = "studygenie_auth.db"
ITERATIONS = 200_000
ROLES = ("admin", "user")
MAX_FAILS = 5

# Starting permissions. They are copied into the role_permissions table the first time,
# and from then on the table is the source of truth.
PERMISSION_VERSION = 4  # raise this when DEFAULT_PERMISSIONS changes: existing databases are reset to the new defaults once
DEFAULT_PERMISSIONS = {
    # The admin looks after the accounts and can fix data errors; students do all the planning and studying.
    "admin": {"view", "export", "manage_users",
              "add_subject", "add_topic", "add_exam", "edit_topic", "edit_exam",
              "delete_subject", "delete_topic", "delete_exam",
              "set_hours", "set_rest_days", "import_data"},
    "user": {"view", "plan", "mark_session", "export", "ai_ask",
             "add_subject", "add_topic", "add_exam", "edit_topic", "edit_exam",
             "delete_subject", "delete_topic", "delete_exam",
             "set_hours", "set_rest_days", "import_data"},
}

ENFORCE = False  # when True, can() refuses everything until someone is logged in
CURRENT = {"username": None, "role": None, "full_name": "", "permissions": set()}


class AuthError(Exception):
    pass


# ---------------------------------------------------------------- database
def connect(path=AUTH_DB):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    init_auth(conn)
    return conn


def init_auth(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS roles (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
    CREATE TABLE IF NOT EXISTS permissions (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
    CREATE TABLE IF NOT EXISTS role_permissions (
        role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
        permission_id INTEGER NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
        PRIMARY KEY (role_id, permission_id));
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        full_name TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '',
        salt TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        role_id INTEGER NOT NULL REFERENCES roles(id),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_login TEXT,
        college TEXT NOT NULL DEFAULT '',
        class_name TEXT NOT NULL DEFAULT '');
    CREATE UNIQUE INDEX IF NOT EXISTS ux_users_email ON users(lower(email)) WHERE email <> '';
    CREATE TABLE IF NOT EXISTS login_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        success INTEGER NOT NULL,
        at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS password_resets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL COLLATE NOCASE,
        salt TEXT NOT NULL,
        code_hash TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        used INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    """)
    have = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    for col in ("college", "class_name"):  # databases made before students had a college and class
        if col not in have:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    for role in ROLES:
        conn.execute("INSERT OR IGNORE INTO roles(name) VALUES (?)", (role,))
    for perm in sorted(set().union(*DEFAULT_PERMISSIONS.values())):
        conn.execute("INSERT OR IGNORE INTO permissions(name) VALUES (?)", (perm,))
    if conn.execute("PRAGMA user_version").fetchone()[0] < PERMISSION_VERSION:
        conn.execute("DELETE FROM role_permissions")  # an older database: apply the current defaults once
    if conn.execute("SELECT COUNT(*) FROM role_permissions").fetchone()[0] == 0:
        for role, perms in DEFAULT_PERMISSIONS.items():
            for perm in perms:
                conn.execute("""INSERT INTO role_permissions(role_id, permission_id)
                                SELECT r.id, p.id FROM roles r, permissions p WHERE r.name=? AND p.name=?""",
                             (role, perm))
    conn.execute(f"PRAGMA user_version = {PERMISSION_VERSION}")
    conn.commit()


def _hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), ITERATIONS).hex()


def _clean(text):
    return " ".join(str(text).split())


def user_count(conn):
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def admin_count(conn):
    return conn.execute("SELECT COUNT(*) FROM users u JOIN roles r ON r.id=u.role_id WHERE r.name='admin'").fetchone()[0]


# ---------------------------------------------------------------- sign up and login
def _check_password(password):
    if (len(password) < 8 or not re.search(r"[A-Z]", password) or not re.search(r"\d", password)
            or not re.search(r"[^A-Za-z0-9]", password)):
        raise AuthError("password needs 8 or more characters, with one uppercase letter, one number "
                        "and one special character such as @")


def create_user(conn, username, password, role="user", full_name="", email="", college="", class_name=""):
    username, email = _clean(username), _clean(email).lower()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,30}", username):
        raise AuthError("username must be 3 to 30 letters, numbers, dots, dashes or underscores")
    if role not in ROLES:
        raise AuthError("role must be admin or user")
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise AuthError("that email address does not look right")
    _check_password(password)
    if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        raise AuthError(f"username '{username}' is already taken")
    if email and conn.execute("SELECT 1 FROM users WHERE lower(email)=?", (email,)).fetchone():
        raise AuthError("an account with that email already exists")
    salt = secrets.token_hex(16)
    conn.execute("""INSERT INTO users(username, full_name, email, salt, password_hash, role_id, college, class_name)
                    VALUES (?,?,?,?,?,(SELECT id FROM roles WHERE name=?),?,?)""",
                 (username, _clean(full_name), email, salt, _hash(password, salt), role,
                  _clean(college), _clean(class_name)))
    conn.commit()


def signup(conn, full_name, username, email, password, confirm, college="", class_name=""):
    """Self sign-up. New accounts are students. The very first account becomes the admin."""
    if not _clean(full_name):
        raise AuthError("please enter your name")
    if not _clean(email):
        raise AuthError("please enter your email")
    if password != confirm:
        raise AuthError("the two passwords do not match")
    role = "admin" if user_count(conn) == 0 else "user"
    create_user(conn, username, password, role, full_name, email, college, class_name)
    return role


def is_locked(conn, username):
    n = conn.execute("""SELECT COUNT(*) FROM login_log
        WHERE username=? COLLATE NOCASE AND success=0 AND at > datetime('now','-5 minutes')
        AND id > COALESCE((SELECT MAX(id) FROM login_log WHERE username=? COLLATE NOCASE AND success=1), 0)""",
                     (_clean(username), _clean(username))).fetchone()[0]
    return n >= MAX_FAILS


def authenticate(conn, username, password):
    """Return {'username','full_name','role'} for a right login, None for a wrong one."""
    username = _clean(username)
    if is_locked(conn, username):
        raise AuthError("too many wrong attempts. Try again in 5 minutes")
    row = conn.execute("""SELECT u.username, u.full_name, u.salt, u.password_hash, r.name
                          FROM users u JOIN roles r ON r.id=u.role_id WHERE u.username=?""", (username,)).fetchone()
    ok = False
    if row is None:
        _hash(password, "00" * 16)  # same work for unknown users
    else:
        ok = hmac.compare_digest(_hash(password, row[2]), row[3])
    conn.execute("INSERT INTO login_log(username, success) VALUES (?,?)", (username, int(ok)))
    if ok:
        conn.execute("UPDATE users SET last_login=CURRENT_TIMESTAMP WHERE username=?", (username,))
    conn.commit()
    return {"username": row[0], "full_name": row[1], "role": row[4]} if ok else None


# ---------------------------------------------------------------- managing users
def list_users(conn):
    return conn.execute("""SELECT u.username, u.full_name, u.email, r.name, u.created_at, COALESCE(u.last_login,'')
                           FROM users u JOIN roles r ON r.id=u.role_id ORDER BY u.username""").fetchall()


def student_profiles(conn):
    """Every student as (username, full name, college, class), for the admin's student picker."""
    return conn.execute("""SELECT u.username, u.full_name, u.college, u.class_name FROM users u
                           JOIN roles r ON r.id=u.role_id WHERE r.name='user' ORDER BY u.username""").fetchall()


def user_db_path(username, data_dir=None):
    """Each student has a private planner database: data/<username>.db (folder set by STUDYGENIE_DATA_DIR)."""
    folder = Path(data_dir or os.environ.get("STUDYGENIE_DATA_DIR", "data"))
    safe = re.sub(r"[^a-z0-9_.-]", "_", username.strip().lower())
    return folder / f"{safe}.db"


def _would_leave_no_admin(conn, username):
    row = conn.execute("SELECT r.name FROM users u JOIN roles r ON r.id=u.role_id WHERE u.username=?", (username,)).fetchone()
    return row is not None and row[0] == "admin" and admin_count(conn) <= 1


def set_role(conn, username, role):
    if role not in ROLES:
        raise AuthError("role must be admin or user")
    if role != "admin" and _would_leave_no_admin(conn, username):
        raise AuthError("there must be at least one admin")
    if conn.execute("UPDATE users SET role_id=(SELECT id FROM roles WHERE name=?) WHERE username=?", (role, username)).rowcount == 0:
        raise AuthError("no such user")
    conn.commit()


def delete_user(conn, username):
    if _would_leave_no_admin(conn, username):
        raise AuthError("there must be at least one admin")
    if conn.execute("DELETE FROM users WHERE username=?", (username,)).rowcount == 0:
        raise AuthError("no such user")
    conn.commit()


def recent_logins(conn, n=20):
    return conn.execute("SELECT at, username, CASE success WHEN 1 THEN 'success' ELSE 'failed' END FROM login_log ORDER BY id DESC LIMIT ?", (n,)).fetchall()


# ---------------------------------------------------------------- permissions
def permissions_for(conn, role):
    return {r[0] for r in conn.execute("""SELECT p.name FROM role_permissions rp
        JOIN roles r ON r.id=rp.role_id JOIN permissions p ON p.id=rp.permission_id WHERE r.name=?""", (role,))}


def role_matrix(conn):
    """{permission: {role: True/False}} read from the tables."""
    perms = [r[0] for r in conn.execute("SELECT name FROM permissions ORDER BY name")]
    have = {role: permissions_for(conn, role) for role in ROLES}
    return {p: {role: p in have[role] for role in ROLES} for p in perms}


def has_permission(role, permission, conn=None):
    allowed = permissions_for(conn, role) if conn is not None else DEFAULT_PERMISSIONS.get(role, set())
    return permission in allowed


def _set_current(conn, user):
    CURRENT.update(username=user["username"], role=user["role"], full_name=user.get("full_name", ""),
                   permissions=permissions_for(conn, user["role"]))


def can(permission):
    """True if the logged-in person may do this. Use it in the app."""
    if CURRENT["role"] is None:
        return not ENFORCE
    return permission in CURRENT["permissions"]


def requires(permission):
    """Decorator for command handlers. Does nothing until login is turned on."""
    def wrap(fn):
        @wraps(fn)
        def inner(*args, **kwargs):
            if ENFORCE and permission not in CURRENT["permissions"]:
                raise AuthError(f"'{CURRENT['username']}' ({CURRENT['role']}) is not allowed to do this. "
                                f"It needs the '{permission}' permission.")
            return fn(*args, **kwargs)
        return inner
    return wrap


# ---------------------------------------------------------------- forgot password / forgot username
RESET_MINUTES = 15  # a reset code works for this long
RESET_MAX_TRIES = 5  # wrong guesses allowed per code
RESET_MAX_PER_HOUR = 5  # reset codes that can be requested per account per hour


def find_account(conn, identifier):
    """(username, email) for a username or an e-mail address, or None. Accounts without an e-mail cannot be recovered."""
    ident = _clean(identifier)
    if not ident:
        return None
    row = conn.execute("""SELECT username, email FROM users
                          WHERE (username=? COLLATE NOCASE OR lower(email)=lower(?)) AND email <> '' LIMIT 1""",
                       (ident, ident)).fetchone()
    return (row[0], row[1]) if row else None


def username_for_email(conn, email):
    """(username, email) of the account that uses this e-mail address, or None."""
    row = conn.execute("SELECT username, email FROM users WHERE lower(email)=lower(?) AND email <> ''",
                       (_clean(email),)).fetchone()
    return (row[0], row[1]) if row else None


def start_password_reset(conn, identifier):
    """Make a 6-digit code for the account. Returns (username, email, code), or None when there is no such account.
    The caller e-mails the code. Only a hash of the code is stored."""
    found = find_account(conn, identifier)
    if found is None:
        return None
    username, email = found
    recent = conn.execute("""SELECT COUNT(*) FROM password_resets
                             WHERE username=? AND created_at > datetime('now','-1 hour')""", (username,)).fetchone()[0]
    if recent >= RESET_MAX_PER_HOUR:
        raise AuthError("too many reset requests. Try again in an hour")
    code = f"{secrets.randbelow(1_000_000):06d}"
    salt = secrets.token_hex(16)
    conn.execute("UPDATE password_resets SET used=1 WHERE username=? AND used=0", (username,))  # only the newest code works
    conn.execute("""INSERT INTO password_resets(username, salt, code_hash, expires_at)
                    VALUES (?,?,?, datetime('now', ?))""",
                 (username, salt, _hash(code, salt), f"+{RESET_MINUTES} minutes"))
    conn.commit()
    return username, email, code


def finish_password_reset(conn, identifier, code, new_password, confirm):
    """Check the code and set the new password. Raises AuthError with a plain reason when something is wrong."""
    wrong = AuthError("that code is wrong or has expired. Ask for a new one")
    found = find_account(conn, identifier)
    if found is None:
        raise wrong
    username = found[0]
    row = conn.execute("""SELECT id, salt, code_hash, attempts FROM password_resets
                          WHERE username=? AND used=0 AND expires_at > datetime('now')
                          ORDER BY id DESC LIMIT 1""", (username,)).fetchone()
    if row is None or row[3] >= RESET_MAX_TRIES:
        raise wrong
    if not hmac.compare_digest(_hash(_clean(code), row[1]), row[2]):
        conn.execute("UPDATE password_resets SET attempts=attempts+1 WHERE id=?", (row[0],))
        conn.commit()
        raise wrong
    if new_password != confirm:
        raise AuthError("the two passwords do not match")
    _check_password(new_password)
    salt = secrets.token_hex(16)
    conn.execute("UPDATE users SET salt=?, password_hash=? WHERE username=?", (salt, _hash(new_password, salt), username))
    conn.execute("UPDATE password_resets SET used=1 WHERE username=?", (username,))
    conn.commit()
    return username


# ---------------------------------------------------------------- Streamlit pages
CSS = """<style>
.sg-hero{font-size:2.8rem;font-weight:800;line-height:1.1;margin:.4rem 0 .2rem;
  background:linear-gradient(90deg,#6C3FC5,#F5B841);-webkit-background-clip:text;background-clip:text;color:transparent}
.sg-sub{opacity:.75;font-size:1.05rem;margin-bottom:1.2rem}
div[data-testid="stForm"]{border:1px solid rgba(108,63,197,.35);border-radius:16px;padding:1.3rem;background:rgba(108,63,197,.07)}
.stButton>button,.stFormSubmitButton>button{border-radius:10px;font-weight:600}
.sg-chip{display:inline-block;padding:2px 10px;border-radius:999px;background:#6C3FC5;color:#fff;font-size:.8rem;font-weight:600}
.sg-chip.admin{background:#F5B841;color:#2D1B4E}
</style>"""


def style_page(st):
    st.markdown(CSS, unsafe_allow_html=True)


def streamlit_gate(auth_db=AUTH_DB):
    """Call once near the top of the app. Shows sign-up and login, and stops until someone is logged in."""
    import streamlit as st
    style_page(st)
    conn = connect(auth_db)
    try:
        user = st.session_state.get("user")
        if user and conn.execute("SELECT 1 FROM users WHERE username=?", (user["username"],)).fetchone():
            fresh = conn.execute("SELECT r.name FROM users u JOIN roles r ON r.id=u.role_id WHERE u.username=?",
                                 (user["username"],)).fetchone()[0]
            user["role"] = fresh  # picks up a role change made by an admin
            _set_current(conn, user)
            _badge(st, user)
            return user
        st.session_state.pop("user", None)
        _login_page(st, conn)
    finally:
        conn.close()
    st.stop()


def _badge(st, user):
    chip = f'<span class="sg-chip {user["role"]}">{user["role"]}</span>'
    st.sidebar.markdown(f"**{user.get('full_name') or user['username']}**  \n@{user['username']} {chip}", unsafe_allow_html=True)
    if st.sidebar.button("Log out", key="sg_logout"):
        st.session_state.pop("user", None)
        st.session_state.pop("sg_enter", None)  # logout returns to the website
        st.session_state.pop("page", None)  # next login starts on the index page again
        st.session_state.pop("open_tab", None)
        CURRENT.update(username=None, role=None, full_name="", permissions=set())
        st.rerun()


def _login_page(st, conn):
    if not st.session_state.get("sg_enter"):  # visitors see the project website first
        import sg_landing
        sg_landing.render(st, first_time=user_count(conn) == 0)
        return
    import sg_landing
    sg_landing.light_theme(st)  # same white and blue look as the landing page
    if st.button("← Back to website", key="sg_back_site"):
        st.session_state.pop("sg_enter", None)
        st.rerun()
    st.markdown('<div class="sg-hero">StudyGenie</div><div class="sg-sub">Plan smarter. Study with rules you can trust.</div>',
                unsafe_allow_html=True)
    _, mid, _ = st.columns([1, 1.6, 1])
    with mid:
        view = st.session_state.get("sg_view")
        if view in ("forgot_pw", "forgot_user") and user_count(conn) > 0:
            if st.button("← Back to log in", key="sg_back_login"):
                st.session_state.pop("sg_view", None)
                for k in ("reset_ident", "reset_sent", "reset_note"):
                    st.session_state.pop(k, None)
                st.rerun()
            (_forgot_password if view == "forgot_pw" else _forgot_username)(st, conn)
        elif user_count(conn) == 0:
            st.info("First-time setup: the account you create now will be the administrator.")
            _signup_form(st, conn, "setup")
        else:
            tab_in, tab_up = st.tabs(["Log in", "Sign up"])
            with tab_in:
                _login_form(st, conn)
                f1, f2 = st.columns(2)
                if f1.button("Forgot password?", key="sg_go_pw", width="stretch"):
                    st.session_state["sg_view"] = "forgot_pw"
                    st.rerun()
                if f2.button("Forgot username?", key="sg_go_user", width="stretch"):
                    st.session_state["sg_view"] = "forgot_user"
                    st.rerun()
            with tab_up:
                st.caption("New accounts are students. An admin can change your role later.")
                _signup_form(st, conn, "signup")


def _show_code_allowed():
    """Demo switch: STUDYGENIE_SHOW_CODE=1 shows codes on screen when e-mail is not set up. Never use it for real users."""
    return os.environ.get("STUDYGENIE_SHOW_CODE", "0").strip().lower() in ("1", "true", "yes", "on")


def _forgot_username(st, conn):
    import sg_mail
    st.subheader("Forgot your username?")
    st.caption("Enter the e-mail address you signed up with (for example yourname@gmail.com). We will send your username to it.")
    with st.form("forgot_user_form"):
        mail = st.text_input("Registered e-mail", key="fu_mail")
        go = st.form_submit_button("Send my username", width="stretch")
    if not go:
        return
    found = username_for_email(conn, mail)
    shown = "If an account uses that e-mail address, we have sent the username to it. Check your inbox and spam folder."
    if found is None:
        st.success(shown)  # same message either way, so nobody can test which e-mails have accounts
        return
    username, email = found
    body = f"Hello,\n\nYour StudyGenie username is: {username}\n\nIf you did not ask for this, you can ignore this e-mail."
    try:
        sg_mail.send(email, "Your StudyGenie username", body, st)
        st.success(shown)
    except sg_mail.MailError as err:
        if _show_code_allowed():
            st.warning(f"Demo mode (e-mail is not working: {err}). Your username is: {username}")
        else:
            st.error(f"We could not send the e-mail: {err}. Please ask your administrator.")


def _forgot_password(st, conn):
    import sg_mail
    st.subheader("Forgot your password?")
    sent = st.session_state.get("reset_sent")
    if not sent:
        st.caption("Enter your username or the e-mail address you signed up with. We will e-mail you a 6-digit code.")
        with st.form("forgot_pw_form"):
            ident = st.text_input("Username or e-mail", key="fp_ident")
            go = st.form_submit_button("Send me a code", width="stretch")
        if not go:
            return
        try:
            result = start_password_reset(conn, ident)
        except AuthError as e:
            st.error(str(e).capitalize())
            return
        note = None
        if result is not None:
            username, email, code = result
            body = (f"Hello,\n\nYour StudyGenie password reset code is: {code}\n\n"
                    f"It works for {RESET_MINUTES} minutes. If you did not ask for this, ignore this e-mail.")
            try:
                sg_mail.send(email, "Your StudyGenie password reset code", body, st)
            except sg_mail.MailError as err:
                if _show_code_allowed():
                    note = f"Demo mode (e-mail is not working: {err}). Your code is: {code}"
                else:
                    st.error(f"We could not send the e-mail: {err}. Please ask your administrator.")
                    return
        st.session_state["reset_ident"] = _clean(ident)
        st.session_state["reset_sent"] = True
        st.session_state["reset_note"] = note
        st.rerun()
    st.success("If an account matches, a 6-digit code has been sent to its e-mail address. Check your inbox and spam folder.")
    if st.session_state.get("reset_note"):
        st.warning(st.session_state["reset_note"])
    with st.form("reset_pw_form"):
        code = st.text_input("6-digit code", key="rp_code", max_chars=6)
        pw = st.text_input("New password", type="password", key="rp_pw",
                           help="8 or more characters, with one uppercase letter, one number and one special character such as @")
        pw2 = st.text_input("Confirm new password", type="password", key="rp_pw2")
        go = st.form_submit_button("Set new password", width="stretch")
    if go:
        try:
            finish_password_reset(conn, st.session_state.get("reset_ident", ""), code, pw, pw2)
        except AuthError as e:
            st.error(str(e).capitalize())
            return
        for k in ("reset_ident", "reset_sent", "reset_note", "sg_view"):
            st.session_state.pop(k, None)
        st.session_state["sg_flash"] = "Password changed. You can log in with the new password."
        st.rerun()
    if st.button("Send a new code", key="rp_again"):
        st.session_state.pop("reset_sent", None)
        st.session_state.pop("reset_note", None)
        st.rerun()


def _login_form(st, conn):
    if st.session_state.get("sg_flash"):
        st.success(st.session_state.pop("sg_flash"))
    with st.form("login_form"):
        name = st.text_input("Username", key="login_username")
        pw = st.text_input("Password", type="password", key="login_password")
        go = st.form_submit_button("Log in", width="stretch")
    if not go:
        return
    try:
        user = authenticate(conn, name, pw)
    except AuthError as e:
        st.error(str(e).capitalize())
        return
    if user is None:
        st.error("Wrong username or password")
        return
    st.session_state["user"] = user
    st.rerun()


def _signup_form(st, conn, prefix):
    with st.form(f"{prefix}_form"):
        full = st.text_input("Full name", key=f"{prefix}_full")
        name = st.text_input("Username", key=f"{prefix}_username", placeholder="for example sricharan12",
                             help="3 to 30 characters: letters, numbers, dots, dashes or underscores. No spaces and no @. This is not your email.")
        mail = st.text_input("Email", key=f"{prefix}_email")
        college = st.text_input("College / school", key=f"{prefix}_college", placeholder="optional")
        klass = st.text_input("Class / course", key=f"{prefix}_class", placeholder="optional, for example B.Tech CSE year 3")
        pw = st.text_input("Password", type="password", key=f"{prefix}_pw", help="8 or more characters, with one uppercase letter, one number and one special character such as @")
        pw2 = st.text_input("Confirm password", type="password", key=f"{prefix}_pw2")
        go = st.form_submit_button("Create account", width="stretch")
    if not go:
        return
    try:
        role = signup(conn, full, name, mail, pw, pw2, college, klass)
    except AuthError as e:
        st.error(str(e).capitalize())
        return
    if prefix == "setup":
        st.session_state["user"] = authenticate(conn, name, pw)
        st.rerun()
    st.success(f"Account created as {role}. Open the Log in tab to enter.")


def render_admin_panel(auth_db=AUTH_DB):
    """Admin page: users, role changes, the role and permission table, and the login history."""
    import pandas as pd
    import streamlit as st
    conn = connect(auth_db)
    try:
        if st.session_state.get("admin_msg"):
            st.success(st.session_state.pop("admin_msg"))
        st.subheader("Users")
        users = list_users(conn)
        profile = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT username, college, class_name FROM users")}
        st.dataframe(pd.DataFrame([(*u[:4], *profile[u[0]], u[4], u[5]) for u in users],
                                  columns=["Username", "Name", "Email", "Role", "College", "Class",
                                           "Created (UTC)", "Last login (UTC)"]),
                     width="stretch", hide_index=True)
        left, right = st.columns(2)
        with left, st.form("role_form"):
            st.markdown("**Change a role**")
            who = st.selectbox("User", [u[0] for u in users], key="role_who")
            role = st.selectbox("New role", ROLES, key="role_new")
            if st.form_submit_button("Save role"):
                try:
                    set_role(conn, who, role)
                    st.session_state["admin_msg"] = f"{who} is now {role}"
                    st.rerun()
                except AuthError as e:
                    st.error(str(e).capitalize())
        with right, st.form("delete_form"):
            st.markdown("**Delete a user**")
            who = st.selectbox("User", [u[0] for u in users], key="del_who")
            sure = st.checkbox("Yes, delete this user", key="del_sure")
            if st.form_submit_button("Delete") and sure:
                try:
                    delete_user(conn, who)
                    st.session_state["admin_msg"] = (f"{who} was deleted. Their planner file "
                                                     f"({user_db_path(who).name}) was kept and can be removed by hand.")
                    st.rerun()
                except AuthError as e:
                    st.error(str(e).capitalize())
        st.subheader("What each role can do")
        matrix = role_matrix(conn)
        st.dataframe(pd.DataFrame({r: ["yes" if matrix[p][r] else "" for p in matrix] for r in ROLES}, index=list(matrix)),
                     width="stretch")
        st.subheader("Recent logins")
        st.dataframe(pd.DataFrame(recent_logins(conn), columns=["Time (UTC)", "Username", "Result"]),
                     width="stretch", hide_index=True)
    finally:
        conn.close()


# ---------------------------------------------------------------- terminal tool