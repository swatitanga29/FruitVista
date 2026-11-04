"""Administrator Sales Analytics dashboard.

Reads the aggregated sales figures for the selected filters and draws them.
Every route is wrapped in ``admin_required``, so the administrator role is
verified on the server for the page and for the JSON API - a regular user is
refused both even if they know the URL.

Nothing in this module writes to the database.
"""

from flask import Blueprint, flash, g, jsonify, render_template, request

from auth.decorators import admin_required
from services import analytics_service

analytics_bp = Blueprint("analytics", __name__, url_prefix="/admin/analytics")


@analytics_bp.route("")
@admin_required
def index():
    """Sales Analytics dashboard: summary cards, charts and filters."""
    filters, errors = analytics_service.parse_filters(request.args)

    for message in errors:
        flash(message, "error")

    return render_template(
        "admin/analytics.html",
        page="admin",
        active_module="sales-analytics",
        user=g.user,
        filters=filters,
        options=analytics_service.filter_options(),
        bounds=analytics_service.analytics.data_bounds(),
    )


@analytics_bp.route("/data")
@admin_required
def data():
    """Aggregated figures for the current filters, as JSON.

    Only totals and series leave the server - the stored records themselves
    are never sent to the browser.
    """
    filters, errors = analytics_service.parse_filters(request.args)

    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    return jsonify(analytics_service.build_dashboard(filters))
