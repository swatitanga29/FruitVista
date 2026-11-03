"""SQL aggregation for the administrator Sales Analytics dashboard.

Every figure the dashboard shows is calculated here, inside SQLite. The
browser only ever receives the aggregated series - a few hundred numbers - so
the 265,050 stored rows are never sent to the client.

Definitions used, all verified against the stored data:

``revenue``
    ``quantity_sold * selling_price`` - the formula agreed for FruitVista.
    The stored ``sales_revenue`` column holds the same value rounded to two
    decimals per row, so summing that column can differ from this by a few
    paise over the whole dataset.
``quantity``
    ``SUM(quantity_sold)`` in kilograms.
``profit``
    ``SUM(net_profit)``, where the dataset defines
    ``net_profit = gross_profit - operating_expenses`` and
    ``gross_profit = revenue - cost_of_goods_sold``.
``wastage``
    ``SUM(damaged_quantity)`` in kilograms. ``wastage_cost`` is the same
    quantity valued at the purchase price.

Only the historical dataset carries profit and wastage columns. A manual
entry records the sale date, district, fruit, quantity, selling price and
revenue - nothing else - so those rows are excluded from the profit and
wastage totals rather than counted as zero. :func:`summary` reports how many
records were included and how many were excluded for that reason.

Every value is bound with ``?`` placeholders; only the fixed, module-level
SQL fragments below are ever formatted into a statement.
"""

from database import get_db

RECORD_SOURCE_HISTORICAL = "historical"
RECORD_SOURCE_MANUAL = "manual"
RECORD_SOURCE_ALL = "all"
RECORD_SOURCES = (RECORD_SOURCE_HISTORICAL, RECORD_SOURCE_MANUAL)

# The single definition of revenue, shared by every card and chart.
REVENUE = "r.quantity_sold * r.selling_price"

# Longest daily series the dashboard draws before it switches to weekly
# buckets. 400 points is roughly thirteen months.
MAX_DAILY_POINTS = 400

# Allowed orderings for :func:`fruit_ranking`. Fixed strings only - nothing
# from a request is ever placed in an ORDER BY clause.
RANKINGS = {
    "revenue": "revenue DESC",
    "quantity_desc": "quantity DESC",
    "quantity_asc": "quantity ASC",
}


def _restrict(filters):
    """Translate the dashboard filters into SQL.

    Returns ``(joins, where, parameters)``. The ``?`` placeholders in ``where``
    line up with ``parameters``.
    """
    joins = ""
    clauses = []
    parameters = []

    # District and fruit are compared through their ids, so no join is needed.
    if filters.get("district_id"):
        clauses.append("r.district_id = ?")
        parameters.append(filters["district_id"])
    if filters.get("fruit_id"):
        clauses.append("r.fruit_id = ?")
        parameters.append(filters["fruit_id"])
    if filters.get("category"):
        # "ff" is used deliberately: it cannot collide with the "f" alias the
        # grouping queries below add for their own output columns.
        joins += " JOIN fruits ff ON ff.id = r.fruit_id"
        clauses.append("ff.category = ?")
        parameters.append(filters["category"])

    if filters.get("record_source") in RECORD_SOURCES:
        clauses.append("r.record_source = ?")
        parameters.append(filters["record_source"])

    if filters.get("date_from"):
        clauses.append("r.record_date >= ?")
        parameters.append(filters["date_from"])
    if filters.get("date_to"):
        clauses.append("r.record_date <= ?")
        parameters.append(filters["date_to"])

    where = " AND ".join(clauses) if clauses else "1 = 1"
    return joins, where, parameters


def _historical_subset(filters):
    """The historical rows *inside* the current selection.

    Profit and wastage are only recorded on the imported historical rows, so
    those two totals are always taken from this subset. A manual-only
    selection contains no historical row at all, and ``None`` is returned so
    the caller reports the totals as unavailable instead of borrowing the
    figures from records the user did not select.
    """
    if filters.get("record_source") == RECORD_SOURCE_MANUAL:
        return None

    return dict(filters, record_source=RECORD_SOURCE_HISTORICAL)


