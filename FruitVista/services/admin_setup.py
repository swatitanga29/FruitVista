"""Environment-driven creation of the initial FruitVista administrator.

The local setup script (``create_admin.py``) and the ``flask create-admin``
command both need a terminal, which a hosting platform does not provide. This
module adds a third, headless path: when the application boots with the five
administrator variables set in the environment, the primary administrator is
created automatically - but only if an administrator does not already exist.

Environment variables::

    ADMIN_NAME
    ADMIN_EMAIL
    ADMIN_PASSWORD
    ADMIN_MOBILE
    ADMIN_DISTRICT

Security rules that shape everything below:

* the password is read from the environment and is *never* written to a log, a
  file or the command line - only its Werkzeug hash reaches the database;
* an existing administrator account, and any existing account using the same
  email address, is never overwritten, reset or promoted;
* nothing here is registered as a Flask route, so the setup can never be
  reached from a browser and the variables are never exposed by an endpoint;
* when the variables are absent the function returns quietly, so a local run
  behaves exactly as before.

Because the hosting filesystem may be recreated on every restart, this runs on
each boot: the "an administrator already exists" guard is what keeps a restart
from duplicating or overwriting the account.
"""

import os
import sqlite3

from werkzeug.security import generate_password_hash

from models import user as user_repo
from models.user import ROLE_ADMIN
from services import validators

# The environment variables that describe the primary administrator.
ENV_NAME = "ADMIN_NAME"
ENV_EMAIL = "ADMIN_EMAIL"
ENV_PASSWORD = "ADMIN_PASSWORD"
ENV_MOBILE = "ADMIN_MOBILE"
ENV_DISTRICT = "ADMIN_DISTRICT"

ENVIRONMENT_VARIABLES = (
    ENV_NAME,
    ENV_EMAIL,
    ENV_PASSWORD,
    ENV_MOBILE,
    ENV_DISTRICT,
)


def find_administrator():
    """Return an existing administrator account, or ``None``.

    The role is passed as a bound parameter, so the query stays parameterized.
    """
    from database import get_db

    return get_db().execute(
        "SELECT id, email, role FROM users WHERE role = ? LIMIT 1",
        (ROLE_ADMIN,),
    ).fetchone()


def create_administrator(full_name, email, mobile, district, password, districts):
    """Validate and insert the primary administrator account.

    Returns ``(user_id, message)``. ``user_id`` is ``None`` when nothing was
    created and ``message`` explains why. Only an ``INSERT`` is ever issued:
    no existing row is updated, so an existing account or password can never be
    overwritten.
    """
    cleaned, errors = validators.validate_registration_fields(
        {
            "full_name": full_name,
            "email": email,
            "mobile": mobile,
            "district": district,
        },
        districts,
    )
    if errors:
        return None, " ".join(errors.values())

    password_value, password_error = validators.validate_password(password)
    if password_error:
        return None, password_error

    # Never reuse an email address that already belongs to an account.
    existing_account = user_repo.get_user_by_email(cleaned["email"])
    if existing_account is not None:
        return None, (
            "An account already uses the configured email address "
            f"(role: {existing_account['role']}). Nothing was changed."
        )

    # Only create when no administrator exists at all.
    admin_account = find_administrator()
    if admin_account is not None:
        return None, "An administrator account already exists. Nothing was changed."

    # Werkzeug hashing: only the hash ever reaches the database.
    user_id = user_repo.create_user(
        full_name=cleaned["full_name"],
        email=cleaned["email"],
        mobile=cleaned["mobile"],
        district=cleaned["district"],
        password_hash=generate_password_hash(password_value),
        role=ROLE_ADMIN,
    )

    return user_id, "Administrator account created."


def read_environment():
    """Return ``(values, missing)`` for the administrator variables.

    ``values`` holds the raw values (the password included) and ``missing``
    lists the names of the variables that are not set. Neither is ever logged.
    """
    values = {name: os.environ.get(name) for name in ENVIRONMENT_VARIABLES}
    missing = [name for name in ENVIRONMENT_VARIABLES if not values[name]]
    return values, missing


def seed_from_environment(app):
    """Create the primary administrator from the environment when appropriate.

    Called once while the application boots. It returns one of the strings
    ``"skipped"``, ``"created"``, ``"existing"`` or ``"invalid"`` so the caller
    (and the test suite) can check the outcome. Messages never contain the
    password or the account's identifying details.
    """
    values, missing = read_environment()

    # Nothing configured: a plain local run. Say nothing and do nothing.
    if len(missing) == len(ENVIRONMENT_VARIABLES):
        return "skipped"

    # Partly configured: name only the missing variables, never any value.
    if missing:
        app.logger.warning(
            "Administrator setup skipped: these environment variables are not "
            "set: %s.",
            ", ".join(missing),
        )
        return "skipped"

    try:
        user_id, message = create_administrator(
            full_name=values[ENV_NAME],
            email=values[ENV_EMAIL],
            mobile=values[ENV_MOBILE],
            district=values[ENV_DISTRICT],
            password=values[ENV_PASSWORD],
            districts=app.config["DISTRICTS"],
        )
    except sqlite3.IntegrityError:
        # A second worker inserted the same account a moment ago.
        app.logger.info("Administrator account is already present.")
        return "existing"

    if user_id is None:
        # The reason is safe to log: it never echoes the password. When the
        # administrator already exists this is the normal, expected outcome.
        if "already exists" in message or "already uses" in message:
            app.logger.info("Administrator setup: %s", message)
            return "existing"
        app.logger.warning("Administrator setup skipped: %s", message)
        return "invalid"

    app.logger.info("Administrator account created.")
    return "created"
