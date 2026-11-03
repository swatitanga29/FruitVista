"""Prediction assembly for the administrator Machine Learning module.

This service does not calculate any market figure of its own. Every number on
the page is produced by :mod:`ml.predictor` from a model that :mod:`ml.training`
has already trained and saved: the forecast engine walks the calendar forward
one day at a time, and the evaluation figures are the ones the model itself
recorded on the days it was never allowed to learn from.

Three rules shape everything below:

* a forecast is only produced when a trained model exists *and* the chosen
  fruit and district series holds enough real recorded days to describe itself
  from history - otherwise the panel reports why instead of inventing a value;
* every prediction is labelled an experimental estimate. Each model is trained
  on the synthetic FruitVista dataset, so its output is not a verified
  real-world market price or demand;
* training is never triggered by opening a page. It happens only when an
  administrator presses the train button, and it always writes a *new* model
  version rather than overwriting an older one.

Filter handling, the date bounds and the currency, weight and percentage
formatters are re-exported from :mod:`services.reports_service` so the ML page
validates a query string and prints a figure exactly as the Reports and
Insights pages do.

Nothing here writes into ``daily_records``, ``stock_movements`` or
``wastage_records``. Saving a forecast only adds a row to ``ml_predictions``.
"""

import json
from datetime import date, datetime

from flask import current_app

from ml import dataset
from ml import predictor
from ml import repository
from ml import training
from services import reports_service
from services.validators import clean_text

# ---------------------------------------------------------------------------
# Re-exports - the routes and templates import everything from this module.
# ---------------------------------------------------------------------------
format_inr = reports_service.format_inr
format_kg = reports_service.format_kg
format_int = reports_service.format_int
format_percent = reports_service.format_percent
format_month = reports_service.format_month
category_label = reports_service.category_label

filter_options = reports_service.filter_options
data_bounds = reports_service.data_bounds
RECORD_SOURCE_ALL = reports_service.RECORD_SOURCE_ALL
RECORD_SOURCE_LABELS = reports_service.RECORD_SOURCE_LABELS
SYNTHETIC_NOTICE = reports_service.SYNTHETIC_NOTICE

DATE_FORMAT = "%Y-%m-%d"

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# The five views of the prediction dashboard, in the order they are offered.
TABS = {
    "price": {"key": "price", "name": "Price Prediction",
              "task": dataset.PRICE_TARGET},
    "sales": {"key": "sales", "name": "Sales Forecasting",
              "task": dataset.QUANTITY_TARGET},
    "demand": {"key": "demand", "name": "Demand Forecasting",
               "task": dataset.DEMAND_TARGET},
    "performance": {"key": "performance", "name": "Model Performance",
                    "task": None},
    "history": {"key": "history", "name": "Prediction History", "task": None},
}
TAB_ORDER = ("price", "sales", "demand", "performance", "history")
DEFAULT_TAB = "price"

# The colour each line is drawn in, so the observed and predicted series look
# the same on every chart.
COLOR_OBSERVED = "#1b7f4b"
COLOR_PREDICTED = "#7048b6"
COLOR_SECOND = "#2b6cb0"
COLOR_STOCK = "#d98908"

EXPERIMENTAL_NOTICE = (
    "Every prediction on this page is an experimental estimate. The models are "
    "trained on the synthetic FruitVista historical dataset, so their output is "
    "a modelled extension of sample data, not a verified real-world market "
    "price or demand. Treat the figures as a demonstration of the method."
)

EXPERIMENTAL_TAG = "Experimental estimate"

METHOD_NOTES = (
    "Each task is trained on the same chronological window: the most recent "
    "stored days, with the last part of that window held back as a later test "
    "period. A model is never scored on a day it was allowed to learn from, so "
    "the metrics describe genuinely unseen days.",
    "Every candidate is trained on the same rows and scored on the same test "
    "rows, and the one with the lowest RMSE is the version that is saved. The "
    "comparison is therefore fair, and the winning algorithm differs from task "
    "to task rather than being fixed in advance.",
    "A multi-day forecast is built one day at a time: each predicted day is fed "
    "back in as the previous day for the next step, which is exactly how the "
    "model was trained. Every value is held inside the range the training data "
    "actually supports and can never be negative.",
    "Only the historical and manual sales rows are read. Nothing is counted "
    "twice: rows are grouped by day, district and fruit first, so a manual "
    "entry recorded beside a historical row for the same position is added "
    "once.",
    EXPERIMENTAL_NOTICE,
)


