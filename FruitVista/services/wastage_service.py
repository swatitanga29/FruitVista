"""Filter parsing, form validation and dashboard assembly for Wastage Management.

The module reads two sources (see ``models/wastage.py``) and never writes into
``daily_records``. All validation and all user-facing wording lives here so the
routes stay short and the pages show one consistent set of messages.
"""

import math
from datetime import datetime

from models import sales as sales_repo
from models import wastage as wastage_repo
from services import sales_service
from services.validators import clean_text

DATE_FORMAT = "%Y-%m-%d"

PAGE_SIZE = 25
RECENT_LIMIT = 8
DAILY_TREND_LIMIT = 14
TOP_FRUIT_LIMIT = 10
CHART_FRUIT_LIMIT = 12

# Guard rails that protect the database from accidental extra zeros.
MAX_QUANTITY = 100000.0
MAX_PRICE = 1000000.0
REASON_MAX = 200

# How a record is labelled in the table and the summary strips.
SOURCE_LABELS = {
    wastage_repo.HISTORICAL: "Historical",
    wastage_repo.MANUAL: "Manual",
}

SOURCE_HINTS = {
    wastage_repo.HISTORICAL: "from the imported synthetic dataset",
    wastage_repo.MANUAL: "recorded by an administrator",
}

SOURCE_CLASSES = {
    wastage_repo.HISTORICAL: "source-historical",
    wastage_repo.MANUAL: "source-manual",
}

SYNTHETIC_NOTICE = (
    "The historical damaged quantities and wastage costs come from the "
    "synthetic FruitVista dataset. They are generated sample data, not verified "
    "real warehouse losses."
)

PERCENTAGE_DENOMINATOR = (
    "Wastage percentage = damaged quantity / available stock x 100, where "
    "available stock is the total historical available stock for the same "
    "dates, districts, categories and fruits."
)

CALCULATION_NOTES = [
    "Wastage cost = damaged quantity x cost per kilogram. For a manual entry "
    "the server calculates the cost from the two values that were typed in and "
    "stores all three, so the figure on the page is the stored value.",
    PERCENTAGE_DENOMINATOR + " Available stock = opening stock + purchased "
    "quantity, the same figure the Inventory Management page publishes as Total "
    "Available Stock, which lets the two dashboards be read side by side.",
    "The percentage denominator always comes from the historical dataset, "
    "because available stock is only recorded there. A manual wastage entry "
    "adds to the damaged quantity but carries no stock figure of its own, so "
    "nothing is invented for it.",
    "Damaged quantities and wastage costs are read once from the historical "
    "dataset and once from the manual wastage records, then added together one "
    "time. Manual wastage lives in its own table, never in the historical rows, "
    "so the same loss can never be counted twice.",
    "For a historical row the cost per kilogram is worked out from the two "
    "stored numbers as wastage cost / damaged quantity. That is the same value "
    "as the purchase price per kilogram the dataset was built with, and it is "
    "shown as 0 for a row with no damage because no cost per kilogram exists "
    "for it.",
    "Rows marked 'Historical' come from the dataset. Rows marked 'Manual' were "
    "recorded in this module. The record-source filter lets each set be looked "
    "at on its own.",
]

RECORDS_NOTICE = (
    "The Wastage Records page lists one row per wastage record: the damaged "
    "quantity the dataset or an administrator recorded for one fruit, in one "
    "district, on one day. Historical rows with no damage are not listed, "
    "because there is no wastage to report for them."
)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
def filter_options():
    """Catalogue values used to build the filter controls."""
    return {
        "districts": sales_repo.list_districts(),
        "fruits": sales_repo.list_fruits(),
        "categories": sales_repo.list_categories(),
        "sources": [
            (source, SOURCE_LABELS[source]) for source in wastage_repo.SOURCES
        ],
    }


def default_filters():
    """The filters used when the page is first opened: the full stored range."""
    bounds = wastage_repo.data_bounds()

    return {
        "date_from": bounds["first_date"] or "",
        "date_to": bounds["last_date"] or "",
        "district_id": None,
        "category": "",
        "fruit_id": None,
        "record_source": "",
        "page": 1,
    }


def _read_date(value):
    """Return an ISO date, or ``None`` when the text is not a real date."""
    text = clean_text(value)
    if not text:
        return None

    try:
        return datetime.strptime(text, DATE_FORMAT).date().isoformat()
    except ValueError:
        return None


def _read_id(value, known_ids):
    """Return a known catalogue id, or ``None`` when the value is not usable."""
    text = clean_text(value)
    if not text.isdigit():
        return None
    if int(text) not in known_ids:
        return None

    return int(text)


