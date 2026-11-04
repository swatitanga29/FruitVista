"""Database access for the administrator Wastage Management module.

Two sources are read side by side and each one keeps its own identity:

* ``daily_records`` with ``record_source = 'historical'`` - the imported
  synthetic dataset. It stores the damaged quantity in ``damaged_quantity`` and
  the wastage value in ``wastage_cost`` (the CSV headers ``damaged_quantity_kg``
  and ``wastage_cost_inr`` are imported into those two columns). The cost per
  kilogram is derived from the two stored numbers, so nothing is invented.
* ``wastage_records`` - the wastage an administrator recorded in this module.
  Those rows carry their own damaged quantity, cost per kilogram, wastage cost
  and reason.

Nothing is ever written back into ``daily_records`` from here, so an imported
row or a manual sales record can never be changed, deleted or counted twice.
The historical damaged quantity is read exactly once and the manual wastage is
read exactly once; the two are added together one time and never again.

The combined view is built in one query::

    wastage cost = damaged quantity x cost per kilogram
    wastage %    = damaged quantity / available stock x 100

The available stock only ever exists in the historical dataset, so the
percentage always compares against the historical available stock for the same
dates, districts, categories and fruits (see ``available_stock``).

Every value is passed to SQLite as a ``?`` placeholder. Nothing is formatted
into the SQL string except the two fixed internal source constants.
"""

from database import get_db

HISTORICAL = "historical"
MANUAL = "manual"

# The two sources a wastage record can come from, in display order.
SOURCES = (HISTORICAL, MANUAL)


def _date_order(filters, date_column):
    """Return the date-range clauses shared by both sources."""
    clauses = []
    parameters = []

    if filters.get("date_from"):
        clauses.append(f"{date_column} >= ?")
        parameters.append(filters["date_from"])
    if filters.get("date_to"):
        clauses.append(f"{date_column} <= ?")
        parameters.append(filters["date_to"])

    return clauses, parameters


def _history_clause(filters, only_damaged=False):
    """Build the WHERE fragment for the historical rows of ``daily_records``.

    Returns ``(joins, where, parameters)``. The date, district and fruit filters
    compare stored ids and dates, so only the fruit-category filter needs a
    join. ``only_damaged`` is used by the records list, which shows the rows
    that actually carry damage.
    """
    joins = ""
    clauses = ["r.record_source = ?"]
    parameters = [HISTORICAL]

    if only_damaged:
        clauses.append("r.damaged_quantity > 0")

    date_clauses, date_parameters = _date_order(filters, "r.record_date")
    clauses += date_clauses
    parameters += date_parameters

    if filters.get("district_id"):
        clauses.append("r.district_id = ?")
        parameters.append(filters["district_id"])
    if filters.get("category"):
        joins = "JOIN fruits fh ON fh.id = r.fruit_id"
        clauses.append("fh.category = ?")
        parameters.append(filters["category"])
    if filters.get("fruit_id"):
        clauses.append("r.fruit_id = ?")
        parameters.append(filters["fruit_id"])

    return joins, " AND ".join(clauses), parameters


def _manual_clause(filters):
    """Build the WHERE fragment for ``wastage_records``."""
    joins = ""
    clauses = ["1 = 1"]
    parameters = []

    date_clauses, date_parameters = _date_order(filters, "w.wastage_date")
    clauses += date_clauses
    parameters += date_parameters

    if filters.get("district_id"):
        clauses.append("w.district_id = ?")
        parameters.append(filters["district_id"])
    if filters.get("category"):
        joins = "JOIN fruits fm ON fm.id = w.fruit_id"
        clauses.append("fm.category = ?")
        parameters.append(filters["category"])
    if filters.get("fruit_id"):
        clauses.append("w.fruit_id = ?")
        parameters.append(filters["fruit_id"])

    return joins, " AND ".join(clauses), parameters


