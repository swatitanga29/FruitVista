"""Database access for the administrator Inventory Management module.

Three sources are read side by side, and each one keeps its own identity:

* ``daily_records`` with ``record_source = 'historical'`` - the imported
  synthetic dataset. It is the only source that carries opening stock,
  purchased quantity, damaged quantity and a reorder level.
* ``daily_records`` with ``record_source = 'manual'`` - the sales an
  administrator entered in the Sales Entry module. Those rows only carry a
  sold quantity.
* ``stock_movements`` - the purchases and stock adjustments entered in this
  module. Those rows carry no stock figures at all; they are a signed
  correction applied on top of the historical stock.

Nothing is ever written back into ``daily_records`` from here, so an imported
row or a manual sales record can never be changed or duplicated.

The combined view is built in one query, grouped by
``(record_date, district_id, fruit_id)``::

    available stock = opening stock + purchased quantity + stock adjustment
    closing stock   = available stock - quantity sold - damaged quantity

Every value is passed to SQLite as a ``?`` placeholder. Nothing is formatted
into the SQL string.
"""

from database import get_db

HISTORICAL = "historical"
MANUAL = "manual"

MOVEMENT_PURCHASE = "purchase"
MOVEMENT_ADJUSTMENT = "adjustment"

# Stock status, derived from the remaining stock and the reorder level.
STATUS_AVAILABLE = "available"
STATUS_LOW_STOCK = "low_stock"
STATUS_OUT_OF_STOCK = "out_of_stock"

STATUSES = (STATUS_AVAILABLE, STATUS_LOW_STOCK, STATUS_OUT_OF_STOCK)
LOW_STOCK_STATUSES = (STATUS_LOW_STOCK, STATUS_OUT_OF_STOCK)

# A position counts as overstocked when its closing stock is more than this
# many times its reorder level. The reorder level is the only stock threshold
# the dataset actually records, so it is the only defensible anchor for "too
# much stock"; the multiple itself is a FruitVista rule and is stated openly on
# the dashboard rather than presented as a market fact.
OVERSTOCK_MULTIPLIER = 2.0

# The signed stock correction carried by ``stock_movements``.
_ADJUSTMENT = "SUM(adjustment_quantity)"
# The stock left at the end of the day. Written out in full because SQLite
# cannot refer to a select alias from the same select list.
_CLOSING = (
    "SUM(opening_stock) + SUM(purchased_quantity) + SUM(adjustment_quantity) "
    "- SUM(quantity_sold) - SUM(damaged_quantity)"
)
_AVAILABLE = "SUM(opening_stock) + SUM(purchased_quantity) + SUM(adjustment_quantity)"


def _record_clause(filters, source):
    """Build the WHERE fragment for one ``record_source`` of ``daily_records``.

    Returns ``(joins, where, parameters)``. The date, district and fruit
    filters compare stored ids and dates, so only the fruit-category filter
    needs a join.
    """
    joins = ""
    clauses = ["r.record_source = ?"]
    parameters = [source]

    if filters.get("date_from"):
        clauses.append("r.record_date >= ?")
        parameters.append(filters["date_from"])
    if filters.get("date_to"):
        clauses.append("r.record_date <= ?")
        parameters.append(filters["date_to"])
    if filters.get("district_id"):
        clauses.append("r.district_id = ?")
        parameters.append(filters["district_id"])
    if filters.get("category"):
        joins = "JOIN fruits ff ON ff.id = r.fruit_id"
        clauses.append("ff.category = ?")
        parameters.append(filters["category"])
    if filters.get("fruit_id"):
        clauses.append("r.fruit_id = ?")
        parameters.append(filters["fruit_id"])

    return joins, " AND ".join(clauses), parameters


