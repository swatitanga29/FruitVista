"""Registration, sign-in and sign-out routes."""

from flask import (
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from auth import auth_bp
from auth.decorators import landing_endpoint, redirect_target
from services import auth_service, validators


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    """Create a FruitVista account.

    The role is never read from the form: ``auth_service.register_user``
    always stores the ``user`` role.
    """
    if g.user is not None:
        return redirect(url_for(landing_endpoint(g.user)))

    districts = current_app.config["DISTRICTS"]
    form = {"full_name": "", "email": "", "mobile": "", "district": ""}
    errors = {}

    if request.method == "POST":
        # Keep what the visitor typed so the form can be re-rendered on error.
        form = validators.read_registration_form(request.form)
        cleaned, errors = validators.validate_registration(request.form, districts)

        if not errors:
            user_id = auth_service.register_user(
                full_name=cleaned["full_name"],
                email=cleaned["email"],
                mobile=cleaned["mobile"],
                district=cleaned["district"],
                password=cleaned["password"],
            )

            if user_id is None:
                errors["email"] = "An account with this email address already exists."
            else:
                flash(
                    "Registration successful. Please sign in with your new account.",
                    "success",
                )
                return redirect(url_for("auth.login"))

    return render_template(
        "auth/register.html",
        page="register",
        form=form,
        errors=errors,
    )


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Verify credentials and start a session."""
    if g.user is not None:
        return redirect(url_for(landing_endpoint(g.user)))

    form = {"email": ""}
    errors = {}
    next_url = redirect_target()

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
            user, previous_login = auth_service.authenticate_user(email, password)

            if user is None:
                # One message for both wrong email and wrong password, so the
                # form cannot be used to discover which addresses are registered.
                errors["form"] = "Incorrect email address or password. Please try again."
            else:
                # Discard the old session contents first: this prevents session
                # fixation and leaves no stale data behind.
                session.clear()
                session["user_id"] = user["id"]
                session["previous_login"] = previous_login
                session.permanent = remember

                flash("You are signed in. Welcome back to FruitVista.", "success")
                return redirect(next_url or url_for(landing_endpoint(user)))

    return render_template(
        "auth/login.html",
        page="login",
        form=form,
        errors=errors,
        next_url=next_url,
    )


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """Clear the session and return to the homepage."""
    session.clear()
    flash("You have been signed out safely.", "success")
    return redirect(url_for("index"))