def _combined(filters):
    """Return ``(sql, parameters)`` holding the ``wastage`` common table expression.

    ``wastage`` has one row per wastage record and carries both the damaged
    quantity and its cost per kilogram, so callers only need to add the columns
    they want, an optional grouping and an ORDER BY.

    The ``record_source`` filter decides which of the two sources take part.
    """
    source = filters.get("record_source")
    include_history = source in (None, "", HISTORICAL)
    include_manual = source in (None, "", MANUAL)

    parts = []
    parameters = []

    if include_history:
        hist_joins, hist_where, hist_parameters = _history_clause(filters, only_damaged=True)
        parts.append(
            f"""
        SELECT NULL                 AS record_id,
               r.record_date        AS wastage_date,
               r.district_id        AS district_id,
               r.fruit_id           AS fruit_id,
               r.damaged_quantity   AS damaged_quantity,
               CASE WHEN r.damaged_quantity > 0
                    THEN r.wastage_cost / r.damaged_quantity
                    ELSE 0.0 END    AS cost_per_kg,
               r.wastage_cost       AS wastage_cost,
               NULL                 AS reason,
               '{HISTORICAL}'       AS record_source
        FROM daily_records r
        {hist_joins}
        WHERE {hist_where}
        """
        )
        parameters += hist_parameters

    if include_manual:
        manual_joins, manual_where, manual_parameters = _manual_clause(filters)
        parts.append(
            f"""
        SELECT w.id                 AS record_id,
               w.wastage_date       AS wastage_date,
               w.district_id        AS district_id,
               w.fruit_id           AS fruit_id,
               w.damaged_quantity   AS damaged_quantity,
               w.cost_per_kg        AS cost_per_kg,
               w.wastage_cost       AS wastage_cost,
               w.reason             AS reason,
               '{MANUAL}'           AS record_source
        FROM wastage_records w
        {manual_joins}
        WHERE {manual_where}
        """
        )
        parameters += manual_parameters

    if not parts:
        # A filter that excludes both sources still has to produce a usable,
        # empty result set instead of invalid SQL.
        parts.append(
            """
        SELECT NULL AS record_id, NULL AS wastage_date, NULL AS district_id,
               NULL AS fruit_id, 0.0 AS damaged_quantity, 0.0 AS cost_per_kg,
               0.0 AS wastage_cost, NULL AS reason, NULL AS record_source
        WHERE 1 = 0
        """
        )

    sql = "WITH wastage AS (\n" + "\nUNION ALL\n".join(parts) + "\n)\n"
    return sql, parameters


def available_stock(filters):
    """Total available stock in the selection, read from the historical dataset.

    Available stock is only ever recorded in the historical dataset, so this is
    the denominator of the wastage percentage whatever the record-source filter
    is. Manual wastage rows carry no stock figure and nothing is invented for
    them.
    """
    joins, where, parameters = _history_clause(filters)

    row = get_db().execute(
        f"SELECT COALESCE(SUM(r.available_stock), 0) AS available_stock "
        f"FROM daily_records r {joins} WHERE {where}",
        parameters,
    ).fetchone()

    return row["available_stock"]


def data_bounds():
    """Return the stored date range and how many manual wastage rows exist."""
    db = get_db()

    historical = db.execute(
        "SELECT MIN(record_date) AS first_date, MAX(record_date) AS last_date "
        "FROM daily_records WHERE record_source = ?",
        (HISTORICAL,),
    ).fetchone()

    manual = db.execute(
        "SELECT MIN(wastage_date) AS first_date, MAX(wastage_date) AS last_date, "
        "       COUNT(*) AS manual_rows "
        "FROM wastage_records"
    ).fetchone()

    first_dates = [value for value in (historical["first_date"], manual["first_date"]) if value]
    last_dates = [value for value in (historical["last_date"], manual["last_date"]) if value]

    return {
        "first_date": min(first_dates) if first_dates else None,
        "last_date": max(last_dates) if last_dates else None,
        "manual_rows": manual["manual_rows"] or 0,
    }


def summary(filters):
    """Damaged quantity and wastage cost across every matching record."""
    sql, parameters = _combined(filters)

    row = get_db().execute(
        sql
        + """
        SELECT COUNT(*)                          AS records,
               COALESCE(SUM(damaged_quantity), 0) AS damaged_quantity,
               COALESCE(SUM(wastage_cost), 0)     AS wastage_cost
        FROM wastage
        """,
        parameters,
    ).fetchone()

    result = dict(row)
    result["available_stock"] = available_stock(filters)
    return result


