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

Terminal tool
    python sg_auth.py create-user ravi admin
    python sg_auth.py list-users
    python sg_auth.py seed-demo            (admin/Admin@123 and student/Student@123, demo only)
"""
import argparse
import getpass
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import sys
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

ENFORCE = False  # the command line turns this on after login; the old tests leave it off
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
    """True if the logged-in person may do this. Use it in the app and the command line."""
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


def cli_login(conn, username=None, password=None):
    global ENFORCE
    if user_count(conn) == 0:
        raise AuthError("no users yet. Create one with:  python sg_auth.py create-user NAME admin")
    username = username or input("Username: ").strip()
    password = password if password is not None else getpass.getpass("Password: ")
    user = authenticate(conn, username, password)
    if user is None:
        raise AuthError("wrong username or password")
    _set_current(conn, user)
    ENFORCE = True
    print(f"Logged in as {user['username']} ({user['role']})")
    return user["role"]


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
        CURRENT.update(username=None, role=None, full_name="", permissions=set())
        st.rerun()


def _login_page(st, conn):
    st.markdown('<div class="sg-hero">StudyGenie</div><div class="sg-sub">Plan smarter. Study with rules you can trust.</div>',
                unsafe_allow_html=True)
    _, mid, _ = st.columns([1, 1.6, 1])
    with mid:
        if user_count(conn) == 0:
            st.info("First-time setup: the account you create now will be the administrator.")
            _signup_form(st, conn, "setup")
        else:
            tab_in, tab_up = st.tabs(["Log in", "Sign up"])
            with tab_in:
                _login_form(st, conn)
            with tab_up:
                st.caption("New accounts are students. An admin can change your role later.")
                _signup_form(st, conn, "signup")


def _login_form(st, conn):
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
def main(argv=None):
    p = argparse.ArgumentParser(description="StudyGenie users")
    p.add_argument("--db", default=AUTH_DB)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-user")
    c.add_argument("username")
    c.add_argument("role", choices=ROLES)
    sub.add_parser("list-users")
    sub.add_parser("seed-demo")
    a = p.parse_args(argv)
    conn = connect(a.db)
    try:
        if a.cmd == "create-user":
            create_user(conn, a.username, getpass.getpass("Password: "), a.role)
            print(f"Created {a.role} '{a.username}'")
        elif a.cmd == "list-users":
            for u, full, mail, role, created, last in list_users(conn):
                print(f"{u:<16} {role:<6} {full:<20} {mail}")
        else:
            for u, pw, role in (("admin", "Admin@123", "admin"), ("student", "Student@123", "user")):
                try:
                    create_user(conn, u, pw, role, full_name=u.capitalize(), email=f"{u}@example.com")
                    print(f"Created {role}: {u} / {pw}   (demo only)")
                except AuthError as e:
                    print(e)
    except AuthError as e:
        print(f"Error: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()