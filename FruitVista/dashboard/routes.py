"""Protected dashboard routes."""

from flask import flash, g, redirect, render_template, request, session, url_for

from auth.decorators import login_required
from dashboard import dashboard_bp
from services import admin_request_service, validators


@dashboard_bp.route("/dashboard")
@login_required
def index():
    """Landing page for signed-in administrators and vendors."""
    return render_template(
        "dashboard/index.html",
        page="dashboard",
        user=g.user,
        previous_login=session.get("previous_login"),
        request_view=admin_request_service.build_user_view(g.user),
    )


@dashboard_bp.route("/request-admin", methods=["GET", "POST"])
@login_required
def request_admin():
    """Request administrator access, and show the status of that request.

    The role is never read from the form. ``admin_request_service`` re-checks
    on the server that the signed-in account is a normal user and that it has
    no request waiting, so a hand-made request cannot promote an account or
    create a duplicate.
    """
    form = {"reason": ""}
    errors = {}

    if request.method == "POST":
        form["reason"] = validators.clean_text(request.form.get("reason"))
        request_id, errors = admin_request_service.submit_request(
            g.user, request.form.get("reason")
        )

        if not errors and request_id is not None:
            flash(
                "Your administrator access request has been submitted for review.",
                "success",
            )
            return redirect(url_for("dashboard.request_admin"))

    return render_template(
        "dashboard/request_admin.html",
        page="dashboard",
        user=g.user,
        form=form,
        errors=errors,
        request_view=admin_request_service.build_user_view(g.user),
    )
