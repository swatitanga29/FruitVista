"""Environment-driven creation of the initial FruitVista administrator.

The local setup script (``create_admin.py``) and the ``flask create-admin``
command both need a terminal, which a hosting platform does not provide. This
module adds a third, headless path: when the application boots with the five
administrator variables set in the environment, the primary administrator is
created automatically - but only if an administrator does not already exist.

Environment variables - every field is read from two names, and the first that
is set wins, so ``ADMIN_*`` takes precedence over ``FRUITVISTA_ADMIN_*``::

    ADMIN_NAME              FRUITVISTA_ADMIN_NAME
    ADMIN_EMAIL             FRUITVISTA_ADMIN_EMAIL
    ADMIN_PASSWORD          FRUITVISTA_ADMIN_PASSWORD
    ADMIN_MOBILE            FRUITVISTA_ADMIN_MOBILE
    ADMIN_DISTRICT          FRUITVISTA_ADMIN_DISTRICT

The ``FRUITVISTA_ADMIN_*`` names are the ones the ``flask create-admin``
command documents; accepting them here means a single Render configuration
works for both the boot-time seed and the command.

Security rules that shape everything below:

* the password is read from the environment and is *never* written to a log, a
  file or the command line - only its Werkzeug hash reaches the database;
* an existing administrator account, and any existing account using the same
  email address, is never overwritten, reset or promoted - not even when the
  address matches the configured administrator email;
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

from flask import current_app
from werkzeug.security import generate_password_hash

from config import BASE_DIR
from models import user as user_repo
from models.user import (
    PERMANENT_ADMIN_ROLES,
    ROLE_PERMANENT_ADMIN,
    is_permanent_admin,
)
from services import validators

# The environment variables that describe the primary administrator. Each field
# maps to its accepted variable names, most significant first: when both names
# of a field are set, the first one is the value that is used.
FIELD_ENV_NAMES = {
    "full_name": ("ADMIN_NAME", "FRUITVISTA_ADMIN_NAME"),
    "email": ("ADMIN_EMAIL", "FRUITVISTA_ADMIN_EMAIL"),
    "password": ("ADMIN_PASSWORD", "FRUITVISTA_ADMIN_PASSWORD"),
    "mobile": ("ADMIN_MOBILE", "FRUITVISTA_ADMIN_MOBILE"),
    "district": ("ADMIN_DISTRICT", "FRUITVISTA_ADMIN_DISTRICT"),
}

# Every accepted variable name, flattened. Kept for callers that only need the
# list of names and never a value.
ENVIRONMENT_VARIABLES = tuple(
    name for names in FIELD_ENV_NAMES.values() for name in names
)


def find_administrator():
    """Return an existing permanent administrator account, or ``None``.

    Both the current ``permanent_admin`` value and the legacy ``admin`` value
    are accepted, so a database created before the three-role model is still
    recognised and its administrator is never duplicated.
    """
    from database import get_db

    placeholders = ", ".join("?" for _ in PERMANENT_ADMIN_ROLES)
    return get_db().execute(
        f"SELECT id, email, role FROM users WHERE role IN ({placeholders}) LIMIT 1",
        PERMANENT_ADMIN_ROLES,
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
        role=ROLE_PERMANENT_ADMIN,
    )

    return user_id, "Administrator account created."


def read_environment():
    """Return ``(values, missing)`` for the administrator variables.

    ``values`` is keyed by field name and holds the raw values (the password
    included); it is never logged. For each field the first variable that is
    set wins, so ``ADMIN_*`` takes precedence over ``FRUITVISTA_ADMIN_*``.
    ``missing`` lists the field names for which neither variable is set.
    """
    values = {}
    missing = []

    for field, names in FIELD_ENV_NAMES.items():
        value = next((os.environ.get(name) for name in names if os.environ.get(name)), None)
        values[field] = value
        if not value:
            missing.append(field)

    return values, missing


def describe_missing_fields(missing):
    """Name the accepted variables for each unset field. Never any value."""
    return ", ".join(
        f"{field} ({' or '.join(FIELD_ENV_NAMES[field])})" for field in missing
    )


def seed_from_environment(app):
    """Create the primary administrator from the environment when appropriate.

    Called once while the application boots. It returns one of the strings
    ``"skipped"``, ``"created"``, ``"existing"`` or ``"invalid"`` so the caller
    (and the test suite) can check the outcome. Messages never contain the
    password or the account's identifying details.
    """
    values, missing = read_environment()

    # Nothing configured: a plain local run. Say nothing and do nothing.
    if len(missing) == len(FIELD_ENV_NAMES):
        return "skipped"

    # Partly configured: name only the missing variables, never any value.
    if missing:
        app.logger.warning(
            "Administrator setup skipped: no value is set for: %s.",
            describe_missing_fields(missing),
        )
        return "skipped"

    try:
        user_id, message = create_administrator(
            full_name=values["full_name"],
            email=values["email"],
            mobile=values["mobile"],
            district=values["district"],
            password=values["password"],
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


def describe_environment():
    """Report which administrator variables are configured.

    Returns ``(entries, complete)``. ``entries`` is a list of
    ``(field, variable_name, is_set)``. Only *whether* a variable is set is
    reported - never its value, so no password can leak into a log or screen.
    """
    entries = [
        (field, name, bool(os.environ.get(name)))
        for field, names in FIELD_ENV_NAMES.items()
        for name in names
    ]
    _, missing = read_environment()
    return entries, not missing


def database_storage_info():
    """Describe where the SQLite file lives and whether it will persist.

    ``persistent`` is ``True`` when the file is outside the project directory,
    which is what a mounted Render disk looks like. Inside the project it sits
    on the ephemeral application filesystem and is lost on redeploy.
    """
    database_path = os.path.abspath(current_app.config["DATABASE"])
    project_root = os.path.abspath(BASE_DIR)

    try:
        inside_project = os.path.commonpath([database_path, project_root]) == project_root
    except ValueError:
        # Different drives on Windows: certainly not inside the project.
        inside_project = False

    return database_path, not inside_project


def verify_configuration():
    """Inspect the administrator configuration without changing anything.

    Strictly read-only: no account is created, promoted, modified or deleted.
    The returned dictionary never contains a password or a password hash - only
    names, set/unset flags and role values.
    """
    entries, complete = describe_environment()
    values, missing = read_environment()

    configured_email = values["email"]
    configured_account = (
        user_repo.get_user_by_email(configured_email) if configured_email else None
    )

    database_path, persistent = database_storage_info()

    return {
        "environment": entries,
        "environment_complete": complete,
        "missing_fields": missing,
        "administrator": find_administrator(),
        "configured_email": configured_email,
        "configured_email_account": configured_account,
        "configured_email_role_ok": bool(
            configured_account is not None
            and is_permanent_admin(configured_account)
        ),
        "database_path": database_path,
        "database_persistent": persistent,
    }
