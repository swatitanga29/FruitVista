"""Database access for manually entered FruitVista sales records.

Manual sales are stored in the existing ``daily_records`` table with
``record_source = 'manual'``. That table was designed for exactly this: it
already carries ``quantity_sold``, ``selling_price`` and ``sales_revenue``,
and its ``record_source`` column keeps imported historical CSV data and
administrator entries in the same place without mixing them up.

Every query here is limited to ``record_source = 'manual'``, so imported
historical rows can never be listed, edited or deleted from the Sales Entry
module. Values are always passed as ``?`` placeholders - nothing is formatted
into the SQL string.
"""

from database import get_db

RECORD_SOURCE = "manual"

# Internal, fixed column list shared by the two read queries below. It is a
# module constant and never contains user input.
_SELECT_COLUMNS = """
    r.id            AS id,
    r.record_date   AS record_date,
    r.district_id   AS district_id,
    r.fruit_id      AS fruit_id,
    r.record_source AS record_source,
    r.quantity_sold AS quantity_sold,
    r.selling_price AS selling_price,
    r.sales_revenue AS sales_revenue,
    r.created_at    AS created_at,
    r.updated_at    AS updated_at,
    d.name          AS district_name,
    f.name          AS fruit_name,
    f.category      AS fruit_category
"""

_SELECT_FROM = """
    FROM daily_records r
    JOIN districts d ON d.id = r.district_id
    JOIN fruits f    ON f.id = r.fruit_id
"""


def list_districts():
    """Return the five supported districts, ordered by name."""
    return get_db().execute("SELECT id, name FROM districts ORDER BY name").fetchall()


def list_fruits():
    """Return the 30 approved fruits with their category."""
    return get_db().execute(
        "SELECT id, name, category FROM fruits ORDER BY category, name"
    ).fetchall()


def list_categories():
    """Return the fruit categories that actually exist in the catalogue."""
    rows = get_db().execute(
        "SELECT DISTINCT category FROM fruits ORDER BY category"
    ).fetchall()
    return [row["category"] for row in rows]


def record_exists_for(record_date, district_id, fruit_id, exclude_id=None):
    """Return ``True`` when a manual record already covers this fruit and day.

    ``exclude_id`` lets an edit ignore the record being changed.
    """
    sql = (
        "SELECT id FROM daily_records "
        "WHERE record_date = ? AND district_id = ? AND fruit_id = ? "
        "AND record_source = ?"
    )
    parameters = [record_date, district_id, fruit_id, RECORD_SOURCE]

    if exclude_id is not None:
        sql += " AND id <> ?"
        parameters.append(exclude_id)

    return get_db().execute(sql, parameters).fetchone() is not None


def list_records(date_from=None, date_to=None, district_id=None, category=None, fruit_id=None):
    """Return the manual sales records matching the given filters.

    Every filter is optional; an empty value is simply left out of the query.
    Results are newest first.
    """
    sql = f"SELECT {_SELECT_COLUMNS} {_SELECT_FROM} WHERE r.record_source = ?"
    parameters = [RECORD_SOURCE]

    if date_from:
        sql += " AND r.record_date >= ?"
        parameters.append(date_from)
    if date_to:
        sql += " AND r.record_date <= ?"
        parameters.append(date_to)
    if district_id:
        sql += " AND r.district_id = ?"
        parameters.append(district_id)
    if category:
        sql += " AND f.category = ?"
        parameters.append(category)
    if fruit_id:
        sql += " AND r.fruit_id = ?"
        parameters.append(fruit_id)

    sql += " ORDER BY r.record_date DESC, r.id DESC"

    return get_db().execute(sql, parameters).fetchall()


def get_record(record_id):
    """Return one manual sales record, or ``None`` when it does not exist."""
    return get_db().execute(
        f"SELECT {_SELECT_COLUMNS} {_SELECT_FROM} WHERE r.id = ? AND r.record_source = ?",
        (record_id, RECORD_SOURCE),
    ).fetchone()


def create_record(record_date, district_id, fruit_id, quantity_sold, selling_price):
    """Insert one manual sales record and return its new id."""
    db = get_db()
    cursor = db.execute(
        "INSERT INTO daily_records "
        "(record_date, district_id, fruit_id, record_source, "
        " quantity_sold, selling_price, sales_revenue) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            record_date,
            district_id,
            fruit_id,
            RECORD_SOURCE,
            quantity_sold,
            selling_price,
            round(quantity_sold * selling_price, 2),
        ),
    )
    db.commit()
    return cursor.lastrowid


def update_record(record_id, record_date, district_id, fruit_id, quantity_sold, selling_price):
    """Update one manual sales record.

    The ``record_source = 'manual'`` condition guarantees that an imported
    historical row can never be modified through this module.
    """
    db = get_db()
    db.execute(
        "UPDATE daily_records "
        "SET record_date = ?, district_id = ?, fruit_id = ?, "
        "    quantity_sold = ?, selling_price = ?, sales_revenue = ?, "
        "    updated_at = datetime('now', 'localtime') "
        "WHERE id = ? AND record_source = ?",
        (
            record_date,
            district_id,
            fruit_id,
            quantity_sold,
            selling_price,
            round(quantity_sold * selling_price, 2),
            record_id,
            RECORD_SOURCE,
        ),
    )
    db.commit()


def delete_record(record_id):
    """Delete one manual sales record. Returns the number of rows removed."""
    db = get_db()
    cursor = db.execute(
        "DELETE FROM daily_records WHERE id = ? AND record_source = ?",
        (record_id, RECORD_SOURCE),
    )
    db.commit()
    return cursor.rowcount


def count_records():
    """Return how many manual sales records are stored."""
    return get_db().execute(
        "SELECT COUNT(*) AS total FROM daily_records WHERE record_source = ?",
        (RECORD_SOURCE,),
    ).fetchone()["total"]
