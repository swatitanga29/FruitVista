"""Dataset loading and feature engineering for the FruitVista ML module.

The training data is the stored daily history in ``daily_records``. Both the
imported historical rows and the manually entered sales rows are read, so a
manual sale the administrator recorded is included in the same series. The two
sources are never counted twice: rows are grouped by
``(record_date, district_id, fruit_id)`` first, and only then is a single value
per position per day produced.

Every feature is built from information that is genuinely available *before*
the day being predicted:

* previous prices (one day, one week and one month back, and their averages),
* the previous day's quantity sold and its weekly average,
* the previous day's estimated demand,
* the previous day's available stock,
* the calendar (day index, month, season) and the catalogue (fruit, district,
  fruit category).

Nothing on a row refers to that same row's own target, and nothing refers to a
later day. That is what keeps the model free of data leakage.

Missing values are never imputed - a row whose "previous price" does not exist
(the first month of a series) is dropped from training instead of being filled
with an invented number. Outliers are not deleted and not altered either: the
observed range of the target is recorded and every prediction is later held
inside that range.
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd

from database import get_db

# ---------------------------------------------------------------------------
# What the models predict
# ---------------------------------------------------------------------------
PRICE_TARGET = "price"
QUANTITY_TARGET = "quantity"
DEMAND_TARGET = "demand"

# model_key -> the aggregated column a model is trained to predict.
TARGETS = {
    PRICE_TARGET: "selling_price",
    QUANTITY_TARGET: "quantity_sold",
    DEMAND_TARGET: "estimated_demand",
}

TARGET_LABELS = {
    PRICE_TARGET: "Selling price per kg",
    QUANTITY_TARGET: "Quantity sold",
    DEMAND_TARGET: "Estimated demand",
}

TARGET_UNITS = {
    PRICE_TARGET: "inr",
    QUANTITY_TARGET: "kg",
    DEMAND_TARGET: "kg",
}

PRICE_TARGET_COLUMN = "selling_price"
QUANTITY_TARGET_COLUMN = "quantity_sold"
DEMAND_TARGET_COLUMN = "estimated_demand"

# Karnataka seasons, by calendar month. The division is the usual
# meteorological one for the state: the south-west monsoon from June to
# September, the post-monsoon months, the cool months and the hot months.
SEASON_BY_MONTH = {
    12: "Winter", 1: "Winter", 2: "Winter",
    3: "Summer", 4: "Summer", 5: "Summer",
    6: "Monsoon", 7: "Monsoon", 8: "Monsoon", 9: "Monsoon",
    10: "Post-monsoon", 11: "Post-monsoon",
}

# The numeric features, in a fixed order. A saved model and the forecast code
# both use this exact list, so their inputs can never drift apart.
NUMERIC_FEATURES = [
    "t",                 # day index since the first stored day
    "month",             # 1-12
    "sin_month",         # smooth seasonality, sine of the month position
    "cos_month",         # smooth seasonality, cosine of the month position
    "price_lag_1",       # previous day's selling price
    "price_lag_7",       # selling price one week back
    "price_lag_30",      # selling price one month back
    "price_roll_7",      # average selling price of the previous week
    "price_roll_30",     # average selling price of the previous month
    "quantity_lag_1",    # previous day's quantity sold
    "quantity_roll_7",   # average quantity sold over the previous week
    "demand_lag_1",      # previous day's estimated demand
    "available_lag_1",   # previous day's available stock
]

# Categorical features, expanded into indicator columns. Everything the
# catalogue contains is known in advance, so a future day can always be
# described with the same columns as a training day.
CATEGORICAL_SOURCES = {
    "fruit": "fruit_name",
    "district": "district_name",
    "category": "category",
    "season": "season",
}

# One row per fruit, district and day. Revenue and the price-weighted value are
# summed so a manual sales record entered beside a historical record for the
# same day is added once, not counted twice.
_RAW_SQL = """
SELECT r.record_date      AS record_date,
       r.district_id      AS district_id,
       d.name             AS district_name,
       f.name             AS fruit_name,
       f.category         AS category,
       SUM(r.quantity_sold)                   AS quantity_sold,
       SUM(r.sales_revenue)                   AS sales_revenue,
       SUM(r.estimated_demand)                AS estimated_demand,
       SUM(r.available_stock)                 AS available_stock,
       SUM(r.quantity_sold * r.selling_price) AS price_weight,
       SUM(r.selling_price)                   AS price_sum,
       COUNT(*)                               AS record_rows,
       SUM(CASE WHEN r.record_source = 'manual' THEN 1 ELSE 0 END) AS manual_rows