def source_totals(filters):
    """Per-source totals, so historical and manual wastage stay distinguishable."""
    sql, parameters = _combined(filters)

    rows = get_db().execute(
        sql
        + """
        SELECT record_source,
               COUNT(*)                          AS records,
               COALESCE(SUM(damaged_quantity), 0) AS damaged_quantity,
               COALESCE(SUM(wastage_cost), 0)     AS wastage_cost
        FROM wastage
        GROUP BY record_source
        ORDER BY record_source
        """,
        parameters,
    ).fetchall()

    return {row["record_source"]: dict(row) for row in rows}


def district_summary(filters):
    """Damaged quantity and wastage cost per district, highest damage first."""
    sql, parameters = _combined(filters)

    return get_db().execute(
        sql
        + """
        SELECT d.name                                AS label,
               COUNT(*)                              AS records,
               COALESCE(SUM(damaged_quantity), 0)    AS damaged_quantity,
               COALESCE(SUM(wastage_cost), 0)        AS wastage_cost
        FROM wastage
        JOIN districts d ON d.id = district_id
        GROUP BY district_id
        ORDER BY damaged_quantity DESC, label
        """,
        parameters,
    ).fetchall()


def category_summary(filters):
    """Damaged quantity and wastage cost per fruit category."""
    sql, parameters = _combined(filters)

    return get_db().execute(
        sql
        + """
        SELECT f.category                            AS label,
               COUNT(*)                              AS records,
               COALESCE(SUM(damaged_quantity), 0)    AS damaged_quantity,
               COALESCE(SUM(wastage_cost), 0)        AS wastage_cost
        FROM wastage
        JOIN fruits f ON f.id = fruit_id
        GROUP BY f.category
        ORDER BY damaged_quantity DESC, label
        """,
        parameters,
    ).fetchall()


def fruit_summary(filters, limit=None):
    """Damaged quantity and wastage cost per fruit, highest damage first."""
    sql, parameters = _combined(filters)

    query = sql + """
        SELECT f.name                                AS label,
               f.category                            AS category,
               COUNT(*)                              AS records,
               COALESCE(SUM(damaged_quantity), 0)    AS damaged_quantity,
               COALESCE(SUM(wastage_cost), 0)        AS wastage_cost
        FROM wastage
        JOIN fruits f ON f.id = fruit_id
        GROUP BY fruit_id
        ORDER BY damaged_quantity DESC, label
    """

    if limit is not None:
        query += " LIMIT ?"
        parameters = parameters + [limit]

    return get_db().execute(query, parameters).fetchall()


def period_summary(filters, period, limit=None):
    """Wastage grouped by day, month or year.

    ``period`` is one of ``day``, ``month`` or ``year``. It is an internal
    constant chosen by the caller, never user input, so it can be written into
    the ``strftime`` pattern safely. ``limit`` keeps only the newest rows, which
    is how the daily trend stays readable across a multi-year selection.
    """
    formats = {"day": None, "month": "%Y-%m", "year": "%Y"}

    if period == "day":
        label = "wastage_date"
    else:
        label = f"strftime('{formats[period]}', wastage_date)"

    sql, parameters = _combined(filters)

    query = sql + f"""
        SELECT {label}                            AS label,
               COUNT(*)                           AS records,
               COALESCE(SUM(damaged_quantity), 0) AS damaged_quantity,
               COALESCE(SUM(wastage_cost), 0)     AS wastage_cost
        FROM wastage
        GROUP BY label
        ORDER BY label
    """

    # The newest periods are the interesting ones, but they must still be shown
    # in date order, so the newest rows are selected first and reversed after.
    ordered = limit is not None and period == "day"

    if ordered:
        query = query.replace("ORDER BY label", "ORDER BY label DESC LIMIT ?")
        parameters = parameters + [limit]

    rows = [dict(row) for row in get_db().execute(query, parameters).fetchall()]

    if ordered:
        rows.reverse()

    return rows


