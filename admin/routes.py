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

from flask import (
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from admin import admin_bp
from auth.decorators import admin_required
from models.user import is_admin
from services import auth_service, validators


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


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    """Administrator sign-in page, handled entirely on this endpoint.

    ``GET`` shows the dedicated administrator sign-in form. ``POST`` verifies
    the credentials here, so this page never borrows the public ``auth.login``
    route: only an account holding an administrator role is signed in and sent
    to the administrator dashboard. Correct credentials for a non-administrator
    account are refused with a clear message and no session is started.

    A visitor who is already signed in is sent straight on - an administrator to
    the dashboard, any other account to its own dashboard.
    """
    user = g.get("user")

    if user is not None:
        if is_admin(user):
            return redirect(url_for("admin.index"))
        flash("The administrator area is for administrator accounts only.", "error")
        return redirect(url_for("dashboard.index"))

    form = {"email": ""}
    errors = {}

    if request.method == "POST":
        form["email"] = validators.clean_text(request.form.get("email"))
        password = request.form.get("password") or ""
        remember = request.form.get("remember") == "on"

        email, error = validators.validate_email(form["email"])
        if error:
            errors["email"] = error

        if not password:
            errors["password"] = "Please enter your password."

        if not errors:
            account, previous_login = auth_service.authenticate_user(email, password)

            if account is None:
                # One message for both an unknown address and a wrong password.
                errors["form"] = "Incorrect email address or password. Please try again."
            elif not is_admin(account):
                errors["form"] = (
                    "This account is not an administrator. "
                    "Please use the normal sign-in page."
                )
            else:
                # Discard the old session first, then start the new one.
                session.clear()
                session["user_id"] = account["id"]
                session["previous_login"] = previous_login
                session.permanent = remember

                flash("You are signed in to the administrator area.", "success")
                return redirect(url_for("admin.index"))

    return render_template(
        "auth/login.html",
        page="admin-login",
        form=form,
        errors=errors,
        next_url=url_for("admin.index"),
        admin_login=True,
    )
