"""Filter handling and report assembly for the administrator Reports module.

A report never calculates anything of its own: it asks the modules that already
own the figures. Sales come from :mod:`models.analytics`, stock from
:mod:`models.inventory` and wastage from :mod:`models.wastage`, so a report and
the dashboard it summarises always show the same numbers.

Nothing here writes to the database. The three source modules already keep the
imported synthetic dataset and the manually entered records apart, so a report
labels them exactly as the dashboards do and never adds a record twice.

The report is built once, as plain data (summary rows, tables and chart
series). The page, the CSV, the Excel workbook and the PDF all render that same
assembly, which is what keeps the four views in agreement.
"""

from datetime import datetime

from models import analytics as analytics_repo
from models import inventory as inventory_repo
from models import sales as sales_repo
from models import wastage as wastage_repo
from services import inventory_service
from services import wastage_service
from services.validators import clean_text

DATE_FORMAT = "%Y-%m-%d"

ISO_CURRENCY = "\u20b9"
PDF_CURRENCY = "Rs."

# Longest series a chart or a monthly table shows, newest months kept.
MONTH_LIMIT = 24
FRUIT_LIMIT = 30
BEST_SELLER_LIMIT = 10
LOW_STOCK_LIMIT = 50

# Display labels for the three fruit categories.
CATEGORY_LABELS = {
    "Dry Fruits": "Dry Fruit",
    "Seasonal Fruits": "Seasonal Fruit",
    "General Fruits": "General Fruit",
}

# The report types the module offers, in the order they appear.
REPORT_TYPES = {
    "sales": {
        "key": "sales",
        "name": "Sales Report",
        "title": "Sales Report",
        "summary": "Revenue, quantity sold, fruit-wise, district-wise and "
                   "monthly and yearly sales, with profit where the data allows.",
    },
    "inventory": {
        "key": "inventory",
        "name": "Inventory Report",
        "title": "Inventory Report",
        "summary": "Opening, purchased, available and closing stock, low-stock "
                   "items and the district-wise stock position.",
    },
    "wastage": {
        "key": "wastage",
        "name": "Wastage Report",
        "title": "Wastage Report",
        "summary": "Damaged quantity, wastage cost, fruit-wise and district-wise "
                   "wastage and the monthly wastage trend.",
    },
    "market": {
        "key": "market",
        "name": "Market Summary Report",
        "title": "Market Summary Report",
        "summary": "Fruit category and district comparison, best-selling fruits "
                   "and a combined stock and wastage summary.",
    },
}

DEFAULT_REPORT_TYPE = "sales"

RECORD_SOURCE_ALL = "all"
RECORD_SOURCE_LABELS = {
    RECORD_SOURCE_ALL: "All records",
    analytics_repo.RECORD_SOURCE_HISTORICAL: "Historical dataset only",
    analytics_repo.RECORD_SOURCE_MANUAL: "Manual entries only",
}

SYNTHETIC_NOTICE = (
    "Historical figures come from the synthetic FruitVista dataset. They are "
    "generated sample data, not verified real market observations."
)

NO_PROFIT_NOTE = (
    "Profit is reported only where the synthetic historical dataset records it. "
    "A manual sales entry stores the sale date, district, fruit, quantity, "
    "selling price and revenue only, so a selection with no historical record "
    "reports profit as 'Not available' rather than as zero."
)

INVENTORY_SOURCE_NOTE = (
    "The record-source filter does not apply to inventory: stock is always the "
    "combination of the historical dataset, the manual sales and the stock "
    "movements, exactly as the Inventory Management page calculates it."
)


# ---------------------------------------------------------------------------
# Formatting helpers (shared by the page and every export)
# ---------------------------------------------------------------------------
def format_inr(value, symbol=ISO_CURRENCY):
    """Format a money value in Indian rupees with two decimals."""
    return f"{symbol} {float(value or 0):,.2f}"


def format_kg(value):
    """Format a quantity in kilograms with two decimals."""
    return f"{float(value or 0):,.2f} kg"


def format_int(value):
    """Format a whole number with thousands separators."""
    return f"{int(value or 0):,}"


def format_percent(value):
    """Format a percentage, or say so when it cannot be worked out."""
    if value is None:
        return "Not available"
    return f"{value:.3f} %"