def _movement_clause(filters):
    """Build the WHERE fragment for ``stock_movements``.

    ``filters`` may also carry ``movement_type``; the dashboard never sets it,
    the movement history page does.
    """
    joins = ""
    clauses = ["1 = 1"]
    parameters = []

    if filters.get("date_from"):
        clauses.append("m.movement_date >= ?")
        parameters.append(filters["date_from"])
    if filters.get("date_to"):
        clauses.append("m.movement_date <= ?")
        parameters.append(filters["date_to"])
    if filters.get("district_id"):
        clauses.append("m.district_id = ?")
        parameters.append(filters["district_id"])
    if filters.get("category"):
        joins = "JOIN fruits ff ON ff.id = m.fruit_id"
        clauses.append("ff.category = ?")
        parameters.append(filters["category"])
    if filters.get("fruit_id"):
        clauses.append("m.fruit_id = ?")
        parameters.append(filters["fruit_id"])
    if filters.get("movement_type"):
        clauses.append("m.movement_type = ?")
        parameters.append(filters["movement_type"])

    return joins, " AND ".join(clauses), parameters


def _combined(filters):
    """Return ``(sql, parameters)`` holding the ``inventory`` common table expression.

    ``inventory`` has one row per (date, district, fruit) and already carries
    the derived ``stock_status``, so callers only need to add the stock-status
    filter, the columns they want and an ORDER BY.
    """
    hist_joins, hist_where, hist_parameters = _record_clause(filters, HISTORICAL)
    manual_joins, manual_where, manual_parameters = _record_clause(filters, MANUAL)
    move_joins, move_where, move_parameters = _movement_clause(filters)

    sql = f"""
    WITH parts AS (
        SELECT r.record_date        AS record_date,
               r.district_id        AS district_id,
               r.fruit_id           AS fruit_id,
               r.opening_stock      AS opening_stock,
               r.purchased_quantity AS purchased_quantity,
               0.0                  AS adjustment_quantity,
               r.quantity_sold      AS quantity_sold,
               r.damaged_quantity   AS damaged_quantity,
               r.reorder_level      AS reorder_level,
               1                    AS historical_rows,
               0                    AS manual_sales_rows,
               0                    AS manual_movement_rows
        FROM daily_records r
        {hist_joins}
        WHERE {hist_where}

        UNION ALL

        SELECT r.record_date, r.district_id, r.fruit_id,
               0.0, 0.0, 0.0,
               r.quantity_sold, 0.0, 0.0,
               0, 1, 0
        FROM daily_records r
        {manual_joins}
        WHERE {manual_where}

        UNION ALL

        SELECT m.movement_date, m.district_id, m.fruit_id,
               0.0,
               CASE WHEN m.movement_type = 'purchase'   THEN m.quantity ELSE 0.0 END,
               CASE WHEN m.movement_type = 'adjustment' THEN m.quantity ELSE 0.0 END,
               0.0, 0.0, 0.0,
               0, 0, 1
        FROM stock_movements m
        {move_joins}
        WHERE {move_where}
    ),
    grouped AS (
        SELECT record_date, district_id, fruit_id,
               SUM(opening_stock)       AS opening_stock,
               SUM(purchased_quantity)  AS purchased_quantity,
               {_ADJUSTMENT}            AS adjustment_quantity,
               {_AVAILABLE}             AS available_stock,
               SUM(quantity_sold)       AS quantity_sold,
               SUM(damaged_quantity)    AS damaged_quantity,
               {_CLOSING}               AS closing_stock,
               MAX(reorder_level)       AS reorder_level,
               MAX(historical_rows)     AS historical_rows,
               MAX(manual_sales_rows)   AS manual_sales_rows,
               MAX(manual_movement_rows) AS manual_movement_rows
        FROM parts
        GROUP BY record_date, district_id, fruit_id
    ),
    inventory AS (
        SELECT *,
               CASE
                   WHEN closing_stock <= 0           THEN '{STATUS_OUT_OF_STOCK}'
                   WHEN closing_stock <= reorder_level THEN '{STATUS_LOW_STOCK}'
                   ELSE '{STATUS_AVAILABLE}'
               END AS stock_status
        FROM grouped
    )
    """

    parameters = hist_parameters + manual_parameters + move_parameters
    return sql, parameters