FROM daily_records r
JOIN fruits f    ON f.id = r.fruit_id
JOIN districts d ON d.id = r.district_id
GROUP BY r.record_date, r.district_id, r.fruit_id
ORDER BY r.record_date, r.district_id, r.fruit_id
"""


# The same read, limited to the days a model is trained on. The date is bound
# as a placeholder, so only the training window is read instead of the whole
# table - which keeps building the feature table quick.
_WINDOW_SQL = """
SELECT r.record_date      AS record_date,
       r.district_id      AS district_id,
       d.name             AS district_name,
       f.name             AS fruit_name,
       f.category         AS category,
       SUM(r.quantity_sold)                   AS quantity_sold,
       SUM(r.sales_revenue)                   AS sales_revenue,
       SUM(r.estimated_demand)                AS estimated_demand,
       SUM(r.available_stock)                 AS available_stock,
       SUM(r.quantity_sold * r.selling_price) AS price_weight,
       SUM(r.selling_price)                   AS price_sum,
       COUNT(*)                               AS record_rows,
       SUM(CASE WHEN r.record_source = 'manual' THEN 1 ELSE 0 END) AS manual_rows
FROM daily_records r
JOIN fruits f    ON f.id = r.fruit_id
JOIN districts d ON d.id = r.district_id
WHERE r.record_date >= ?
GROUP BY r.record_date, r.district_id, r.fruit_id
ORDER BY r.record_date, r.district_id, r.fruit_id
"""


def data_bounds():
    """The earliest and latest stored day, as ISO strings."""
    row = get_db().execute(
        "SELECT MIN(record_date) AS first_date, MAX(record_date) AS last_date "
        "FROM daily_records"
    ).fetchone()

    return {"first_date": row["first_date"], "last_date": row["last_date"]}


def load_history(since=None):
    """Return the stored history as one aggregated daily frame.

    One row per ``(record_date, district_id, fruit_id)``. ``selling_price`` is
    the quantity-weighted average price of the day, so a historical row and a
    manual row for the same position combine into a single, correct price
    rather than two competing ones.

    ``since`` limits the read to that ISO date onwards. It is only ever a
    bound placeholder; nothing is formatted into the SQL.
    """
    if since:
        frame = pd.read_sql(_WINDOW_SQL, get_db(), params=(since,))
    else:
        frame = pd.read_sql(_RAW_SQL, get_db())

    return _finish(frame)


# One series only. The fruit and district ids are bound as placeholders, so
# this reads a single indexed slice instead of the whole table - which is what
# a forecast needs, and what keeps a page load quick.
_SERIES_SQL = """
SELECT r.record_date      AS record_date,
       r.district_id      AS district_id,
       d.name             AS district_name,
       f.name             AS fruit_name,
       f.category         AS category,
       SUM(r.quantity_sold)                   AS quantity_sold,
       SUM(r.sales_revenue)                   AS sales_revenue,
       SUM(r.estimated_demand)                AS estimated_demand,
       SUM(r.available_stock)                 AS available_stock,
       SUM(r.quantity_sold * r.selling_price) AS price_weight,
       SUM(r.selling_price)                   AS price_sum,
       COUNT(*)                               AS record_rows,
       SUM(CASE WHEN r.record_source = 'manual' THEN 1 ELSE 0 END) AS manual_rows
