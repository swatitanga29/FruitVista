"""Candidate regression models and their evaluation metrics.

Four candidates are compared on the *same* held-out, later test period:

``linear_regression``
    The baseline the task asks for. Fast and easy to read, but it can only
    describe a straight-line relationship, so it struggles with a curve such as
    a price that rises and falls with the season.
``random_forest``
    Averages many decision trees. It captures seasonality and the interactions
    between fruit, district and time without being told about them, and is
    robust to the odd unusual day.
``gradient_boosting``
    Builds trees one after another, each correcting the previous one. Usually
    the strongest of the three on this kind of tabular data, and fast because
    scikit-learn's histogram implementation is used.
``seasonal_naive``
    A time-series approach rather than a learned model: it simply repeats a
    value from a fixed number of days earlier. It is included because the
    dataset has a clear seasonal shape, and it is the honest yardstick a real
    model has to beat - if a learned model cannot do better than "same as last
    week", it is not adding anything.

No algorithm is chosen because the task lists it. Every candidate is trained on
the same rows, scored on the same later rows, and the one with the lowest RMSE
is the one that is saved.
"""

import numpy as np
from sklearn.base import BaseEstimator, RegressorMixin
from sklearn.ensemble import (
    HistGradientBoostingRegressor,
    RandomForestRegressor,
)
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score


class SeasonalNaiveRegressor(BaseEstimator, RegressorMixin):
    """Repeat the value from a fixed number of days earlier.

    A deliberately "unintelligent" time-series baseline. Whichever lag column
    it is given is returned untouched, so it can be compared with the learned
    models on exactly the same rows and the same metric.
    """

    def __init__(self, lag_column="price_lag_7"):
        self.lag_column = lag_column

    def fit(self, X, y):  # noqa: N803 - scikit-learn's naming
        """Nothing is learned; only the column to repeat is remembered."""
        self.n_features_in_ = X.shape[1]
        if self.lag_column not in X.columns:
            raise ValueError(f"'{self.lag_column}' is not a feature of the dataset")
        return self

    def predict(self, X):  # noqa: N803 - scikit-learn's naming
        return X[self.lag_column].to_numpy(dtype=float)


def _linear_regression(lag_column):
    return LinearRegression()


def _random_forest(lag_column):
    return RandomForestRegressor(
        n_estimators=40,
        max_depth=16,
        min_samples_leaf=5,
        max_features=0.5,
        random_state=42,
        n_jobs=-1,
    )


def _gradient_boosting(lag_column):
    return HistGradientBoostingRegressor(
        max_iter=150,
        learning_rate=0.1,
        max_leaf_nodes=31,
        min_samples_leaf=20,
        random_state=42,
    )


def _seasonal_naive(lag_column):
    return SeasonalNaiveRegressor(lag_column=lag_column)


# algorithm key -> label, factory and the column the naive baseline repeats.
# The naive column differs per task so it compares the right quantity.
CANDIDATES = {
    "linear_regression": {
        "label": "Linear Regression (baseline)",
        "family": "learned",
        "factory": _linear_regression,
    },
    "random_forest": {
        "label": "Random Forest Regressor",
        "family": "learned",
        "factory": _random_forest,
    },
    "gradient_boosting": {
        "label": "Gradient Boosting Regressor",
        "family": "learned",
        "factory": _gradient_boosting,
    },
    "seasonal_naive": {
        "label": "Seasonal naive baseline (time series)",
        "family": "baseline",
        "factory": _seasonal_naive,
    },
}

# Which column each task's naive baseline repeats.
NAIVE_LAG = {
    "price": "price_lag_7",
    "quantity": "quantity_roll_7",
    "demand": "demand_lag_1",
}


def build_candidate(algorithm, model_key):
    """Create one fresh candidate estimator for a task."""
    entry = CANDIDATES[algorithm]
    lag_column = NAIVE_LAG.get(model_key, "price_lag_7")
    return entry["factory"](lag_column)


def evaluate(y_true, y_pred):
    """MAE, RMSE and R-squared for one set of predictions.

    ``r2`` is ``None`` when the test period holds a single value, because
    R-squared is not defined there.
    """
    truth = np.asarray(y_true, dtype=float)
    predicted = np.asarray(y_pred, dtype=float)

    metrics = {
        "mae": float(mean_absolute_error(truth, predicted)),
        "rmse": float(np.sqrt(mean_squared_error(truth, predicted))),
        "r2": None,
    }

    if truth.size > 1 and np.var(truth) > 0:
        metrics["r2"] = float(r2_score(truth, predicted))

    return metrics


def clip_predictions(values, lower, upper):
    """Hold predictions inside the range the data actually supports.

    A negative price or a negative weight is meaningless, and a tree model can
    never leave the range it was trained on, so this mainly protects the
    straight-line baseline from extrapolating anywhere it likes.
    """
    array = np.asarray(values, dtype=float)
    return np.clip(array, float(lower), float(upper))
