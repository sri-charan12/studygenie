"""Test cases for sign up, login, roles and permissions. Save this file in the tests folder."""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sg_auth  # noqa: E402


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA foreign_keys = ON")
    sg_auth.init_auth(c)
    sg_auth.create_user(c, "asha", "Admin@pass1", "admin", "Asha Rao", "asha@example.com")
    sg_auth.create_user(c, "ravi", "Student@pass1", "user", "Ravi K", "ravi@example.com")
    yield c
    c.close()


@pytest.fixture(autouse=True)
def reset_login():
    yield
    sg_auth.ENFORCE = False
    sg_auth.CURRENT.update(username=None, role=None, full_name="", permissions=set())


# ---------- tables ----------
def test_role_tables_are_created_and_filled(conn):
    assert {r[0] for r in conn.execute("SELECT name FROM roles")} == {"admin", "user"}
    assert conn.execute("SELECT COUNT(*) FROM permissions").fetchone()[0] > 10
    assert conn.execute("SELECT COUNT(*) FROM role_permissions").fetchone()[0] > 0


def test_permissions_are_read_from_the_table_not_from_code(conn):
    assert "add_exam" in sg_auth.permissions_for(conn, "user")
    conn.execute("""DELETE FROM role_permissions WHERE role_id=(SELECT id FROM roles WHERE name='user')
                    AND permission_id=(SELECT id FROM permissions WHERE name='add_exam')""")
    assert "add_exam" not in sg_auth.permissions_for(conn, "user")


def test_an_older_permission_table_is_replaced_once_when_the_rules_change():
    c = sqlite3.connect(":memory:")
    sg_auth.init_auth(c)
    c.execute("PRAGMA user_version = 0")  # as if the database was made by an older version
    c.execute("""INSERT INTO role_permissions(role_id, permission_id)
                 SELECT r.id, p.id FROM roles r, permissions p WHERE r.name='admin' AND p.name='plan'""")
    c.commit()
    sg_auth.init_auth(c)
    assert "plan" not in sg_auth.permissions_for(c, "admin")


def test_a_second_start_does_not_reset_changed_permissions(conn):
    conn.execute("DELETE FROM role_permissions WHERE permission_id=(SELECT id FROM permissions WHERE name='ai_ask')")
    conn.commit()
    sg_auth.init_auth(conn)
    assert "ai_ask" not in sg_auth.permissions_for(conn, "user")