# ---------------------------------------------------------------------------
# Filter parsing
# ---------------------------------------------------------------------------
def horizon_bounds():
    """The allowed forecast horizon, from the application configuration."""
    config = current_app.config

    return (
        int(config.get("ML_HORIZON_MIN", 1)),
        int(config.get("ML_HORIZON_MAX", 90)),
        int(config.get("ML_HORIZON_DEFAULT", 30)),
    )


def _read_date(value):
    """Return an ISO date string, or ``None`` when the text is not a date."""
    text = clean_text(value)

    if not text:
        return None

    try:
        return datetime.strptime(text, DATE_FORMAT).date().isoformat()
    except ValueError:
        return None


def _read_int(value):
    """Return a whole number, or ``None`` when the text is not one."""
    text = clean_text(value)

    if not text:
        return None

    try:
        return int(text)
    except ValueError:
        return None


def parse_filters(args):
    """Read the ML filters out of a query string.

    Returns ``(filters, errors)``. The fruit, district, category and date
    filters are validated by the shared Reports parser, so they are interpreted
    identically on both pages. The prediction view, the forecast start date and
    the horizon are validated here; a value that cannot be trusted is dropped
    and reported rather than used.
    """
    filters, errors = reports_service.parse_filters(args)

    horizon_min, horizon_max, horizon_default = horizon_bounds()

    raw_tab = clean_text(args.get("tab"))
    if raw_tab and raw_tab not in TABS:
        errors.append(
            "The selected prediction view does not exist, so Price Prediction "
            "was shown."
        )
    filters["tab"] = raw_tab if raw_tab in TABS else DEFAULT_TAB

    raw_horizon = clean_text(args.get("horizon"))
    filters["horizon"] = horizon_default

    if raw_horizon:
        value = _read_int(raw_horizon)

        if value is None or value < horizon_min or value > horizon_max:
            errors.append(
                f"The forecast horizon must be a whole number of days between "
                f"{horizon_min} and {horizon_max}. The default of "
                f"{horizon_default} days was used."
            )
        else:
            filters["horizon"] = value

    raw_start = clean_text(args.get("start"))
    filters["start"] = ""

    if raw_start:
        parsed = _read_date(raw_start)

        if parsed is None:
            errors.append(
                f"'{raw_start}' is not a valid forecast start date. Please use "
                "the YYYY-MM-DD format."
            )
        else:
            filters["start"] = parsed

    return filters, errors


def filter_query(filters):
    """The non-empty filters as a dictionary ready for ``url_for``."""
    return {
        name: value
        for name, value in filters.items()
        if value not in (None, "")
    }


def tab_link(filters, tab):
    """The filters for one tab, keeping every other choice in place."""
    query = filter_query(filters)
    query["tab"] = tab

    return query


# ---------------------------------------------------------------------------
# Small formatting helpers
# ---------------------------------------------------------------------------
def _day_label(iso_date):
    """``2026-11-03`` becomes ``03 Nov 26``."""
    try:
        value = date.fromisoformat(str(iso_date)[:10])
    except ValueError:
        return str(iso_date)

    return f"{value.day:02d} {MONTHS[value.month - 1]} {str(value.year)[2:]}"


def _month_label(iso_month):
    """``2026-11`` becomes ``Nov 2026``."""
    return reports_service.format_month(iso_month)


def _label_for(granularity, label):
    """Format one aggregated bucket label for a chart or a table."""
    if granularity == "month":
        return _month_label(label)

    return _day_label(label)


def _card(label, value, hint=None):
    return {"label": label, "value": value, "hint": hint}


def _line(chart_id, title, subtitle, labels, datasets, unit):
    """One line chart that may carry more than one series."""
    return {
        "id": chart_id,
        "title": title,
        "subtitle": subtitle,
        "kind": "line",
        "unit": unit,
        "horizontal": False,
        "labels": labels,
        "datasets": datasets,
    }


def _bar(chart_id, title, subtitle, labels, datasets, unit):
    """One bar chart that may carry more than one series."""
    return {
        "id": chart_id,
        "title": title,
        "subtitle": subtitle,
        "kind": "bar",
        "unit": unit,
        "horizontal": False,
        "labels": labels,
        "datasets": datasets,
    }


def _series(label, values, color, fill=False, dash=False):
    return {
        "label": label,
        "values": values,
        "color": color,
        "fill": fill,
        "dash": dash,
    }


def _table(title, columns, rows, note=None):
    return {"title": title, "columns": columns, "rows": rows, "note": note}


