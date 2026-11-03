"""Profile and password management for any signed-in account.

The account is always taken from the session (``g.user``), never from the
submitted form, so a visitor cannot edit somebody else's record. The ``role``
column is not writable here.
"""

from flask import (
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

from account import account_bp
from auth.decorators import login_required
from services import auth_service, validators


@account_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    """Show and update the signed-in account."""
    user = g.user
    districts = current_app.config["DISTRICTS"]

    form = {
        "full_name": user["full_name"],
        "email": user["email"],
        "mobile": user["mobile"],
        "district": user["district"],
    }
    errors = {}
    password_errors = {}

    if request.method == "POST":
        action = request.form.get("action", "profile")

        if action == "password":
            ok, password_errors = auth_service.change_password(
                user_id=user["id"],
                current_password=request.form.get("current_password") or "",
                new_password=request.form.get("new_password") or "",
                confirm_password=request.form.get("confirm_password") or "",
            )
            if ok:
                flash("Your password has been updated.", "success")
                return redirect(url_for("account.profile"))
        else:
            form = validators.read_registration_form(request.form)
            cleaned, errors = auth_service.update_profile(
                user["id"], request.form, districts
            )
            if not errors:
                flash("Your profile has been updated.", "success")
                return redirect(url_for("account.profile"))

    return render_template(
        "account/profile.html",
        page="profile",
        user=user,
        form=form,
        errors=errors,
        password_errors=password_errors,
        districts=districts,
    )
