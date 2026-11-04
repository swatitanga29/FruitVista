"""One-time primary administrator setup for FruitVista.

This standalone script creates the primary FruitVista administrator account.

It is a local setup script, not a web page: nothing here is registered as a
Flask route, so the setup process can never be reached from a browser.

The password is typed at an interactive prompt (Python's ``getpass``) and is
therefore never echoed on screen, never passed on the command line and never
written to disk as plain text. Only its Werkzeug hash is stored, in the
existing SQLite ``users`` table.

The script is safe to run again: it refuses to continue when this email
address already exists or when an administrator account already exists, so no
account and no password is ever overwritten. Existing records are only ever
read - the new account is inserted, never updated.

Run it from the project folder on Windows 11::

    .\\.venv\\Scripts\\python.exe create_admin.py
"""

import sys
from getpass import getpass

from werkzeug.security import generate_password_hash

from app import create_app
from database import get_db
from models import user as user_repo
from models.user import PERMANENT_ADMIN_ROLES, ROLE_PERMANENT_ADMIN
from services import validators

# Details of the primary administrator. Nothing secret is kept here: the
# password is only ever typed at the prompt inside ``read_password``.
ADMIN_FULL_NAME = "Swathi Tanga"
ADMIN_EMAIL = "Swatitanga2@gmail.com"
ADMIN_MOBILE = "9832678552"
ADMIN_DISTRICT = "Vijayapura"


def existing_administrator():
    """Return a permanent administrator account that already exists, or ``None``.

    Both the current ``permanent_admin`` value and the legacy ``admin`` value
    are accepted, so an administrator created before the three-role model is
    still recognised. The roles are passed as bound parameters.
    """
    placeholders = ", ".join("?" for _ in PERMANENT_ADMIN_ROLES)
    return get_db().execute(
        f"SELECT id, full_name, email, role FROM users WHERE role IN ({placeholders}) LIMIT 1",
        PERMANENT_ADMIN_ROLES,
    ).fetchone()


def read_password():
    """Ask for the password twice with ``getpass`` and validate its strength."""
    while True:
        password = getpass("Password for the administrator account: ")
        confirmation = getpass("Confirm the password: ")

        if password != confirmation:
            print("The two passwords do not match. Please try again.")
            print()
            continue

        _, error = validators.validate_password(password)
        if error:
            print(f"{error} Please try again.")
            print()
            continue

        return password


def create_primary_admin(full_name, email, mobile, district, password, districts):
    """Create the primary administrator account.

    Returns ``(user_id, message)``. ``user_id`` is ``None`` when nothing was
    created, and ``message`` explains why. The account is only ever inserted:
    no existing row is updated, so an existing account or password can never
    be overwritten.
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

    # 1. Never overwrite an account that already uses this email address.
    existing_account = user_repo.get_user_by_email(cleaned["email"])
    if existing_account is not None:
        return None, (
            f"An account with {cleaned['email']} already exists "
            f"(role: {existing_account['role']}). Nothing was changed."
        )

    # 2. This one-time setup creates only the primary administrator.
    admin_account = existing_administrator()
    if admin_account is not None:
        return None, (
            "An administrator account already exists "
            f"({admin_account['email']}). This setup creates only the primary "
            "administrator, so nothing was changed."
        )

    # Werkzeug hashing: only the hash ever reaches the database.
    user_id = user_repo.create_user(
        full_name=cleaned["full_name"],
        email=cleaned["email"],
        mobile=cleaned["mobile"],
        district=cleaned["district"],
        password_hash=generate_password_hash(password_value),
        role=ROLE_PERMANENT_ADMIN,
    )

    return user_id, cleaned["email"]


def main():
    """Run the one-time setup."""
    # create_app also applies the safe, additive database migrations, so the
    # ``users`` table (including its ``role`` column) is ready if needed.
    # Existing tables and records are never removed or rewritten.
    app = create_app()

    with app.app_context():
        print("FruitVista - primary administrator setup")
        print("---------------------------------------")
        print(f"Full name : {ADMIN_FULL_NAME}")
        print(f"Email     : {ADMIN_EMAIL}")
        print(f"Mobile    : {ADMIN_MOBILE}")
        print(f"District  : {ADMIN_DISTRICT}")
        print(f"Role      : {ROLE_PERMANENT_ADMIN}")
        print()
        print("The password you type below is never shown or saved as text.")
        print()

        password = read_password()

        user_id, message = create_primary_admin(
            ADMIN_FULL_NAME,
            ADMIN_EMAIL,
            ADMIN_MOBILE,
            ADMIN_DISTRICT,
            password,
            app.config["DISTRICTS"],
        )

        print()
        if user_id is None:
            print(f"Not created: {message}")
            return 1

        print(f"Administrator created: {message} (id {user_id}).")
        print("Sign in at http://127.0.0.1:5000/admin/login")
        return 0


if __name__ == "__main__":
    sys.exit(main())