# ---------------------------------------------------------------------------
# Model and selection helpers
# ---------------------------------------------------------------------------
def _model_meta(model_row):
    """The public description of one saved model version."""
    if not model_row:
        return {"available": False}

    return {
        "available": True,
        "model_key": model_row["model_key"],
        "algorithm": model_row["algorithm"],
        "algorithm_label": model_row["algorithm_label"],
        "version": int(model_row["version"] or 1),
        "trained_at": model_row["trained_at"],
        "dataset_label": model_row["dataset_label"],
        "feature_count": int(model_row["feature_count"] or 0),
        "train_rows": int(model_row["train_rows"] or 0),
        "test_rows": int(model_row["test_rows"] or 0),
        "train_start": model_row["train_start"],
        "train_end": model_row["train_end"],
        "test_start": model_row["test_start"],
        "test_end": model_row["test_end"],
        "target_min": model_row["target_min"],
        "target_max": model_row["target_max"],
        "mae": model_row["mae"],
        "rmse": model_row["rmse"],
        "r2": model_row["r2"],
    }


def _load_candidates(model_row):
    """The scoring rows of every candidate that competed for one task."""
    if not model_row:
        return []

    try:
        rows = json.loads(model_row["candidates"] or "[]")
    except (TypeError, ValueError):
        return []

    return rows if isinstance(rows, list) else []


def _resolve_selection(filters, options):
    """The fruit and district a forecast is built for.

    When the administrator has not chosen one, the first fruit and the first
    district in the catalogue are used, so the page always has a concrete
    series to describe.
    """
    fruit = next(
        (row for row in options["fruits"] if row["id"] == filters.get("fruit_id")),
        None,
    )
    if fruit is None and options["fruits"]:
        fruit = options["fruits"][0]

    district = next(
        (row for row in options["districts"] if row["id"] == filters.get("district_id")),
        None,
    )
    if district is None and options["districts"]:
        district = options["districts"][0]

    return (
        dict(fruit) if fruit else None,
        dict(district) if district else None,
    )


def _selection_label(fruit, district):
    """A short description of what a forecast covers."""
    if not fruit or not district:
        return "No fruit and district selected"

    return f"{fruit['name']} in {district['name']}"


def _observed_stock(series, model_key):
    """The most recent recorded value of one column of a series, if any."""
    if series is None or series.empty:
        return None

    column = dataset.TARGETS.get(model_key)

    if not column or column not in series.columns:
        return None

    return float(series.iloc[-1][column])


def _forecast(model_key, model_row, series, fruit, district, horizon, start):
    """Run one forecast, returning ``(error, artifact, result)``.

    Exactly one of ``error`` and ``result`` is meaningful: when ``error`` is set
    a human-readable reason is returned and no prediction is produced.
    """
    if not model_row:
        return "No trained model is available for this task yet.", None, None

    artifact = training.load_artifact(model_row)

    if artifact is None:
        return (
            "The saved model file could not be read. Train the models again to "
            "replace it.",
            None,
            None,
        )

    result = predictor.forecast(
        model_key,
        artifact,
        series,
        fruit["name"],
        district["name"],
        fruit["category"],
        horizon,
        start or None,
    )

    if not result.get("ok"):
        return result.get("error", "The forecast could not be produced."), artifact, None

    return None, artifact, result