def _status_clause(filters):
    """Return the extra clause and parameter for the stock-status filter."""
    if filters.get("stock_status"):
        return " AND inv.stock_status = ?", [filters["stock_status"]]

    return "", []


def data_bounds():
    """Return the stored date range and how much of each source exists.

    ``None`` dates mean the database is still empty.
    """
    db = get_db()

    record_row = db.execute(
        "SELECT MIN(record_date) AS first_date, MAX(record_date) AS last_date, "
        "       SUM(record_source = 'historical') AS historical_rows, "
        "       SUM(record_source = 'manual')     AS manual_sales_rows "
        "FROM daily_records"
    ).fetchone()

    movement_row = db.execute(
        "SELECT MIN(movement_date) AS first_date, MAX(movement_date) AS last_date, "
        "       COUNT(*) AS movement_rows "
        "FROM stock_movements"
    ).fetchone()

    first_dates = [value for value in (record_row["first_date"], movement_row["first_date"]) if value]
    last_dates = [value for value in (record_row["last_date"], movement_row["last_date"]) if value]

    return {
        "first_date": min(first_dates) if first_dates else None,
        "last_date": max(last_dates) if last_dates else None,
        "historical_rows": record_row["historical_rows"] or 0,
        "manual_sales_rows": record_row["manual_sales_rows"] or 0,
        "movement_rows": movement_row["movement_rows"] or 0,
    }


def summary(filters):
    """Totals across every combined row matching the filters."""
    sql, parameters = _combined(filters)
    status_sql, status_parameters = _status_clause(filters)

    row = get_db().execute(
        sql
        + f"""
        SELECT COUNT(*)                       AS records,
               COALESCE(SUM(opening_stock), 0)      AS opening_stock,
               COALESCE(SUM(purchased_quantity), 0) AS purchased_quantity,
               COALESCE(SUM(adjustment_quantity), 0) AS adjustment_quantity,
               COALESCE(SUM(available_stock), 0)    AS available_stock,
               COALESCE(SUM(quantity_sold), 0)      AS quantity_sold,
               COALESCE(SUM(damaged_quantity), 0)   AS damaged_quantity,
               COALESCE(SUM(closing_stock), 0)      AS closing_stock,
               COALESCE(SUM(historical_rows), 0)    AS historical_rows,
               COALESCE(SUM(manual_sales_rows), 0)  AS manual_sales_rows,
               COALESCE(SUM(manual_movement_rows), 0) AS manual_movement_rows
        FROM inventory inv
        WHERE 1 = 1{status_sql}
        """,
        parameters + status_parameters,
    ).fetchone()

    return dict(row)


def latest_date(filters):
    """Return the most recent date in the current selection, or ``None``."""
    sql, parameters = _combined(filters)
    status_sql, status_parameters = _status_clause(filters)

    row = get_db().execute(
        sql + f"SELECT MAX(inv.record_date) AS latest FROM inventory inv WHERE 1 = 1{status_sql}",
        parameters + status_parameters,
    ).fetchone()

    return row["latest"]


def low_stock_positions(filters, limit=None):
    """Stock positions that are at or below their reorder level on the latest day.

    ``filters`` must already be the *unfiltered by status* selection, because a
    position is reported here when it is low or out of stock. Returns
    ``(latest_date, rows)``.
    """
    latest = latest_date(filters)

    if latest is None:
        return None, []

    sql, parameters = _combined(filters)
    sql += """
        SELECT inv.record_date, inv.district_id, inv.fruit_id,
               inv.closing_stock, inv.reorder_level, inv.stock_status,
               inv.historical_rows, inv.manual_movement_rows,
               d.name AS district_name,
               f.name AS fruit_name,
               f.category AS fruit_category
        FROM inventory inv
        JOIN districts d ON d.id = inv.district_id
        JOIN fruits f    ON f.id = inv.fruit_id
        WHERE inv.record_date = ?
          AND inv.stock_status IN (?, ?)
        ORDER BY inv.closing_stock ASC, d.name, f.name
    """
    list_parameters = parameters + [latest, STATUS_LOW_STOCK, STATUS_OUT_OF_STOCK]

    if limit is not None:
        sql += " LIMIT ?"
        list_parameters.append(limit)

    return latest, get_db().execute(sql, list_parameters).fetchall()


