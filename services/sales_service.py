"""Validation and save logic for the administrator Sales Entry module.

Every value typed on the Sales Entry page is checked here before it reaches
the database:

* the date of sale must be a real calendar date and never in the future,
* the district and the fruit must exist in the FruitVista catalogue,
* the fruit must belong to the selected category,
* quantity and selling price must be numbers greater than zero.

Records are written to the shared ``daily_records`` table with
``record_source = 'manual'``, so imported historical data is never touched.
"""

import math
import sqlite3
from datetime import date, datetime

from models import sales as sales_repo
from services.validators import clean_text

DATE_FORMAT = "%Y-%m-%d"

# Guard rails that protect the database from accidental extra zeros.
MAX_QUANTITY = 100000.0  # kilograms
MAX_PRICE = 1000000.0  # INR per kilogram

_DUPLICATE_MESSAGE = (
    "A sales record for this fruit and district already exists on that date. "
    "Edit the existing record instead of adding a second one."
)


def read_sales_form(source):
    """Copy the submitted values so the form can be re-rendered after an error."""
    return {
        "record_date": clean_text(source.get("record_date")),
        "district_id": clean_text(source.get("district_id")),
        "category": clean_text(source.get("category")),
        "fruit_id": clean_text(source.get("fruit_id")),
        "quantity_sold": clean_text(source.get("quantity_sold")),
        "selling_price": clean_text(source.get("selling_price")),
    }


def validate_record_date(value):
    """Accept a real calendar date in ``YYYY-MM-DD`` form, today or earlier."""
    text = clean_text(value)

    if not text:
        return None, "Please choose the date of sale."

    try:
        parsed = datetime.strptime(text, DATE_FORMAT).date()
    except ValueError:
        return None, "Please enter the date of sale as YYYY-MM-DD."

    if parsed > date.today():
        return None, "The date of sale cannot be in the future."

    return parsed.isoformat(), None


def validate_quantity(value):
    """Accept a positive, finite quantity in kilograms."""
    text = clean_text(value)

    if not text:
        return None, "Please enter the quantity sold in kilograms."

    try:
        quantity = float(text)
    except ValueError:
        return None, "Quantity sold must be a number in kilograms."

    if not math.isfinite(quantity):
        return None, "Quantity sold must be a number in kilograms."
    if quantity <= 0:
        return None, "Quantity sold must be greater than zero."
    if quantity > MAX_QUANTITY:
        return None, f"Quantity sold must be {MAX_QUANTITY:,.0f} kg or less."

    return round(quantity, 3), None


def validate_selling_price(value):
    """Accept a positive, finite selling price in INR per kilogram."""
    text = clean_text(value)

    if not text:
        return None, "Please enter the selling price per kilogram."

    try:
        price = float(text)
    except ValueError:
        return None, "Selling price must be a number in INR per kilogram."

    if not math.isfinite(price):
        return None, "Selling price must be a number in INR per kilogram."
    if price <= 0:
        return None, "Selling price must be greater than zero."
    if price > MAX_PRICE:
        return None, f"Selling price must be {MAX_PRICE:,.0f} INR or less."

    return round(price, 2), None


def _match(rows, column, value):
    """Return the row whose ``column`` equals ``value``, or ``None``."""
    for row in rows:
        if str(row[column]) == value:
            return row
    return None


def validate_district(value, districts):
    """Accept only one of the districts stored in the catalogue."""
    text = clean_text(value)

    if not text:
        return None, None, "Please choose a district."

    row = _match(districts, "id", text)
    if row is None:
        return None, None, "Please choose one of the supported districts."

    return row["id"], row["name"], None


def validate_fruit(value, fruits):
    """Accept only a fruit stored in the catalogue. Returns (id, name, category)."""
    text = clean_text(value)

    if not text:
        return None, None, None, "Please choose a fruit."

    row = _match(fruits, "id", text)
    if row is None:
        return None, None, None, "Please choose one of the approved fruits."

    return row["id"], row["name"], row["category"], None