FROM daily_records r
JOIN fruits f    ON f.id = r.fruit_id
JOIN districts d ON d.id = r.district_id
WHERE r.fruit_id = ? AND r.district_id = ?
GROUP BY r.record_date
ORDER BY r.record_date
"""


def load_series(fruit_id, district_id):
    """Return one fruit-and-district daily series, oldest first.

    Only the rows for the requested position are read, so a forecast does not
    have to load the whole stored history.
    """
    frame = pd.read_sql(_SERIES_SQL, get_db(), params=(fruit_id, district_id))

    if frame.empty:
        return frame

    return _finish(frame).reset_index(drop=True)


def _finish(frame):
    """Shared post-processing for the two queries above."""
    if frame.empty:
        return frame

    frame["record_date"] = pd.to_datetime(frame["record_date"])
    frame["district_id"] = frame["district_id"].astype(int)

    # Weight the price by the quantity actually sold; when nothing was sold the
    # row's own recorded price is used instead, so a zero-sale day still has a
    # price rather than a division by zero.
    sold = frame["quantity_sold"].astype(float)
    weight = frame["price_weight"].astype(float)
    count = frame["record_rows"].astype(float).replace(0, np.nan)

    frame["selling_price"] = np.where(
        sold > 0,
        weight / sold.replace(0, np.nan),
        frame["price_sum"].astype(float) / count,
    )

    frame["season"] = frame["record_date"].dt.month.map(SEASON_BY_MONTH)

    return frame.sort_values(["district_id", "fruit_name", "record_date"]).reset_index(drop=True)


def _add_features(frame):
    """Add every engineered feature to an already-aggregated history frame."""
    frame = frame.copy()

    origin = frame["record_date"].min()
    frame.attrs["origin"] = origin

    frame["t"] = (frame["record_date"] - origin).dt.days
    frame["month"] = frame["record_date"].dt.month
    frame["sin_month"] = np.sin(2 * np.pi * frame["month"] / 12.0)
    frame["cos_month"] = np.cos(2 * np.pi * frame["month"] / 12.0)

    grouped = frame.groupby(["district_id", "fruit_name"], sort=False)
    price = grouped["selling_price"]
    quantity = grouped["quantity_sold"]

    # shift(1) before every rolling window keeps the window strictly in the
    # past, so a row is never described using its own value.
    frame["price_lag_1"] = price.shift(1)
    frame["price_lag_7"] = price.shift(7)
    frame["price_lag_30"] = price.shift(30)
    frame["price_roll_7"] = price.shift(1).rolling(7).mean()
    frame["price_roll_30"] = price.shift(1).rolling(30).mean()
    frame["quantity_lag_1"] = quantity.shift(1)
    frame["quantity_roll_7"] = quantity.shift(1).rolling(7).mean()
    frame["demand_lag_1"] = grouped["estimated_demand"].shift(1)
    frame["available_lag_1"] = grouped["available_stock"].shift(1)

    return frame


def _expand_categories(frame):
    """Turn the categorical features into indicator columns.

    Returns ``(frame, dummy_columns)``. The indicator names are derived from
    the catalogue and are recorded with the model, so a later forecast builds
    exactly the same columns.
    """
    prefixes = list(CATEGORICAL_SOURCES.keys())
    columns = list(CATEGORICAL_SOURCES.values())

    expanded = pd.get_dummies(
        frame, columns=columns, prefix=prefixes, prefix_sep="_", dtype=float
    )

    dummy_columns = [
        name for name in expanded.columns if any(name.startswith(p + "_") for p in prefixes)
    ]
    dummy_columns.sort()

    return expanded, dummy_columns


def build_training_data(train_days, test_days):
    """Build the feature table and split it chronologically.

    The most recent ``train_days`` of stored history are used, and the last
    ``test_days`` of that window are held back. The test period therefore
    always lies *after* the training period, and a model is never evaluated on
    a day it was allowed to learn from.

    Returns a dictionary with the fitted feature list, the two frames and the
    dates that describe the split. ``None`` is returned when there is not
    enough stored history to train.
    """
    bounds = data_bounds()

    if not bounds["last_date"]:
        return None

    last_date = date.fromisoformat(bounds["last_date"])
    window_start = max(
        date.fromisoformat(bounds["first_date"]),
        last_date - timedelta(days=int(train_days) - 1),
    )

    # Only the training window is read from the database.
    history = load_history(window_start.isoformat())

    if history.empty:
        return None

    window = history[history["record_date"] >= pd.Timestamp(window_start)].copy()
    window = _add_features(window)

    # The day index ``t`` is measured from this date, so it is kept with the
    # model and reused when a forecast is built.
    origin = window["record_date"].min().date()

    # A stable "district|fruit" key is carried alongside the indicator columns
    # so the held-back rows can still be traced back to one series (the
    # categorical names themselves are consumed when the indicators are built).
    window["series_key"] = (
        window["district_id"].astype(str) + "|" + window["fruit_name"]
    )

    window, dummy_columns = _expand_categories(window)

    features = NUMERIC_FEATURES + dummy_columns

    # A row without a complete set of "previous day" values cannot describe
    # itself from history, so it is dropped rather than filled in.
    window = window.dropna(subset=NUMERIC_FEATURES)

    if window.empty:
        return None

    test_start = last_date - timedelta(days=int(test_days) - 1)

    train = window[window["record_date"] < pd.Timestamp(test_start)]
    test = window[window["record_date"] >= pd.Timestamp(test_start)]

    if train.empty or test.empty:
        return None

    return {
        "features": features,
        "dummy_columns": dummy_columns,
        "origin": origin,
        "train": train,
        "test": test,
        "window": window,
        "train_start": train["record_date"].min().date(),
        "train_end": train["record_date"].max().date(),
        "test_start": test["record_date"].min().date(),
        "test_end": test["record_date"].max().date(),
    }


def series_history(frame, fruit_name, district_id):
    """Return one fruit-and-district series, oldest first."""
    rows = frame[
        (frame["fruit_name"] == fruit_name) & (frame["district_id"] == district_id)
    ]
    return rows.sort_values("record_date").reset_index(drop=True)


def latest_series_values(frame, fruit_name, district_id):
    """The last observed day of one series, as the seed for a forecast."""
    series = series_history(frame, fruit_name, district_id)

    if series.empty:
        return None

    row = series.iloc[-1]
    return {
        "record_date": row["record_date"].date(),
        "selling_price": float(row["selling_price"]),
        "quantity_sold": float(row["quantity_sold"]),
        "estimated_demand": float(row["estimated_demand"]),
        "available_stock": float(row["available_stock"]),
    }


def build_feature_row(features, numeric_values, dummy_values):
    """Build a single feature row in the exact order the model was trained on.

    ``numeric_values`` carries the lag and calendar values; ``dummy_values``
    names the indicator columns that should be set to 1. Every other column is
    zero, so the row always matches ``features`` exactly.
    """
    row = {name: 0.0 for name in features}
    row.update({name: float(value) for name, value in numeric_values.items() if name in row})

    for name in dummy_values:
        if name in row:
            row[name] = 1.0

    return pd.DataFrame([row], columns=features)


def dummy_names(fruit_name, district_name, category, month):
    """The indicator columns that describe one catalogue position and month."""
    return [
        f"fruit_{fruit_name}",
        f"district_{district_name}",
        f"category_{category}",
        f"season_{SEASON_BY_MONTH[int(month)]}",
    ]


def lag_values(buffer, target_date, origin):
    """The lag features for ``target_date``, read from the working buffer.

    ``buffer`` is the recent history of one series (oldest first) with the keys
    ``selling_price``, ``quantity_sold``, ``estimated_demand`` and
    ``available_stock``. It holds real observations, then each predicted day as
    the forecast walks forward, so a multi-day forecast is built one day at a
    time from the day before it - exactly the situation the model was trained
    for.
    """
    prices = [point["selling_price"] for point in buffer]
    quantities = [point["quantity_sold"] for point in buffer]

    month = target_date.month

    return {
        "t": float((target_date - origin).days),
        "month": float(month),
        "sin_month": float(np.sin(2 * np.pi * month / 12.0)),
        "cos_month": float(np.cos(2 * np.pi * month / 12.0)),
        "price_lag_1": float(prices[-1]),
        "price_lag_7": float(prices[-7]),
        "price_lag_30": float(prices[-30]),
        "price_roll_7": float(np.mean(prices[-7:])),
        "price_roll_30": float(np.mean(prices[-30:])),
        "quantity_lag_1": float(quantities[-1]),
        "quantity_roll_7": float(np.mean(quantities[-7:])),
        "demand_lag_1": float(buffer[-1]["estimated_demand"]),
        "available_lag_1": float(buffer[-1]["available_stock"]),
    }


def target_series(frame, model_key):
    """The target values a model of ``model_key`` is trained on."""
    return frame[TARGETS[model_key]].astype(float)


def today():
    """Today's date, used when a forecast has no explicit start date."""
    return date.today()
