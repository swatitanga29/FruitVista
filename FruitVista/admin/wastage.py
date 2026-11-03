"""Administrator Wastage Management module.

FruitVista shows how much fruit was damaged or lost, what it was worth, and
where the wastage is concentrated: by fruit, by district, by category, and
across days, months and years.

Two sets of records are read side by side and never mixed up (see
``models/wastage.py`` for the full explanation):

* the damaged quantity and wastage cost of the imported synthetic dataset,
* the wastage entries recorded here.

Manual wastage is written to its own ``wastage_records`` table. Nothing in this
module writes to ``daily_records``, so an imported historical row or a manual
sales record can never be overwritten, deleted or counted twice.

Every route is wrapped in ``admin_required``, so the administrator role is
verified on the server for each page and for the JSON API - a regular user is
refused both even when the URL is typed by hand.
"""

from datetime import date

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from auth.decorators import admin_required
from models import sales as sales_repo
from models import wastage as wastage_repo
from services import wastage_service

wastage_bp = Blueprint("wastage", __name__, url_prefix="/admin/wastage")


def _format_number(value):
    """Render a stored number without pointless trailing zeros."""
    return f"{float(value):g}"


def _empty_form():
    """A blank Record Wastage form, dated today."""
    return {
        "wastage_date": date.today().isoformat(),
        "district_id": "",
        "category": "",
        "fruit_id": "",
        "damaged_quantity": "",
        "cost_per_kg": "",
        "reason": "",
    }


def _catalogue():
    """The district, fruit and category lists used by every form."""
    return sales_repo.list_districts(), sales_repo.list_fruits(), sales_repo.list_categories()


@wastage_bp.route("")
@admin_required
def index():
    """Wastage dashboard: summary cards, summaries, charts and records."""
    filters, errors = wastage_service.parse_filters(request.args)

    for message in errors:
        flash(message, "error")

    return render_template(
        "admin/wastage/dashboard.html",
        page="admin",
        active_module="wastage",
        user=g.user,
        filters=filters,
        options=wastage_service.filter_options(),
        bounds=wastage_repo.data_bounds(),
        **wastage_service.build_dashboard(filters),
    )


@wastage_bp.route("/summary.json")
@admin_required
def summary_data():
    """Aggregated wastage figures for the current filters, as JSON.

    Only totals per fruit, district, category and period leave the server; the
    stored rows themselves are never sent to the browser.
    """
    filters, errors = wastage_service.parse_filters(request.args)

    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    return jsonify(wastage_service.build_chart_payload(filters))


@wastage_bp.route("/records")
@admin_required
def records():
    """Wastage Records: every historical and manual wastage record."""
    filters, errors = wastage_service.parse_filters(request.args)

    for message in errors:
        flash(message, "error")

    return render_template(
        "admin/wastage/records.html",
        page="admin",
        active_module="wastage",
        user=g.user,
        filters=filters,
        options=wastage_service.filter_options(),
        **wastage_service.build_records(filters),
    )


@wastage_bp.route("/entry", methods=["GET", "POST"])
@admin_required
def entry():
    """Record a manual wastage entry for one fruit in one district on one day."""
    districts, fruits, categories = _catalogue()

    form = _empty_form()
    errors = {}

    if request.method == "POST":
        form = wastage_service.read_entry_form(request.form)
        cleaned, errors = wastage_service.validate_entry_form(
            request.form, districts, fruits
        )

        if not errors:
            record_id, save_errors = wastage_service.save_entry(cleaned)

            if not save_errors:
                flash(
                    "Wastage recorded: "
                    f"{_format_number(cleaned['damaged_quantity'])} kg of "
                    f"{cleaned['fruit_name']} in {cleaned['district_name']} "
                    f"on {cleaned['wastage_date']}.",
                    "success",
                )
                return redirect(url_for("wastage.records"))

            errors = save_errors

    return render_template(
        "admin/wastage/entry.html",
        page="admin",
        active_module="wastage",
        user=g.user,
        mode="create",
        form=form,
        errors=errors,
        today=date.today().isoformat(),
        districts=districts,
        fruits=fruits,
        categories=categories,
        record=None,
    )


@wastage_bp.route("/records/<int:record_id>/edit", methods=["GET", "POST"])
@admin_required
def edit(record_id):
    """Edit one manually recorded wastage entry."""
    record = wastage_repo.get_manual_record(record_id)
    if record is None:
        abort(404)

    districts, fruits, categories = _catalogue()

    form = wastage_service.form_from_record(record)
    errors = {}

    if request.method == "POST":
        form = wastage_service.read_entry_form(request.form)
        cleaned, errors = wastage_service.validate_entry_form(
            request.form, districts, fruits, exclude_id=record_id
        )

        if not errors:
            saved, save_errors = wastage_service.update_entry(record_id, cleaned)

            if saved:
                flash(
                    "Wastage entry updated: "
                    f"{_format_number(cleaned['damaged_quantity'])} kg of "
                    f"{cleaned['fruit_name']} in {cleaned['district_name']} "
                    f"on {cleaned['wastage_date']}.",
                    "success",
                )
                return redirect(url_for("wastage.records"))

            errors = save_errors

    return render_template(
        "admin/wastage/entry.html",
        page="admin",
        active_module="wastage",
        user=g.user,
        mode="edit",
        form=form,
        errors=errors,
        today=date.today().isoformat(),
        districts=districts,
        fruits=fruits,
        categories=categories,
        record=record,
    )
