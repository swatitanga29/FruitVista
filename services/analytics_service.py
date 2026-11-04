"""Filter handling and assembly for the Sales Analytics dashboard.

The service turns the query string into a validated filter set, asks
:mod:`models.analytics` for the aggregated numbers, and returns one payload
that the dashboard page and its JSON API both use. Nothing here writes to the
database - the analytics module is read-only.

Comparisons use whatever fields the dataset actually provides, and where a
figure does not exist the dashboard says so instead of showing a zero.
"""

from datetime import datetime

from models import analytics
from models import sales

RECORD_SOURCE_LABELS = {
    analytics.RECORD_SOURCE_HISTORICAL: "Historical (synthetic dataset)",
    analytics.RECORD_SOURCE_MANUAL: "Manual entry",
}

# How many fruits the chart panels show.
PERFORMANCE_LIMIT = 10
TOP_LIMIT = 5
BOTTOM_LIMIT = 5

SYNTHETIC_NOTICE = (
    "Historical figures come from the synthetic FruitVista dataset. They are "
    "generated sample data, not verified real market observations."
)


def filter_options():
    """Return the dropdown choices for the filter bar."""
    return {
        "districts": [dict(row) for row in sales.list_districts()],
        "fruits": [dict(row) for row in sales.list_fruits()],
        "categories": sales.list_categories(),
    }


def default_filters():
    """Return the filters used when the dashboard is opened with no query string."""
    bounds = analytics.data_bounds()

    return {
        "date_from": bounds["first_date"] or "",
        "date_to": bounds["last_date"] or "",
        "district_id": None,
        "category": "",
        "fruit_id": None,
        "record_source": analytics.RECORD_SOURCE_ALL,
    }


def _read_date(value, label, errors):
    """Validate one ``YYYY-MM-DD`` value; return it or an empty string."""
    value = (value or "").strip()

    if not value:
        return ""

    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        errors.append(f"{label} must be a real date in YYYY-MM-DD format.")
        return ""

    if parsed.strftime("%Y-%m-%d") != value:
        errors.append(f"{label} must be a real date in YYYY-MM-DD format.")
        return ""

    return value


def _read_id(value, allowed, label, errors):
    """Validate an optional integer id against a set of known ids."""
    value = (value or "").strip()

    if not value:
        return None

    if not value.isdigit() or int(value) not in allowed:
        errors.append(f"{label} is not one of the supported values.")
        return None

    return int(value)


def parse_filters(args):
    """Turn a request's query string into a validated filter dict.

    Returns ``(filters, errors)``. Unknown or malformed values fall back to
    "no filter" and are reported in ``errors``.
    """
    base = default_filters()
    errors = []

    filters = {
        "date_from": _read_date(args.get("date_from"), "Start date", errors),
        "date_to": _read_date(args.get("date_to"), "End date", errors),
        "district_id": _read_id(
            args.get("district_id"),
            {row["id"] for row in sales.list_districts()},
            "District",
            errors,
        ),
        "fruit_id": _read_id(
            args.get("fruit_id"),
            {row["id"] for row in sales.list_fruits()},
            "Fruit",
            errors,
        ),
        "category": (args.get("category") or "").strip(),
        "record_source": (args.get("record_source") or analytics.RECORD_SOURCE_ALL).strip(),
    }

    if not filters["date_from"]:
        filters["date_from"] = base["date_from"]
    if not filters["date_to"]:
        filters["date_to"] = base["date_to"]

    if filters["date_from"] and filters["date_to"] and filters["date_from"] > filters["date_to"]:
        errors.append("The start date must not be after the end date.")
        filters["date_from"], filters["date_to"] = filters["date_to"], filters["date_from"]

    if filters["category"] and filters["category"] not in sales.list_categories():
        errors.append("Fruit category is not one of the supported values.")
        filters["category"] = ""

    if filters["record_source"] not in (
        analytics.RECORD_SOURCE_ALL,
        analytics.RECORD_SOURCE_HISTORICAL,
        analytics.RECORD_SOURCE_MANUAL,
    ):
        errors.append("Record source must be All, Historical or Manual.")
        filters["record_source"] = analytics.RECORD_SOURCE_ALL

    return filters, errors


def _notes(summary, granularity, split):
    """Explain how each figure was produced and what it does not cover."""
    notes = [
        "Revenue is quantity sold \u00d7 selling price per kg. Quantity sold is "
        "the sum of the stored sale quantities in kilograms.",
        "Total Profit is the sum of net profit (gross profit less operating "
        "expenses) and Total Wastage is the sum of damaged quantity in "
        "kilograms.",
        SYNTHETIC_NOTICE,
    ]

    excluded = summary["profit_excluded_records"]

    if not summary["profit_available"]:
        notes.append(
            "Total Profit and Total Wastage are not available for this "
            "selection. A manual entry stores the sale date, district, fruit, "
            "quantity, selling price and revenue only - it carries no profit, "
            "wastage or stock fields. Nothing is shown as zero because nothing "
            "was recorded."
        )
    elif excluded:
        notes.append(
            "Total Profit and Total Wastage cover the "
            f"{summary['profit_records']:,} historical records in this "
            f"selection. The other {excluded:,} manual record(s) are excluded "
            "from those two totals because a manual entry does not record "
            "profit or wastage."
        )

    if granularity == "weekly":
        notes.append(
            "The daily trend is grouped into weekly buckets because the "
            "selected range spans more than "
            f"{analytics.MAX_DAILY_POINTS} days. Each point is the week "
            "starting on the date shown."
        )

    if summary["records"] and len(split) > 1:
        parts = ", ".join(
            f"{RECORD_SOURCE_LABELS.get(row['label'], row['label'])}: "
            f"{row['records']:,} records"
            for row in split
        )
        notes.append("Records in this selection - " + parts + ".")

    return notes


def build_dashboard(filters):
    """Return every number the dashboard needs, already aggregated."""
    summary = analytics.summary(filters)
    split = analytics.source_split(filters)
    points, granularity = analytics.daily_series(filters)

    return {
        "ok": True,
        "filters": {
            **filters,
            "district_name": next(
                (
                    row["name"]
                    for row in sales.list_districts()
                    if row["id"] == filters["district_id"]
                ),
                "",
            ),
            "fruit_name": next(
                (
                    row["name"]
                    for row in sales.list_fruits()
                    if row["id"] == filters["fruit_id"]
                ),
                "",
            ),
        },
        "bounds": analytics.data_bounds(),
        "summary": summary,
        "source_split": [
            {
                **row,
                "label": RECORD_SOURCE_LABELS.get(row["label"], row["label"]),
                "record_source": row["label"],
            }
            for row in split
        ],
        "daily": {"granularity": granularity, "points": points},
        "monthly": analytics.monthly_series(filters),
        "yearly": analytics.yearly_series(filters),
        "district": analytics.district_revenue(filters),
        "category": analytics.category_sales(filters),
        "fruit": analytics.fruit_ranking(filters, "revenue", PERFORMANCE_LIMIT),
        "top_fruits": analytics.fruit_ranking(filters, "quantity_desc", TOP_LIMIT),
        "bottom_fruits": analytics.fruit_ranking(filters, "quantity_asc", BOTTOM_LIMIT),
        "notes": _notes(summary, granularity, split),
    }
