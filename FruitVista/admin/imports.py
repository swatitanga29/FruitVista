"""Administrator-only historical CSV import.

This module is the only place where a dataset file is read into the database.
Every route is wrapped in ``admin_required``, so the role is verified on the
server for each request; the progress requests are protected exactly like the
page itself and each one carries a CSRF token.

The import runs in slices so the page can show genuine progress: the page asks
for the next slice until the server reports that the import is finished.
"""

from flask import Blueprint, g, jsonify, render_template, request

from auth.decorators import admin_required
from models import imports as imports_repo
from services import import_service

data_import_bp = Blueprint("data_import", __name__, url_prefix="/admin/import")


def _counts():
    """How many historical and manual rows are stored right now."""
    return {
        "historical": imports_repo.count_rows(imports_repo.RECORD_SOURCE),
        "manual": imports_repo.count_rows("manual"),
    }


@data_import_bp.route("")
@admin_required
def index():
    """Show the dataset, the import controls and the previous import runs."""
    return render_template(
        "admin/import.html",
        page="admin",
        active_module="data-import",
        user=g.user,
        summary=import_service.dataset_summary(),
        batches=import_service.recent_batches(8),
        counts=_counts(),
        coverage=[dict(row) for row in imports_repo.coverage_by_district()],
    )


@data_import_bp.route("/start", methods=["POST"])
@admin_required
def start():
    """Validate the dataset and open a new import run."""
    try:
        result = import_service.start_import()
    except import_service.DatasetError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except Exception as error:  # pragma: no cover - unexpected read failure
        return jsonify({"ok": False, "error": f"The dataset could not be prepared: {error}"}), 500

    return jsonify(result)


@data_import_bp.route("/run", methods=["POST"])
@admin_required
def run():
    """Import the next slice of rows and report progress."""
    run_id = request.form.get("run_id", "")
    chunk_size = request.form.get("chunk_size") or import_service.DEFAULT_CHUNK_SIZE

    if not str(run_id).isdigit():
        return jsonify({"ok": False, "error": "The import run is missing."}), 400

    try:
        result = import_service.run_import_chunk(int(run_id), chunk_size)
    except Exception as error:  # pragma: no cover - defensive
        return jsonify({"ok": False, "error": f"The import stopped: {error}"}), 500

    return jsonify(result)