def validate_sales_form(source, districts, fruits):
    """Validate a Sales Entry form.

    Returns ``(cleaned, errors)``. ``errors`` is empty when every field is
    valid. A submitted ``role`` or any other unknown field is ignored.
    """
    errors = {}
    categories = sorted({row["category"] for row in fruits})

    record_date, error = validate_record_date(source.get("record_date"))
    if error:
        errors["record_date"] = error

    district_id, district_name, error = validate_district(source.get("district_id"), districts)
    if error:
        errors["district_id"] = error

    category = clean_text(source.get("category"))
    if not category:
        errors["category"] = "Please choose a fruit category."
    elif category not in categories:
        errors["category"] = "Please choose one of the supported fruit categories."

    fruit_id, fruit_name, fruit_category, error = validate_fruit(source.get("fruit_id"), fruits)
    if error:
        errors["fruit_id"] = error
    elif "category" not in errors and fruit_category != category:
        errors["fruit_id"] = "The chosen fruit does not belong to the selected category."

    quantity_sold, error = validate_quantity(source.get("quantity_sold"))
    if error:
        errors["quantity_sold"] = error

    selling_price, error = validate_selling_price(source.get("selling_price"))
    if error:
        errors["selling_price"] = error

    cleaned = {
        "record_date": record_date,
        "district_id": district_id,
        "district_name": district_name,
        "category": category,
        "fruit_id": fruit_id,
        "fruit_name": fruit_name,
        "quantity_sold": quantity_sold,
        "selling_price": selling_price,
    }

    return cleaned, errors


def save_new_record(cleaned):
    """Insert a validated sales record.

    Returns ``(record_id, errors)``. One record may exist per fruit, district,
    date and source, so a duplicate is refused with a readable message rather
    than written twice.
    """
    if sales_repo.record_exists_for(
        cleaned["record_date"], cleaned["district_id"], cleaned["fruit_id"]
    ):
        return None, {"form": _DUPLICATE_MESSAGE}

    try:
        record_id = sales_repo.create_record(
            record_date=cleaned["record_date"],
            district_id=cleaned["district_id"],
            fruit_id=cleaned["fruit_id"],
            quantity_sold=cleaned["quantity_sold"],
            selling_price=cleaned["selling_price"],
        )
    except sqlite3.IntegrityError:
        return None, {"form": _DUPLICATE_MESSAGE}

    return record_id, {}


def save_existing_record(record_id, cleaned):
    """Update the record being edited. Returns ``(ok, errors)``."""
    if sales_repo.record_exists_for(
        cleaned["record_date"],
        cleaned["district_id"],
        cleaned["fruit_id"],
        exclude_id=record_id,
    ):
        return False, {"form": _DUPLICATE_MESSAGE}

    try:
        sales_repo.update_record(
            record_id=record_id,
            record_date=cleaned["record_date"],
            district_id=cleaned["district_id"],
            fruit_id=cleaned["fruit_id"],
            quantity_sold=cleaned["quantity_sold"],
            selling_price=cleaned["selling_price"],
        )
    except sqlite3.IntegrityError:
        return False, {"form": _DUPLICATE_MESSAGE}

    return True, {}


def delete_record(record_id):
    """Delete one manual sales record. Returns ``True`` when a row was removed."""
    return sales_repo.delete_record(record_id) > 0


def read_filters(source, districts, fruits):
    """Read the Sales Records filters.

    Values that do not match the catalogue are dropped (never rejected), so a
    hand-edited query string cannot break the page. Returns ``(filters,
    query)`` where ``query`` is ready to be passed to ``url_for``.
    """
    categories = sorted({row["category"] for row in fruits})

    filters = {"date_from": "", "date_to": "", "district_id": "", "category": "", "fruit_id": ""}

    filters["date_from"] = _clean_date(source.get("date_from"))
    filters["date_to"] = _clean_date(source.get("date_to"))

    district_id, _, error = validate_district(source.get("district_id"), districts)
    if not error:
        filters["district_id"] = district_id

    category = clean_text(source.get("category"))
    if category in categories:
        filters["category"] = category

    fruit_id, _, _, error = validate_fruit(source.get("fruit_id"), fruits)
    if not error:
        filters["fruit_id"] = fruit_id

    query = {name: value for name, value in filters.items() if value}
    return filters, query


def _clean_date(value):
    """Return an ISO date string, or an empty string when it is not usable."""
    text = clean_text(value)

    if not text:
        return ""

    try:
        return datetime.strptime(text, DATE_FORMAT).date().isoformat()
    except ValueError:
        return ""