def data_bounds():
    """Return the stored date range and how many rows came from each source."""
    row = get_db().execute(
        "SELECT MIN(record_date) AS first_date, "
        "       MAX(record_date) AS last_date, "
        "       SUM(CASE WHEN record_source = 'historical' THEN 1 ELSE 0 END) "
        "           AS historical_rows, "
        "       SUM(CASE WHEN record_source = 'manual' THEN 1 ELSE 0 END) "
        "           AS manual_rows "
        "FROM daily_records"
    ).fetchone()

    return {
        "first_date": row["first_date"],
        "last_date": row["last_date"],
        "historical_rows": row["historical_rows"] or 0,
        "manual_rows": row["manual_rows"] or 0,
    }


def summary(filters):
    """Return the five dashboard totals for the selected filters."""
    db = get_db()
    joins, where, parameters = _restrict(filters)

    totals = db.execute(
        "SELECT COUNT(*) AS records, "
        "       COALESCE(SUM(r.quantity_sold), 0) AS quantity, "
        f"      COALESCE(SUM({REVENUE}), 0) AS revenue "
        f"FROM daily_records r {joins} WHERE {where}",
        parameters,
    ).fetchone()

    result = {
        "records": totals["records"],
        "quantity": totals["quantity"],
        "revenue": totals["revenue"],
        "profit": None,
        "wastage_kg": None,
        "profit_available": False,
        "profit_records": 0,
        "profit_excluded_records": totals["records"],
    }

    if not totals["records"]:
        return result

    # Profit and wastage exist only on the historical rows. Asking for the
    # historical subset of this selection keeps "all" and "historical"
    # consistent, and lets a "manual only" selection be reported as
    # unavailable instead of as zero.
    subset = _historical_subset(filters)

    if subset is None:
        return result

    extra_joins, extra_where, extra_parameters = _restrict(subset)
    coverage = db.execute(
        "SELECT COUNT(*) AS records, "
        "       COALESCE(SUM(r.net_profit), 0) AS profit, "
        "       COALESCE(SUM(r.damaged_quantity), 0) AS wastage_kg "
        f"FROM daily_records r {extra_joins} WHERE {extra_where}",
        extra_parameters,
    ).fetchone()

    result["profit_records"] = coverage["records"]
    result["profit_excluded_records"] = totals["records"] - coverage["records"]

    if coverage["records"]:
        result["profit"] = coverage["profit"]
        result["wastage_kg"] = coverage["wastage_kg"]
        result["profit_available"] = True

    return result


def source_split(filters):
    """Return records, quantity and revenue per record source.

    This is what keeps the synthetic historical dataset and the manually
    entered sales visibly separate on the dashboard.
    """
    joins, where, parameters = _restrict(filters)

    rows = get_db().execute(
        "SELECT r.record_source AS label, "
        "       COUNT(*) AS records, "
        "       COALESCE(SUM(r.quantity_sold), 0) AS quantity, "
        f"      COALESCE(SUM({REVENUE}), 0) AS revenue "
        f"FROM daily_records r {joins} WHERE {where} "
        "GROUP BY r.record_source ORDER BY r.record_source",
        parameters,
    ).fetchall()

    return [dict(row) for row in rows]


def daily_series(filters):
    """Return the sales trend, one point per day or per week.

    A range longer than :data:`MAX_DAILY_POINTS` days is grouped into weekly
    buckets (each label is the Monday of that week) so the line stays
    readable. Returns ``(points, granularity)``.
    """
    db = get_db()
    joins, where, parameters = _restrict(filters)

    days = db.execute(
        f"SELECT COUNT(DISTINCT r.record_date) AS days "
        f"FROM daily_records r {joins} WHERE {where}",
        parameters,
    ).fetchone()["days"]

    if days > MAX_DAILY_POINTS:
        bucket = "date(r.record_date, 'weekday 0', '-6 days')"
        granularity = "weekly"
    else:
        bucket = "r.record_date"
        granularity = "daily"

    rows = db.execute(
        f"SELECT {bucket} AS label, "
        "       COALESCE(SUM(r.quantity_sold), 0) AS quantity, "
        f"      COALESCE(SUM({REVENUE}), 0) AS revenue "
        f"FROM daily_records r {joins} WHERE {where} "
        "GROUP BY label ORDER BY label",
        parameters,
    ).fetchall()

    return [dict(row) for row in rows], granularity