# ---------------------------------------------------------------------------
# Price prediction
# ---------------------------------------------------------------------------
def _price_view(model_row, series, fruit, district, horizon, start):
    """Predicted selling price per kilogram, with the observed trend."""
    meta = _model_meta(model_row)
    error, artifact, result = _forecast(
        dataset.PRICE_TARGET, model_row, series, fruit, district, horizon, start
    )

    if error:
        return {"available": False, "error": error, "model": meta}

    points = result["points"]
    observed = predictor.observed_points(series, dataset.PRICE_TARGET)
    summary = predictor.summarise(points)

    latest_observed = observed[-1]["value"] if observed else None
    change = (
        round(summary["mean"] - latest_observed, 4)
        if latest_observed is not None else None
    )

    cards = [
        _card("Last recorded price", format_inr(latest_observed) + " / kg",
              "the most recent stored day of this series"),
        _card("Predicted average price", format_inr(summary["mean"]) + " / kg",
              f"average over the next {summary['days']} day(s)"),
        _card("Predicted range",
              format_inr(summary["min"]) + " - " + format_inr(summary["max"]),
              "lowest and highest predicted price per kg"),
        _card("Change against the last recorded price",
              ("Not available" if change is None
               else ("+" if change >= 0 else "") + format_inr(change) + " / kg"),
              "predicted average minus the last recorded price"),
    ]

    weekly = predictor.aggregate(points, "week", mode="mean")
    monthly = predictor.aggregate(points, "month", mode="mean")

    charts = [
        _line(
            "mlPriceObserved", "Historical price trend",
            "The most recent recorded selling price per kg, day by day",
            [_day_label(point["date"]) for point in observed],
            [_series("Recorded price (INR/kg)",
                     [point["value"] for point in observed],
                     COLOR_OBSERVED, fill=True)],
            "inr",
        ),
        _line(
            "mlPriceForecast", "Predicted price trend",
            "The model's own price per kg for each future day",
            [_label_for("day", point["date"]) for point in points],
            [_series("Predicted price (INR/kg)",
                     [point["value"] for point in points],
                     COLOR_PREDICTED, fill=True)],
            "inr",
        ),
    ]

    tables = [
        _table(
            "Predicted price by week",
            ["Week starting", "Predicted average price (INR/kg)"],
            [[_label_for("week", row["label"]), f"{row['value']:,.2f}"]
             for row in weekly],
            note="A week is Monday to Sunday. The average price is the mean of "
                 "that week's daily predictions.",
        ),
        _table(
            "Predicted price by month",
            ["Month", "Predicted average price (INR/kg)"],
            [[_label_for("month", row["label"]), f"{row['value']:,.2f}"]
             for row in monthly],
            note="The average price of every predicted day that falls in that "
                 "calendar month.",
        ),
    ]

    return {
        "available": True,
        "error": None,
        "model": meta,
        "cards": cards,
        "charts": charts,
        "tables": tables,
        "forecast": points,
        "observed": observed,
        "summary": summary,
        "granularity": {"weekly": weekly, "monthly": monthly},
        "period": {
            "last_observed": result["last_observed"],
            "start_date": result["start_date"],
            "end_date": result["end_date"],
            "days": result["horizon_days"],
        },
        "unit": "inr",
    }


