"""Filter parsing, form validation and dashboard assembly for Inventory Management.

The module reads three sources (see ``models/inventory.py``) and never writes
into ``daily_records``. All validation and all user-facing wording lives here
so the routes stay short and the pages show one consistent set of messages.
"""

import math
from datetime import datetime

from models import inventory as inventory_repo
from models import sales as sales_repo
from services import sales_service
from services.validators import clean_text

DATE_FORMAT = "%Y-%m-%d"

PAGE_SIZE = 25
ALERT_LIMIT = 20

# Guard rails that protect the database from accidental extra zeros.
MAX_QUANTITY = 100000.0
MAX_PRICE = 1000000.0
REASON_MAX = 200

STATUS_LABELS = {
    inventory_repo.STATUS_AVAILABLE: "Available",
    inventory_repo.STATUS_LOW_STOCK: "Low Stock",
    inventory_repo.STATUS_OUT_OF_STOCK: "Out of Stock",
}

STATUS_HINTS = {
    inventory_repo.STATUS_AVAILABLE: "stock above the reorder level",
    inventory_repo.STATUS_LOW_STOCK: "stock at or below the reorder level",
    inventory_repo.STATUS_OUT_OF_STOCK: "no stock left",
}

MOVEMENT_LABELS = {
    inventory_repo.MOVEMENT_PURCHASE: "Purchase",
    inventory_repo.MOVEMENT_ADJUSTMENT: "Stock adjustment",
}

# How a combined row is labelled in the table.
SOURCE_HISTORICAL = "Historical"
SOURCE_MIXED = "Historical + manual"
SOURCE_MANUAL = "Manual only"

SYNTHETIC_NOTICE = (
    "The stock levels come from the synthetic FruitVista dataset. They are "
    "generated sample data, not verified real warehouse counts."
)

CALCULATION_NOTES = [
    "Available stock = opening stock + purchased quantity + stock adjustment. "
    "Closing stock = available stock - quantity sold - damaged quantity. The "
    "database applies both formulas to every row, and they are the same "
    "formulas the historical dataset was built with.",
    "Opening stock, purchased quantity, damaged quantity and the reorder level "
    "come from the synthetic historical dataset. A purchase or an adjustment "
    "recorded here changes only the purchased quantity and the stock "
    "adjustment column; it never rewrites a dataset row.",
    "Sales entered in the Sales Entry module add to the quantity sold for the "
    "same day, district and fruit. Nothing is counted twice: the historical "
    "rows, the manual sales rows and the stock movements are three separate "
    "sets of records that are added together once.",
    "Stock status is worked out from the remaining (closing) stock compared "
    "with the reorder level: Available above the level, Low Stock at or below "
    "it while still above zero, and Out of Stock at zero or less. The dataset "
    "was imported with its own labels (In Stock, Low Stock, Reorder) that use "
    "different wording and a different threshold, so FruitVista recalculates "
    "the status rather than trusting the stored label.",
    "Rows marked 'Historical' come from the dataset alone. 'Historical + "
    "manual' means a purchase, a stock adjustment or a manual sale also exists "
    "for that day, district and fruit. 'Manual only' means there is no dataset "
    "row for that day at all.",
    "Total available stock and total opening stock are the sums of the "
    "matching rows over the selected days, not a single stock snapshot. Read "
    "the low-stock alerts for the current position, which uses the latest day "
    "in the selection.",
]

NO_STOCK_NOTICE = (
    "Opening stock, purchased quantity, damaged quantity and the reorder "
    "level are only recorded in the historical dataset. A purchase or a stock "
    "adjustment adds to the purchased quantity and the adjustment column but "
    "carries no stock figures of its own, so nothing is shown as zero for a "
    "day the dataset does not cover - the row simply has no opening stock."
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
        "statuses": [
            (status, STATUS_LABELS[status]) for status in inventory_repo.STATUSES
        ],
    }