def monthly_series(filters):
    """Return sales per calendar month, oldest first."""
    joins, where, parameters = _restrict(filters)

    rows = get_db().execute(
        "SELECT strftime('%Y-%m', r.record_date) AS label, "
        "       COALESCE(SUM(r.quantity_sold), 0) AS quantity, "
        f"      COALESCE(SUM({REVENUE}), 0) AS revenue "
        f"FROM daily_records r {joins} WHERE {where} "
        "GROUP BY label ORDER BY label",
        parameters,
    ).fetchall()

    return [dict(row) for row in rows]


def seasonal_series(filters):
    """Return sales per calendar month, January to December.

    Every year in the selection is folded onto the twelve calendar months, so
    the result shows the seasonal shape rather than one chronological run.
    ``average_price`` is revenue divided by quantity sold for that calendar
    month, and is ``None`` when nothing was sold in it.
    """
    joins, where, parameters = _restrict(filters)

    rows = get_db().execute(
        "SELECT strftime('%m', r.record_date) AS label, "
        "       COUNT(*) AS records, "
        "       COALESCE(SUM(r.quantity_sold), 0) AS quantity, "
        f"      COALESCE(SUM({REVENUE}), 0) AS revenue "
        f"FROM daily_records r {joins} WHERE {where} "
        "GROUP BY label ORDER BY label",
        parameters,
    ).fetchall()

    series = []
    for row in rows:
        point = dict(row)
        point["average_price"] = (
            point["revenue"] / point["quantity"] if point["quantity"] else None
        )
        series.append(point)

    return series


def yearly_series(filters):
    """Return sales per calendar year, oldest first, with net profit.

    Net profit is only recorded on the historical rows, so a manual-only
    selection reports it as unavailable (``None``) for every year.
    """
    joins, where, parameters = _restrict(filters)
    subset = _historical_subset(filters)

    rows = get_db().execute(
        "SELECT strftime('%Y', r.record_date) AS label, "
        "       COALESCE(SUM(r.quantity_sold), 0) AS quantity, "
        f"      COALESCE(SUM({REVENUE}), 0) AS revenue "
        f"FROM daily_records r {joins} WHERE {where} "
        "GROUP BY label ORDER BY label",
        parameters,
    ).fetchall()

    profit_by_year = {}

    if subset is not None:
        profit_joins, profit_where, profit_parameters = _restrict(subset)
        profit_rows = get_db().execute(
            "SELECT strftime('%Y', r.record_date) AS label, "
            "       COALESCE(SUM(r.net_profit), 0) AS profit "
            f"FROM daily_records r {profit_joins} WHERE {profit_where} "
            "GROUP BY label",
            profit_parameters,
        ).fetchall()
        profit_by_year = {row["label"]: row["profit"] for row in profit_rows}

    series = []
    for row in rows:
        point = dict(row)
        point["profit"] = profit_by_year.get(point["label"])
        series.append(point)

    return series


def district_revenue(filters):
    """Return revenue and quantity per district, highest revenue first."""
    joins, where, parameters = _restrict(filters)

    rows = get_db().execute(
        "SELECT d.name AS label, "
        "       COALESCE(SUM(r.quantity_sold), 0) AS quantity, "
        f"      COALESCE(SUM({REVENUE}), 0) AS revenue "
        f"FROM daily_records r {joins} "
        "JOIN districts d ON d.id = r.district_id "
        f"WHERE {where} "
        "GROUP BY d.id ORDER BY revenue DESC",
        parameters,
    ).fetchall()

    return [dict(row) for row in rows]