# ---------------------------------------------------------------------------
# Sales forecasting
# ---------------------------------------------------------------------------
def _sales_view(model_row, price_model_row, series, fruit, district, horizon, start):
    """Predicted quantity sold and the revenue it would earn."""
    meta = _model_meta(model_row)
    error, artifact, result = _forecast(
        dataset.QUANTITY_TARGET, model_row, series, fruit, district, horizon, start
    )

    if error:
        return {"available": False, "error": error, "model": meta}

    points = result["points"]
    observed = predictor.observed_points(series, dataset.QUANTITY_TARGET)
    summary = predictor.summarise(points)

    # Revenue needs a price for each predicted day. The price model is used when
    # it is available; otherwise the last recorded price is held flat, and the
    # page says which of the two was used.
    price_points = []
    price_error, price_artifact, price_result = _forecast(
        dataset.PRICE_TARGET, price_model_row, series, fruit, district, horizon, start
    )

    if price_result:
        price_points = price_result["points"]
        price_source = (
            "the price model's own prediction for each day"
        )
    else:
        latest_price = _observed_stock(series, dataset.PRICE_TARGET)
        price_points = [
            {"date": point["date"], "value": latest_price or 0.0} for point in points
        ]
        price_source = (
            "the last recorded selling price held flat, because no price model "
            "is available"
        )

    revenue = predictor.combine_revenue(points, price_points)
    revenue_summary = predictor.summarise(revenue)

    daily = predictor.aggregate(points, "day")
    weekly = predictor.aggregate(points, "week")
    monthly = predictor.aggregate(points, "month")
    revenue_daily = predictor.aggregate(revenue, "day")

    cards = [
        _card("Predicted quantity sold", format_kg(summary["total"]),
              f"total over the next {summary['days']} day(s)"),
        _card("Predicted revenue", format_inr(revenue_summary["total"]),
              "predicted quantity multiplied by the predicted price"),
        _card("Average per day", format_kg(summary["mean"]),
              "predicted quantity divided by the number of days"),
        _card("Busiest predicted day", format_kg(summary["max"]),
              "the highest single-day prediction"),
    ]

    charts = [
        _line(
            "mlSalesObserved", "Recorded quantity sold",
            "The most recent recorded kilograms sold, day by day",
            [_day_label(point["date"]) for point in observed],
            [_series("Recorded quantity (kg)",
                     [point["value"] for point in observed],
                     COLOR_OBSERVED, fill=True)],
            "kg",
        ),
        _line(
            "mlSalesDaily", "Predicted quantity sold per day",
            "The model's own kilograms for each future day",
            [_label_for("day", point["date"]) for point in points],
            [_series("Predicted quantity (kg)",
                     [point["value"] for point in points],
                     COLOR_PREDICTED, fill=True)],
            "kg",
        ),
        _bar(
            "mlSalesWeekly", "Predicted quantity sold per week",
            "Each predicted day added together for its Monday-to-Sunday week",
            [_label_for("week", row["label"]) for row in weekly],
            [_series("Predicted quantity (kg)",
                     [row["value"] for row in weekly], COLOR_SECOND)],
            "kg",
        ),
        _bar(
            "mlSalesMonthly", "Predicted quantity sold per month",
            "Each predicted day added together for its calendar month",
            [_label_for("month", row["label"]) for row in monthly],
            [_series("Predicted quantity (kg)",
                     [row["value"] for row in monthly], COLOR_SECOND)],
            "kg",
        ),
        _line(
            "mlSalesRevenue", "Predicted revenue per day",
            "Predicted quantity multiplied by the predicted price for the same day",
            [_label_for("day", point["date"]) for point in revenue],
            [_series("Predicted revenue (INR)",
                     [point["value"] for point in revenue], COLOR_OBSERVED, fill=True)],
            "inr",
        ),
    ]

    tables = [
        _table(
            "Predicted sales by week",
            ["Week starting", "Predicted quantity (kg)"],
            [[_label_for("week", row["label"]), f"{row['value']:,.2f}"]
             for row in weekly],
        ),
        _table(
            "Predicted sales by month",
            ["Month", "Predicted quantity (kg)"],
            [[_label_for("month", row["label"]), f"{row['value']:,.2f}"]
             for row in monthly],
        ),
        _table(
            "Predicted revenue by day",
            ["Date", "Predicted quantity (kg)", "Predicted price (INR/kg)",
             "Predicted revenue (INR)"],
            [[_label_for("day", point["date"]),
              f"{points[index]['value']:,.2f}",
              f"{price_points[index]['value']:,.2f}",
              f"{point['value']:,.2f}"]
             for index, point in enumerate(revenue)],
            note=f"The price used for each day is {price_source}. Revenue is "
                 "the predicted quantity multiplied by that price.",
        ),
    ]

    return {
        "available": True,
        "error": None,
        "model": meta,
        "cards": cards,
        "charts": charts,
        "tables": tables,
        "forecast": points,
        "observed": observed,
        "revenue": revenue,
        "summary": summary,
        "revenue_summary": revenue_summary,
        "price_source": price_source,
        "granularity": {"daily": daily, "weekly": weekly, "monthly": monthly,
                        "revenue_daily": revenue_daily},
        "period": {
            "last_observed": result["last_observed"],
            "start_date": result["start_date"],
            "end_date": result["end_date"],
            "days": result["horizon_days"],
        },
        "unit": "kg",
    }