def parse_filters(args):
    """Read the Wastage filters out of a query string.

    Returns ``(filters, errors)``. A value that cannot be trusted is dropped and
    reported instead of being used, so a hand-edited address cannot break the
    page or reach the database as anything but a bound parameter.
    """
    errors = []
    options = filter_options()

    district_ids = {row["id"] for row in options["districts"]}
    fruit_ids = {row["id"] for row in options["fruits"]}
    categories = set(options["categories"])
    sources = set(wastage_repo.SOURCES)

    bounds = wastage_repo.data_bounds()
    filters = default_filters()

    for field in ("date_from", "date_to"):
        raw = clean_text(args.get(field))
        if not raw:
            filters[field] = ""
            continue

        parsed = _read_date(raw)
        if parsed is None:
            errors.append(
                f"'{raw}' is not a valid date. Please use the YYYY-MM-DD format."
            )
            filters[field] = ""
        else:
            filters[field] = parsed

    if filters["date_from"] and filters["date_to"] and filters["date_from"] > filters["date_to"]:
        filters["date_from"], filters["date_to"] = filters["date_to"], filters["date_from"]
        errors.append("The start date was after the end date, so the two dates were swapped.")

    if not filters["date_from"]:
        filters["date_from"] = bounds["first_date"] or ""
    if not filters["date_to"]:
        filters["date_to"] = bounds["last_date"] or ""

    raw_district = clean_text(args.get("district_id"))
    if raw_district:
        filters["district_id"] = _read_id(raw_district, district_ids)
        if filters["district_id"] is None:
            errors.append("The selected district does not exist, so that filter was cleared.")

    raw_category = clean_text(args.get("category"))
    if raw_category:
        if raw_category in categories:
            filters["category"] = raw_category
        else:
            errors.append("The selected fruit category does not exist, so that filter was cleared.")

    raw_fruit = clean_text(args.get("fruit_id"))
    if raw_fruit:
        filters["fruit_id"] = _read_id(raw_fruit, fruit_ids)
        if filters["fruit_id"] is None:
            errors.append("The selected fruit does not exist, so that filter was cleared.")

    raw_source = clean_text(args.get("record_source"))
    if raw_source:
        if raw_source in sources:
            filters["record_source"] = raw_source
        else:
            errors.append("The selected record source does not exist, so that filter was cleared.")

    raw_page = clean_text(args.get("page"))
    if raw_page:
        filters["page"] = int(raw_page) if raw_page.isdigit() and int(raw_page) > 0 else 1

    return filters, errors


def filter_query(filters, extra=None):
    """Return the non-empty filters as a dictionary ready for ``url_for``."""
    query = {
        name: value
        for name, value in filters.items()
        if name != "page" and value not in (None, "")
    }

    if extra:
        query.update({name: value for name, value in extra.items() if value not in (None, "")})

    return query


def build_pager(filters, total, page_size=PAGE_SIZE):
    """Describe the current page and the links to the neighbouring pages."""
    pages = max(1, math.ceil(total / page_size)) if total else 1
    page = min(max(1, filters.get("page") or 1), pages)

    def query(number):
        if number <= 1:
            return filter_query(filters)
        return filter_query(filters, {"page": number})

    return {
        "page": page,
        "pages": pages,
        "page_size": page_size,
        "total": total,
        "first_row": (page - 1) * page_size + 1 if total else 0,
        "last_row": min(page * page_size, total),
        "has_prev": page > 1,
        "has_next": page < pages,
        "first_query": query(1),
        "prev_query": query(page - 1),
        "next_query": query(page + 1),
        "last_query": query(pages),
    }


def source_label(record_source):
    """Human-readable label for a record source."""
    return SOURCE_LABELS.get(record_source, record_source)