def district_summary(filters):
    """Stock totals per district, ordered by closing stock, highest first."""
    sql, parameters = _combined(filters)
    status_sql, status_parameters = _status_clause(filters)

    return get_db().execute(
        sql
        + f"""
        SELECT d.name AS label,
               COUNT(*) AS records,
               COALESCE(SUM(inv.opening_stock), 0)      AS opening_stock,
               COALESCE(SUM(inv.purchased_quantity), 0) AS purchased_quantity,
               COALESCE(SUM(inv.available_stock), 0)    AS available_stock,
               COALESCE(SUM(inv.quantity_sold), 0)      AS quantity_sold,
               COALESCE(SUM(inv.damaged_quantity), 0)   AS damaged_quantity,
               COALESCE(SUM(inv.closing_stock), 0)      AS closing_stock
        FROM inventory inv
        JOIN districts d ON d.id = inv.district_id
        WHERE 1 = 1{status_sql}
        GROUP BY inv.district_id
        ORDER BY closing_stock DESC
        """,
        parameters + status_parameters,
    ).fetchall()


def category_summary(filters):
    """Stock totals per fruit category, ordered by closing stock, highest first."""
    sql, parameters = _combined(filters)
    status_sql, status_parameters = _status_clause(filters)

    return get_db().execute(
        sql
        + f"""
        SELECT f.category AS label,
               COUNT(*) AS records,
               COALESCE(SUM(inv.available_stock), 0)  AS available_stock,
               COALESCE(SUM(inv.quantity_sold), 0)    AS quantity_sold,
               COALESCE(SUM(inv.damaged_quantity), 0) AS damaged_quantity,
               COALESCE(SUM(inv.closing_stock), 0)    AS closing_stock
        FROM inventory inv
        JOIN fruits f ON f.id = inv.fruit_id
        WHERE 1 = 1{status_sql}
        GROUP BY f.category
        ORDER BY closing_stock DESC
        """,
        parameters + status_parameters,
    ).fetchall()


def count_inventory(filters):
    """Number of combined rows matching the filters."""
    sql, parameters = _combined(filters)
    status_sql, status_parameters = _status_clause(filters)

    row = get_db().execute(
        sql + f"SELECT COUNT(*) AS total FROM inventory inv WHERE 1 = 1{status_sql}",
        parameters + status_parameters,
    ).fetchone()

    return row["total"]


def list_inventory(filters, limit, offset):
    """One page of combined inventory rows, newest day first."""
    sql, parameters = _combined(filters)
    status_sql, status_parameters = _status_clause(filters)

    return get_db().execute(
        sql
        + f"""
        SELECT inv.record_date, inv.district_id, inv.fruit_id,
               inv.opening_stock, inv.purchased_quantity, inv.adjustment_quantity,
               inv.available_stock, inv.quantity_sold, inv.damaged_quantity,
               inv.closing_stock, inv.reorder_level, inv.stock_status,
               inv.historical_rows, inv.manual_sales_rows, inv.manual_movement_rows,
               d.name AS district_name,
               f.name AS fruit_name,
               f.category AS fruit_category
        FROM inventory inv
        JOIN districts d ON d.id = inv.district_id
        JOIN fruits f    ON f.id = inv.fruit_id
        WHERE 1 = 1{status_sql}
        ORDER BY inv.record_date DESC, d.name, f.name
        LIMIT ? OFFSET ?
        """,
        parameters + status_parameters + [limit, offset],
    ).fetchall()