# ---------------------------------------------------------------------------
# Demand forecasting
# ---------------------------------------------------------------------------
def _demand_view(model_row, series, fruit, district, horizon, start):
    """Predicted demand, compared with the stock that is on hand."""
    meta = _model_meta(model_row)
    error, artifact, result = _forecast(
        dataset.DEMAND_TARGET, model_row, series, fruit, district, horizon, start
    )

    if error:
        return {"available": False, "error": error, "model": meta}

    points = result["points"]
    observed = predictor.observed_points(series, dataset.DEMAND_TARGET)
    summary = predictor.summarise(points)

    # The stock the predicted demand is compared against: the most recent
    # recorded available stock for this fruit and district.
    stock = None

    if series is not None and not series.empty and "available_stock" in series.columns:
        stock = float(series.iloc[-1]["available_stock"])

    cover_days = (
        round(stock / summary["mean"], 1)
        if stock is not None and summary["mean"] > 0 else None
    )
    shortfall = (
        round(summary["total"] - stock, 4)
        if stock is not None else None
    )

    cards = [
        _card("Predicted demand", format_kg(summary["total"]),
              f"total over the next {summary['days']} day(s)"),
        _card("Average per day", format_kg(summary["mean"]),
              "predicted demand divided by the number of days"),
        _card("Available stock", format_kg(stock),
              "the most recent recorded available stock for this position"),
        _card("Stock cover",
              ("Not available" if cover_days is None else f"{cover_days:,.1f} day(s)"),
              "available stock divided by the average predicted daily demand"),
    ]

    monthly = predictor.aggregate(points, "month")

    charts = [
        _line(
            "mlDemandObserved", "Recorded demand",
            "The most recent recorded estimated demand, day by day",
            [_day_label(point["date"]) for point in observed],
            [_series("Recorded demand (kg)",
                     [point["value"] for point in observed],
                     COLOR_OBSERVED, fill=True)],
            "kg",
        ),
        _line(
            "mlDemandForecast", "Predicted demand against available stock",
            "The model's own demand per day beside the stock currently on hand",
            [_label_for("day", point["date"]) for point in points],
            [
                _series("Predicted demand (kg)",
                        [point["value"] for point in points],
                        COLOR_PREDICTED, fill=True),
                _series("Available stock (kg)",
                        [stock if stock is not None else 0.0 for _ in points],
                        COLOR_STOCK, dash=True),
            ],
            "kg",
        ),
    ]

    tables = [
        _table(
            "Predicted demand by month",
            ["Month", "Predicted demand (kg)"],
            [[_label_for("month", row["label"]), f"{row['value']:,.2f}"]
             for row in monthly],
        ),
        _table(
            "Predicted demand against available stock",
            ["Measure", "Value"],
            [
                ["Predicted demand over the horizon", format_kg(summary["total"])],
                ["Average predicted demand per day", format_kg(summary["mean"])],
                ["Available stock on hand", format_kg(stock)],
                ["Difference (demand minus stock)",
                 "Not available" if shortfall is None
                 else ("+" if shortfall >= 0 else "") + f"{shortfall:,.2f} kg"],
                ["Stock cover",
                 "Not available" if cover_days is None else f"{cover_days:,.1f} day(s)"],
            ],
            note="Available stock is the most recent recorded figure for this "
                 "fruit and district. A negative difference means the predicted "
                 "demand over the horizon is smaller than the stock on hand; a "
                 "positive difference means the stock would run out before the "
                 "horizon ends, if the prediction holds.",
        ),
    ]

    return {
        "available": True,
        "error": None,
        "model": meta,
        "cards": cards,
        "charts": charts,
        "tables": tables,
        "forecast": points,
        "observed": observed,
        "summary": summary,
        "stock": stock,
        "cover_days": cover_days,
        "shortfall": shortfall,
        "granularity": {"monthly": monthly},
        "period": {
            "last_observed": result["last_observed"],
            "start_date": result["start_date"],
            "end_date": result["end_date"],
            "days": result["horizon_days"],
        },
        "unit": "kg",
    }


# ---------------------------------------------------------------------------
# Model performance
# ---------------------------------------------------------------------------
def _performance_task(model_key, model_row):
    """One task's active model: how it was picked, scored and what it missed."""
    label = dataset.TARGET_LABELS[model_key]
    unit = dataset.TARGET_UNITS[model_key]
    meta = _model_meta(model_row)

    if not model_row:
        return {
            "model_key": model_key,
            "label": label,
            "unit": unit,
            "available": False,
            "error": "This task has not been trained yet.",
            "candidates": [],
            "charts": [],
        }

    candidates = _load_candidates(model_row)
    test = predictor.test_series(training.load_artifact(model_row))

    winners = [row for row in candidates if row.get("algorithm") == model_row["algorithm"]]

    charts = []

    if test:
        charts.append(_line(
            f"mlPerf{model_key.capitalize()}", f"Actual versus predicted - {label}",
            "The winning model's own output on the held-back days, averaged per day",
            [_day_label(value) for value in test["dates"]],
            [
                _series("Actual", test["actual"], COLOR_OBSERVED, fill=True),
                _series("Predicted", test["predicted"], COLOR_PREDICTED, dash=True),
            ],
            unit,
        ))

    return {
        "model_key": model_key,
        "label": label,
        "unit": unit,
        "available": True,
        "error": None,
        "model": meta,
        "candidates": candidates,
        "winner": winners[0] if winners else None,
        "charts": charts,
        "test_series": test,
    }


def _performance_view():
    """Every task side by side, with the comparison and the stored history."""
    tasks = [
        _performance_task(model_key, repository.active_model(model_key))
        for model_key in training.TASKS
    ]

    versions = [_model_meta_row(row) for row in repository.model_history(limit=20)]

    return {
        "tasks": tasks,
        "counts": repository.model_counts(),
        "versions": versions,
    }


