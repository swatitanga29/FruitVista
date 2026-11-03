"""Registration, sign-in and account-management logic for FruitVista.

Passwords are always stored as Werkzeug hashes and compared with
``check_password_hash`` - a plain-text password is never written to the
database and never leaves this module.

Role rules live here as well:

* public registration always creates a ``user`` account,
* the role is never read from a submitted form,
* only the ``create-admin`` command can create an ``admin`` account.
"""

import sqlite3

from werkzeug.security import check_password_hash, generate_password_hash

from models import user as user_repo
from models.user import ROLE_USER
from services import validators

# Compared against when an email address is unknown, so that "no such account"
# and "wrong password" take a similar amount of time to answer.
_PLACEHOLDER_HASH = generate_password_hash("fruitvista-placeholder-password")


def register_user(full_name, email, mobile, district, password):
    """Create a new public account with the ``user`` role.

    Returns the new user id, or ``None`` when the email address is already
    registered. The role is hard-coded here and is never taken from the
    request, so a visitor cannot register as an administrator.
    """
    if user_repo.get_user_by_email(email) is not None:
        return None

    password_hash = generate_password_hash(password)

    try:
        return user_repo.create_user(
            full_name=full_name,
            email=email,
            mobile=mobile,
            district=district,
            password_hash=password_hash,
            role=ROLE_USER,
        )
    except sqlite3.IntegrityError:
        # The unique email index rejected the row, meaning another request
        # registered the same address a moment earlier.
        return None


def authenticate_user(email, password):
    """Verify credentials.

    Returns ``(user_row, previous_login)`` when the email address and password
    are correct, and ``(None, None)`` otherwise. ``previous_login`` is the
    sign-in time recorded before this one, used by the dashboard.
    """
    row = user_repo.get_user_by_email(email)

    if row is None:
        check_password_hash(_PLACEHOLDER_HASH, password)
        return None, None

    if not check_password_hash(row["password_hash"], password):
        return None, None

    previous_login = row["last_login_at"]
    user_repo.touch_last_login(row["id"])

    return user_repo.get_user_by_id(row["id"]), previous_login


def is_admin(user):
    """Return ``True`` when ``user`` is an administrator account."""
    return bool(user) and user["role"] == "admin"


def update_profile(user_id, source, districts):
    """Validate and save the editable parts of a profile.

    Returns ``(cleaned, errors)``. Even when the submitted form contains a
    ``role`` field it is ignored: only ``full_name``, ``email``, ``mobile``
    and ``district`` are written.
    """
    cleaned, errors = validators.validate_registration_fields(source, districts)

    if errors:
        return cleaned, errors

    if user_repo.email_used_by_another_user(cleaned["email"], user_id):
        errors["email"] = "Another account already uses this email address."
        return cleaned, errors

    user_repo.update_profile(
        user_id=user_id,
        full_name=cleaned["full_name"],
        email=cleaned["email"],
        mobile=cleaned["mobile"],
        district=cleaned["district"],
    )
    return cleaned, errors


def change_password(user_id, current_password, new_password, confirm_password):
    """Change the password of the signed-in account.

    Returns ``(ok, errors)``. The role and every other field are left
    untouched.
    """
    errors = {}
    row = user_repo.get_user_by_id(user_id)

    if row is None:
        return False, {"form": "Your account could not be found. Please sign in again."}

    if not current_password:
        errors["current_password"] = "Please enter your current password."
    elif not check_password_hash(row["password_hash"], current_password):
        errors["current_password"] = "Your current password is not correct."

    new_value, error = validators.validate_password(new_password)
    if error:
        errors["new_password"] = error
    elif new_value == current_password:
        errors["new_password"] = "Your new password must be different from the current one."

    confirm_error = validators.validate_confirm_password(
        new_password or "", confirm_password or ""
    )
    if confirm_error:
        errors["confirm_password"] = confirm_error

    if errors:
        return False, errors

    user_repo.update_password(user_id, generate_password_hash(new_value))
    return True, errors
