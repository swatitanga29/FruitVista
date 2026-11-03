"""Read-only insight generation for the administrator Market Insights module.

Insights are not a new set of figures. Every number on the page is asked from
the module that already owns it - sales from :mod:`models.analytics`, stock
from :mod:`models.inventory` and wastage from :mod:`models.wastage` - so an
insight always agrees with the Sales Analytics, Inventory, Wastage and Reports
dashboards. This service only arranges those figures and writes the plain
language observations around them.

Three rules shape the wording every insight is built with:

* an observation is only made from a figure that exists in the selection, and
  the period and the district it covers are always stated beside it;
* a correlation is never presented as a proven cause - the wording says the
  data "shows" or "records", never "because";
* the imported synthetic dataset and the manually entered records are labelled
  separately, and an insight whose evidence comes from the synthetic dataset
  is tagged as such.

Nothing here writes to the database: the whole module is read-only.

Filter handling, the date bounds and the currency, weight and percentage
formatters are re-exported from :mod:`services.reports_service` so the two
modules validate a query string and print a figure in exactly the same way.
"""

from models import analytics as analytics_repo
from models import inventory as inventory_repo
from models import sales as sales_repo
from models import wastage as wastage_repo
from services import reports_service
from services import wastage_service

# ---------------------------------------------------------------------------
# Re-exports - the routes import everything they need from this one module.
# ---------------------------------------------------------------------------
RECORD_SOURCE_ALL = reports_service.RECORD_SOURCE_ALL
RECORD_SOURCE_LABELS = reports_service.RECORD_SOURCE_LABELS
SYNTHETIC_NOTICE = reports_service.SYNTHETIC_NOTICE
INVENTORY_SOURCE_NOTE = reports_service.INVENTORY_SOURCE_NOTE

format_inr = reports_service.format_inr
format_kg = reports_service.format_kg
format_int = reports_service.format_int
format_percent = reports_service.format_percent
format_month = reports_service.format_month
category_label = reports_service.category_label

filter_options = reports_service.filter_options
default_filters = reports_service.default_filters
parse_filters = reports_service.parse_filters
filter_query = reports_service.filter_query
data_bounds = reports_service.data_bounds

# ---------------------------------------------------------------------------
# Limits and labels
# ---------------------------------------------------------------------------
# How many fruits each ranking table shows.
TOP_LIMIT = 5
# How many districts, fruits or positions a table or a chart panel shows.
TABLE_LIMIT = 10
# The longest monthly series a table or a chart draws, newest months kept.
MONTH_LIMIT = 24
# How many reorder suggestions are offered.
REORDER_LIMIT = 5

MONTH_LABELS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# The open explanation of how "overstocked" is decided, shown with the table.
OVERSTOCK_RULE = (
    "Overstocked means closing stock greater than "
    f"{inventory_repo.OVERSTOCK_MULTIPLIER:g} times the reorder level. The "
    "reorder level is the only stock threshold the dataset records, so it is "
    "the only defensible anchor for this rule; the multiple is a FruitVista "
    "rule, not a market fact. Positions with no reorder level are not listed."
)

