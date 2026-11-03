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


def get_db():
    """Return the SQLite connection bound to the current app context.

    The connection is created on first use and stored on Flask's ``g``
    object, so every request reuses a single connection and closes it
    automatically when the request ends.
    """
    if "db" not in g:
        database_path = current_app.config["DATABASE"]
        os.makedirs(os.path.dirname(database_path), exist_ok=True)

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


def bootstrap(app):
    """Prepare the database before the first request is served."""
    os.makedirs(os.path.dirname(app.config["DATABASE"]), exist_ok=True)
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
