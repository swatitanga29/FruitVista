"""Training, comparison and versioned saving of the FruitVista ML models.

Three tasks are trained, one per prediction the dashboard offers:

``price``
    The selling price per kg of one fruit in one district on one day.
``quantity``
    The quantity sold (kg) of one fruit in one district on one day.
``demand``
    The estimated demand (kg) for one fruit in one district on one day.

For each task every candidate in :data:`ml.models.CANDIDATES` is trained on the
*same* training rows and scored on the *same* later test rows, so the
comparison is fair. The candidate with the lowest RMSE wins, is refitted on the
whole window and is saved with ``joblib`` as a new version.

Nothing is retrained when a page is opened. Training only happens when an
administrator asks for it, and it always writes a *new* version rather than
overwriting an older one - the registry keeps the history.

Two safeguards keep the run quick and honest:

* the training frame is capped at ``max_rows`` rows (a deterministic sample),
  so a run finishes in a reasonable time on a laptop while the test period is
  always used in full;
* every value is clipped to the range observed in the *training* period, so a
  straight-line model can never extrapolate to a negative price or a negative
  weight.
"""

import os
from datetime import datetime

import joblib
import numpy as np
import pandas as pd

from ml import dataset
from ml import models
from ml import repository

# The tasks trained, in the order the dashboard presents them.
TASKS = (dataset.PRICE_TARGET, dataset.QUANTITY_TARGET, dataset.DEMAND_TARGET)

DATASET_LABEL = "Synthetic historical dataset"


def _cap(frame, max_rows):
    """Return a deterministic sample of at most ``max_rows`` rows.

    A fixed seed keeps the sample - and therefore the metrics - reproducible
    from one run to the next.
    """
    if not max_rows or len(frame) <= max_rows:
        return frame

    return frame.sample(n=int(max_rows), random_state=42)


def _score(candidate, model_key, train, test, features, lower, upper):
    """Fit one candidate on the training rows and score it on the test rows.

    Returns ``(metrics, predicted)``. The predictions are the ones the model
    made for the held-out days *before* it was refitted on the whole window, so
    they are an honest "actual versus predicted" record of the evaluation.
    """
    X_train = train[features].astype(float)
    y_train = dataset.target_series(train, model_key)
    X_test = test[features].astype(float)
    y_test = dataset.target_series(test, model_key)

    candidate.fit(X_train, y_train)
    predicted = models.clip_predictions(candidate.predict(X_test), lower, upper)

    return models.evaluate(y_test, predicted), predicted


def _test_series(test, model_key, predicted):
    """Actual and predicted values on the held-out period, averaged per day.

    Every series (fruit and district) is folded together per calendar day, so
    the chart shows one honest point per test day rather than one per position.
    The values are exactly the model's own output on the later days it was
    never allowed to learn from.
    """
    frame = pd.DataFrame({
        "record_date": test["record_date"].dt.strftime("%Y-%m-%d"),
        "actual": dataset.target_series(test, model_key).to_numpy(dtype=float),
        "predicted": np.asarray(predicted, dtype=float),
    })

    grouped = frame.groupby("record_date", sort=True).agg(
        actual=("actual", "mean"), predicted=("predicted", "mean")
    )

    return {
        "dates": list(grouped.index),
        "actual": [round(float(value), 4) for value in grouped["actual"]],
        "predicted": [round(float(value), 4) for value in grouped["predicted"]],
        "days": int(len(grouped)),
        "rows": int(len(frame)),
    }


