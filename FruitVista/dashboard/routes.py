"""Protected dashboard route."""

from flask import g, render_template, session

from auth.decorators import login_required
from dashboard import dashboard_bp


@dashboard_bp.route("/dashboard")
@login_required
def index():
    """Landing page for signed-in administrators and vendors."""
    return render_template(
        "dashboard/index.html",
        page="dashboard",
        user=g.user,
        previous_login=session.get("previous_login"),
    )
