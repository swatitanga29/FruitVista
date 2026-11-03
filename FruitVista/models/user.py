"""Database access for the FruitVista ``users`` table.

Every query uses ``?`` placeholders - values are passed separately and are
never formatted into the SQL string, which protects the application from SQL
injection.

The ``role`` column is written only by trusted server-side code: public
registration always stores ``ROLE_USER`` and the ``create-admin`` command is
the only place that stores ``ROLE_ADMIN``.
"""

from database import get_db

ROLE_USER = "user"
ROLE_ADMIN = "admin"


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
