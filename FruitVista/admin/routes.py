"""Administrator-only pages.

Every route here is wrapped in ``admin_required``, so the account role is
checked on the server for each request - never by what the browser shows.

Sales Analytics, Inventory Management, Wastage Management, Reports and Market
Insights are the implemented modules. Their real pages live in
``admin/analytics.py``, ``admin/inventory.py``, ``admin/wastage.py``,
``admin/reports.py`` and ``admin/insights.py`` and are linked from the
administrator navigation menu. This module keeps the administrator dashboard
and the administrator sign-in page.
"""

from flask import abort, g, redirect, render_template, session, url_for

from admin import admin_bp
from auth.decorators import admin_required


@admin_bp.route("/")
@admin_required
def index():
    """Administrator dashboard: entry point to the management modules."""
    return render_template(
        "admin/index.html",
        page="admin",
        user=g.user,
        previous_login=session.get("previous_login"),
    )


@admin_bp.route("/login")
def login():
    """Administrator sign-in page.

    Anonymous visitors see the sign-in form. After a successful login the
    account role is read from the database: only administrators are sent to
    the admin dashboard, while a signed-in account without the ``admin`` role
    receives an access-denied response.
    """
    user = g.get("user")

    if user is not None:
        if user["role"] == "admin":
            return redirect(url_for("admin.index"))
        abort(403)

    return render_template(
        "auth/login.html",
        page="admin-login",
        form={"email": ""},
        errors={},
        next_url=url_for("admin.index"),
        admin_login=True,
    )