def wastage_percentage(damaged_quantity, available_stock):
    """Return the wastage percentage, or ``None`` when it cannot be worked out.

    The denominator is the historical available stock for the same selection.
    When there is no available stock to compare against, no percentage is
    reported rather than a misleading zero.
    """
    if not available_stock:
        return None

    return (damaged_quantity / available_stock) * 100


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
def build_dashboard(filters):
    """Everything the Wastage dashboard page needs."""
    # The dashboard shows only the newest few records; the full paginated list
    # lives on the Wastage Records page.
    rows = [
        dict(row)
        for row in wastage_repo.list_wastage(filters, limit=RECENT_LIMIT, offset=0)
    ]

    # Label each row so the page can keep historical and manual wastage apart.
    for row in rows:
        row["source_label"] = source_label(row["record_source"])

    summary = wastage_repo.summary(filters)
    summary["wastage_percentage"] = wastage_percentage(
        summary["damaged_quantity"], summary["available_stock"]
    )

    fruits = [dict(row) for row in wastage_repo.fruit_summary(filters)]

    return {
        "summary": summary,
        "sources": wastage_repo.source_totals(filters),
        "district": [dict(row) for row in wastage_repo.district_summary(filters)],
        "category": [dict(row) for row in wastage_repo.category_summary(filters)],
        "fruits": fruits,
        "top_fruits": fruits[:TOP_FRUIT_LIMIT],
        "chart_fruits": fruits[:CHART_FRUIT_LIMIT],
        "monthly": wastage_repo.period_summary(filters, "month"),
        "yearly": wastage_repo.period_summary(filters, "year"),
        "daily": wastage_repo.period_summary(filters, "day", limit=DAILY_TREND_LIMIT),
        "rows": rows,
        "record_query": filter_query(filters),
        "notes": CALCULATION_NOTES,
        "percentage_denominator": PERCENTAGE_DENOMINATOR,
        "synthetic_notice": SYNTHETIC_NOTICE,
    }


def build_records(filters):
    """One page of wastage records with its totals and pager."""
    total = wastage_repo.count_wastage(filters)
    pager = build_pager(filters, total)

    rows = [
        dict(row)
        for row in wastage_repo.list_wastage(
            filters, limit=pager["page_size"], offset=(pager["page"] - 1) * pager["page_size"]
        )
    ]

    for row in rows:
        row["source_label"] = source_label(row["record_source"])

    summary = wastage_repo.summary(filters)
    summary["wastage_percentage"] = wastage_percentage(
        summary["damaged_quantity"], summary["available_stock"]
    )

    return {
        "rows": rows,
        "pager": pager,
        "summary": summary,
        "sources": wastage_repo.source_totals(filters),
        "notice": RECORDS_NOTICE,
    }


def build_chart_payload(filters):
    """The aggregated series the charts are drawn from.

    Only totals per fruit, district and category and per period are sent to the
    browser, never the rows behind them.
    """
    summary = wastage_repo.summary(filters)
    percentages = wastage_percentage(summary["damaged_quantity"], summary["available_stock"])

    fruits = [dict(row) for row in wastage_repo.fruit_summary(filters)]

    return {
        "ok": True,
        "summary": {**summary, "wastage_percentage": percentages},
        "district": [dict(row) for row in wastage_repo.district_summary(filters)],
        "category": [dict(row) for row in wastage_repo.category_summary(filters)],
        "fruits": fruits[:CHART_FRUIT_LIMIT],
        "monthly": wastage_repo.period_summary(filters, "month"),
        "yearly": wastage_repo.period_summary(filters, "year"),
    }


# ---------------------------------------------------------------------------
# Manual wastage entry
# ---------------------------------------------------------------------------
def read_entry_form(source):
    """Copy the submitted wastage values so the form can be re-rendered."""
    return {
        "wastage_date": clean_text(source.get("wastage_date")),
        "district_id": clean_text(source.get("district_id")),
        "category": clean_text(source.get("category")),
        "fruit_id": clean_text(source.get("fruit_id")),
        "damaged_quantity": clean_text(source.get("damaged_quantity")),
        "cost_per_kg": clean_text(source.get("cost_per_kg")),
        "reason": clean_text(source.get("reason")),
    }


def form_from_record(record):
    """Fill the form with the values of a saved manual wastage record."""
    return {
        "wastage_date": record["wastage_date"],
        "district_id": str(record["district_id"]),
        "category": record["fruit_category"],
        "fruit_id": str(record["fruit_id"]),
        "damaged_quantity": f"{float(record['damaged_quantity']):g}",
        "cost_per_kg": f"{float(record['cost_per_kg']):g}",
        "reason": record["reason"] or "",
    }


def validate_damaged_quantity(value):
    """Accept a positive, finite damaged quantity in kilograms."""
    text = clean_text(value)

    if not text:
        return None, "Please enter the damaged quantity in kilograms."

    try:
        quantity = float(text)
    except ValueError:
        return None, "Damaged quantity must be a number in kilograms."

    if not math.isfinite(quantity):
        return None, "Damaged quantity must be a number in kilograms."
    if quantity <= 0:
        return None, "Damaged quantity must be greater than zero."
    if quantity > MAX_QUANTITY:
        return None, f"Damaged quantity must be {MAX_QUANTITY:,.0f} kg or less."

    return round(quantity, 3), None


