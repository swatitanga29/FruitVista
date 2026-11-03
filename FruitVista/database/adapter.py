"""One database interface for SQLite and PostgreSQL.

FruitVista talks to its database only through the small surface defined here,
so the feature modules (``models``, ``ml``, ``services``, ``admin``) never
import a driver directly and never contain two versions of the same query.

What this layer does
--------------------
* opens the connection for the configured backend (``get_db`` lives in
  :mod:`database` and delegates here),
* presents the cursor surface the project already used on SQLite -
  ``execute``, ``executemany``, ``fetchone``, ``fetchall``, iteration,
  ``rowcount``, ``lastrowid``, ``description``,
* returns rows that can be read by column name *and* by position, so
  ``row["name"]`` and ``dict(row)`` keep working exactly as they did with
  ``sqlite3.Row``,
* keeps ``total_changes`` working on both backends,
* translates the handful of SQLite spellings the project uses into their
  PostgreSQL equivalents.

Deliberate limits
-----------------
The translation is intentionally a bounded list. Constructs whose *meaning*
has to change with the schema - ``strftime`` bucketing, the ``date(...)``
week modifier, ``SUM(<condition>)`` and the case-insensitive email lookup -
are **not** translated here. They are listed in the migration plan and are
fixed in the feature modules in the phase that switches the backend over.
Running the application against PostgreSQL before those are fixed would fail
on those specific queries, and nowhere else.

SQLite stays the default backend and its behaviour is unchanged: the SQLite
implementation below is a thin wrapper that passes every statement straight
through to ``sqlite3``.
"""

import os
import re
import sqlite3

SQLITE = "sqlite"
POSTGRES = "postgres"

# Tables whose primary key is an ``id INTEGER PRIMARY KEY``. Those are the only
# tables where PostgreSQL can be asked for the new id with ``RETURNING id`` so
# that ``cursor.lastrowid`` keeps working. ``schema_migrations`` is keyed by
# ``version`` and is never asked for a generated id.
_ID_TABLES = frozenset({
    "districts",
    "fruits",
    "daily_records",
    "users",
    "import_batches",
    "stock_movements",
    "wastage_records",
    "ml_models",
    "ml_predictions",
})