def format_month(label):
    """Turn ``2024-03`` into ``Mar 2024``; other labels pass through."""
    text = str(label or "")
    parts = text.split("-")

    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return text

    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    index = int(parts[1]) - 1

    if 0 <= index < 12:
        return f"{months[index]} {parts[0]}"

    return text


def category_label(value):
    """Display label for a fruit category."""
    return CATEGORY_LABELS.get(value, value)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
def filter_options():
    """Catalogue values used to build the filter controls."""
    return {
        "districts": [dict(row) for row in sales_repo.list_districts()],
        "fruits": [dict(row) for row in sales_repo.list_fruits()],
        "categories": sales_repo.list_categories(),
        "report_types": [dict(REPORT_TYPES[key]) for key in REPORT_TYPES],
        "record_sources": [
            (key, RECORD_SOURCE_LABELS[key])
            for key in (RECORD_SOURCE_ALL,
                        analytics_repo.RECORD_SOURCE_HISTORICAL,
                        analytics_repo.RECORD_SOURCE_MANUAL)
        ],
    }


def data_bounds():
    """The stored date range across sales, inventory and wastage."""
    sales_bounds = analytics_repo.data_bounds()
    inventory_bounds = inventory_repo.data_bounds()
    wastage_bounds = wastage_repo.data_bounds()

    first_dates = [
        value
        for value in (
            sales_bounds["first_date"],
            inventory_bounds["first_date"],
            wastage_bounds["first_date"],
        )
        if value
    ]
    last_dates = [
        value
        for value in (
            sales_bounds["last_date"],
            inventory_bounds["last_date"],
            wastage_bounds["last_date"],
        )
        if value
    ]

    return {
        "first_date": min(first_dates) if first_dates else None,
        "last_date": max(last_dates) if last_dates else None,
    }