def default_filters():
    """The filters used when the page is first opened: the full stored range."""
    bounds = inventory_repo.data_bounds()

    return {
        "date_from": bounds["first_date"] or "",
        "date_to": bounds["last_date"] or "",
        "district_id": None,
        "category": "",
        "fruit_id": None,
        "stock_status": "",
        "movement_type": "",
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
    """Read the Inventory filters out of a query string.

    Returns ``(filters, errors)``. A value that cannot be trusted is dropped
    and reported instead of being used, so a hand-edited address cannot break
    the page or reach the database as anything but a bound parameter.
    """
    errors = []
    options = filter_options()

    district_ids = {row["id"] for row in options["districts"]}
    fruit_ids = {row["id"] for row in options["fruits"]}
    categories = set(options["categories"])
    statuses = set(inventory_repo.STATUSES)
    movement_types = {inventory_repo.MOVEMENT_PURCHASE, inventory_repo.MOVEMENT_ADJUSTMENT}

    bounds = inventory_repo.data_bounds()
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

    raw_status = clean_text(args.get("stock_status"))
    if raw_status:
        if raw_status in statuses:
            filters["stock_status"] = raw_status
        else:
            errors.append("The selected stock status does not exist, so that filter was cleared.")

    raw_movement = clean_text(args.get("movement_type"))
    if raw_movement:
        if raw_movement in movement_types:
            filters["movement_type"] = raw_movement
        else:
            errors.append("The selected movement type does not exist, so that filter was cleared.")

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


def source_label(row):
    """Label a combined row as historical, mixed or manual."""
    if row["historical_rows"]:
        if row["manual_sales_rows"] or row["manual_movement_rows"]:
            return SOURCE_MIXED
        return SOURCE_HISTORICAL

    return SOURCE_MANUAL


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
def build_dashboard(filters):
    """Everything the Inventory dashboard page needs."""
    total = inventory_repo.count_inventory(filters)
    pager = build_pager(filters, total)

    rows = inventory_repo.list_inventory(
        filters, limit=pager["page_size"], offset=(pager["page"] - 1) * pager["page_size"]
    )
    rows = [dict(row) for row in rows]

    # Label each row so the page can keep historical and manual stock apart.
    for row in rows:
        row["source_label"] = source_label(row)

    # Every flagged position is counted, but only the first few are listed.
    latest, alerts = inventory_repo.low_stock_positions(filters)

    return {
        "summary": inventory_repo.summary(filters),
        "district": [dict(row) for row in inventory_repo.district_summary(filters)],
        "category": [dict(row) for row in inventory_repo.category_summary(filters)],
        "rows": rows,
        "pager": pager,
        "latest_date": latest,
        "alerts": [dict(row) for row in alerts[:ALERT_LIMIT]],
        "alert_count": len(alerts),
        "notes": CALCULATION_NOTES,
        "no_stock_notice": NO_STOCK_NOTICE,
        "synthetic_notice": SYNTHETIC_NOTICE,
    }


def build_chart_payload(filters):
    """The aggregated series the charts are drawn from.

    Only totals per district and per category are sent to the browser, never
    the rows behind them.
    """
    latest, alerts = inventory_repo.low_stock_positions(filters)

    by_district = {}
    for row in alerts:
        entry = by_district.setdefault(
            row["district_name"], {"label": row["district_name"], "low": 0, "out": 0}
        )
        if row["stock_status"] == inventory_repo.STATUS_OUT_OF_STOCK:
            entry["out"] += 1
        else:
            entry["low"] += 1

    return {
        "ok": True,
        "summary": inventory_repo.summary(filters),
        "district": [dict(row) for row in inventory_repo.district_summary(filters)],
        "category": [dict(row) for row in inventory_repo.category_summary(filters)],
        "latest_date": latest,
        "alert_count": len(alerts),
        "alerts_by_district": sorted(by_district.values(), key=lambda item: item["label"]),
    }


# ---------------------------------------------------------------------------
# Stock purchase entry
# ---------------------------------------------------------------------------
def read_purchase_form(source):
    """Copy the submitted purchase values so the form can be re-rendered."""
    return {
        "movement_date": clean_text(source.get("movement_date")),
        "district_id": clean_text(source.get("district_id")),
        "category": clean_text(source.get("category")),
        "fruit_id": clean_text(source.get("fruit_id")),
        "quantity": clean_text(source.get("quantity")),
        "unit_price": clean_text(source.get("unit_price")),
        "reason": clean_text(source.get("reason")),
    }


def read_adjustment_form(source):
    """Copy the submitted adjustment values so the form can be re-rendered."""
    return {
        "movement_date": clean_text(source.get("movement_date")),
        "district_id": clean_text(source.get("district_id")),
        "category": clean_text(source.get("category")),
        "fruit_id": clean_text(source.get("fruit_id")),
        "quantity": clean_text(source.get("quantity")),
        "reason": clean_text(source.get("reason")),
    }


def validate_quantity(value, allow_negative=False):
    """Accept a positive (or, for an adjustment, any non-zero) quantity in kg."""
    text = clean_text(value)

    if not text:
        return None, "Please enter the quantity in kilograms."

    try:
        quantity = float(text)
    except ValueError:
        return None, "Quantity must be a number in kilograms."

    if not math.isfinite(quantity):
        return None, "Quantity must be a number in kilograms."

    if allow_negative:
        if quantity == 0:
            return None, "A stock adjustment cannot be zero. Use a positive quantity to add stock and a negative quantity to remove it."
    elif quantity <= 0:
        return None, "Quantity must be greater than zero."

    if abs(quantity) > MAX_QUANTITY:
        return None, f"Quantity must be between -{MAX_QUANTITY:,.0f} kg and {MAX_QUANTITY:,.0f} kg."

    return round(quantity, 3), None


def validate_unit_price(value):
    """Accept a positive, finite purchase price in INR per kilogram."""
    text = clean_text(value)

    if not text:
        return None, "Please enter the purchase price per kilogram."

    try:
        price = float(text)
    except ValueError:
        return None, "Purchase price must be a number in INR per kilogram."

    if not math.isfinite(price):
        return None, "Purchase price must be a number in INR per kilogram."
    if price <= 0:
        return None, "Purchase price must be greater than zero."
    if price > MAX_PRICE:
        return None, f"Purchase price must be {MAX_PRICE:,.0f} INR or less."

    return round(price, 2), None


def validate_reason(value, required):
    """Accept a short reason. Required for a stock adjustment."""
    text = clean_text(value)

    if not text:
        if required:
            return None, "Please give a reason for the stock adjustment."
        return None, None

    if len(text) > REASON_MAX:
        return None, f"The reason must be {REASON_MAX} characters or fewer."

    return text, None


def _validate_common(source, districts, fruits):
    """Validate the fields shared by the purchase and adjustment forms."""
    errors = {}
    categories = sorted({row["category"] for row in fruits})

    movement_date, error = sales_service.validate_record_date(source.get("movement_date"))
    if error:
        errors["movement_date"] = error.replace("date of sale", "date of the movement")

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

    return movement_date, district_id, district_name, fruit_id, fruit_name, errors


def validate_purchase_form(source, districts, fruits):
    """Validate the Record Purchase form. Returns ``(cleaned, errors)``."""
    movement_date, district_id, district_name, fruit_id, fruit_name, errors = _validate_common(
        source, districts, fruits
    )

    quantity, error = validate_quantity(source.get("quantity"))
    if error:
        errors["quantity"] = error

    unit_price, error = validate_unit_price(source.get("unit_price"))
    if error:
        errors["unit_price"] = error

    reason, error = validate_reason(source.get("reason"), required=False)
    if error:
        errors["reason"] = error

    cleaned = {
        "movement_date": movement_date,
        "district_id": district_id,
        "district_name": district_name,
        "fruit_id": fruit_id,
        "fruit_name": fruit_name,
        "quantity": quantity,
        "unit_price": unit_price,
        "reason": reason,
    }

    return cleaned, errors


def validate_adjustment_form(source, districts, fruits):
    """Validate the Stock Adjustment form. Returns ``(cleaned, errors)``."""
    movement_date, district_id, district_name, fruit_id, fruit_name, errors = _validate_common(
        source, districts, fruits
    )

    quantity, error = validate_quantity(source.get("quantity"), allow_negative=True)
    if error:
        errors["quantity"] = error

    reason, error = validate_reason(source.get("reason"), required=True)
    if error:
        errors["reason"] = error

    cleaned = {
        "movement_date": movement_date,
        "district_id": district_id,
        "district_name": district_name,
        "fruit_id": fruit_id,
        "fruit_name": fruit_name,
        "quantity": quantity,
        "reason": reason,
    }

    return cleaned, errors


def save_purchase(cleaned):
    """Store a validated purchase. Returns ``(movement_id, errors)``."""
    try:
        movement_id = inventory_repo.create_movement(
            movement_type=inventory_repo.MOVEMENT_PURCHASE,
            movement_date=cleaned["movement_date"],
            district_id=cleaned["district_id"],
            fruit_id=cleaned["fruit_id"],
            quantity=cleaned["quantity"],
            unit_price=cleaned["unit_price"],
            amount=round(cleaned["quantity"] * cleaned["unit_price"], 2),
            reason=cleaned["reason"],
        )
    except Exception:
        return None, {"form": "The purchase could not be saved. Please try again."}

    return movement_id, {}


def save_adjustment(cleaned):
    """Store a validated stock adjustment. Returns ``(movement_id, errors)``."""
    try:
        movement_id = inventory_repo.create_movement(
            movement_type=inventory_repo.MOVEMENT_ADJUSTMENT,
            movement_date=cleaned["movement_date"],
            district_id=cleaned["district_id"],
            fruit_id=cleaned["fruit_id"],
            quantity=cleaned["quantity"],
            unit_price=0.0,
            amount=0.0,
            reason=cleaned["reason"],
        )
    except Exception:
        return None, {"form": "The stock adjustment could not be saved. Please try again."}

    return movement_id, {}


# ---------------------------------------------------------------------------
# Stock movement history
# ---------------------------------------------------------------------------
def parse_movement_filters(args):
    """Read the Stock Movement History filters out of a query string."""
    filters, errors = parse_filters(args)

    # The history page starts with no date restriction so the newest movements
    # are visible straight away.
    if not clean_text(args.get("date_from")):
        filters["date_from"] = ""
    if not clean_text(args.get("date_to")):
        filters["date_to"] = ""

    return filters, errors


def read_movement_history(filters):
    """One page of stock movements with its totals and pager."""
    total = inventory_repo.count_movements(filters)
    pager = build_pager(filters, total)

    rows = inventory_repo.list_movements(
        filters, limit=pager["page_size"], offset=(pager["page"] - 1) * pager["page_size"]
    )

    return {
        "rows": [dict(row) for row in rows],
        "pager": pager,
        "totals": inventory_repo.movement_totals(filters),
    }