# ---------------------------------------------------------------------------
# Manual stock movements
# ---------------------------------------------------------------------------
_MOVEMENT_COLUMNS = """
    m.id            AS id,
    m.movement_date AS movement_date,
    m.district_id   AS district_id,
    m.fruit_id      AS fruit_id,
    m.movement_type AS movement_type,
    m.quantity      AS quantity,
    m.unit_price    AS unit_price,
    m.amount        AS amount,
    m.reason        AS reason,
    m.created_at    AS created_at,
    d.name          AS district_name,
    f.name          AS fruit_name,
    f.category      AS fruit_category
"""


def create_movement(movement_type, movement_date, district_id, fruit_id,
                    quantity, unit_price=0.0, amount=0.0, reason=None):
    """Insert one purchase or stock adjustment and return its new id.

    The insert runs inside a transaction: the row is committed on success and
    rolled back if the database refuses it, so a rejected entry leaves no
    half-written record behind.
    """
    db = get_db()
    try:
        cursor = db.execute(
            "INSERT INTO stock_movements "
            "(movement_date, district_id, fruit_id, movement_type, quantity, "
            " unit_price, amount, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (movement_date, district_id, fruit_id, movement_type,
             quantity, unit_price, amount, reason),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return cursor.lastrowid


def list_movements(filters, limit, offset):
    """One page of stock movements, newest first."""
    sql = f"SELECT {_MOVEMENT_COLUMNS} FROM stock_movements m " \
          "JOIN districts d ON d.id = m.district_id " \
          "JOIN fruits f    ON f.id = m.fruit_id WHERE 1 = 1"
    parameters = []

    if filters.get("date_from"):
        sql += " AND m.movement_date >= ?"
        parameters.append(filters["date_from"])
    if filters.get("date_to"):
        sql += " AND m.movement_date <= ?"
        parameters.append(filters["date_to"])
    if filters.get("district_id"):
        sql += " AND m.district_id = ?"
        parameters.append(filters["district_id"])
    if filters.get("category"):
        sql += " AND f.category = ?"
        parameters.append(filters["category"])
    if filters.get("fruit_id"):
        sql += " AND m.fruit_id = ?"
        parameters.append(filters["fruit_id"])
    if filters.get("movement_type"):
        sql += " AND m.movement_type = ?"
        parameters.append(filters["movement_type"])

    sql += " ORDER BY m.movement_date DESC, m.id DESC LIMIT ? OFFSET ?"
    parameters += [limit, offset]

    return get_db().execute(sql, parameters).fetchall()


def count_movements(filters):
    """Number of stock movements matching the filters."""
    sql = (
        "SELECT COUNT(*) AS total FROM stock_movements m "
        "JOIN fruits f ON f.id = m.fruit_id WHERE 1 = 1"
    )
    parameters = []

    if filters.get("date_from"):
        sql += " AND m.movement_date >= ?"
        parameters.append(filters["date_from"])
    if filters.get("date_to"):
        sql += " AND m.movement_date <= ?"
        parameters.append(filters["date_to"])
    if filters.get("district_id"):
        sql += " AND m.district_id = ?"
        parameters.append(filters["district_id"])
    if filters.get("category"):
        sql += " AND f.category = ?"
        parameters.append(filters["category"])
    if filters.get("fruit_id"):
        sql += " AND m.fruit_id = ?"
        parameters.append(filters["fruit_id"])
    if filters.get("movement_type"):
        sql += " AND m.movement_type = ?"
        parameters.append(filters["movement_type"])

    return get_db().execute(sql, parameters).fetchone()["total"]