def default_filters():
    """The filters used when the Reports page is first opened."""
    bounds = data_bounds()

    return {
        "report_type": DEFAULT_REPORT_TYPE,
        "date_from": bounds["first_date"] or "",
        "date_to": bounds["last_date"] or "",
        "district_id": None,
        "category": "",
        "fruit_id": None,
        "record_source": RECORD_SOURCE_ALL,
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
    """Read the Reports filters out of a query string.

    Returns ``(filters, errors)``. A value that cannot be trusted is dropped and
    reported instead of being used, so a hand-edited address cannot break a page
    or reach the database as anything but a bound parameter.
    """
    errors = []
    options = filter_options()

    district_ids = {row["id"] for row in options["districts"]}
    fruit_ids = {row["id"] for row in options["fruits"]}
    categories = set(options["categories"])

    bounds = data_bounds()
    filters = default_filters()

    raw_report = clean_text(args.get("report_type"))
    if raw_report:
        if raw_report in REPORT_TYPES:
            filters["report_type"] = raw_report
        else:
            errors.append("The selected report type does not exist, so the Sales report was shown.")

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
        if raw_source in RECORD_SOURCE_LABELS:
            filters["record_source"] = raw_source
        else:
            errors.append("The selected record source does not exist, so that filter was cleared.")

    return filters, errors


def filter_query(filters):
    """Return the non-empty filters as a dictionary ready for ``url_for``."""
    return {
        name: value
        for name, value in filters.items()
        if value not in (None, "")
    }


def filter_rows(filters):
    """The filters of a report as readable label/value pairs."""
    district_name = next(
        (row["name"] for row in sales_repo.list_districts()
         if row["id"] == filters.get("district_id")),
        "",
    )
    fruit_name = next(
        (row["name"] for row in sales_repo.list_fruits()
         if row["id"] == filters.get("fruit_id")),
        "",
    )

    report_type = REPORT_TYPES.get(filters.get("report_type"), REPORT_TYPES[DEFAULT_REPORT_TYPE])

    return [
        {"label": "Report type", "value": report_type["name"]},
        {"label": "Start date", "value": filters.get("date_from") or "Earliest"},
        {"label": "End date", "value": filters.get("date_to") or "Latest"},
        {"label": "District", "value": district_name or "All districts"},
        {"label": "Fruit category",
         "value": category_label(filters["category"]) if filters.get("category") else "All categories"},
        {"label": "Fruit name", "value": fruit_name or "All fruits"},
        {"label": "Record source",
         "value": RECORD_SOURCE_LABELS.get(filters.get("record_source"), "All records")},
    ]


def _sales_filters(filters):
    """The filters shaped for :mod:`models.analytics`."""
    return {
        "date_from": filters.get("date_from") or "",
        "date_to": filters.get("date_to") or "",
        "district_id": filters.get("district_id"),
        "category": filters.get("category") or "",
        "fruit_id": filters.get("fruit_id"),
        "record_source": filters.get("record_source") or RECORD_SOURCE_ALL,
    }


def _wastage_filters(filters):
    """The filters shaped for :mod:`models.wastage` (empty means both sources)."""
    source = filters.get("record_source") or RECORD_SOURCE_ALL

    return {
        "date_from": filters.get("date_from") or "",
        "date_to": filters.get("date_to") or "",
        "district_id": filters.get("district_id"),
        "category": filters.get("category") or "",
        "fruit_id": filters.get("fruit_id"),
        "record_source": "" if source == RECORD_SOURCE_ALL else source,
    }


# ---------------------------------------------------------------------------
# Chart helpers
# ---------------------------------------------------------------------------
def _bar_chart(chart_id, title, subtitle, labels, values, unit,
               horizontal=False):
    """One bar chart, ready for the page and the PDF."""
    return {
        "id": chart_id,
        "title": title,
        "subtitle": subtitle,
        "kind": "bar",
        "unit": unit,
        "horizontal": horizontal,
        "labels": labels,
        "values": values,
    }


def _line_chart(chart_id, title, subtitle, labels, values, unit):
    """One line chart, ready for the page and the PDF."""
    return {
        "id": chart_id,
        "title": title,
        "subtitle": subtitle,
        "kind": "line",
        "unit": unit,
        "horizontal": False,
        "labels": labels,
        "values": values,
    }


# ---------------------------------------------------------------------------
# Report builders
# ---------------------------------------------------------------------------
def _build_sales(filters):
    """Sales report: revenue, quantity, fruit, district, monthly, yearly."""
    sales_filters = _sales_filters(filters)
    summary = analytics_repo.summary(sales_filters)

    fruits = [dict(row) for row in analytics_repo.fruit_ranking(
        sales_filters, "revenue", FRUIT_LIMIT)]
    districts = [dict(row) for row in analytics_repo.district_revenue(sales_filters)]
    monthly = [dict(row) for row in analytics_repo.monthly_series(sales_filters)]
    yearly = [dict(row) for row in analytics_repo.yearly_series(sales_filters)]
    split = [dict(row) for row in analytics_repo.source_split(sales_filters)]

    profit_value = (
        format_inr(summary["profit"]) if summary["profit_available"] else "Not available"
    )
    wastage_value = (
        format_kg(summary["wastage_kg"]) if summary["wastage_kg"] is not None else "Not available"
    )

    summary_rows = [
        {"label": "Total revenue", "value": format_inr(summary["revenue"])},
        {"label": "Total quantity sold", "value": format_kg(summary["quantity"])},
        {"label": "Sales records", "value": format_int(summary["records"])},
        {"label": "Total net profit", "value": profit_value},
        {"label": "Recorded wastage", "value": wastage_value},
    ]

    tables = [
        {
            "title": "Fruit-wise sales",
            "columns": ["Fruit", "Category", "Quantity (kg)", "Revenue (INR)"],
            "rows": [
                [row["label"], category_label(row["category"]),
                 f"{float(row['quantity']):,.2f}", f"{float(row['revenue']):,.2f}"]
                for row in fruits
            ],
        },
        {
            "title": "District-wise sales",
            "columns": ["District", "Quantity (kg)", "Revenue (INR)"],
            "rows": [
                [row["label"], f"{float(row['quantity']):,.2f}", f"{float(row['revenue']):,.2f}"]
                for row in districts
            ],
        },
        {
            "title": "Monthly sales",
            "columns": ["Month", "Quantity (kg)", "Revenue (INR)"],
            "rows": [
                [format_month(row["label"]), f"{float(row['quantity']):,.2f}",
                 f"{float(row['revenue']):,.2f}"]
                for row in monthly
            ],
        },
        {
            "title": "Yearly sales",
            "columns": ["Year", "Quantity (kg)", "Revenue (INR)", "Net profit (INR)"],
            "rows": [
                [row["label"], f"{float(row['quantity']):,.2f}", f"{float(row['revenue']):,.2f}",
                 f"{float(row['profit']):,.2f}" if row["profit"] is not None else "Not available"]
                for row in yearly
            ],
        },
        {
            "title": "Record source split",
            "columns": ["Record source", "Records", "Quantity (kg)", "Revenue (INR)"],
            "rows": [
                [RECORD_SOURCE_LABELS.get(row["label"], row["label"]),
                 format_int(row["records"]), f"{float(row['quantity']):,.2f}",
                 f"{float(row['revenue']):,.2f}"]
                for row in split
            ],
        },
    ]

    top_fruits = fruits[:10]
    monthly_tail = monthly[-MONTH_LIMIT:]

    charts = [
        _line_chart(
            "reportMonthlyRevenue", "Monthly revenue trend",
            "Total revenue recorded in each month of the selection",
            [format_month(row["label"]) for row in monthly_tail],
            [float(row["revenue"]) for row in monthly_tail], "inr",
        ),
        _bar_chart(
            "reportDistrictRevenue", "District-wise revenue",
            "Revenue for every district in the selection",
            [row["label"] for row in districts],
            [float(row["revenue"]) for row in districts], "inr",
        ),
        _bar_chart(
            "reportTopFruits", "Top fruits by revenue",
            "The ten fruits earning the most in the selection",
            [row["label"] for row in top_fruits],
            [float(row["revenue"]) for row in top_fruits], "inr", horizontal=True,
        ),
    ]

    notes = [NO_PROFIT_NOTE, SYNTHETIC_NOTICE]

    if summary["records"] and summary["profit_excluded_records"]:
        notes.append(
            f"Net profit covers the {format_int(summary['profit_records'])} historical "
            f"record(s) in this selection. The other "
            f"{format_int(summary['profit_excluded_records'])} manual record(s) are "
            "excluded because a manual entry does not record profit."
        )

    has_data = bool(summary["records"] or summary["quantity"] or summary["revenue"])

    return summary_rows, tables, charts, notes, has_data


def _build_inventory(filters):
    """Inventory report: opening, purchased, sold, closing and low stock."""
    inventory_filters = dict(filters)
    inventory_filters["stock_status"] = ""

    summary = inventory_repo.summary(inventory_filters)
    districts = [dict(row) for row in inventory_repo.district_summary(inventory_filters)]
    categories = [dict(row) for row in inventory_repo.category_summary(inventory_filters)]
    latest, alerts = inventory_repo.low_stock_positions(inventory_filters)

    summary_rows = [
        {"label": "Opening stock", "value": format_kg(summary["opening_stock"])},
        {"label": "Purchased quantity", "value": format_kg(summary["purchased_quantity"])},
        {"label": "Available stock", "value": format_kg(summary["available_stock"])},
        {"label": "Quantity sold", "value": format_kg(summary["quantity_sold"])},
        {"label": "Damaged quantity", "value": format_kg(summary["damaged_quantity"])},
        {"label": "Closing stock", "value": format_kg(summary["closing_stock"])},
        {"label": "Low-stock items",
         "value": format_int(sum(1 for item in alerts
                                 if item["stock_status"] == inventory_repo.STATUS_LOW_STOCK))},
        {"label": "Out-of-stock items",
         "value": format_int(sum(1 for item in alerts
                                 if item["stock_status"] == inventory_repo.STATUS_OUT_OF_STOCK))},
    ]

    low_stock_rows = [
        [
            row["district_name"],
            row["fruit_name"],
            category_label(row["fruit_category"]),
            f"{float(row['closing_stock']):,.2f}",
            f"{float(row['reorder_level']):,.2f}",
            inventory_service.STATUS_LABELS.get(row["stock_status"], row["stock_status"]),
        ]
        for row in alerts[:LOW_STOCK_LIMIT]
    ]

    tables = [
        {
            "title": "District-wise inventory",
            "columns": ["District", "Opening (kg)", "Purchased (kg)", "Available (kg)",
                        "Sold (kg)", "Damaged (kg)", "Closing (kg)"],
            "rows": [
                [row["label"], f"{float(row['opening_stock']):,.2f}",
                 f"{float(row['purchased_quantity']):,.2f}",
                 f"{float(row['available_stock']):,.2f}",
                 f"{float(row['quantity_sold']):,.2f}",
                 f"{float(row['damaged_quantity']):,.2f}",
                 f"{float(row['closing_stock']):,.2f}"]
                for row in districts
            ],
        },
        {
            "title": "Category-wise inventory",
            "columns": ["Fruit category", "Available (kg)", "Sold (kg)",
                        "Damaged (kg)", "Closing (kg)"],
            "rows": [
                [category_label(row["label"]), f"{float(row['available_stock']):,.2f}",
                 f"{float(row['quantity_sold']):,.2f}",
                 f"{float(row['damaged_quantity']):,.2f}",
                 f"{float(row['closing_stock']):,.2f}"]
                for row in categories
            ],
        },
        {
            "title": "Low-stock items"
                     + (f" (latest day: {latest})" if latest else ""),
            "columns": ["District", "Fruit", "Category", "Closing (kg)",
                        "Reorder level (kg)", "Status"],
            "rows": low_stock_rows,
            "note": (
                "A position is listed when its closing stock is at or below its "
                "reorder level on the latest day of the selection."
            ),
        },
    ]

    charts = [
        _bar_chart(
            "reportDistrictClosing", "District-wise closing stock",
            "Stock remaining at the end of the selection, per district",
            [row["label"] for row in districts],
            [float(row["closing_stock"]) for row in districts], "kg",
        ),
        _bar_chart(
            "reportCategoryClosing", "Category-wise closing stock",
            "Stock remaining at the end of the selection, per category",
            [category_label(row["label"]) for row in categories],
            [float(row["closing_stock"]) for row in categories], "kg",
        ),
    ]

    notes = list(inventory_service.CALCULATION_NOTES) + [
        INVENTORY_SOURCE_NOTE,
        inventory_service.SYNTHETIC_NOTICE,
    ]

    has_data = bool(summary["records"])

    return summary_rows, tables, charts, notes, has_data


def _build_wastage(filters):
    """Wastage report: damaged quantity, wastage cost and trends."""
    wastage_filters = _wastage_filters(filters)

    summary = wastage_repo.summary(wastage_filters)
    districts = [dict(row) for row in wastage_repo.district_summary(wastage_filters)]
    fruits = [dict(row) for row in wastage_repo.fruit_summary(wastage_filters, FRUIT_LIMIT)]
    monthly = [dict(row) for row in wastage_repo.period_summary(wastage_filters, "month")]
    sources = wastage_repo.source_totals(wastage_filters)

    percentage = (
        (summary["damaged_quantity"] / summary["available_stock"]) * 100
        if summary["available_stock"] else None
    )

    summary_rows = [
        {"label": "Total damaged quantity", "value": format_kg(summary["damaged_quantity"])},
        {"label": "Total wastage cost", "value": format_inr(summary["wastage_cost"])},
        {"label": "Wastage percentage", "value": format_percent(percentage)},
        {"label": "Wastage records", "value": format_int(summary["records"])},
        {"label": "Available stock compared against",
         "value": format_kg(summary["available_stock"])},
    ]

    source_rows = [
        [
            wastage_repo.SOURCES[i].capitalize(),
            format_int(sources.get(wastage_repo.SOURCES[i], {}).get("records", 0)),
            f"{float(sources.get(wastage_repo.SOURCES[i], {}).get('damaged_quantity', 0)):,.2f}",
            f"{float(sources.get(wastage_repo.SOURCES[i], {}).get('wastage_cost', 0)):,.2f}",
        ]
        for i in range(len(wastage_repo.SOURCES))
    ]

    tables = [
        {
            "title": "Fruit-wise wastage",
            "columns": ["Fruit", "Category", "Damaged (kg)", "Wastage cost (INR)"],
            "rows": [
                [row["label"], category_label(row["category"]),
                 f"{float(row['damaged_quantity']):,.2f}",
                 f"{float(row['wastage_cost']):,.2f}"]
                for row in fruits
            ],
        },
        {
            "title": "District-wise wastage",
            "columns": ["District", "Damaged (kg)", "Wastage cost (INR)"],
            "rows": [
                [row["label"], f"{float(row['damaged_quantity']):,.2f}",
                 f"{float(row['wastage_cost']):,.2f}"]
                for row in districts
            ],
        },
        {
            "title": "Monthly wastage trend",
            "columns": ["Month", "Damaged (kg)", "Wastage cost (INR)"],
            "rows": [
                [format_month(row["label"]), f"{float(row['damaged_quantity']):,.2f}",
                 f"{float(row['wastage_cost']):,.2f}"]
                for row in monthly
            ],
        },
        {
            "title": "Record source split",
            "columns": ["Record source", "Records", "Damaged (kg)", "Wastage cost (INR)"],
            "rows": source_rows,
        },
    ]

    monthly_tail = monthly[-MONTH_LIMIT:]
    top_fruits = fruits[:10]

    charts = [
        _line_chart(
            "reportMonthlyWastage", "Monthly wastage trend",
            "Damaged kilograms recorded in each month of the selection",
            [format_month(row["label"]) for row in monthly_tail],
            [float(row["damaged_quantity"]) for row in monthly_tail], "kg",
        ),
        _bar_chart(
            "reportFruitWastage", "Fruit-wise wastage",
            "The ten fruits with the most damaged weight",
            [row["label"] for row in top_fruits],
            [float(row["damaged_quantity"]) for row in top_fruits], "kg", horizontal=True,
        ),
        _bar_chart(
            "reportDistrictWastage", "District-wise wastage",
            "Damaged kilograms for every district in the selection",
            [row["label"] for row in districts],
            [float(row["damaged_quantity"]) for row in districts], "kg",
        ),
    ]

    notes = list(wastage_service.CALCULATION_NOTES) + [wastage_service.SYNTHETIC_NOTICE]

    has_data = bool(summary["records"])

    return summary_rows, tables, charts, notes, has_data


def _build_market(filters):
    """Market summary: category and district comparison, best sellers, stock."""
    sales_filters = _sales_filters(filters)
    wastage_filters = _wastage_filters(filters)
    inventory_filters = dict(filters)
    inventory_filters["stock_status"] = ""

    sales_summary = analytics_repo.summary(sales_filters)
    categories = [dict(row) for row in analytics_repo.category_sales(sales_filters)]
    districts = [dict(row) for row in analytics_repo.district_revenue(sales_filters)]
    best_sellers = [dict(row) for row in analytics_repo.fruit_ranking(
        sales_filters, "quantity_desc", BEST_SELLER_LIMIT)]

    wastage_summary = wastage_repo.summary(wastage_filters)
    wastage_districts = {
        dict(row)["label"]: dict(row)
        for row in wastage_repo.district_summary(wastage_filters)
    }
    inventory_districts = {
        dict(row)["label"]: dict(row)
        for row in inventory_repo.district_summary(inventory_filters)
    }
    inventory_summary = inventory_repo.summary(inventory_filters)

    percentage = (
        (wastage_summary["damaged_quantity"] / wastage_summary["available_stock"]) * 100
        if wastage_summary["available_stock"] else None
    )

    best_seller = best_sellers[0]["label"] if best_sellers else "Not available"

    summary_rows = [
        {"label": "Total revenue", "value": format_inr(sales_summary["revenue"])},
        {"label": "Total quantity sold", "value": format_kg(sales_summary["quantity"])},
        {"label": "Total damaged quantity", "value": format_kg(wastage_summary["damaged_quantity"])},
        {"label": "Total wastage cost", "value": format_inr(wastage_summary["wastage_cost"])},
        {"label": "Total closing stock", "value": format_kg(inventory_summary["closing_stock"])},
        {"label": "Best-selling fruit", "value": best_seller},
    ]

    # Merge the three per-district views by district name, so each district is
    # listed once with its sales, wastage and stock side by side.
    district_names = sorted(set(
        [row["label"] for row in districts]
        + list(wastage_districts.keys())
        + list(inventory_districts.keys())
    ))

    district_rows = []
    for name in district_names:
        sales_row = next((row for row in districts if row["label"] == name), None)
        wastage_row = wastage_districts.get(name, {})
        inventory_row = inventory_districts.get(name, {})

        district_rows.append([
            name,
            f"{float(sales_row['quantity'] if sales_row else 0):,.2f}",
            f"{float(sales_row['revenue'] if sales_row else 0):,.2f}",
            f"{float(wastage_row.get('damaged_quantity', 0)):,.2f}",
            f"{float(inventory_row.get('closing_stock', 0)):,.2f}",
        ])

    tables = [
        {
            "title": "Fruit category comparison",
            "columns": ["Fruit category", "Quantity sold (kg)", "Revenue (INR)"],
            "rows": [
                [category_label(row["label"]), f"{float(row['quantity']):,.2f}",
                 f"{float(row['revenue']):,.2f}"]
                for row in categories
            ],
        },
        {
            "title": "District comparison",
            "columns": ["District", "Quantity sold (kg)", "Sales revenue (INR)",
                        "Wastage (kg)", "Closing stock (kg)"],
            "rows": district_rows,
        },
        {
            "title": "Best-selling fruits",
            "columns": ["Fruit", "Category", "Quantity sold (kg)", "Revenue (INR)"],
            "rows": [
                [row["label"], category_label(row["category"]),
                 f"{float(row['quantity']):,.2f}", f"{float(row['revenue']):,.2f}"]
                for row in best_sellers
            ],
        },
        {
            "title": "Stock summary",
            "columns": ["Measure", "Value"],
            "rows": [
                ["Opening stock", format_kg(inventory_summary["opening_stock"])],
                ["Purchased quantity", format_kg(inventory_summary["purchased_quantity"])],
                ["Available stock", format_kg(inventory_summary["available_stock"])],
                ["Quantity sold", format_kg(inventory_summary["quantity_sold"])],
                ["Damaged quantity", format_kg(inventory_summary["damaged_quantity"])],
                ["Closing stock", format_kg(inventory_summary["closing_stock"])],
            ],
        },
        {
            "title": "Wastage summary",
            "columns": ["Measure", "Value"],
            "rows": [
                ["Total damaged quantity", format_kg(wastage_summary["damaged_quantity"])],
                ["Total wastage cost", format_inr(wastage_summary["wastage_cost"])],
                ["Wastage percentage", format_percent(percentage)],
                ["Available stock compared against",
                 format_kg(wastage_summary["available_stock"])],
            ],
        },
    ]

    charts = [
        _bar_chart(
            "reportCategoryRevenue", "Fruit category comparison",
            "Revenue for each fruit category in the selection",
            [category_label(row["label"]) for row in categories],
            [float(row["revenue"]) for row in categories], "inr",
        ),
        _bar_chart(
            "reportBestSellers", "Best-selling fruits",
            "The ten fruits sold in the greatest quantity",
            [row["label"] for row in best_sellers],
            [float(row["quantity"]) for row in best_sellers], "kg", horizontal=True,
        ),
        _bar_chart(
            "reportDistrictRevenueMarket", "District comparison",
            "Revenue for every district in the selection",
            [row["label"] for row in districts],
            [float(row["revenue"]) for row in districts], "inr",
        ),
    ]

    notes = [
        "The market summary brings the Sales, Inventory and Wastage figures "
        "together. Each figure is calculated by the module that owns it, so it "
        "matches the relevant dashboard exactly.",
        NO_PROFIT_NOTE,
        SYNTHETIC_NOTICE,
    ]

    has_data = bool(sales_summary["records"]
                    or inventory_summary["records"]
                    or wastage_summary["records"])

    return summary_rows, tables, charts, notes, has_data


_BUILDERS = {
    "sales": _build_sales,
    "inventory": _build_inventory,
    "wastage": _build_wastage,
    "market": _build_market,
}


def build_report(filters):
    """Assemble the whole report: header, summary, tables, charts and notes.

    The same dictionary drives the page and the CSV, Excel and PDF exports.
    """
    report_type = filters.get("report_type") or DEFAULT_REPORT_TYPE
    meta = REPORT_TYPES.get(report_type, REPORT_TYPES[DEFAULT_REPORT_TYPE])
    builder = _BUILDERS.get(report_type, _build_sales)

    summary_rows, tables, charts, notes, has_data = builder(filters)

    generated = datetime.now()

    return {
        "report_type": report_type,
        "app_name": "FruitVista",
        "tagline": "Fresh Insights, Smarter Markets",
        "title": meta["title"],
        "description": meta["summary"],
        "generated_at": generated.strftime("%d %b %Y, %H:%M"),
        "generated_iso": generated.strftime("%Y-%m-%d %H:%M:%S"),
        "filter_rows": filter_rows(filters),
        "summary": summary_rows,
        "tables": tables,
        "charts": charts,
        "notes": notes,
        "has_data": has_data,
    }
