"""Administrator Inventory Management module.

FruitVista shows the stock position of every fruit in every district: the
opening stock, the quantities purchased and sold, the damaged quantity, the
remaining (closing) stock and whether the fruit is above, at, or below its
reorder level.

Three sets of records are read side by side and never mixed up (see
``models/inventory.py`` for the full explanation):

* the imported synthetic historical dataset,
* the manual sales entered in the Sales Entry module,
* the purchases and stock adjustments entered here.

Purchases and adjustments are written to their own ``stock_movements`` table.
Nothing in this module writes to ``daily_records``, so an imported historical
row or a manual sales record can never be overwritten, deleted or counted
twice.

Every route is wrapped in ``admin_required``, so the administrator role is
verified on the server for each page and for the JSON API - a regular user is
refused both even when the URL is typed by hand.
"""

from datetime import date

from flask import (
    Blueprint,
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
from services import inventory_service

inventory_bp = Blueprint("inventory", __name__, url_prefix="/admin/inventory")


def _format_number(value):
    """Render a stored number without pointless trailing zeros."""
    return f"{float(value):g}"


def _empty_form():
    """A blank purchase or adjustment form, dated today."""
    return {
        "movement_date": date.today().isoformat(),
        "district_id": "",
        "category": "",
        "fruit_id": "",
        "quantity": "",
        "unit_price": "",
        "reason": "",
    }


def _catalogue():
    """The district, fruit and category lists used by every form."""
    return sales_repo.list_districts(), sales_repo.list_fruits(), sales_repo.list_categories()


@inventory_bp.route("")
@admin_required
def index():
    """Inventory dashboard: summary cards, summaries, alerts and the table."""
    filters, errors = inventory_service.parse_filters(request.args)

    for message in errors:
        flash(message, "error")

    return render_template(
        "admin/inventory/dashboard.html",
        page="admin",
        active_module="inventory",
        user=g.user,
        filters=filters,
        options=inventory_service.filter_options(),
        bounds=inventory_service.inventory_repo.data_bounds(),
        **inventory_service.build_dashboard(filters),
    )


@inventory_bp.route("/summary.json")
@admin_required
def summary_data():
    """Aggregated stock figures for the current filters, as JSON.

    Only totals per district and per category leave the server; the stored
    rows themselves are never sent to the browser.
    """
    filters, errors = inventory_service.parse_filters(request.args)

    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    return jsonify(inventory_service.build_chart_payload(filters))


@inventory_bp.route("/purchase", methods=["GET", "POST"])
@admin_required
def purchase():
    """Record a fruit purchase for one district on one day."""
    districts, fruits, categories = _catalogue()

    form = _empty_form()
    errors = {}

    if request.method == "POST":
        form = inventory_service.read_purchase_form(request.form)
        cleaned, errors = inventory_service.validate_purchase_form(request.form, districts, fruits)

        if not errors:
            movement_id, save_errors = inventory_service.save_purchase(cleaned)

            if not save_errors:
                flash(
                    "Purchase recorded: "
                    f"{_format_number(cleaned['quantity'])} kg of "
                    f"{cleaned['fruit_name']} in {cleaned['district_name']} "
                    f"on {cleaned['movement_date']}.",
                    "success",
                )
                return redirect(url_for("inventory.movements"))

            errors = save_errors

    return render_template(
        "admin/inventory/purchase.html",
        page="admin",
        active_module="inventory",
        user=g.user,
        form=form,
        errors=errors,
        today=date.today().isoformat(),
        districts=districts,
        fruits=fruits,
        categories=categories,
    )


@inventory_bp.route("/adjustment", methods=["GET", "POST"])
@admin_required
def adjustment():
    """Record a stock adjustment, with the reason it was made."""
    districts, fruits, categories = _catalogue()

    form = _empty_form()
    errors = {}

    if request.method == "POST":
        form = inventory_service.read_adjustment_form(request.form)
        cleaned, errors = inventory_service.validate_adjustment_form(
            request.form, districts, fruits
        )

        if not errors:
            movement_id, save_errors = inventory_service.save_adjustment(cleaned)

            if not save_errors:
                direction = "added to" if cleaned["quantity"] > 0 else "removed from"
                flash(
                    "Stock adjustment recorded: "
                    f"{_format_number(abs(cleaned['quantity']))} kg {direction} "
                    f"{cleaned['fruit_name']} stock in {cleaned['district_name']} "
                    f"on {cleaned['movement_date']}.",
                    "success",
                )
                return redirect(url_for("inventory.movements"))

            errors = save_errors

    return render_template(
        "admin/inventory/adjustment.html",
        page="admin",
        active_module="inventory",
        user=g.user,
        form=form,
        errors=errors,
        today=date.today().isoformat(),
        districts=districts,
        fruits=fruits,
        categories=categories,
    )


@inventory_bp.route("/movements")
@admin_required
def movements():
    """Stock Movement History: every purchase and stock adjustment recorded."""
    filters, errors = inventory_service.parse_movement_filters(request.args)

    for message in errors:
        flash(message, "error")

    return render_template(
        "admin/inventory/movements.html",
        page="admin",
        active_module="inventory",
        user=g.user,
        filters=filters,
        options=inventory_service.filter_options(),
        **inventory_service.read_movement_history(filters),
    )