def movement_totals(filters):
    """Totals for the movements currently listed."""
    sql = "SELECT movement_type, COUNT(*) AS records, COALESCE(SUM(quantity), 0) AS quantity, " \
          "COALESCE(SUM(amount), 0) AS amount " \
          "FROM stock_movements m JOIN fruits f ON f.id = m.fruit_id WHERE 1 = 1"
    parameters = []

    if filters.get("date_from"):
        sql += " AND m.movement_date >= ?"
        parameters.append(filters["date_from"])
    if filters.get("date_to"):
        sql += " AND m.movement_date <= ?"
        parameters.append(filters["date_to"])
    if filters.get("district_id"):
        sql += " AND m.district_id = ?"
        parameters.append(filters["district_id"])
    if filters.get("category"):
        sql += " AND f.category = ?"
        parameters.append(filters["category"])
    if filters.get("fruit_id"):
        sql += " AND m.fruit_id = ?"
        parameters.append(filters["fruit_id"])
    if filters.get("movement_type"):
        sql += " AND m.movement_type = ?"
        parameters.append(filters["movement_type"])

    sql += " GROUP BY movement_type"

    return {row["movement_type"]: dict(row) for row in get_db().execute(sql, parameters).fetchall()}


def overstocked_positions(filters, limit=None):
    """Stock positions holding well above their reorder level on the latest day.

    A position is overstocked when its closing stock is greater than
    :data:`OVERSTOCK_MULTIPLIER` times its reorder level. A position with no
    recorded reorder level (level 0) is left out, because there is nothing to
    compare against. Returns ``(latest_date, rows)``.
    """
    latest = latest_date(filters)

    if latest is None:
        return None, []

    sql, parameters = _combined(filters)
    sql += """
        SELECT inv.record_date, inv.closing_stock, inv.reorder_level,
               inv.available_stock, inv.quantity_sold,
               inv.historical_rows, inv.manual_movement_rows,
               d.name AS district_name,
               f.name AS fruit_name,
               f.category AS fruit_category
        FROM inventory inv
        JOIN districts d ON d.id = inv.district_id
        JOIN fruits f    ON f.id = inv.fruit_id
        WHERE inv.record_date = ?
          AND inv.reorder_level > 0
          AND inv.closing_stock > ? * inv.reorder_level
        ORDER BY (inv.closing_stock - inv.reorder_level) DESC, d.name, f.name
    """
    list_parameters = parameters + [latest, OVERSTOCK_MULTIPLIER]

    if limit is not None:
        sql += " LIMIT ?"
        list_parameters.append(limit)

    return latest, get_db().execute(sql, list_parameters).fetchall()


def purchase_by_fruit(filters, limit=None):
    """Purchased quantity recorded in ``stock_movements`` per fruit.

    Only the purchases entered in the Inventory module appear here: the
    historical dataset carries no purchase records of its own. This is the
    "frequently purchased fruits" figure, ordered by purchased quantity.
    """
    sql = (
        "SELECT f.name AS label, f.category AS category, "
        "       COUNT(*) AS records, "
        "       COALESCE(SUM(m.quantity), 0) AS quantity, "
        "       COALESCE(SUM(m.amount), 0)   AS amount "
        "FROM stock_movements m "
        "JOIN fruits f ON f.id = m.fruit_id "
        "WHERE m.movement_type = ?"
    )
    parameters = [MOVEMENT_PURCHASE]

    if filters.get("date_from"):
        sql += " AND m.movement_date >= ?"
        parameters.append(filters["date_from"])
    if filters.get("date_to"):
        sql += " AND m.movement_date <= ?"
        parameters.append(filters["date_to"])
    if filters.get("district_id"):
        sql += " AND m.district_id = ?"
        parameters.append(filters["district_id"])
    if filters.get("category"):
        sql += " AND f.category = ?"
        parameters.append(filters["category"])
    if filters.get("fruit_id"):
        sql += " AND m.fruit_id = ?"
        parameters.append(filters["fruit_id"])

    sql += " GROUP BY f.id ORDER BY quantity DESC, f.name"

    if limit is not None:
        sql += " LIMIT ?"
        parameters.append(limit)

    return get_db().execute(sql, parameters).fetchall()