def _model_meta_row(row):
    """One row of the trained-version list, as plain data."""
    return {
        "model_key": row["model_key"],
        "label": dataset.TARGET_LABELS.get(row["model_key"], row["model_key"]),
        "algorithm_label": row["algorithm_label"],
        "version": int(row["version"] or 1),
        "trained_at": row["trained_at"],
        "train_start": row["train_start"],
        "train_end": row["train_end"],
        "test_start": row["test_start"],
        "test_end": row["test_end"],
        "mae": row["mae"],
        "rmse": row["rmse"],
        "r2": row["r2"],
        "is_active": bool(row["is_active"]),
        "dataset_label": row["dataset_label"],
    }


# ---------------------------------------------------------------------------
# Prediction history
# ---------------------------------------------------------------------------
def _history_view():
    """The forecasts an administrator chose to keep."""
    rows = repository.prediction_history(limit=25)

    return {
        "rows": [
            {
                "created_at": row["created_at"],
                "prediction_type": row["prediction_type"],
                "label": dataset.TARGET_LABELS.get(
                    row["prediction_type"], row["prediction_type"]
                ),
                "fruit_name": row["fruit_name"] or "Not available",
                "district_name": row["district_name"] or "Not available",
                "algorithm_label": row["algorithm_label"],
                "model_version": row["model_version"],
                "horizon_days": row["horizon_days"],
                "start_date": row["start_date"],
                "end_date": row["end_date"],
                "predicted": row["predicted"],
                "predicted_revenue": row["predicted_revenue"],
                "notes": row["notes"],
            }
            for row in rows
        ],
        "count": repository.prediction_count(),
    }


# ---------------------------------------------------------------------------
# Assembling the page
# ---------------------------------------------------------------------------
def model_status():
    """The active model of every task, keyed by model key."""
    return {row["model_key"]: row for row in repository.active_models()}


def build_ml(filters):
    """Assemble the whole Machine Learning page for the chosen view.

    ``filters`` is the validated dictionary returned by :func:`parse_filters`.
    The same payload drives the page and the JSON API, so the two can never
    disagree.
    """
    options = filter_options()
    fruit, district = _resolve_selection(filters, options)
    horizon = int(filters.get("horizon", 30))
    start = filters.get("start") or ""
    tab = filters.get("tab", DEFAULT_TAB)

    active = model_status()
    series = (
        dataset.load_series(fruit["id"], district["id"])
        if fruit and district else None
    )

    price_task = active.get(dataset.PRICE_TARGET)
    quantity_task = active.get(dataset.QUANTITY_TARGET)
    demand_task = active.get(dataset.DEMAND_TARGET)

    price = _price_view(price_task, series, fruit, district, horizon, start) \
        if fruit and district else {"available": False,
                                    "error": "No fruit and district are available to forecast.",
                                    "model": {"available": False}}
    sales = _sales_view(quantity_task, price_task, series, fruit, district, horizon, start) \
        if fruit and district else {"available": False,
                                    "error": "No fruit and district are available to forecast.",
                                    "model": {"available": False}}
    demand = _demand_view(demand_task, series, fruit, district, horizon, start) \
        if fruit and district else {"available": False,
                                    "error": "No fruit and district are available to forecast.",
                                    "model": {"available": False}}

    performance = _performance_view()
    history = _history_view()

    views = {
        "price": price,
        "sales": sales,
        "demand": demand,
        "performance": performance,
        "history": history,
    }

    charts = _active_charts(tab, views)

    return {
        "ok": True,
        "generated_at": _timestamp(),
        "tab": tab,
        "tabs": [dict(TABS[key]) for key in TAB_ORDER],
        "bounds": data_bounds(),
        "filters": _display_filters(filters, fruit, district),
        "selection": {
            "fruit": fruit,
            "district": district,
            "label": _selection_label(fruit, district),
            "observations": int(len(series)) if series is not None else 0,
        },
        "horizon": horizon,
        "views": views,
        "price": price,
        "sales": sales,
        "demand": demand,
        "performance": performance,
        "history": history,
        "charts": charts,
        "counts": performance["counts"],
        "trained": bool(active),
        "experimental_notice": EXPERIMENTAL_NOTICE,
        "experimental_tag": EXPERIMENTAL_TAG,
        "synthetic_notice": SYNTHETIC_NOTICE,
        "notes": list(METHOD_NOTES),
    }