def category_sales(filters):
    """Return revenue and quantity per fruit category, highest revenue first."""
    joins, where, parameters = _restrict(filters)

    rows = get_db().execute(
        "SELECT f.category AS label, "
        "       COALESCE(SUM(r.quantity_sold), 0) AS quantity, "
        f"      COALESCE(SUM({REVENUE}), 0) AS revenue "
        f"FROM daily_records r {joins} "
        "JOIN fruits f ON f.id = r.fruit_id "
        f"WHERE {where} "
        "GROUP BY f.category ORDER BY revenue DESC",
        parameters,
    ).fetchall()

    return [dict(row) for row in rows]


def fruit_ranking(filters, ranking="revenue", limit=10):
    """Return fruits ordered by revenue, or by quantity, best first.

    ``ranking`` must be one of the keys of :data:`RANKINGS`; anything else
    falls back to revenue. Fruits with no sales in the selected range are not
    listed, so a "lowest selling" result only covers fruits that were sold.
    """
    order = RANKINGS.get(ranking, RANKINGS["revenue"])
    joins, where, parameters = _restrict(filters)

    rows = get_db().execute(
        "SELECT f.name AS label, f.category AS category, "
        "       COALESCE(SUM(r.quantity_sold), 0) AS quantity, "
        f"      COALESCE(SUM({REVENUE}), 0) AS revenue "
        f"FROM daily_records r {joins} "
        "JOIN fruits f ON f.id = r.fruit_id "
        f"WHERE {where} "
        f"GROUP BY f.id ORDER BY {order}, f.name LIMIT ?",
        parameters + [limit],
    ).fetchall()

    return [dict(row) for row in rows]


def top_fruit_by_month(filters):
    """Return the best-selling fruit in each month of the selection.

    One row per calendar month carrying the fruit that sold the greatest
    quantity that month, used by the month-wise performance insight. The
    monthly totals themselves come from :func:`monthly_series`.
    """
    joins, where, parameters = _restrict(filters)

    query = f"""
    WITH monthly AS (
        SELECT strftime('%Y-%m', r.record_date) AS label,
               f.name        AS fruit,
               f.category    AS category,
               COALESCE(SUM(r.quantity_sold), 0) AS quantity,
               COALESCE(SUM({REVENUE}), 0)       AS revenue
        FROM daily_records r {joins}
        JOIN fruits f ON f.id = r.fruit_id
        WHERE {where}
        GROUP BY label, f.id
    ),
    ranked AS (
        SELECT label, fruit, category, quantity, revenue,
               ROW_NUMBER() OVER (
                   PARTITION BY label ORDER BY quantity DESC, fruit
               ) AS position
        FROM monthly
    )
    SELECT label, fruit, category, quantity, revenue
    FROM ranked
    WHERE position = 1
    ORDER BY label
    """

    return [dict(row) for row in get_db().execute(query, parameters).fetchall()]


def availability_vs_demand(filters, limit=10):
    """Return historical availability and demand per fruit, largest demand first.

    ``demand`` is the quantity sold and ``availability`` is the total available
    stock recorded for the fruit. Available stock is only ever recorded on the
    imported historical rows, so both figures are taken from the historical
    subset of the selection. A manual-only selection therefore reports nothing
    rather than borrowing figures from records the user did not select.
    """
    if filters.get("record_source") == RECORD_SOURCE_MANUAL:
        return []

    subset = dict(filters, record_source=RECORD_SOURCE_HISTORICAL)
    joins, where, parameters = _restrict(subset)

    rows = get_db().execute(
        "SELECT f.name AS label, f.category AS category, "
        "       COALESCE(SUM(r.quantity_sold), 0) AS demand, "
        "       COALESCE(SUM(r.available_stock), 0) AS availability "
        f"FROM daily_records r {joins} "
        "JOIN fruits f ON f.id = r.fruit_id "
        f"WHERE {where} "
        "GROUP BY f.id ORDER BY demand DESC, f.name LIMIT ?",
        parameters + [limit],
    ).fetchall()

    return [dict(row) for row in rows]
