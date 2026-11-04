"""Shared web-security setup for FruitVista.

CSRF protection is enabled for every state-changing request. Flask-WTF checks
the hidden ``csrf_token`` field on each POST, so a form cannot be submitted
from another site. Templates add the token with ``{{ csrf_token() }}``.
"""

from flask_wtf.csrf import CSRFProtect

csrf = CSRFProtect()


def init_app(app):
    """Enable CSRF protection on the Flask application."""
    csrf.init_app(app)
