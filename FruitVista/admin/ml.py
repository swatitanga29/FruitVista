"""Administrator Machine Learning and prediction module.

Five views share one page: Price Prediction, Sales Forecasting, Demand
Forecasting, Model Performance and Prediction History. Each forecast is
produced by :mod:`ml.predictor` from a model that :mod:`ml.training` has
already trained and saved - nothing here calculates a market figure of its own.

Two administrator actions sit beside the read-only views:

* training a fresh version of every model, and
* keeping the forecast currently on screen in the prediction history.

Both are POST requests and both carry the CSRF token, so neither can be
triggered by a link or by a page load. A model is never retrained by opening a
page.

Every route - the pages, the training endpoint and the JSON prediction API -
is wrapped in ``admin_required``, so the administrator role is verified on the
server even when an address is typed by hand.
"""

from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, url_for

from auth.decorators import admin_required
from services import ml_service

ml_bp = Blueprint("ml", __name__, url_prefix="/admin/ml")


@ml_bp.route("")
@admin_required
def index():
    """The prediction dashboard for the view chosen in the query string."""
    filters, errors = ml_service.parse_filters(request.args)

    for message in errors:
        flash(message, "error")

    return render_template(
        "admin/ml/dashboard.html",
        page="admin",
        active_module="ml",
        user=g.user,
        filters=filters,
        options=ml_service.filter_options(),
        bounds=ml_service.data_bounds(),
        ml=ml_service.build_ml(filters),
        # The templates keep the current filters and only change the view, so
        # every tab link carries the same query string the page was opened with.
        tab_link=lambda tab: ml_service.tab_link(filters, tab),
    )


@ml_bp.route("/data")
@admin_required
def data():
    """The same prediction payload as JSON, for programmatic access.

    Only the assembled figures, the evaluation results and the forecasts leave
    the server - the 265,050 stored rows are never sent to the browser.
    """
    filters, errors = ml_service.parse_filters(request.args)

    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    return jsonify(ml_service.build_ml(filters))


@ml_bp.route("/train", methods=("POST",))
@admin_required
def train():
    """Train and save a fresh version of every model task.

    Refitting is never automatic: this endpoint is the only place training
    starts, and it always writes a new version instead of overwriting an older
    one, so the registry keeps the history of every run.
    """
    filters, _errors = ml_service.parse_filters(request.args)
    result = ml_service.train_models()

    if not result.get("ok"):
        flash(result.get("error") or "No model could be trained.", "error")
        return redirect(url_for("ml.index", tab="performance"))

    for task in result["tasks"]:
        if task["ok"]:
            flash(
                f"{task['label']}: {task['algorithm_label']} trained and saved as "
                f"version {task['version']} "
                f"(MAE {task['mae']:.4f}, RMSE {task['rmse']:.4f}, "
                f"R2 {task['r2']:.4f}).",
                "success",
            )
        else:
            flash(f"{task['label']}: {task['error']}", "error")

    flash(
        f"Evaluation used the days up to {result['train_end']} for training and "
        f"{result['test_start']} to {result['test_end']} as the later test period.",
        "success",
    )

    return redirect(url_for("ml.index", tab="performance"))


@ml_bp.route("/keep", methods=("POST",))
@admin_required
def keep():
    """Keep the forecast currently on screen in the prediction history."""
    filters, _errors = ml_service.parse_filters(request.args)
    saved, message = ml_service.save_prediction(filters)

    flash(message, "success" if saved else "error")

    return redirect(url_for("ml.index", **ml_service.tab_link(filters, filters["tab"])))
