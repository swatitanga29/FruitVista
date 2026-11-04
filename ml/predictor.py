"""Forecast engine for the FruitVista machine-learning module.

A forecast walks the calendar forward one day at a time, exactly the way the
model was trained: on each future day it is given only the days that come
before it, starting with the real observations and then its own earlier
predictions.

Nothing here reads a value from after the day being predicted, and nothing is
invented: every point is the output of the saved estimator, held inside the
range the training data actually supports. A series that does not hold enough
recorded days to describe itself from history is refused rather than filled in.

The three tasks share the same machinery because they are trained from the same
feature list (:data:`ml.dataset.NUMERIC_FEATURES`); only the column that is
predicted changes.
"""

from datetime import date, datetime, timedelta

import numpy as np

from ml import dataset
from ml import models

# How many recorded days a series needs before it can describe itself. The
# widest lag feature is one month back, so a shorter series would have to be
# filled in with a made-up value - which this module never does.
MIN_HISTORY = 30

# How many observed days a chart shows behind the forecast.
HISTORY_POINTS = 180


def _as_date(value):
    """Return a ``datetime.date`` for the value a stored frame carries."""
    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    return date.fromisoformat(str(value)[:10])


def _safe_float(value):
    """Return a finite float, treating a missing value as zero."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0

    return number if np.isfinite(number) else 0.0


def build_buffer(series):
    """Turn one stored series into the working buffer a forecast walks through.

    Oldest day first. Each point carries the four values the lag features read:
    the price, the quantity sold, the estimated demand and the available stock.
    """
    buffer = []

    for _, row in series.iterrows():
        buffer.append({
            "record_date": _as_date(row["record_date"]),
            "selling_price": _safe_float(row["selling_price"]),
            "quantity_sold": _safe_float(row["quantity_sold"]),
            "estimated_demand": _safe_float(row["estimated_demand"]),
            "available_stock": _safe_float(row["available_stock"]),
        })

    return buffer


def _advance(previous, model_key, value, target_date):
    """Carry the buffer forward by one predicted day.

    Only the column the model predicts is replaced with the prediction; the
    other values keep their most recent real observation, because the model was
    trained to read them as "the day before".
    """
    point = {
        "record_date": target_date,
        "selling_price": previous["selling_price"],
        "quantity_sold": previous["quantity_sold"],
        "estimated_demand": previous["estimated_demand"],
        "available_stock": previous["available_stock"],
    }

    point[dataset.TARGETS[model_key]] = value
    return point


def observed_points(series, model_key, limit=HISTORY_POINTS):
    """The most recent recorded days of one series, as chart points."""
    if series is None or series.empty:
        return []

    column = dataset.TARGETS[model_key]
    points = []

    for _, row in series.tail(limit).iterrows():
        points.append({
            "date": _as_date(row["record_date"]).isoformat(),
            "value": round(_safe_float(row[column]), 4),
        })

    return points


def forecast(model_key, artifact, series, fruit_name, district_name, category,
             horizon_days, start_date=None):
    """Predict ``horizon_days`` future days for one fruit and district.

    ``artifact`` is the dictionary saved by :mod:`ml.training`. ``series`` is
    the stored history of the selected fruit and district. ``start_date`` is
    optional: when it is not given (or when it is not after the last recorded
    day) the forecast simply begins the day after the last observation.
    """
    if not artifact:
        return {"ok": False, "error": "No trained model is available for this task."}

    horizon = int(horizon_days)

    if horizon <= 0:
        return {"ok": False, "error": "The forecast horizon must be at least one day."}

    buffer = build_buffer(series)

    if len(buffer) < MIN_HISTORY:
        return {
            "ok": False,
            "error": (
                f"Only {len(buffer)} recorded day(s) exist for this fruit and "
                f"district. At least {MIN_HISTORY} days are needed so every lag "
                "feature can be read from real history."
            ),
        }

    last_date = buffer[-1]["record_date"]
    origin = _as_date(artifact["origin"])
    features = artifact["features"]
    estimator = artifact["estimator"]
    lower = max(0.0, _safe_float(artifact.get("target_min")))
    upper = _safe_float(artifact.get("target_max"))

    # The forecast always lies strictly in the future of the observed series.
    start = last_date + timedelta(days=1)
    requested = _as_date(start_date) if start_date else None

    if requested and requested > last_date:
        start = requested

    working = list(buffer)
    points = []

    for step in range(horizon):
        target = start + timedelta(days=step)
        lags = dataset.lag_values(working, target, origin)
        dummies = dataset.dummy_names(fruit_name, district_name, category, target.month)
        feature_row = dataset.build_feature_row(features, lags, dummies)

        raw = float(estimator.predict(feature_row)[0])
        value = float(models.clip_predictions([raw], lower, upper)[0])
        value = max(0.0, value)

        points.append({"date": target.isoformat(), "value": round(value, 4)})
        working.append(_advance(working[-1], model_key, value, target))

    return {
        "ok": True,
        "points": points,
        "origin": origin.isoformat(),
        "last_observed": last_date.isoformat(),
        "start_date": points[0]["date"] if points else None,
        "end_date": points[-1]["date"] if points else None,
        "horizon_days": len(points),
        "observations": len(buffer),
        "target_min": lower,
        "target_max": upper,
    }


def summarise(points):
    """Total, average, lowest and highest of a set of forecast points."""
    values = [point["value"] for point in points]

    if not values:
        return {"total": 0.0, "mean": 0.0, "min": 0.0, "max": 0.0, "days": 0}

    return {
        "total": round(float(np.sum(values)), 4),
        "mean": round(float(np.mean(values)), 4),
        "min": round(float(np.min(values)), 4),
        "max": round(float(np.max(values)), 4),
        "days": len(values),
    }


def daily_points(points):
    """Forecast points as chart-ready ``{label, value}`` rows."""
    return [{"label": point["date"], "value": point["value"]} for point in points]


def _week_start(iso_date):
    """The Monday of the ISO week an ``YYYY-MM-DD`` date falls in."""
    day = date.fromisoformat(iso_date)
    return (day - timedelta(days=day.weekday())).isoformat()


def aggregate(points, granularity, mode="sum"):
    """Fold forecast points into weekly or monthly buckets.

    ``mode`` is ``sum`` for a quantity or a revenue (a total per period) and
    ``mean`` for a price (the average of the period).
    """
    if granularity == "day":
        return daily_points(points)

    buckets = {}
    order = []

    for point in points:
        if granularity == "month":
            key = point["date"][:7]
        else:
            key = _week_start(point["date"])

        if key not in buckets:
            buckets[key] = {"label": key, "value": 0.0, "count": 0}
            order.append(key)

        buckets[key]["value"] += point["value"]
        buckets[key]["count"] += 1

    rows = []

    for key in order:
        bucket = buckets[key]
        if mode == "mean":
            value = bucket["value"] / bucket["count"] if bucket["count"] else 0.0
        else:
            value = bucket["value"]

        rows.append({"label": key, "value": round(float(value), 4),
                     "count": bucket["count"]})

    return rows


def combine_revenue(quantity_points, price_points):
    """Revenue per day, as predicted quantity multiplied by predicted price.

    Both lists walk the same calendar, so day *i* of each pair refers to the
    same future date. When the price model is not available the caller passes
    the same length of points built from the last observed price instead.
    """
    revenue = []

    for index, quantity in enumerate(quantity_points):
        price = price_points[index]["value"] if index < len(price_points) else 0.0
        revenue.append({
            "date": quantity["date"],
            "value": round(quantity["value"] * price, 4),
        })

    return revenue


def test_series(artifact):
    """The stored actual-versus-predicted record, or ``None`` when absent."""
    if not artifact:
        return None

    series = artifact.get("test_series")

    if not series or not series.get("dates"):
        return None

    return series
