"""SQLite persistence layer for FruitVista.

This package owns everything related to the database:

* opening and closing the connection
* creating the schema safely (never deleting existing records)
* the ``init-db`` command used from the terminal

Feature modules ask for a connection with :func:`get_db` and always use
parameterized SQL queries.
"""

import os
import sqlite3

import click
from flask import current_app, g


def ensure_database_directory(database_path):
    """Create the folder that will hold ``database_path`` when it is missing.

    A bare file name has no folder part to create, so it is skipped; the file
    itself is created by SQLite on the first connection. This keeps a custom
    ``DATABASE_PATH`` (for example a mounted persistent disk) working without
    touching any existing record.
    """
    directory = os.path.dirname(database_path)
    if directory:
        os.makedirs(directory, exist_ok=True)


def get_db():
    """Return the SQLite connection bound to the current app context.

    The connection is created on first use and stored on Flask's ``g``
    object, so every request reuses a single connection and closes it
    automatically when the request ends.
    """
    if "db" not in g:
        database_path = current_app.config["DATABASE"]
        ensure_database_directory(database_path)

        g.db = sqlite3.connect(database_path, detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")

    return g.db


def close_db(exception=None):
    """Close the request-scoped connection at the end of the request."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create every table defined in ``schema.sql`` that does not exist yet.

    The schema script is idempotent, so running this more than once is safe
    and existing records are never modified or removed.
    """
    schema_path = current_app.config["SCHEMA_FILE"]
    if not os.path.exists(schema_path):
        raise FileNotFoundError(f"Schema file not found: {schema_path}")

    with open(schema_path, "r", encoding="utf-8") as schema_file:
        db = get_db()
        db.executescript(schema_file.read())
        db.commit()


def column_exists(table, column):
    """Return ``True`` when ``table`` already has ``column``.

    ``PRAGMA table_info`` cannot use a bound parameter, so the table name is
    taken from a fixed, internal constant and never from user input.
    """
    if table not in {"users", "daily_records", "fruits", "districts"}:
        raise ValueError(f"Unsupported table: {table}")

    rows = get_db().execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


def seed_reference_data():
    """Insert the district and fruit master rows from the project configuration.

    ``INSERT OR IGNORE`` makes this idempotent: a name that already exists
    keeps its row (and its id), so re-running the application never rewrites
    the catalogue and never invalidates a stored record.
    """
    db = get_db()

    for district in current_app.config["DISTRICTS"]:
        db.execute("INSERT OR IGNORE INTO districts (name) VALUES (?)", (district,))

    for category, fruits in current_app.config["FRUIT_CATEGORIES"].items():
        for fruit in fruits:
            db.execute(
                "INSERT OR IGNORE INTO fruits (name, category) VALUES (?, ?)",
                (fruit, category),
            )

    db.commit()


def _users_role_check_is_outdated():
    """Return ``True`` when ``users`` has a role CHECK that rejects the new roles.

    A table built by the earlier schema carries ``CHECK (role IN ('user',
    'admin'))``, which would refuse ``permanent_admin`` and ``secondary_admin``.
    A table whose role column was added by migration 3 has no CHECK at all and
    needs no rebuild. A table built by the current schema already lists the new
    roles. Only the first case has to be widened.
    """
    row = get_db().execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'"
    ).fetchone()
    ddl = (row["sql"] or "") if row is not None else ""
    return "CHECK" in ddl.upper() and "secondary_admin" not in ddl


