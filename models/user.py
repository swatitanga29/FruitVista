"""Database access for the FruitVista ``users`` table.

Every query uses ``?`` placeholders - values are passed separately and are
never formatted into the SQL string, which protects the application from SQL
injection.

The ``role`` column is written only by trusted server-side code: public
registration always stores ``ROLE_USER``, the administrator setup writes
``ROLE_PERMANENT_ADMIN`` and the Secondary Admin approval workflow writes
``ROLE_SECONDARY_ADMIN``. A request form can never change a role.

Roles
-----
``permanent_admin``
    The original FruitVista administrator, with full authority. Accounts
    created before this role existed were stored as ``admin`` and are still
    recognised as permanent administrators (see ``PERMANENT_ADMIN_ROLES``), so
    an existing administrator is never locked out or downgraded.
``secondary_admin``
    An administrator approved by the permanent administrator.
``user``
    A normal registered account.
"""

from database import get_db

ROLE_USER = "user"
# Legacy name for the original administrator. Kept so databases created before
# the three-role model keep working; new accounts use ROLE_PERMANENT_ADMIN.
ROLE_ADMIN = "admin"
ROLE_PERMANENT_ADMIN = "permanent_admin"
ROLE_SECONDARY_ADMIN = "secondary_admin"

# Every role value a row may hold. Used by the schema check and the migration.
ROLES = (ROLE_USER, ROLE_ADMIN, ROLE_PERMANENT_ADMIN, ROLE_SECONDARY_ADMIN)

# The two values that mean "the original, full-authority administrator". The
# legacy ``admin`` value is included so an account stored before the migration
# is never treated as anything less.
PERMANENT_ADMIN_ROLES = (ROLE_ADMIN, ROLE_PERMANENT_ADMIN)

# Every value that may open the administrator area (permanent and secondary).
ADMIN_ROLES = PERMANENT_ADMIN_ROLES + (ROLE_SECONDARY_ADMIN,)

# How each role is described on screen.
ROLE_LABELS = {
    ROLE_USER: "User",
    ROLE_ADMIN: "Permanent Administrator",
    ROLE_PERMANENT_ADMIN: "Permanent Administrator",
    ROLE_SECONDARY_ADMIN: "Secondary Administrator",
}


def is_permanent_admin(user):
    """Return ``True`` when ``user`` is the original, full-authority admin."""
    return bool(user) and user["role"] in PERMANENT_ADMIN_ROLES


def is_secondary_admin(user):
    """Return ``True`` when ``user`` is an approved secondary administrator."""
    return bool(user) and user["role"] == ROLE_SECONDARY_ADMIN


def is_admin(user):
    """Return ``True`` for any administrator account (permanent or secondary)."""
    return bool(user) and user["role"] in ADMIN_ROLES


def role_label(role):
    """Return the human-readable name of a role value."""
    return ROLE_LABELS.get(role, role)


def create_user(full_name, email, mobile, district, password_hash, role=ROLE_USER):
    """Insert a new account and return its new id.

    ``role`` defaults to a regular user; the caller must never pass a value
    that came from a public request.
    """
    db = get_db()
    cursor = db.execute(
        "INSERT INTO users (full_name, email, mobile, district, password_hash, role) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (full_name, email, mobile, district, password_hash, role),
    )
    db.commit()
    return cursor.lastrowid


def get_user_by_email(email):
    """Return the account registered with this email address, or ``None``.

    The ``email`` column is declared ``COLLATE NOCASE``, so the comparison is
    case-insensitive even for records written before normalization.
    """
    return get_db().execute(
        "SELECT * FROM users WHERE email = ?",
        (email,),
    ).fetchone()


def get_user_by_id(user_id):
    """Return the account with this id, or ``None``."""
    return get_db().execute(
        "SELECT * FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()


def email_used_by_another_user(email, user_id):
    """Return ``True`` when ``email`` belongs to a different account."""
    row = get_db().execute(
        "SELECT id FROM users WHERE email = ? AND id <> ?",
        (email, user_id),
    ).fetchone()
    return row is not None


def touch_last_login(user_id):
    """Record that the account has just signed in."""
    db = get_db()
    db.execute(
        "UPDATE users "
        "SET last_login_at = datetime('now', 'localtime'), "
        "    updated_at = datetime('now', 'localtime') "
        "WHERE id = ?",
        (user_id,),
    )
    db.commit()


def update_profile(user_id, full_name, email, mobile, district):
    """Update the editable profile fields of one account.

    ``role`` is deliberately absent: it can never be changed from a profile
    form, which stops a user from promoting their own account.
    """
    db = get_db()
    db.execute(
        "UPDATE users "
        "SET full_name = ?, email = ?, mobile = ?, district = ?, "
        "    updated_at = datetime('now', 'localtime') "
        "WHERE id = ?",
        (full_name, email, mobile, district, user_id),
    )
    db.commit()


def update_password(user_id, password_hash):
    """Replace the stored password hash for one account."""
    db = get_db()
    db.execute(
        "UPDATE users "
        "SET password_hash = ?, updated_at = datetime('now', 'localtime') "
        "WHERE id = ?",
        (password_hash, user_id),
    )
    db.commit()


def get_user_by_role(role):
    """Return every account holding ``role``, ordered by name.

    The role is passed as a bound parameter; ``role`` always comes from the
    fixed constants above, never from a request.
    """
    return get_db().execute(
        "SELECT * FROM users WHERE role = ? ORDER BY full_name COLLATE NOCASE",
        (role,),
    ).fetchall()


def set_role(user_id, role, expected_role=None):
    """Change one account's role and report whether the change was applied.

    ``role`` must be one of the fixed constants: this function is called only
    by the approval workflow, never with a value from a form or the URL.

    ``expected_role`` makes the write conditional, which is what keeps a
    concurrent list of requests safe: the update only succeeds when the account
    is still in the state the caller expects. Returns ``True`` when a row was
    changed. The calling service controls the transaction and commits.
    """
    if role not in ROLES:
        raise ValueError(f"Unsupported role: {role}")
    if expected_role is not None and expected_role not in ROLES:
        raise ValueError(f"Unsupported role: {expected_role}")

    db = get_db()

    if expected_role is None:
        cursor = db.execute(
            "UPDATE users SET role = ?, updated_at = datetime('now', 'localtime') "
            "WHERE id = ?",
            (role, user_id),
        )
    else:
        cursor = db.execute(
            "UPDATE users SET role = ?, updated_at = datetime('now', 'localtime') "
            "WHERE id = ? AND role = ?",
            (role, user_id, expected_role),
        )

    return cursor.rowcount == 1
