"""Account blueprint for FruitVista.

Holds the profile page shared by every signed-in account. Administrators reach
it from the admin dashboard and regular users from their landing page.
"""

from flask import Blueprint

account_bp = Blueprint("account", __name__)


def init_app(app):
    """Register the account blueprint."""
    app.register_blueprint(account_bp)


# Imported last: routes.py needs ``account_bp`` to exist before it is imported.
from account import routes  # noqa: E402,F401
