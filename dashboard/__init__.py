"""Dashboard blueprint for FruitVista.

Only the protected landing page for signed-in users lives here. Sales
analytics, charts and price prediction are added in later phases.
"""

from flask import Blueprint

dashboard_bp = Blueprint("dashboard", __name__)


def init_app(app):
    """Register the dashboard blueprint."""
    app.register_blueprint(dashboard_bp)


# Imported last: routes.py needs ``dashboard_bp`` to exist first.
from dashboard import routes  # noqa: E402,F401
