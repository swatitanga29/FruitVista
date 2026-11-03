"""Administrator Market Insights module.

The page presents the observations FruitVista can defend from its own stored
data: best and least-selling fruits, revenue leaders, growth between months,
daily, monthly, yearly and seasonal patterns, category performance, stock and
reorder positions, wastage hot spots and a five-district comparison.

No figure is calculated here. Each one is asked from the module that already
owns it - sales from Sales Analytics, stock from Inventory Management and
wastage from Wastage Management - so every insight on this page agrees with the
matching dashboard and with the Reports module. Nothing in this blueprint
writes to the database.

Every route is wrapped in ``admin_required``, so the administrator role is
verified on the server for the page and for the JSON API alike - a regular user
is refused both even when the address is typed by hand.
"""

from flask import Blueprint, flash, g, jsonify, render_template, request

from auth.decorators import admin_required
from services import insights_service

insights_bp = Blueprint("insights", __name__, url_prefix="/admin/insights")


@insights_bp.route("")
@admin_required
def index():
    """Market Insights page: filters, summary cards, charts and observations."""
    filters, errors = insights_service.parse_filters(request.args)

    for message in errors:
        flash(message, "error")

    return render_template(
        "admin/insights/dashboard.html",
        page="admin",
        active_module="insights",
        user=g.user,
        filters=filters,
        options=insights_service.filter_options(),
        bounds=insights_service.data_bounds(),
        insights=insights_service.build_insights(filters),
    )


@insights_bp.route("/data")
@admin_required
def data():
    """The same insight payload as JSON, for the filter controls to refresh.

    Only the assembled figures and the generated observations leave the
    server - the stored records themselves are never sent to the browser.
    """
    filters, errors = insights_service.parse_filters(request.args)

    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    return jsonify(insights_service.build_insights(filters))
