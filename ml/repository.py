"""Database access for the machine learning registry and prediction history.

Two tables are owned by this module (see ``database/schema.sql``, migration 8):

* ``ml_models`` - one row per trained model version, with the evaluation
  results, the chronological train and test periods and the path to the saved
  estimator file.
* ``ml_predictions`` - one row per forecast the administrator chose to keep.

Nothing here ever writes into ``daily_records``, ``stock_movements`` or
``wastage_records``. Training a model and generating a forecast are read-only
actions on the stored data; they only add rows to the two tables above.

Every value is passed to SQLite as a ``?`` placeholder and every statement runs
inside a transaction, so a failure part-way leaves the database untouched.
"""

import json

from database import get_db


def next_version(model_key):
    """The next version number for a model key, starting at 1."""
    row = get_db().execute(
        "SELECT MAX(version) AS version FROM ml_models WHERE model_key = ?",
        (model_key,),
    ).fetchone()

    return int(row["version"] or 0) + 1


def store_model(model_key, algorithm, algorithm_label, artifact_path, entry,
                is_active=True):
    """Save one trained model version and, by default, make it the active one.

    The previous active version of the same model key is deactivated in the
    same transaction, so the partial unique index never sees two active rows.
    """
    db = get_db()
    version = next_version(model_key)

    try:
        if is_active:
            db.execute(
                "UPDATE ml_models SET is_active = 0 WHERE model_key = ?",
                (model_key,),
            )

        cursor = db.execute(
            "INSERT INTO ml_models ("
            "  model_key, version, algorithm, algorithm_label, artifact_path,"
            "  feature_count, train_rows, test_rows, train_start, train_end,"
            "  test_start, test_end, target_min, target_max, mae, rmse, r2,"
            "  is_active, candidates, dataset_label, notes"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                model_key,
                version,
                algorithm,
                algorithm_label,
                artifact_path,
                entry.get("feature_count", 0),
                entry.get("train_rows", 0),
                entry.get("test_rows", 0),
                entry.get("train_start"),
                entry.get("train_end"),
                entry.get("test_start"),
                entry.get("test_end"),
                entry.get("target_min"),
                entry.get("target_max"),
                entry.get("mae"),
                entry.get("rmse"),
                entry.get("r2"),
                1 if is_active else 0,
                json.dumps(entry.get("candidates", [])),
                entry.get("dataset_label", "Synthetic historical dataset"),
                entry.get("notes"),
            ),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return cursor.lastrowid, version


def active_model(model_key):
    """The active model row for a task, or ``None`` when nothing is trained."""
    return get_db().execute(
        "SELECT * FROM ml_models WHERE model_key = ? AND is_active = 1",
        (model_key,),
    ).fetchone()


def active_models():
    """Every active model, ordered by task name."""
    rows = get_db().execute(
        "SELECT * FROM ml_models WHERE is_active = 1 ORDER BY model_key"
    ).fetchall()

    return [dict(row) for row in rows]


def model_history(limit=20):
    """The most recently trained model versions, newest first."""
    rows = get_db().execute(
        "SELECT * FROM ml_models ORDER BY trained_at DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()

    return [dict(row) for row in rows]


def model_counts():
    """How many model versions are stored, and how many are active."""
    row = get_db().execute(
        "SELECT COUNT(*) AS total, "
        "       SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) AS active "
        "FROM ml_models"
    ).fetchone()

    return {"total": int(row["total"] or 0), "active": int(row["active"] or 0)}


def record_prediction(model_id, prediction_type, district_id, fruit_id,
                      horizon_days, start_date, end_date, predicted,
                      predicted_revenue=None, interval_low=None,
                      interval_high=None, notes=None):
    """Keep one forecast in the prediction history."""
    db = get_db()

    try:
        cursor = db.execute(
            "INSERT INTO ml_predictions ("
            "  model_id, prediction_type, district_id, fruit_id, horizon_days,"
            "  start_date, end_date, predicted, predicted_revenue, interval_low,"
            "  interval_high, notes"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                model_id,
                prediction_type,
                district_id,
                fruit_id,
                horizon_days,
                start_date,
                end_date,
                predicted,
                predicted_revenue,
                interval_low,
                interval_high,
                notes,
            ),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return cursor.lastrowid


def prediction_history(limit=20):
    """The kept forecasts, newest first, with the fruit and district named."""
    rows = get_db().execute(
        "SELECT p.*, "
        "       d.name AS district_name, "
        "       f.name AS fruit_name, "
        "       m.algorithm_label AS algorithm_label, "
        "       m.version AS model_version "
        "FROM ml_predictions p "
        "LEFT JOIN districts d ON d.id = p.district_id "
        "LEFT JOIN fruits f    ON f.id = p.fruit_id "
        "LEFT JOIN ml_models m ON m.id = p.model_id "
        "ORDER BY p.created_at DESC, p.id DESC LIMIT ?",
        (limit,),
    ).fetchall()

    return [dict(row) for row in rows]


def prediction_count():
    """How many forecasts have been kept."""
    return int(
        get_db().execute("SELECT COUNT(*) AS total FROM ml_predictions").fetchone()["total"]
    )