def validate_cost_per_kg(value):
    """Accept a positive, finite cost in INR per kilogram."""
    text = clean_text(value)

    if not text:
        return None, "Please enter the cost per kilogram."

    try:
        price = float(text)
    except ValueError:
        return None, "Cost per kilogram must be a number in INR."

    if not math.isfinite(price):
        return None, "Cost per kilogram must be a number in INR."
    if price <= 0:
        return None, "Cost per kilogram must be greater than zero."
    if price > MAX_PRICE:
        return None, f"Cost per kilogram must be {MAX_PRICE:,.0f} INR or less."

    return round(price, 2), None


def validate_reason(value):
    """Accept a short reason for the wastage. It is always required."""
    text = clean_text(value)

    if not text:
        return None, "Please give a reason for the wastage."

    if len(text) > REASON_MAX:
        return None, f"The reason must be {REASON_MAX} characters or fewer."

    return text, None


def validate_entry_form(source, districts, fruits, exclude_id=None):
    """Validate the Record Wastage form. Returns ``(cleaned, errors)``."""
    errors = {}
    categories = sorted({row["category"] for row in fruits})

    wastage_date, error = sales_service.validate_record_date(source.get("wastage_date"))
    if error:
        errors["wastage_date"] = error.replace("date of sale", "date of the wastage")

    district_id, district_name, error = sales_service.validate_district(
        source.get("district_id"), districts
    )
    if error:
        errors["district_id"] = error

    category = clean_text(source.get("category"))
    if not category:
        errors["category"] = "Please choose a fruit category."
    elif category not in categories:
        errors["category"] = "Please choose one of the supported fruit categories."

    fruit_id, fruit_name, fruit_category, error = sales_service.validate_fruit(
        source.get("fruit_id"), fruits
    )
    if error:
        errors["fruit_id"] = error
    elif "category" not in errors and fruit_category != category:
        errors["fruit_id"] = "The chosen fruit does not belong to the selected category."

    damaged_quantity, error = validate_damaged_quantity(source.get("damaged_quantity"))
    if error:
        errors["damaged_quantity"] = error

    cost_per_kg, error = validate_cost_per_kg(source.get("cost_per_kg"))
    if error:
        errors["cost_per_kg"] = error

    reason, error = validate_reason(source.get("reason"))
    if error:
        errors["reason"] = error

    # A second entry for the same day, district and fruit is refused, so wastage
    # for one position is corrected by editing the existing row.
    if not errors and wastage_repo.manual_record_exists(
        wastage_date, district_id, fruit_id, exclude_id=exclude_id
    ):
        errors["form"] = (
            "A manual wastage entry already exists for this fruit and district "
            "on that date. Edit the existing entry instead of adding a second one."
        )

    cleaned = {
        "wastage_date": wastage_date,
        "district_id": district_id,
        "district_name": district_name,
        "fruit_id": fruit_id,
        "fruit_name": fruit_name,
        "damaged_quantity": damaged_quantity,
        "cost_per_kg": cost_per_kg,
        "reason": reason,
    }

    return cleaned, errors


def save_entry(cleaned):
    """Store a validated manual wastage entry. Returns ``(record_id, errors)``."""
    wastage_cost = round(cleaned["damaged_quantity"] * cleaned["cost_per_kg"], 2)

    try:
        record_id = wastage_repo.create_manual_record(
            wastage_date=cleaned["wastage_date"],
            district_id=cleaned["district_id"],
            fruit_id=cleaned["fruit_id"],
            damaged_quantity=cleaned["damaged_quantity"],
            cost_per_kg=cleaned["cost_per_kg"],
            wastage_cost=wastage_cost,
            reason=cleaned["reason"],
        )
    except Exception:
        return None, {"form": "The wastage entry could not be saved. Please try again."}

    return record_id, {}


def update_entry(record_id, cleaned):
    """Update a validated manual wastage entry. Returns ``(saved, errors)``."""
    wastage_cost = round(cleaned["damaged_quantity"] * cleaned["cost_per_kg"], 2)

    try:
        saved = wastage_repo.update_manual_record(
            record_id=record_id,
            wastage_date=cleaned["wastage_date"],
            district_id=cleaned["district_id"],
            fruit_id=cleaned["fruit_id"],
            damaged_quantity=cleaned["damaged_quantity"],
            cost_per_kg=cleaned["cost_per_kg"],
            wastage_cost=wastage_cost,
            reason=cleaned["reason"],
        )
    except Exception:
        return False, {"form": "The wastage entry could not be updated. Please try again."}

    return saved, {}
