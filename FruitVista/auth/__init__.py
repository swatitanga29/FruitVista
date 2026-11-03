"""Authentication blueprint for FruitVista.

Handles registration, sign-in and sign-out, keeps the signed-in user on
``flask.g`` for every request, and exposes it to templates as ``current_user``.
"""

from flask import Blueprint, g

auth_bp = Blueprint("auth", __name__)


def init_app(app):
    """Register the blueprint, the session loader and the template helper."""
    from auth.decorators import load_logged_in_user

    app.register_blueprint(auth_bp)
    app.before_request(load_logged_in_user)
    app.context_processor(inject_current_user)


def inject_current_user():
    """Make ``current_user`` (a database row or ``None``) available to templates."""
    return {"current_user": g.get("user")}


# Imported last: routes.py needs ``auth_bp`` to exist before it is imported.
from auth import routes  # noqa: E402,F401