def count_wastage(filters):
    """Number of wastage records matching the filters."""
    sql, parameters = _combined(filters)

    row = get_db().execute(
        sql + "SELECT COUNT(*) AS total FROM wastage",
        parameters,
    ).fetchone()

    return row["total"]


def list_wastage(filters, limit, offset):
    """One page of wastage records, newest day first."""
    sql, parameters = _combined(filters)

    return get_db().execute(
        sql
        + """
        SELECT record_id, wastage_date, district_id, fruit_id,
               damaged_quantity, cost_per_kg, wastage_cost,
               reason, record_source,
               d.name AS district_name,
               f.name AS fruit_name,
               f.category AS fruit_category
        FROM wastage
        JOIN districts d ON d.id = district_id
        JOIN fruits f    ON f.id = fruit_id
        ORDER BY wastage_date DESC, d.name, f.name
        LIMIT ? OFFSET ?
        """,
        parameters + [limit, offset],
    ).fetchall()


# ---------------------------------------------------------------------------
# Manually recorded wastage
# ---------------------------------------------------------------------------
_MANUAL_COLUMNS = """
    w.id               AS id,
    w.wastage_date     AS wastage_date,
    w.district_id      AS district_id,
    w.fruit_id         AS fruit_id,
    w.damaged_quantity AS damaged_quantity,
    w.cost_per_kg      AS cost_per_kg,
    w.wastage_cost     AS wastage_cost,
    w.reason           AS reason,
    w.created_at       AS created_at,
    d.name             AS district_name,
    f.name             AS fruit_name,
    f.category         AS fruit_category
"""


def get_manual_record(record_id):
    """One manual wastage record with its district and fruit names."""
    return get_db().execute(
        f"SELECT {_MANUAL_COLUMNS} "
        "FROM wastage_records w "
        "JOIN districts d ON d.id = w.district_id "
        "JOIN fruits f    ON f.id = w.fruit_id "
        "WHERE w.id = ?",
        (record_id,),
    ).fetchone()


def manual_record_exists(wastage_date, district_id, fruit_id, exclude_id=None):
    """Return ``True`` when a manual entry already covers this fruit and day.

    ``exclude_id`` lets an edit ignore the record being changed.
    """
    sql = (
        "SELECT id FROM wastage_records "
        "WHERE wastage_date = ? AND district_id = ? AND fruit_id = ?"
    )
    parameters = [wastage_date, district_id, fruit_id]

    if exclude_id is not None:
        sql += " AND id <> ?"
        parameters.append(exclude_id)

    return get_db().execute(sql, parameters).fetchone() is not None


def create_manual_record(wastage_date, district_id, fruit_id,
                         damaged_quantity, cost_per_kg, wastage_cost, reason):
    """Insert one manual wastage record and return its new id.

    The insert runs inside a transaction: the row is committed on success and
    rolled back if the database refuses it, so a rejected entry leaves no
    half-written record behind.
    """
    db = get_db()
    try:
        cursor = db.execute(
            "INSERT INTO wastage_records "
            "(wastage_date, district_id, fruit_id, damaged_quantity, "
            " cost_per_kg, wastage_cost, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (wastage_date, district_id, fruit_id,
             damaged_quantity, cost_per_kg, wastage_cost, reason),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return cursor.lastrowid


def update_manual_record(record_id, wastage_date, district_id, fruit_id,
                         damaged_quantity, cost_per_kg, wastage_cost, reason):
    """Update one manual wastage record and report whether a row changed."""
    db = get_db()
    try:
        cursor = db.execute(
            "UPDATE wastage_records "
            "SET wastage_date = ?, district_id = ?, fruit_id = ?, "
            "    damaged_quantity = ?, cost_per_kg = ?, wastage_cost = ?, reason = ? "
            "WHERE id = ?",
            (wastage_date, district_id, fruit_id,
             damaged_quantity, cost_per_kg, wastage_cost, reason, record_id),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return cursor.rowcount > 0