_INSERT_OR_IGNORE = re.compile(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", re.IGNORECASE)
_INSERT_INTO = re.compile(r"^\s*INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
_HAS_RETURNING = re.compile(r"\bRETURNING\b", re.IGNORECASE)

# SQLite's two ways of writing "the current timestamp". The first is UTC and
# the second is the host machine's local time; FruitVista runs in Karnataka, so
# the local form keeps meaning Indian Standard Time. The already stored values
# are never rewritten - these defaults only apply to new rows.
_UTC_NOW = re.compile(r"datetime\(\s*'now'\s*\)", re.IGNORECASE)
_LOCAL_NOW = re.compile(r"datetime\(\s*'now'\s*,\s*'localtime'\s*\)", re.IGNORECASE)

_UTC_NOW_SQL = "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"
_LOCAL_NOW_SQL = (
    "to_char(now() AT TIME ZONE 'Asia/Kolkata', 'YYYY-MM-DD HH24:MI:SS')"
)


class IntegrityError(sqlite3.IntegrityError):
    """A write broke a database constraint.

    It inherits from ``sqlite3.IntegrityError`` on purpose: every existing
    ``except sqlite3.IntegrityError`` in the project therefore keeps working
    whichever backend is in use, with no change to the feature modules.
    """


class Row(dict):
    """One result row, readable by column name and by position.

    ``sqlite3.Row`` allowed both ``row["name"]`` and ``row[0]``, and ``dict(row)``
    on top of that. This reproduces all three, which is what the existing code
    relies on.
    """

    __slots__ = ("_values",)

    def __init__(self, columns, values):
        super().__init__(zip(columns, values))
        self._values = tuple(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


class Cursor:
    """The cursor surface the project uses, over either driver's cursor."""

    def __init__(self, cursor):
        self._cursor = cursor
        self._columns = None
        self.lastrowid = None

    def _read_columns(self):
        description = self._cursor.description
        if description is None:
            return None
        return [column[0] for column in description]

    def fetchone(self):
        self._columns = self._read_columns()
        values = self._cursor.fetchone()
        if values is None:
            return None
        return Row(self._columns, values)

    def fetchall(self):
        self._columns = self._read_columns()
        return [Row(self._columns, values) for values in self._cursor.fetchall()]

    def __iter__(self):
        self._columns = self._read_columns()
        for values in self._cursor:
            yield Row(self._columns, values)

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description


class SQLiteDatabase:
    """The default backend. A thin pass-through to :mod:`sqlite3`."""

    backend = SQLITE

    def __init__(self, path):
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        self.raw = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES)
        self.raw.row_factory = sqlite3.Row
        self.raw.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql, parameters=None):
        cursor = self.raw.cursor()
        if parameters is None:
            cursor.execute(sql)
        else:
            cursor.execute(sql, parameters)

        wrapped = Cursor(cursor)
        wrapped.lastrowid = cursor.lastrowid
        return wrapped

    def executemany(self, sql, rows):
        cursor = self.raw.cursor()
        cursor.executemany(sql, rows)

        wrapped = Cursor(cursor)
        wrapped.lastrowid = cursor.lastrowid
        return wrapped

    def executescript(self, script):
        self.raw.executescript(script)

    @property
    def total_changes(self):
        return self.raw.total_changes

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()

    def table_names(self):
        rows = self.raw.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall()

        return [row["name"] for row in rows]


class PostgresDatabase:
    """The PostgreSQL backend, used once the migration is approved.

    ``psycopg2`` is imported when the connection is opened rather than at
    module import, so a SQLite-only install never needs the package.
    """

    backend = POSTGRES

    def __init__(self, database_url):
        import psycopg2

        self._psycopg2 = psycopg2
        self.raw = psycopg2.connect(database_url)
        self._changes = 0

    def execute(self, sql, parameters=None):
        statement = translate_sql(
            sql, has_parameters=parameters is not None, wants_lastrowid=True
        )
        return self._run(statement, parameters, single=True)

    def executemany(self, sql, rows):
        # ``executemany`` never needs the generated id, and asking for one
        # would make the driver return a result set it cannot expose.
        statement = translate_sql(sql, has_parameters=True, wants_lastrowid=False)
        return self._run(statement, rows, single=False)

    def executescript(self, script):
        # The schema script is already written in PostgreSQL syntax and is sent
        # as one multi-statement command. No parameters are bound, so no
        # percent signs in it are interpreted.
        cursor = self.raw.cursor()
        try:
            cursor.execute(script)
        except Exception as error:
            self.raw.rollback()
            raise self._as_integrity_error(error) from error

    def _run(self, statement, parameters, single):
        cursor = self.raw.cursor()

        try:
            if single:
                if parameters is None:
                    cursor.execute(statement)
                else:
                    cursor.execute(statement, parameters)
            else:
                cursor.executemany(statement, parameters)
        except Exception as error:
            # PostgreSQL aborts the whole transaction on a failed statement, so
            # the connection has to be reset before anything else can run.
            self.raw.rollback()
            raise self._as_integrity_error(error) from error

        if cursor.rowcount and cursor.rowcount > 0:
            self._changes += cursor.rowcount

        wrapped = Cursor(cursor)
        if single and _HAS_RETURNING.search(statement):
            generated = cursor.fetchone()
            if generated is not None:
                wrapped.lastrowid = generated[0]

        return wrapped

    def _as_integrity_error(self, error):
        """Report a constraint violation as the project's ``IntegrityError``."""
        code = getattr(error, "pgcode", None)

        if code and str(code).startswith("23"):
            return IntegrityError(str(error))

        return error

    @property
    def total_changes(self):
        return self._changes

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()

    def table_names(self):
        rows = self.execute(
            "SELECT table_name AS name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
            "ORDER BY table_name"
        ).fetchall()

        return [row["name"] for row in rows]


def translate_sql(sql, has_parameters=False, wants_lastrowid=False):
    """Return ``sql`` written the way PostgreSQL expects it.

    Only the spellings this project actually uses are translated:

    * ``?`` placeholders become ``%s`` (and literal ``%`` is escaped, because
      the driver interprets it once parameters are bound),
    * ``INSERT OR IGNORE INTO`` becomes ``INSERT INTO ... ON CONFLICT DO
      NOTHING``,
    * an ``INSERT`` into one of the known tables gains ``RETURNING id`` so
      ``cursor.lastrowid`` keeps working,
    * ``datetime('now')`` / ``datetime('now', 'localtime')`` become the
      matching timestamp expression.

    The function is pure, so it is unit-testable without a database.
    """
    text = sql
    ignore_conflicts = bool(_INSERT_OR_IGNORE.search(text))

    if ignore_conflicts:
        text = _INSERT_OR_IGNORE.sub("INSERT INTO", text)

    text = text.strip().rstrip(";").rstrip()

    if ignore_conflicts and not _HAS_RETURNING.search(text):
        text += " ON CONFLICT DO NOTHING"

    if wants_lastrowid and not _HAS_RETURNING.search(text):
        match = _INSERT_INTO.match(text)
        if match and match.group(1).lower() in _ID_TABLES:
            text += " RETURNING id"

    text = _LOCAL_NOW.sub(_LOCAL_NOW_SQL, text)
    text = _UTC_NOW.sub(_UTC_NOW_SQL, text)

    if has_parameters:
        text = text.replace("%", "%%")

    return text.replace("?", "%s")


def connect(app):
    """Open the connection for the backend named in the configuration."""
    backend = str(app.config.get("DB_BACKEND") or SQLITE).lower()

    if backend == POSTGRES:
        database_url = app.config.get("DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "FRUITVISTA_DB_BACKEND is 'postgres' but DATABASE_URL is not "
                "set. Set the connection string before starting FruitVista."
            )
        return PostgresDatabase(database_url)

    return SQLiteDatabase(app.config["DATABASE"])


def read_sql(query, database, params=None):
    """Run a SELECT and return a :class:`pandas.DataFrame` on either backend.

    On SQLite the raw connection is handed to pandas exactly as before, so the
    existing behaviour and column types are untouched. PostgreSQL has no
    SQLAlchemy dependency in this project, so the rows are read through the
    adapter and assembled into a frame instead.
    """
    import pandas as pd

    if database.backend == SQLITE:
        if params is None:
            return pd.read_sql(query, database.raw)
        return pd.read_sql(query, database.raw, params=params)

    cursor = database.execute(query, params)
    columns = [column[0] for column in cursor.description]
    rows = cursor.fetchall()

    values = [[row[index] for index in range(len(columns))] for row in rows]

    return pd.DataFrame(values, columns=columns)
