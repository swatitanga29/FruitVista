"""Administrator area blueprint for FruitVista.

Only accounts with the ``admin`` role can reach anything registered here.
The administrator navigation menu lives in this blueprint. Sales Analytics,
Inventory Management, Wastage Management, Reports, Market Insights and
Machine Learning are implemented modules, each with its own blueprint
registered below.
"""

from flask import Blueprint

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def init_app(app):
    """Register the administrator blueprints and command."""
    from admin.analytics import analytics_bp
    from admin.cli import create_admin_command, verify_admin_command
    from admin.imports import data_import_bp
    from admin.insights import insights_bp
    from admin.inventory import inventory_bp
    from admin.ml import ml_bp
    from admin.reports import reports_bp
    from admin.sales import sales_bp
    from admin.secondary_admins import secondary_admins_bp
    from admin.wastage import wastage_bp

    app.register_blueprint(admin_bp)
    app.register_blueprint(sales_bp)
    app.register_blueprint(data_import_bp)
    app.register_blueprint(analytics_bp)
    app.register_blueprint(inventory_bp)
    app.register_blueprint(wastage_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(insights_bp)
    app.register_blueprint(ml_bp)
    app.register_blueprint(secondary_admins_bp)
    app.cli.add_command(create_admin_command)
    app.cli.add_command(verify_admin_command)


# Imported last: routes.py needs ``admin_bp`` to exist before it is imported.
from admin import routes  # noqa: E402,F401