def _active_charts(tab, views):
    """The charts the active view draws, as one flat list."""
    view = views.get(tab) or {}

    if tab == "performance":
        charts = []
        for task in view.get("tasks", []):
            charts.extend(task.get("charts", []))
        return charts

    return view.get("charts", []) or []


def _display_filters(filters, fruit, district):
    """Add the readable names the page quotes to the filter dictionary."""
    source = filters.get("record_source") or RECORD_SOURCE_ALL

    return {
        **filters,
        "fruit_name": fruit["name"] if fruit else "",
        "district_name": district["name"] if district else "",
        "category_display": (
            category_label(filters["category"]) if filters.get("category") else ""
        ),
        "source_display": RECORD_SOURCE_LABELS.get(source, "All records"),
        "tab_name": TABS.get(filters.get("tab", DEFAULT_TAB), TABS[DEFAULT_TAB])["name"],
    }


def _timestamp():
    return datetime.now().strftime("%d %b %Y, %H:%M")


# ---------------------------------------------------------------------------
# Administrator actions
# ---------------------------------------------------------------------------
def train_models():
    """Train, compare and save a fresh version of every model task.

    The window, the held-back test period and the minimum number of rows come
    from the application configuration, so the run is identical whatever page
    or command started it.
    """
    config = current_app.config

    return training.train_all(
        model_dir=config["ML_MODEL_DIR"],
        train_days=int(config.get("ML_TRAIN_DAYS", 1095)),
        test_days=int(config.get("ML_TEST_DAYS", 90)),
        min_rows=int(config.get("ML_MIN_TRAIN_ROWS", 2000)),
        max_rows=int(config.get("ML_MAX_TRAIN_ROWS", 0)) or None,
    )


def save_prediction(filters):
    """Keep the current forecast in the prediction history.

    Returns ``(saved, message)``. Nothing is written when the model for the
    chosen task is missing or the forecast cannot be produced, so the history
    never holds a figure that was not actually predicted.
    """
    options = filter_options()
    fruit, district = _resolve_selection(filters, options)

    if not fruit or not district:
        return False, "Choose a fruit and a district before saving a forecast."

    tab = filters.get("tab", DEFAULT_TAB)
    model_key = TABS.get(tab, TABS[DEFAULT_TAB])["task"]

    if not model_key:
        return False, "The current view does not produce a forecast to save."

    model_row = repository.active_model(model_key)

    if not model_row:
        return False, "Train the models before saving a forecast."

    series = dataset.load_series(fruit["id"], district["id"])
    error, _artifact, result = _forecast(
        model_key,
        model_row,
        series,
        fruit,
        district,
        int(filters.get("horizon", 30)),
        filters.get("start") or "",
    )

    if error:
        return False, error

    points = result["points"]
    summary = predictor.summarise(points)

    if model_key == dataset.PRICE_TARGET:
        predicted = summary["mean"]
        predicted_revenue = None
        notes = f"Average predicted price over {summary['days']} day(s), in INR per kg."
    else:
        predicted = summary["total"]
        predicted_revenue = None

        if model_key == dataset.QUANTITY_TARGET:
            price_points = [
                {"date": point["date"],
                 "value": _observed_stock(series, dataset.PRICE_TARGET) or 0.0}
                for point in points
            ]
            price_row = repository.active_model(dataset.PRICE_TARGET)

            if price_row:
                _price_error, _price_artifact, price_result = _forecast(
                    dataset.PRICE_TARGET, price_row, series, fruit, district,
                    int(filters.get("horizon", 30)), filters.get("start") or "",
                )
                if price_result:
                    price_points = price_result["points"]

            revenue = predictor.combine_revenue(points, price_points)
            predicted_revenue = predictor.summarise(revenue)["total"]

        notes = f"Total predicted quantity over {summary['days']} day(s), in kg."

        if model_key == dataset.DEMAND_TARGET:
            notes = f"Total predicted demand over {summary['days']} day(s), in kg."

    repository.record_prediction(
        model_id=model_row["id"],
        prediction_type=model_key,
        district_id=district["id"],
        fruit_id=fruit["id"],
        horizon_days=result["horizon_days"],
        start_date=result["start_date"],
        end_date=result["end_date"],
        predicted=round(predicted, 4),
        predicted_revenue=(
            round(predicted_revenue, 4) if predicted_revenue is not None else None
        ),
        notes=notes,
    )

    return (
        True,
        f"The forecast for {_selection_label(fruit, district)} was saved to the "
        "prediction history.",
    )