REORDER_RULE = (
    "A reorder suggestion brings a low or out-of-stock position back up to its "
    "reorder level, so the suggested quantity is reorder level minus closing "
    "stock. It is a minimum top-up based on the stored threshold, not a "
    "forecast of future demand."
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def calendar_month_label(value):
    """Turn a two-digit calendar month ('03') into 'Mar'."""
    text = str(value or "")

    if text.isdigit() and 1 <= int(text) <= 12:
        return MONTH_LABELS[int(text) - 1]

    return text


def _period_label(filters):
    """The period an insight covers, in words."""
    start = filters.get("date_from")
    end = filters.get("date_to")

    if not start and not end:
        return "the whole stored period"

    return f"{start or 'the earliest record'} to {end or 'the latest record'}"


def _display_filters(filters):
    """Add the readable names an insight quotes to the filter dictionary."""
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
    source = filters.get("record_source") or RECORD_SOURCE_ALL

    return {
        **filters,
        "district_name": district_name,
        "fruit_name": fruit_name,
        "category_display": (
            category_label(filters["category"]) if filters.get("category") else ""
        ),
        "source_display": RECORD_SOURCE_LABELS.get(source, "All records"),
        "period_label": _period_label(filters),
        "scope_label": f"{district_name} district" if district_name else "all districts",
    }


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


def _inventory_filters(filters):
    """The filters shaped for :mod:`models.inventory`.

    Inventory always reads every source (historical rows, manual sales and
    stock movements), so the record-source filter is deliberately not passed
    on - see ``INVENTORY_SOURCE_NOTE``.
    """
    return {
        "date_from": filters.get("date_from") or "",
        "date_to": filters.get("date_to") or "",
        "district_id": filters.get("district_id"),
        "category": filters.get("category") or "",
        "fruit_id": filters.get("fruit_id"),
        "stock_status": "",
    }


def _insight(section, title, observation, evidence, suggestion=None,
             tone="neutral", synthetic=False):
    """One observation panel: what was found, the data behind it, what to do."""
    return {
        "section": section,
        "title": title,
        "observation": observation,
        "evidence": evidence,
        "suggestion": suggestion,
        "tone": tone,
        "synthetic": synthetic,
    }


# ---------------------------------------------------------------------------
# Data assembly - one function per insight area
# ---------------------------------------------------------------------------
def _sales_data(filters):
    """Every sales figure the sales and pattern insights are built from."""
    summary = analytics_repo.summary(filters)
    monthly = [dict(row) for row in analytics_repo.monthly_series(filters)]
    points, granularity = analytics_repo.daily_series(filters)

    # Month-to-month change, from the two newest months in the selection.
    growth = {"available": False}

    if len(monthly) >= 2:
        previous, latest = monthly[-2], monthly[-1]
        change = float(latest["revenue"]) - float(previous["revenue"])
        growth = {
            "available": True,
            "previous_label": format_month(previous["label"]),
            "latest_label": format_month(latest["label"]),
            "previous_revenue": float(previous["revenue"]),
            "latest_revenue": float(latest["revenue"]),
            "change": change,
            "change_percent": (
                change / float(previous["revenue"]) * 100
                if previous["revenue"] else None
            ),
        }

    return {
        "summary": summary,
        "top_quantity": [dict(row) for row in
                         analytics_repo.fruit_ranking(filters, "quantity_desc", TOP_LIMIT)],
        "bottom_quantity": [dict(row) for row in
                            analytics_repo.fruit_ranking(filters, "quantity_asc", TOP_LIMIT)],
        "top_revenue": [dict(row) for row in
                        analytics_repo.fruit_ranking(filters, "revenue", TABLE_LIMIT)],
        "category": [dict(row) for row in analytics_repo.category_sales(filters)],
        "monthly": monthly,
        "yearly": [dict(row) for row in analytics_repo.yearly_series(filters)],
        "daily": {"granularity": granularity, "points": points},
        "growth": growth,
        "source_split": [dict(row) for row in analytics_repo.source_split(filters)],
    }


def _seasonal_data(filters):
    """The seasonal shape: calendar months, month-wise leaders, stock vs demand."""
    return {
        "months": [dict(row) for row in analytics_repo.seasonal_series(filters)],
        "top_by_month": [dict(row) for row in analytics_repo.top_fruit_by_month(filters)],
        "availability": [dict(row) for row in
                         analytics_repo.availability_vs_demand(filters, TABLE_LIMIT)],
    }


def _inventory_data(filters):
    """Low stock, overstock, purchases and reorder suggestions."""
    latest, low = inventory_repo.low_stock_positions(filters, limit=TABLE_LIMIT)
    _, over = inventory_repo.overstocked_positions(filters, limit=TABLE_LIMIT)

    low_rows = [dict(row) for row in low]
    over_rows = [dict(row) for row in over]

    # A suggestion tops the position back up to its reorder level.
    reorder = []
    for row in low_rows[:REORDER_LIMIT]:
        needed = float(row["reorder_level"]) - float(row["closing_stock"])
        reorder.append({
            **row,
            "suggested_quantity": round(needed, 2) if needed > 0 else 0.0,
        })

    return {
        "summary": inventory_repo.summary(filters),
        "district": [dict(row) for row in inventory_repo.district_summary(filters)],
        "category": [dict(row) for row in inventory_repo.category_summary(filters)],
        "latest_date": latest,
        "low_stock": low_rows,
        "overstocked": over_rows,
        "purchases": [dict(row) for row in
                      inventory_repo.purchase_by_fruit(filters, TABLE_LIMIT)],
        "reorder": reorder,
    }


def _wastage_data(filters):
    """Damaged quantity, cost and the areas where wastage is concentrated."""
    summary = wastage_repo.summary(filters)
    percentage = wastage_service.wastage_percentage(
        summary["damaged_quantity"], summary["available_stock"]
    )

    return {
        "summary": {**summary, "wastage_percentage": percentage},
        "district": [dict(row) for row in wastage_repo.district_summary(filters)],
        "category": [dict(row) for row in wastage_repo.category_summary(filters)],
        "fruits": [dict(row) for row in wastage_repo.fruit_summary(filters, TABLE_LIMIT)],
        "monthly": wastage_repo.period_summary(filters, "month"),
        "sources": wastage_repo.source_totals(filters),
    }


def _district_data(sales_filters, inventory_filters, wastage_filters):
    """The five districts with sales, stock and wastage side by side."""
    sales_rows = {row["label"]: dict(row)
                  for row in analytics_repo.district_revenue(sales_filters)}
    stock_rows = {row["label"]: dict(row)
                  for row in inventory_repo.district_summary(inventory_filters)}
    wastage_rows = {row["label"]: dict(row)
                    for row in wastage_repo.district_summary(wastage_filters)}

    # Every supported district is listed, even when it has no record in the
    # selection, so the five-district comparison is always complete.
    names = sorted(
        {row["name"] for row in sales_repo.list_districts()}
        | set(sales_rows) | set(stock_rows) | set(wastage_rows)
    )

    rows = []
    for name in names:
        sales = sales_rows.get(name, {})
        stock = stock_rows.get(name, {})
        waste = wastage_rows.get(name, {})

        rows.append({
            "district": name,
            "quantity": float(sales.get("quantity", 0) or 0),
            "revenue": float(sales.get("revenue", 0) or 0),
            "closing_stock": float(stock.get("closing_stock", 0) or 0),
            "stock_sold": float(stock.get("quantity_sold", 0) or 0),
            "damaged_quantity": float(waste.get("damaged_quantity", 0) or 0),
            "wastage_cost": float(waste.get("wastage_cost", 0) or 0),
        })

    total_revenue = sum(row["revenue"] for row in rows)

    for row in rows:
        row["revenue_share"] = (
            row["revenue"] / total_revenue * 100 if total_revenue else None
        )

    rows.sort(key=lambda row: row["revenue"], reverse=True)
    return {"rows": rows, "total_revenue": total_revenue}


# ---------------------------------------------------------------------------
# Insight text - built only from figures that exist in the selection
# ---------------------------------------------------------------------------
def _sales_insights(sales, display):
    """Best and least-selling fruits, revenue leaders, growth, categories."""
    period = display["period_label"]
    scope = display["scope_label"]
    items = []
    summary = sales["summary"]

    if not summary["records"]:
        return items

    total_quantity = float(summary["quantity"] or 0)

    best = sales["top_quantity"][0] if sales["top_quantity"] else None
    if best and total_quantity:
        share = float(best["quantity"]) / total_quantity * 100
        items.append(_insight(
            "Sales",
            f"{best['label']} is the best-selling fruit",
            f"{best['label']} sold {format_kg(best['quantity'])}, more than any "
            "other fruit in this selection.",
            f"Period: {period}. Scope: {scope}. That is {share:.1f}% of the "
            f"{format_kg(total_quantity)} sold in total.",
            suggestion=f"Keep {best['label']} well stocked - it carries the "
                       "largest share of demand here.",
            tone="positive",
        ))

    weakest = sales["bottom_quantity"][0] if sales["bottom_quantity"] else None
    if weakest:
        items.append(_insight(
            "Sales",
            f"{weakest['label']} is the least-selling fruit",
            f"{weakest['label']} sold {format_kg(weakest['quantity'])}, the "
            "smallest quantity of any fruit that sold in this selection.",
            f"Period: {period}. Scope: {scope}. Fruits with no sale at all are "
            "not ranked, because the dataset records no row for them.",
            suggestion="Review how much is ordered for the slowest-moving "
                       "fruit so stock is not left unsold.",
            tone="warning",
        ))

    top_revenue = sales["top_revenue"][0] if sales["top_revenue"] else None
    if top_revenue:
        items.append(_insight(
            "Sales",
            f"{top_revenue['label']} earns the most revenue",
            f"{top_revenue['label']} brought in {format_inr(top_revenue['revenue'])} "
            f"on {format_kg(top_revenue['quantity'])} sold.",
            f"Period: {period}. Scope: {scope}. Revenue is quantity sold "
            "multiplied by the selling price per kilogram.",
            tone="positive",
        ))

    growth = sales["growth"]
    if growth.get("available"):
        rising = growth["change"] >= 0
        direction = "rose" if rising else "fell"
        percent = (
            f", a change of {growth['change_percent']:+.1f}%"
            if growth["change_percent"] is not None else ""
        )
        items.append(_insight(
            "Sales",
            f"Revenue {direction} from {growth['previous_label']} to "
            f"{growth['latest_label']}",
            f"Revenue moved from {format_inr(growth['previous_revenue'])} to "
            f"{format_inr(growth['latest_revenue'])}{percent}.",
            f"Period: {period}. Scope: {scope}. Two consecutive months are "
            "compared; a monthly change can reflect seasonality, so this is an "
            "observation and not a proven cause.",
            tone="positive" if rising else "warning",
        ))

    if sales["category"]:
        leader = sales["category"][0]
        category_total = sum(float(row["revenue"]) for row in sales["category"])
        share = (
            f" ({float(leader['revenue']) / category_total * 100:.1f}% of the "
            "category total)" if category_total else ""
        )
        items.append(_insight(
            "Sales",
            f"{category_label(leader['label'])} leads category revenue",
            f"{category_label(leader['label'])} fruits earned "
            f"{format_inr(leader['revenue'])}{share}.",
            f"Period: {period}. Scope: {scope}. Categories are the three "
            "groups the catalogue uses: Dry Fruits, Seasonal Fruits and "
            "General Fruits.",
            tone="neutral",
        ))

    if sales["yearly"]:
        newest = sales["yearly"][-1]
        items.append(_insight(
            "Sales",
            f"{newest['label']} is the newest year in the selection",
            f"{newest['label']} recorded {format_kg(newest['quantity'])} sold "
            f"and {format_inr(newest['revenue'])} in revenue.",
            f"Period: {period}. Scope: {scope}. The yearly figure is the sum of "
            "every record dated in that year.",
            tone="neutral",
        ))

    return items


def _seasonal_insights(seasonal, display):
    """Seasonal demand, seasonal prices and month-wise performance."""
    period = display["period_label"]
    scope = display["scope_label"]
    items = []
    months = [row for row in seasonal["months"] if row["quantity"]]

    if months:
        peak = max(months, key=lambda row: row["quantity"])
        quiet = min(months, key=lambda row: row["quantity"])
        items.append(_insight(
            "Seasonal",
            f"{calendar_month_label(peak['label'])} carries the highest demand",
            f"Folding every year in the selection onto the calendar, "
            f"{calendar_month_label(peak['label'])} shows the largest quantity "
            f"sold at {format_kg(peak['quantity'])}.",
            f"Period: {period}. Scope: {scope}. All years in the selection are "
            "added together per calendar month, so this is a seasonal shape "
            "rather than one particular year.",
            suggestion=f"Plan supply for {calendar_month_label(peak['label'])} "
                       "ahead of the other months.",
            tone="neutral",
        ))
        items.append(_insight(
            "Seasonal",
            f"{calendar_month_label(quiet['label'])} is the quietest month",
            f"{calendar_month_label(quiet['label'])} shows the smallest "
            f"quantity sold at {format_kg(quiet['quantity'])}.",
            f"Period: {period}. Scope: {scope}. Only calendar months that sold "
            "something are compared.",
            suggestion="Use the quieter month to review pricing and storage "
                       "rather than holding excess stock.",
            tone="neutral",
        ))

    priced = [row for row in seasonal["months"] if row["average_price"] is not None]
    if priced:
        highest = max(priced, key=lambda row: row["average_price"])
        lowest = min(priced, key=lambda row: row["average_price"])
        items.append(_insight(
            "Seasonal",
            "Average selling price changes through the year",
            f"The average price is highest in "
            f"{calendar_month_label(highest['label'])} at "
            f"{format_inr(highest['average_price'])} per kg and lowest in "
            f"{calendar_month_label(lowest['label'])} at "
            f"{format_inr(lowest['average_price'])} per kg.",
            f"Period: {period}. Scope: {scope}. The average price is revenue "
            "divided by quantity sold for that calendar month, so it is a "
            "recorded average, not a quoted market rate.",
            tone="neutral",
        ))

    leaders = seasonal["top_by_month"]
    if leaders:
        newest = leaders[-1]
        items.append(_insight(
            "Seasonal",
            f"{format_month(newest['label'])}'s top-selling fruit is "
            f"{newest['fruit']}",
            f"In {format_month(newest['label'])} the most sold fruit was "
            f"{newest['fruit']} at {format_kg(newest['quantity'])}.",
            f"Period: {period}. Scope: {scope}. The leader is chosen by "
            "quantity sold within that single month.",
            tone="neutral",
        ))

    availability = seasonal["availability"]
    if availability:
        busiest = availability[0]
        items.append(_insight(
            "Seasonal",
            f"{busiest['label']}'s availability compared with its demand",
            f"{busiest['label']} is the fruit with the highest historical "
            f"demand in this selection at {format_kg(busiest['demand'])}, "
            f"against {format_kg(busiest['availability'])} of available stock "
            "recorded over the same records.",
            f"Period: {period}. Scope: {scope}. Available stock is only "
            "recorded on the historical rows, so this compares the two "
            "historical totals and does not cover manual entries.",
            tone="info",
            synthetic=True,
        ))

    return items


def _inventory_insights(inventory, display):
    """Low stock, overstock, frequent purchases and reorder suggestions."""
    period = display["period_label"]
    scope = display["scope_label"]
    items = []
    latest = inventory["latest_date"]

    low = inventory["low_stock"]
    if low:
        worst = low[0]
        items.append(_insight(
            "Inventory",
            f"{len(low)} stock position(s) are at or below their reorder level",
            f"On {latest}, {len(low)} district-and-fruit positions are low or "
            f"out of stock. The lowest is {worst['fruit_name']} in "
            f"{worst['district_name']} at {format_kg(worst['closing_stock'])} "
            f"(reorder level {format_kg(worst['reorder_level'])}).",
            f"Period: {period}. Scope: {scope}. Stock is opening stock plus "
            "purchases plus adjustments, less what was sold and damaged, "
            "exactly as the Inventory dashboard calculates it.",
            suggestion="Raise a purchase for the lowest positions, or record a "
                       "stock adjustment if the stored count is wrong.",
            tone="warning",
            synthetic=True,
        ))

    suggestions = [row for row in inventory["reorder"] if row["suggested_quantity"] > 0]
    if suggestions:
        first = suggestions[0]
        items.append(_insight(
            "Inventory",
            "Reorder suggestions based on the stored thresholds",
            f"Topping the lowest position up to its reorder level means buying "
            f"about {format_kg(first['suggested_quantity'])} of "
            f"{first['fruit_name']} for {first['district_name']}.",
            f"Period: {period}. Scope: {scope}. " + REORDER_RULE,
            suggestion="Treat the figure as a minimum top-up and adjust it for "
                       "how quickly the fruit sells.",
            tone="info",
        ))

    over = inventory["overstocked"]
    if over:
        worst = over[0]
        items.append(_insight(
            "Inventory",
            f"{len(over)} stock position(s) hold far more than their reorder level",
            f"{worst['fruit_name']} in {worst['district_name']} holds "
            f"{format_kg(worst['closing_stock'])} against a reorder level of "
            f"{format_kg(worst['reorder_level'])}.",
            f"Period: {period}. Scope: {scope}. " + OVERSTOCK_RULE,
            suggestion="Pause replenishment for the most overstocked fruits "
                       "until the stock comes down.",
            tone="warning",
            synthetic=True,
        ))

    purchases = inventory["purchases"]
    if purchases:
        busiest = purchases[0]
        items.append(_insight(
            "Inventory",
            f"{busiest['label']} is the most frequently purchased fruit",
            f"{busiest['label']} accounts for the largest purchased quantity "
            f"at {format_kg(busiest['quantity'])} across "
            f"{format_int(busiest['records'])} purchase record(s).",
            f"Period: {period}. Scope: {scope}. Only purchases entered in the "
            "Inventory module appear here; the historical dataset carries no "
            "purchase records of its own.",
            tone="neutral",
        ))

    stock_by_district = [row for row in inventory["district"] if row["records"]]
    if len(stock_by_district) >= 2:
        ordered = sorted(stock_by_district, key=lambda row: row["closing_stock"])
        lowest, highest = ordered[0], ordered[-1]
        items.append(_insight(
            "Inventory",
            "Closing stock differs between districts",
            f"Closing stock ranges from {format_kg(lowest['closing_stock'])} in "
            f"{lowest['label']} to {format_kg(highest['closing_stock'])} in "
            f"{highest['label']}.",
            f"Period: {period}. Scope: {scope}. Each district total is the sum "
            "of its matching rows, not a single stock snapshot.",
            suggestion="Move stock, or rebalance the next orders, from the "
                       "districts holding the most towards the ones holding the "
                       "least.",
            tone="neutral",
            synthetic=True,
        ))

    return items


def _wastage_insights(wastage, display):
    """Where wastage is concentrated and what it costs."""
    period = display["period_label"]
    scope = display["scope_label"]
    items = []
    summary = wastage["summary"]

    if summary["records"]:
        items.append(_insight(
            "Wastage",
            "Wastage recorded in this selection",
            f"{format_kg(summary['damaged_quantity'])} was damaged, worth "
            f"{format_inr(summary['wastage_cost'])}"
            + (
                f", or {format_percent(summary['wastage_percentage'])} of the "
                "available stock recorded"
                if summary["wastage_percentage"] is not None else ""
            )
            + ".",
            f"Period: {period}. Scope: {scope}. Wastage cost is damaged "
            "quantity multiplied by cost per kilogram; the percentage compares "
            "damage with the historical available stock for the same filters.",
            tone="warning",
            synthetic=True,
        ))

    fruits = wastage["fruits"]
    if fruits:
        worst = fruits[0]
        items.append(_insight(
            "Wastage",
            f"{worst['label']} has the highest wastage",
            f"{worst['label']} lost {format_kg(worst['damaged_quantity'])}, "
            f"worth {format_inr(worst['wastage_cost'])}.",
            f"Period: {period}. Scope: {scope}. Damaged quantity is the "
            "historical damage plus any wastage recorded in the Wastage "
            "module, added together once.",
            suggestion=f"Review handling and storage for {worst['label']} - it "
                       "is the largest single loss in this selection.",
            tone="warning",
            synthetic=True,
        ))

    districts = [row for row in wastage["district"] if row["records"]]
    if districts:
        worst = max(districts, key=lambda row: row["damaged_quantity"])
        items.append(_insight(
            "Wastage",
            f"{worst['label']} records the most wastage",
            f"{worst['label']} lost {format_kg(worst['damaged_quantity'])}, "
            f"worth {format_inr(worst['wastage_cost'])}.",
            f"Period: {period}. Scope: {scope}. Every district with wastage in "
            "the selection is compared by damaged quantity.",
            suggestion="Start reduction work in the district with the largest "
                       "loss before spreading it across all five.",
            tone="warning",
            synthetic=True,
        ))

    monthly = [row for row in wastage["monthly"] if row["damaged_quantity"]]
    if monthly:
        peak = max(monthly, key=lambda row: row["damaged_quantity"])
        items.append(_insight(
            "Wastage",
            f"{format_month(peak['label'])} shows the highest monthly wastage",
            f"{format_month(peak['label'])} recorded "
            f"{format_kg(peak['damaged_quantity'])} of damage worth "
            f"{format_inr(peak['wastage_cost'])}.",
            f"Period: {period}. Scope: {scope}. Only months with recorded "
            "damage are compared.",
            tone="neutral",
            synthetic=True,
        ))

    return items


def _district_insights(districts, display):
    """The five-district comparison, read back as one observation."""
    period = display["period_label"]
    items = []
    rows = [row for row in districts["rows"] if row["revenue"]]

    if rows and districts["total_revenue"]:
        leader = rows[0]
        items.append(_insight(
            "Districts",
            f"{leader['district']} leads on sales revenue",
            f"{leader['district']} earned {format_inr(leader['revenue'])}, "
            f"{leader['revenue_share']:.1f}% of the "
            f"{format_inr(districts['total_revenue'])} sold across the "
            "districts.",
            f"Period: {period}. Scope: all districts. Revenue is quantity sold "
            "multiplied by the selling price per kilogram.",
            tone="positive",
        ))

    waste = [row for row in districts["rows"] if row["damaged_quantity"]]
    if len(waste) >= 2:
        highest = max(waste, key=lambda row: row["damaged_quantity"])
        lowest = min(waste, key=lambda row: row["damaged_quantity"])
        items.append(_insight(
            "Districts",
            "Wastage is not spread evenly across districts",
            f"{highest['district']} lost the most at "
            f"{format_kg(highest['damaged_quantity'])}, while "
            f"{lowest['district']} lost the least at "
            f"{format_kg(lowest['damaged_quantity'])}.",
            f"Period: {period}. Scope: all districts. The difference is an "
            "observation about the stored data and does not by itself explain "
            "why one district loses more than another.",
            suggestion="Compare the storage and transport arrangements of the "
                       "two districts before deciding what to change.",
            tone="info",
            synthetic=True,
        ))

    return items


# ---------------------------------------------------------------------------
# Tables and charts
# ---------------------------------------------------------------------------
def _table(title, columns, rows, note=None):
    return {"title": title, "columns": columns, "rows": rows, "note": note}


def _bar(chart_id, title, subtitle, labels, values, unit, horizontal=False):
    return {
        "id": chart_id, "title": title, "subtitle": subtitle, "kind": "bar",
        "unit": unit, "horizontal": horizontal, "labels": labels, "values": values,
    }


def _line(chart_id, title, subtitle, labels, values, unit):
    return {
        "id": chart_id, "title": title, "subtitle": subtitle, "kind": "line",
        "unit": unit, "horizontal": False, "labels": labels, "values": values,
    }


def _build_tables(sales, seasonal, inventory, wastage, districts):
    """Every table on the page, formatted the same way as the Reports page."""
    tables = []

    if sales["top_quantity"]:
        tables.append(_table(
            "Best-selling fruits",
            ["Fruit", "Category", "Quantity (kg)", "Revenue (INR)"],
            [[row["label"], category_label(row["category"]),
              f"{float(row['quantity']):,.2f}", f"{float(row['revenue']):,.2f}"]
             for row in sales["top_quantity"]],
        ))

    if sales["bottom_quantity"]:
        tables.append(_table(
            "Least-selling fruits",
            ["Fruit", "Category", "Quantity (kg)", "Revenue (INR)"],
            [[row["label"], category_label(row["category"]),
              f"{float(row['quantity']):,.2f}", f"{float(row['revenue']):,.2f}"]
             for row in sales["bottom_quantity"]],
            note="Only fruits that sold at least once are ranked; a fruit with "
                 "no sale has no row to rank.",
        ))

    if sales["top_revenue"]:
        tables.append(_table(
            "Highest-revenue fruits",
            ["Fruit", "Category", "Revenue (INR)", "Quantity (kg)"],
            [[row["label"], category_label(row["category"]),
              f"{float(row['revenue']):,.2f}", f"{float(row['quantity']):,.2f}"]
             for row in sales["top_revenue"]],
        ))

    if sales["category"]:
        tables.append(_table(
            "Fruit category performance",
            ["Fruit category", "Quantity sold (kg)", "Revenue (INR)"],
            [[category_label(row["label"]), f"{float(row['quantity']):,.2f}",
              f"{float(row['revenue']):,.2f}"] for row in sales["category"]],
        ))

    if sales["monthly"]:
        tables.append(_table(
            "Monthly sales pattern",
            ["Month", "Quantity (kg)", "Revenue (INR)"],
            [[format_month(row["label"]), f"{float(row['quantity']):,.2f}",
              f"{float(row['revenue']):,.2f}"]
             for row in sales["monthly"][-MONTH_LIMIT:]],
        ))

    if sales["yearly"]:
        tables.append(_table(
            "Yearly sales pattern",
            ["Year", "Quantity (kg)", "Revenue (INR)"],
            [[row["label"], f"{float(row['quantity']):,.2f}",
              f"{float(row['revenue']):,.2f}"] for row in sales["yearly"]],
        ))

    if seasonal["months"]:
        tables.append(_table(
            "Seasonal demand and average price by calendar month",
            ["Calendar month", "Quantity sold (kg)", "Revenue (INR)",
             "Average price (INR/kg)"],
            [[calendar_month_label(row["label"]),
              f"{float(row['quantity']):,.2f}", f"{float(row['revenue']):,.2f}",
              f"{float(row['average_price']):,.2f}"
              if row["average_price"] is not None else "Not available"]
             for row in seasonal["months"]],
            note="Every year in the selection is added together for each "
                 "calendar month, so the figures show the seasonal shape.",
        ))

    if seasonal["top_by_month"]:
        tables.append(_table(
            "Month-wise top-selling fruit",
            ["Month", "Top-selling fruit", "Category", "Quantity (kg)"],
            [[format_month(row["label"]), row["fruit"],
              category_label(row["category"]), f"{float(row['quantity']):,.2f}"]
             for row in seasonal["top_by_month"]],
        ))

    if seasonal["availability"]:
        tables.append(_table(
            "Historical availability and demand",
            ["Fruit", "Category", "Demand (kg sold)", "Availability (kg)"],
            [[row["label"], category_label(row["category"]),
              f"{float(row['demand']):,.2f}", f"{float(row['availability']):,.2f}"]
             for row in seasonal["availability"]],
            note="Both columns come from the synthetic historical dataset. "
                 "Available stock is only recorded there.",
        ))

    if inventory["low_stock"]:
        tables.append(_table(
            "Low-stock positions"
            + (f" (latest day: {inventory['latest_date']})"
               if inventory["latest_date"] else ""),
            ["District", "Fruit", "Category", "Closing (kg)",
             "Reorder level (kg)", "Status"],
            [[row["district_name"], row["fruit_name"],
              category_label(row["fruit_category"]),
              f"{float(row['closing_stock']):,.2f}",
              f"{float(row['reorder_level']):,.2f}",
              "Out of Stock" if not float(row["closing_stock"]) > 0 else "Low Stock"]
             for row in inventory["low_stock"]],
            note="A position is listed when its closing stock is at or below "
                 "its reorder level on the latest day of the selection.",
        ))

    if inventory["overstocked"]:
        tables.append(_table(
            "Overstocked positions",
            ["District", "Fruit", "Category", "Closing (kg)", "Reorder level (kg)"],
            [[row["district_name"], row["fruit_name"],
              category_label(row["fruit_category"]),
              f"{float(row['closing_stock']):,.2f}",
              f"{float(row['reorder_level']):,.2f}"]
             for row in inventory["overstocked"]],
            note=OVERSTOCK_RULE,
        ))

    if inventory["purchases"]:
        tables.append(_table(
            "Frequently purchased fruits",
            ["Fruit", "Category", "Purchases", "Quantity (kg)", "Amount (INR)"],
            [[row["label"], category_label(row["category"]),
              format_int(row["records"]), f"{float(row['quantity']):,.2f}",
              f"{float(row['amount']):,.2f}"] for row in inventory["purchases"]],
            note="Only purchases entered in the Inventory module are counted.",
        ))

    if inventory["reorder"]:
        tables.append(_table(
            "Reorder suggestions",
            ["District", "Fruit", "Closing (kg)", "Reorder level (kg)",
             "Suggested top-up (kg)"],
            [[row["district_name"], row["fruit_name"],
              f"{float(row['closing_stock']):,.2f}",
              f"{float(row['reorder_level']):,.2f}",
              f"{float(row['suggested_quantity']):,.2f}"]
             for row in inventory["reorder"]],
            note=REORDER_RULE,
        ))

    if wastage["fruits"]:
        tables.append(_table(
            "Fruits with the highest wastage",
            ["Fruit", "Category", "Damaged (kg)", "Wastage cost (INR)"],
            [[row["label"], category_label(row["category"]),
              f"{float(row['damaged_quantity']):,.2f}",
              f"{float(row['wastage_cost']):,.2f}"] for row in wastage["fruits"]],
        ))

    if wastage["district"]:
        tables.append(_table(
            "Districts with the highest wastage",
            ["District", "Damaged (kg)", "Wastage cost (INR)"],
            [[row["label"], f"{float(row['damaged_quantity']):,.2f}",
              f"{float(row['wastage_cost']):,.2f}"]
             for row in sorted(wastage["district"],
                               key=lambda r: r["damaged_quantity"], reverse=True)],
        ))

    if wastage["monthly"]:
        tables.append(_table(
            "Monthly wastage pattern",
            ["Month", "Damaged (kg)", "Wastage cost (INR)"],
            [[format_month(row["label"]),
              f"{float(row['damaged_quantity']):,.2f}",
              f"{float(row['wastage_cost']):,.2f}"]
             for row in wastage["monthly"][-MONTH_LIMIT:]],
        ))

    tables.append(_table(
        "District comparison",
        ["District", "Quantity sold (kg)", "Revenue (INR)", "Revenue share",
         "Closing stock (kg)", "Wastage (kg)", "Wastage cost (INR)"],
        [[row["district"], f"{row['quantity']:,.2f}", f"{row['revenue']:,.2f}",
          f"{row['revenue_share']:.1f} %" if row["revenue_share"] is not None
          else "Not available",
          f"{row['closing_stock']:,.2f}", f"{row['damaged_quantity']:,.2f}",
          f"{row['wastage_cost']:,.2f}"] for row in districts["rows"]],
        note="Sales and wastage respect the record-source filter; stock always "
             "combines the historical dataset, manual sales and stock "
             "movements, as the Inventory dashboard does.",
    ))

    return tables


def _build_charts(sales, seasonal, inventory, wastage):
    """The chart series the page draws, whatever the current filters."""
    charts = []

    if sales["top_revenue"]:
        leaders = sales["top_revenue"][:8]
        charts.append(_bar(
            "insightsTopRevenue", "Top fruits by revenue",
            "The fruits earning the most in the selection",
            [row["label"] for row in leaders],
            [float(row["revenue"]) for row in leaders], "inr", horizontal=True,
        ))

    if sales["bottom_quantity"]:
        charts.append(_bar(
            "insightsBottomQuantity", "Least-selling fruits",
            "The fruits sold in the smallest quantity",
            [row["label"] for row in sales["bottom_quantity"]],
            [float(row["quantity"]) for row in sales["bottom_quantity"]],
            "kg", horizontal=True,
        ))

    if sales["monthly"]:
        tail = sales["monthly"][-MONTH_LIMIT:]
        charts.append(_line(
            "insightsMonthlyRevenue", "Monthly sales pattern",
            "Revenue recorded in each month of the selection",
            [format_month(row["label"]) for row in tail],
            [float(row["revenue"]) for row in tail], "inr",
        ))

    if seasonal["months"]:
        charts.append(_bar(
            "insightsSeasonalDemand", "Seasonal demand by calendar month",
            "Quantity sold in each calendar month, all years added together",
            [calendar_month_label(row["label"]) for row in seasonal["months"]],
            [float(row["quantity"]) for row in seasonal["months"]], "kg",
        ))
        charts.append(_line(
            "insightsSeasonalPrice", "Seasonal average selling price",
            "Revenue divided by quantity sold, per calendar month",
            [calendar_month_label(row["label"]) for row in seasonal["months"]],
            [float(row["average_price"]) if row["average_price"] is not None else None
             for row in seasonal["months"]], "inr",
        ))

    if sales["category"]:
        charts.append(_bar(
            "insightsCategoryRevenue", "Fruit category performance",
            "Revenue for each of the three fruit categories",
            [category_label(row["label"]) for row in sales["category"]],
            [float(row["revenue"]) for row in sales["category"]], "inr",
        ))

    if any(row["revenue"] for row in _district_chart_rows(sales)):
        rows = [row for row in _district_chart_rows(sales) if row["revenue"]]
        charts.append(_bar(
            "insightsDistrictRevenue", "District revenue comparison",
            "Revenue for every district in the selection",
            [row["label"] for row in rows],
            [float(row["revenue"]) for row in rows], "inr",
        ))

    if wastage["fruits"]:
        charts.append(_bar(
            "insightsWastageFruits", "Highest-wastage fruits",
            "Damaged kilograms for the fruits losing the most",
            [row["label"] for row in wastage["fruits"]],
            [float(row["damaged_quantity"]) for row in wastage["fruits"]],
            "kg", horizontal=True,
        ))

    return charts


def _district_chart_rows(sales):
    """Sales rows per district, used only to decide whether to draw the chart."""
    return sales["district_chart_rows"]


def _build_cards(sales, inventory, wastage, best_seller):
    """The summary metric cards at the top of the page."""
    summary = sales["summary"]

    return [
        {"label": "Total revenue",
         "value": format_inr(summary["revenue"]),
         "hint": "quantity sold x selling price"},
        {"label": "Total quantity sold",
         "value": format_kg(summary["quantity"]),
         "hint": "sum of the stored sale quantities"},
        {"label": "Best-selling fruit",
         "value": best_seller or "Not available",
         "hint": "largest quantity sold in the selection"},
        {"label": "Total closing stock",
         "value": format_kg(inventory["summary"]["closing_stock"]),
         "hint": "available less sold and damaged"},
        {"label": "Total damaged quantity",
         "value": format_kg(wastage["summary"]["damaged_quantity"]),
         "hint": "historical and manual wastage together"},
        {"label": "Wastage percentage",
         "value": format_percent(wastage["summary"]["wastage_percentage"]),
         "hint": "damaged quantity / available stock x 100"},
    ]


def _build_notes(sales, inventory, wastage):
    """How the figures are produced, and what they deliberately do not claim."""
    return [
        "Every figure on this page is calculated in the database by the module "
        "that owns it: sales by Sales Analytics, stock by Inventory Management "
        "and wastage by Wastage Management. Nothing is recalculated here, so "
        "an insight always agrees with the matching dashboard and report.",
        "The observations describe the stored data for the filters above. They "
        "are not proof of cause: a rise or fall in one measure is reported "
        "alongside another, never as the reason for it.",
        "Revenue is quantity sold x selling price per kg. Quantity is the sum "
        "of the stored sale quantities in kilograms. The average price in the "
        "seasonal tables is revenue divided by quantity, a recorded average "
        "rather than a quoted market rate.",
        INVENTORY_SOURCE_NOTE,
        "Sales and wastage respect the record-source filter, so the imported "
        "synthetic dataset and the manual entries can be read separately. "
        "Inventory always combines every source because stock is only "
        "meaningful as the full position.",
        SYNTHETIC_NOTICE,
    ]


def build_insights(filters):
    """Assemble the whole Market Insights page: cards, tables, charts and text.

    ``filters`` is the validated dictionary returned by :func:`parse_filters`.
    The same payload drives the page and the JSON API, so the two can never
    disagree.
    """
    display = _display_filters(filters)

    sales_filters = _sales_filters(filters)
    inventory_filters = _inventory_filters(filters)
    wastage_filters = _wastage_filters(filters)

    sales = _sales_data(sales_filters)
    seasonal = _seasonal_data(sales_filters)
    inventory = _inventory_data(inventory_filters)
    wastage = _wastage_data(wastage_filters)
    districts = _district_data(sales_filters, inventory_filters, wastage_filters)

    # The distict chart is drawn from the same sales rows used everywhere else.
    sales["district_chart_rows"] = [dict(row) for row in
                                    analytics_repo.district_revenue(sales_filters)]

    best_seller = (
        sales["top_quantity"][0]["label"] if sales["top_quantity"] else ""
    )

    insights = (
        _sales_insights(sales, display)
        + _seasonal_insights(seasonal, display)
        + _inventory_insights(inventory, display)
        + _wastage_insights(wastage, display)
        + _district_insights(districts, display)
    )

    return {
        "ok": True,
        "filters": display,
        "period": {
            "from": display["date_from"],
            "to": display["date_to"],
            "label": display["period_label"],
            "scope": display["scope_label"],
        },
        "bounds": data_bounds(),
        "cards": _build_cards(sales, inventory, wastage, best_seller),
        "source_split": [
            {**row, "label": RECORD_SOURCE_LABELS.get(row["label"], row["label"])}
            for row in sales["source_split"]
        ],
        "sales": sales,
        "seasonal": seasonal,
        "inventory": inventory,
        "wastage": wastage,
        "districts": districts,
        "tables": _build_tables(sales, seasonal, inventory, wastage, districts),
        "charts": _build_charts(sales, seasonal, inventory, wastage),
        "insights": insights,
        "notes": _build_notes(sales, inventory, wastage),
        "synthetic_notice": SYNTHETIC_NOTICE,
        "has_data": bool(
            sales["summary"]["records"]
            or inventory["summary"]["records"]
            or wastage["summary"]["records"]
        ),
    }