def test_users_belong_to_a_real_role(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO users(username, salt, password_hash, role_id) VALUES ('x','00','00',999)")


# ---------- sign up ----------
def test_signup_creates_a_student(conn):
    assert sg_auth.signup(conn, "Meena S", "meena", "meena@example.com", "Secret@123", "Secret@123") == "user"
    assert sg_auth.authenticate(conn, "meena", "Secret@123")["role"] == "user"


def test_the_very_first_signup_becomes_the_admin():
    c = sqlite3.connect(":memory:")
    sg_auth.init_auth(c)
    assert sg_auth.signup(c, "First Person", "first", "f@example.com", "Secret@123", "Secret@123") == "admin"
    assert sg_auth.signup(c, "Second Person", "second", "s@example.com", "Secret@123", "Secret@123") == "user"


def test_signup_refuses_mismatched_passwords(conn):
    with pytest.raises(sg_auth.AuthError):
        sg_auth.signup(conn, "Meena S", "meena", "meena@example.com", "Secret@123", "Secret@124")


@pytest.mark.parametrize("password", ["Sh@rt1", "onlyletters", "12345678", "alllower@123", "NoNumber@abc", "NoSpecial123"])
def test_signup_refuses_weak_passwords(conn, password):
    with pytest.raises(sg_auth.AuthError):
        sg_auth.signup(conn, "Meena S", "meena", "meena@example.com", password, password)


@pytest.mark.parametrize("username", ["ab", "has space!", "x" * 31])
def test_signup_refuses_bad_usernames(conn, username):
    with pytest.raises(sg_auth.AuthError):
        sg_auth.signup(conn, "Meena S", username, "meena@example.com", "Secret@123", "Secret@123")


def test_signup_refuses_a_bad_email(conn):
    with pytest.raises(sg_auth.AuthError):
        sg_auth.signup(conn, "Meena S", "meena", "not-an-email", "Secret@123", "Secret@123")


def test_duplicate_username_and_email_are_refused(conn):
    with pytest.raises(sg_auth.AuthError):
        sg_auth.signup(conn, "Other", "  RAVI ", "other@example.com", "Secret@123", "Secret@123")
    with pytest.raises(sg_auth.AuthError):
        sg_auth.signup(conn, "Other", "other", "RAVI@example.com", "Secret@123", "Secret@123")


def test_nobody_can_sign_up_as_admin(conn):
    sg_auth.signup(conn, "Meena S", "meena", "meena@example.com", "Secret@123", "Secret@123")
    assert sg_auth.authenticate(conn, "meena", "Secret@123")["role"] == "user"


# ---------- login ----------
def test_correct_password_logs_in_with_the_right_role(conn):
    assert sg_auth.authenticate(conn, "asha", "Admin@pass1")["role"] == "admin"
    assert sg_auth.authenticate(conn, "ravi", "Student@pass1")["role"] == "user"


def test_wrong_password_and_unknown_user_are_refused(conn):
    assert sg_auth.authenticate(conn, "asha", "wrong-pass1") is None
    assert sg_auth.authenticate(conn, "nobody", "Admin@pass1") is None


def test_username_is_not_case_sensitive(conn):
    assert sg_auth.authenticate(conn, "ASHA", "Admin@pass1")["username"] == "asha"


def test_password_is_never_stored_as_plain_text(conn):
    row = conn.execute("SELECT salt, password_hash FROM users WHERE username='asha'").fetchone()
    assert "Admin@pass1" not in row[0] + row[1]


def test_same_password_gets_a_different_hash_for_each_user(conn):
    sg_auth.create_user(conn, "meena", "Student@pass1", "user")
    hashes = [r[0] for r in conn.execute("SELECT password_hash FROM users WHERE username IN ('ravi','meena')")]
    assert hashes[0] != hashes[1]


def test_every_login_try_is_logged(conn):
    sg_auth.authenticate(conn, "asha", "wrong-pass1")
    sg_auth.authenticate(conn, "asha", "Admin@pass1")
    assert [r[2] for r in sg_auth.recent_logins(conn)][:2] == ["success", "failed"]


def test_five_wrong_tries_lock_the_account_even_for_the_right_password(conn):
    for _ in range(sg_auth.MAX_FAILS):
        sg_auth.authenticate(conn, "ravi", "wrong-pass1")
    with pytest.raises(sg_auth.AuthError):
        sg_auth.authenticate(conn, "ravi", "Student@pass1")


def test_a_successful_login_resets_the_failed_count(conn):
    for _ in range(sg_auth.MAX_FAILS - 1):
        sg_auth.authenticate(conn, "ravi", "wrong-pass1")
    assert sg_auth.authenticate(conn, "ravi", "Student@pass1") is not None
    assert sg_auth.authenticate(conn, "ravi", "wrong-pass1") is None


# ---------- roles and permissions ----------
def test_students_and_admins_can_both_add_and_fix_exams(conn):
    for role in ("user", "admin"):
        for perm in ("add_exam", "edit_exam", "delete_exam", "delete_subject", "import_data"):
            assert sg_auth.has_permission(role, perm, conn)


def test_a_student_can_view_plan_mark_sessions_and_ask_ai(conn):
    for perm in ("view", "plan", "mark_session", "ai_ask"):
        assert sg_auth.has_permission("user", perm, conn)


def test_only_admin_can_manage_users(conn):
    assert sg_auth.has_permission("admin", "manage_users", conn)
    assert not sg_auth.has_permission("user", "manage_users", conn)


def test_a_student_has_every_permission_except_managing_users(conn):
    everything = {r[0] for r in conn.execute("SELECT name FROM permissions")}
    assert sg_auth.permissions_for(conn, "user") == everything - {"manage_users"}


def test_an_admin_does_not_plan_or_study(conn):
    for perm in ("plan", "mark_session", "ai_ask"):
        assert not sg_auth.has_permission("admin", perm, conn)


def test_unknown_role_has_no_permissions(conn):
    assert not sg_auth.has_permission("guest", "view", conn)


def test_role_matrix_shows_what_each_role_can_do(conn):
    matrix = sg_auth.role_matrix(conn)
    assert matrix["add_exam"] == {"admin": True, "user": True}
    assert matrix["plan"] == {"admin": False, "user": True}
    assert matrix["view"] == {"admin": True, "user": True}


def test_an_admin_can_change_a_role(conn):
    sg_auth.set_role(conn, "ravi", "admin")
    assert sg_auth.authenticate(conn, "ravi", "Student@pass1")["role"] == "admin"


def test_the_last_admin_cannot_be_removed_or_demoted(conn):
    with pytest.raises(sg_auth.AuthError):
        sg_auth.set_role(conn, "asha", "user")
    with pytest.raises(sg_auth.AuthError):
        sg_auth.delete_user(conn, "asha")


def test_deleting_a_user_removes_them(conn):
    sg_auth.delete_user(conn, "ravi")
    assert sg_auth.authenticate(conn, "ravi", "Student@pass1") is None


# ---------- command line ----------
def test_requires_blocks_a_student_but_lets_an_admin_through(conn):
    @sg_auth.requires("manage_users")
    def add_exam():
        return "added"

    sg_auth.cli_login(conn, "ravi", "Student@pass1")
    with pytest.raises(sg_auth.AuthError):
        add_exam()
    sg_auth.cli_login(conn, "asha", "Admin@pass1")
    assert add_exam() == "added"


def test_requires_does_nothing_while_login_is_off():
    @sg_auth.requires("add_exam")
    def add_exam():
        return "added"

    assert add_exam() == "added"


def test_cli_login_with_a_wrong_password_fails(conn):
    with pytest.raises(sg_auth.AuthError):
        sg_auth.cli_login(conn, "asha", "wrong-pass1")
    assert sg_auth.ENFORCE is False


# ---------- students, colleges and private planner files ----------
def test_signup_stores_college_and_class(conn):
    sg_auth.signup(conn, "Meena S", "meena", "meena@example.com", "Secret@123", "Secret@123", "CBIT", "B.Tech CSE")
    assert conn.execute("SELECT college, class_name FROM users WHERE username='meena'").fetchone() == ("CBIT", "B.Tech CSE")
    assert ("meena", "Meena S", "CBIT", "B.Tech CSE") in sg_auth.student_profiles(conn)


def test_student_list_has_students_only(conn):
    assert [r[0] for r in sg_auth.student_profiles(conn)] == ["ravi"]


def test_each_student_gets_a_private_database_file(tmp_path):
    first, second = sg_auth.user_db_path("Ravi", tmp_path), sg_auth.user_db_path("meena", tmp_path)
    assert first != second
    assert first.name == "ravi.db" and first.parent == tmp_path == second.parent


def test_a_database_path_can_never_leave_the_data_folder(tmp_path):
    assert sg_auth.user_db_path("../../evil", tmp_path).parent == tmp_path


def test_an_older_users_table_gets_the_college_and_class_columns():
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE roles(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
        CREATE TABLE users(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            full_name TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '', salt TEXT NOT NULL,
            password_hash TEXT NOT NULL, role_id INTEGER NOT NULL REFERENCES roles(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, last_login TEXT);""")
    sg_auth.init_auth(c)
    assert {"college", "class_name"} <= {r[1] for r in c.execute("PRAGMA table_info(users)")}