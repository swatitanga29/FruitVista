"""Database access for the historical CSV import.

The imported market data goes into the existing ``daily_records`` table with
``record_source = 'historical'``; the ``import_batches`` table records what
each import run did. Both are written with ``?`` placeholders - no value is
ever formatted into a SQL string.

Nothing here updates or deletes an existing record: historical rows are added
with ``INSERT OR IGNORE`` and manual rows are never touched.
"""

from database import get_db

RECORD_SOURCE = "historical"
DATASET_LABEL = "Synthetic historical dataset"

# Fixed column list shared by the import statement. It is a module constant
# and never contains user input.
_INSERT_COLUMNS = """
    record_date, district_id, fruit_id, record_source,
    opening_stock, purchased_quantity, purchase_price, purchase_cost,
    available_stock, estimated_demand, quantity_sold, selling_price,
    sales_revenue, damaged_quantity, wastage_cost, closing_stock,
    reorder_level, stock_status, cost_of_goods_sold, operating_expenses,
    gross_profit, net_profit, profit_margin, availability_percentage
"""

# One row per imported day / district / fruit. ``INSERT OR IGNORE`` together
# with the UNIQUE (record_date, district_id, fruit_id, record_source)
# constraint is what makes a repeated import harmless: a row that is already
# stored is skipped instead of being duplicated or overwritten.
IMPORT_COLUMN_ORDER = [
    "record_date",
    "district_id",
    "fruit_id",
    "record_source",
    "opening_stock",
    "purchased_quantity",
    "purchase_price",
    "purchase_cost",
    "available_stock",
    "estimated_demand",
    "quantity_sold",
    "selling_price",
    "sales_revenue",
    "damaged_quantity",
    "wastage_cost",
    "closing_stock",
    "reorder_level",
    "stock_status",
    "cost_of_goods_sold",
    "operating_expenses",
    "gross_profit",
    "net_profit",
    "profit_margin",
    "availability_percentage",
]

_INSERT_SQL = (
    f"INSERT OR IGNORE INTO daily_records ({_INSERT_COLUMNS}) "
    "VALUES (" + ", ".join(["?"] * len(IMPORT_COLUMN_ORDER)) + ")"
)


def insert_historical_rows(rows):
    """Insert a batch of historical rows in one transaction.

    ``rows`` is an iterable of 24-value tuples already ordered like
    :data:`IMPORT_COLUMN_ORDER`. Returns how many rows were genuinely added;
    rows that already existed are counted as duplicates by the caller.

    Everything is committed as a single transaction. If any row cannot be
    written the transaction is rolled back and the error is passed on, so the
    table is left exactly as it was before the call.
    """
    db = get_db()
    before = db.total_changes

    try:
        db.executemany(_INSERT_SQL, rows)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return db.total_changes - before


def count_rows(record_source):
    """Return how many rows are stored for one ``record_source``."""
    return get_db().execute(
        "SELECT COUNT(*) AS total FROM daily_records WHERE record_source = ?",
        (record_source,),
    ).fetchone()["total"]


def coverage_by_district():
    """Return the historical row count and date range per district."""
    return get_db().execute(
        "SELECT d.name AS district_name, "
        "       COUNT(*) AS total, "
        "       MIN(r.record_date) AS first_date, "
        "       MAX(r.record_date) AS last_date "
        "FROM daily_records r "
        "JOIN districts d ON d.id = r.district_id "
        "WHERE r.record_source = ? "
        "GROUP BY d.name "
        "ORDER BY d.name",
        (RECORD_SOURCE,),
    ).fetchall()


def create_batch(file_name, file_hash):
    """Open an import run and return its id."""
    db = get_db()
    cursor = db.execute(
        "INSERT INTO import_batches "
        "(file_name, file_hash, dataset_label, status) "
        "VALUES (?, ?, ?, 'running')",
        (file_name, file_hash, DATASET_LABEL),
    )
    db.commit()
    return cursor.lastrowid


def finish_batch(batch_id, total_rows, imported_rows, duplicate_rows, skipped_rows, notes=None):
    """Mark an import run as completed with its final counters."""
    db = get_db()
    db.execute(
        "UPDATE import_batches "
        "SET total_rows = ?, imported_rows = ?, duplicate_rows = ?, "
        "    skipped_rows = ?, status = 'completed', notes = ?, "
        "    finished_at = datetime('now', 'localtime') "
        "WHERE id = ?",
        (total_rows, imported_rows, duplicate_rows, skipped_rows, notes, batch_id),
    )
    db.commit()


def fail_batch(batch_id, total_rows, imported_rows, duplicate_rows, skipped_rows, notes):
    """Mark an import run as failed and explain why."""
    db = get_db()
    db.execute(
        "UPDATE import_batches "
        "SET total_rows = ?, imported_rows = ?, duplicate_rows = ?, "
        "    skipped_rows = ?, status = 'failed', notes = ?, "
        "    finished_at = datetime('now', 'localtime') "
        "WHERE id = ?",
        (total_rows, imported_rows, duplicate_rows, skipped_rows, notes, batch_id),
    )
    db.commit()


def list_batches(limit=10):
    """Return the most recent import runs, newest first."""
    return get_db().execute(
        "SELECT id, file_name, dataset_label, total_rows, imported_rows, "
        "       duplicate_rows, skipped_rows, status, notes, "
        "       started_at, finished_at "
        "FROM import_batches "
        "ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