def _train_task(model_key, data, model_dir, min_rows, max_rows):
    """Train, compare and save the winning model for one task."""
    features = data["features"]
    train = _cap(data["train"], max_rows)
    test = data["test"]
    window = _cap(data["window"], max_rows)

    if len(train) < min_rows:
        return {
            "ok": False,
            "model_key": model_key,
            "label": dataset.TARGET_LABELS[model_key],
            "error": (
                f"Only {len(train):,} training row(s) are available, which is "
                f"below the minimum of {int(min_rows):,}. Import more history "
                "before training."
            ),
        }

    # The bounds a prediction is held inside. They come from the training rows
    # only, so no information from the test period leaks into the model.
    y_train = dataset.target_series(train, model_key)
    lower = max(0.0, float(y_train.min()))
    upper = float(y_train.max())

    results = []
    predictions = {}
    for algorithm in models.CANDIDATES:
        candidate = models.build_candidate(algorithm, model_key)
        metrics, predicted = _score(candidate, model_key, train, test, features, lower, upper)
        results.append({
            "algorithm": algorithm,
            "label": models.CANDIDATES[algorithm]["label"],
            "family": models.CANDIDATES[algorithm]["family"],
            **metrics,
        })
        predictions[algorithm] = predicted

    winner = min(results, key=lambda row: row["rmse"])

    # The chart data: the winning model's own predictions for the held-out days,
    # made before it was refitted, averaged per day.
    test_series = _test_series(test, model_key, predictions[winner["algorithm"]])

    # Refit the winner on the whole window before saving, so the deployed
    # model uses every row that is allowed to be learned from.
    estimator = models.build_candidate(winner["algorithm"], model_key)
    estimator.fit(window[features].astype(float),
                  dataset.target_series(window, model_key))

    version = repository.next_version(model_key)
    os.makedirs(model_dir, exist_ok=True)
    filename = f"{model_key}_v{version}.joblib"
    artifact_path = os.path.join(model_dir, filename)

    trained_at = datetime.now()
    artifact = {
        "model_key": model_key,
        "algorithm": winner["algorithm"],
        "algorithm_label": winner["label"],
        "estimator": estimator,
        "features": features,
        "numeric_features": list(dataset.NUMERIC_FEATURES),
        "origin": data["origin"].isoformat(),
        "target_min": lower,
        "target_max": upper,
        "trained_at": trained_at.strftime("%Y-%m-%d %H:%M:%S"),
        "dataset_label": DATASET_LABEL,
        # The evaluation record: dates, actual values and the model's own
        # predictions on the later days it was never trained on.
        "test_series": test_series,
        "train_start": data["train_start"].isoformat(),
        "train_end": data["train_end"].isoformat(),
        "test_start": data["test_start"].isoformat(),
        "test_end": data["test_end"].isoformat(),
    }

    joblib.dump(artifact, artifact_path)

    entry = {
        "feature_count": len(features),
        "train_rows": len(train),
        "test_rows": len(test),
        "train_start": data["train_start"].isoformat(),
        "train_end": data["train_end"].isoformat(),
        "test_start": data["test_start"].isoformat(),
        "test_end": data["test_end"].isoformat(),
        "target_min": lower,
        "target_max": upper,
        "mae": winner["mae"],
        "rmse": winner["rmse"],
        "r2": winner["r2"],
        "candidates": results,
        "dataset_label": DATASET_LABEL,
        "notes": (
            "Chronological split: the model was trained on the days up to "
            f"{data['train_end'].isoformat()} and scored on the later days "
            f"from {data['test_start'].isoformat()} to "
            f"{data['test_end'].isoformat()}."
        ),
    }

    model_id, version = repository.store_model(
        model_key, winner["algorithm"], winner["label"], artifact_path, entry
    )

    return {
        "ok": True,
        "model_key": model_key,
        "label": dataset.TARGET_LABELS[model_key],
        "model_id": model_id,
        "version": version,
        "algorithm": winner["algorithm"],
        "algorithm_label": winner["label"],
        "artifact_path": artifact_path,
        "slope_rows": len(train),
        "test_rows": len(test),
        "train_start": entry["train_start"],
        "train_end": entry["train_end"],
        "test_start": entry["test_start"],
        "test_end": entry["test_end"],
        "mae": winner["mae"],
        "rmse": winner["rmse"],
        "r2": winner["r2"],
        "candidates": results,
    }


def train_all(model_dir, train_days, test_days, min_rows, max_rows):
    """Train every task and report what happened.

    Returns a dictionary with ``ok``, the tasks that were trained (or the
    reason each one was refused) and the shared train and test periods.
    """
    data = dataset.build_training_data(train_days, test_days)

    if data is None:
        return {
            "ok": False,
            "error": (
                "There is not enough stored history to build a training table. "
                "Import the historical dataset first."
            ),
            "tasks": [],
        }

    tasks = [
        _train_task(model_key, data, model_dir, min_rows, max_rows)
        for model_key in TASKS
    ]

    trained = [task for task in tasks if task["ok"]]

    return {
        "ok": bool(trained),
        "error": None if trained else "No model could be trained on the stored history.",
        "trained_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "train_start": data["train_start"].isoformat(),
        "train_end": data["train_end"].isoformat(),
        "test_start": data["test_start"].isoformat(),
        "test_end": data["test_end"].isoformat(),
        "feature_count": len(data["features"]),
        "tasks": tasks,
    }


def load_artifact(model_row):
    """Load a saved model from disk, or return ``None`` when it is missing."""
    if model_row is None:
        return None

    path = model_row["artifact_path"]

    if not path or not os.path.exists(path):
        return None

    try:
        return joblib.load(path)
    except Exception:
        # A file that can no longer be read is reported as "not trained" rather
        # than crashing the page; training it again fixes it.
        return None


def is_frame_empty(frame):
    """Small guard used by the callers that hold a frame."""
    return frame is None or isinstance(frame, pd.DataFrame) and frame.empty