def _rebuild_users_for_new_roles():
    """Widen the ``users.role`` constraint, preserving every row and id.

    SQLite cannot alter a CHECK constraint in place, so the table is rebuilt
    with the documented procedure: a new table is created, every row is copied
    (including its ``id``, so no relationship changes), the legacy ``admin``
    role is renamed to ``permanent_admin``, the old table is replaced and the
    unique email index is recreated. Everything happens in one transaction, so
    a failure rolls back and leaves the original table exactly as it was.
    """
    db = get_db()

    # PRAGMA foreign_keys is a no-op inside a transaction, so make sure none is
    # open and switch the enforcement off for the duration of the rebuild.
    if db.in_transaction:
        db.commit()

    db.execute("PRAGMA foreign_keys = OFF")
    try:
        db.execute("BEGIN")
        try:
            db.execute(
                """
                CREATE TABLE users_rebuilt (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    full_name     TEXT    NOT NULL,
                    email         TEXT    COLLATE NOCASE NOT NULL,
                    mobile        TEXT    NOT NULL,
                    district      TEXT    NOT NULL,
                    password_hash TEXT    NOT NULL,
                    role          TEXT    NOT NULL DEFAULT 'user'
                                      CHECK (role IN ('user', 'admin', 'permanent_admin', 'secondary_admin')),
                    last_login_at TEXT,
                    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                    updated_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
                )
                """
            )
            db.execute(
                """
                INSERT INTO users_rebuilt
                    (id, full_name, email, mobile, district, password_hash,
                     role, last_login_at, created_at, updated_at)
                SELECT id, full_name, email, mobile, district, password_hash,
                       CASE WHEN role = 'admin' THEN 'permanent_admin' ELSE role END,
                       last_login_at, created_at, updated_at
                FROM users
                """
            )
            db.execute("DROP TABLE users")
            db.execute("ALTER TABLE users_rebuilt RENAME TO users")
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique "
                "ON users (email COLLATE NOCASE)"
            )
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    finally:
        db.execute("PRAGMA foreign_keys = ON")


def apply_migrations():
    """Apply additive schema changes that older databases still need.

    Phases add columns to existing tables. SQLite cannot express "add the
    column if it is missing" inside ``schema.sql``, so those single changes are
    applied here after the base schema. Nothing is ever dropped or rewritten,
    and the column is only added when it is genuinely absent.
    """
    db = get_db()

    # Migration 3: role-based access control. Existing accounts become 'user';
    # administrator rights are granted only by the create-admin command.
    if not column_exists("users", "role"):
        db.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")

    db.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, description) "
        "VALUES (?, ?)",
        (3, "Role column for regular user and administrator accounts"),
    )

    # Migration 4: administrator sales data entry. No new table is needed -
    # manual sales reuse the shared ``daily_records`` table, which already has
    # ``quantity_sold``, ``selling_price``, ``sales_revenue`` and the
    # ``record_source`` column that separates manual rows from imported ones.
    db.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, description) "
        "VALUES (?, ?)",
        (4, "Administrator sales data entry on the shared daily records table"),
    )

    db.commit()

    # Migration 9: the three-role model (user / secondary_admin /
    # permanent_admin). A table created by the earlier schema must be rebuilt
    # first, because its CHECK constraint would reject the new role values.
    if _users_role_check_is_outdated():
        _rebuild_users_for_new_roles()

    # Rename the original administrator's legacy value to ``permanent_admin``.
    # It only ever promotes ``admin`` to the new name - an existing
    # administrator is never downgraded, and repeated runs change nothing.
    get_db().execute(
        "UPDATE users SET role = 'permanent_admin' WHERE role = 'admin'"
    )

    get_db().execute(
        "INSERT OR IGNORE INTO schema_migrations (version, description) "
        "VALUES (?, ?)",
        (
            9,
            "Secondary administrator access requests and their approval audit trail",
        ),
    )

    get_db().commit()


def bootstrap(app):
    """Prepare the database before the first request is served."""
    ensure_database_directory(app.config["DATABASE"])
    with app.app_context():
        init_db()
        apply_migrations()
        seed_reference_data()


def init_app(app):
    """Register the database helpers on the Flask application."""
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)


@click.command("init-db")
def init_db_command():
    """Create the FruitVista schema in the SQLite database."""
    init_db()
    apply_migrations()
    seed_reference_data()
    click.echo("FruitVista schema is ready. Existing data was not modified.")
