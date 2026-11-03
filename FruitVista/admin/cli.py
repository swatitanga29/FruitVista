"""One-time administrator setup for FruitVista.

Administrator accounts are never created through public registration. This
command is the only place that writes the ``admin`` role.

Values are read from command-line options first, then from environment
variables, then prompted for interactively. No password is ever hardcoded.

Environment variables::

    FRUITVISTA_ADMIN_NAME
    FRUITVISTA_ADMIN_EMAIL
    FRUITVISTA_ADMIN_MOBILE
    FRUITVISTA_ADMIN_DISTRICT
    FRUITVISTA_ADMIN_PASSWORD

Usage::

    flask --app app create-admin
"""

import os

import click
from flask import current_app
from flask.cli import with_appcontext
from werkzeug.security import generate_password_hash

from models import user as user_repo
from models.user import ROLE_ADMIN
from services import validators


@click.command("create-admin")
@click.option("--name", default=None, help="Full name (or FRUITVISTA_ADMIN_NAME).")
@click.option("--email", default=None, help="Email address (or FRUITVISTA_ADMIN_EMAIL).")
@click.option("--mobile", default=None, help="Mobile number (or FRUITVISTA_ADMIN_MOBILE).")
@click.option("--district", default=None, help="District (or FRUITVISTA_ADMIN_DISTRICT).")
@click.option(
    "--password",
    default=None,
    help="Password (or FRUITVISTA_ADMIN_PASSWORD). Prefer the environment variable.",
)
@with_appcontext
def create_admin_command(name, email, mobile, district, password):
    """Create the first FruitVista administrator account."""
    districts = current_app.config["DISTRICTS"]

    name = name or os.environ.get("FRUITVISTA_ADMIN_NAME")
    email = email or os.environ.get("FRUITVISTA_ADMIN_EMAIL")
    mobile = mobile or os.environ.get("FRUITVISTA_ADMIN_MOBILE")
    district = district or os.environ.get("FRUITVISTA_ADMIN_DISTRICT")
    password = password or os.environ.get("FRUITVISTA_ADMIN_PASSWORD")

    if not name:
        name = click.prompt("Full name")
    if not email:
        email = click.prompt("Email address")
    if not mobile:
        mobile = click.prompt("Mobile number")
    if not district:
        district = click.prompt(
            "District", type=click.Choice(districts), default=districts[0]
        )
    if not password:
        password = click.prompt(
            "Password", hide_input=True, confirmation_prompt=True
        )

    cleaned, errors = validators.validate_registration_fields(
        {
            "full_name": name,
            "email": email,
            "mobile": mobile,
            "district": district,
        },
        districts,
    )
    password_value, password_error = validators.validate_password(password)

    if errors or password_error:
        for message in list(errors.values()) + ([password_error] if password_error else []):
            click.echo(f"Error: {message}", err=True)
        raise SystemExit(1)

    existing = user_repo.get_user_by_email(cleaned["email"])
    if existing is not None:
        click.echo(
            f"An account with {cleaned['email']} already exists "
            f"(role: {existing['role']}).",
            err=True,
        )
        click.echo(
            "Promoting an existing account is intentionally not supported; "
            "use a different email address.",
            err=True,
        )
        raise SystemExit(1)

    user_id = user_repo.create_user(
        full_name=cleaned["full_name"],
        email=cleaned["email"],
        mobile=cleaned["mobile"],
        district=cleaned["district"],
        password_hash=generate_password_hash(password_value),
        role=ROLE_ADMIN,
    )

    click.echo(f"Administrator created: {cleaned['email']} (id {user_id}).")
