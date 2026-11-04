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
    flask --app app verify-admin
"""

import os

import click
from flask import current_app
from flask.cli import with_appcontext
from werkzeug.security import generate_password_hash

from models import user as user_repo
from models.user import ROLE_PERMANENT_ADMIN, role_label
from services import admin_setup, validators


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
        role=ROLE_PERMANENT_ADMIN,
    )

    click.echo(f"Administrator created: {cleaned['email']} (id {user_id}).")


@click.command("verify-admin")
@with_appcontext
def verify_admin_command():
    """Report on the primary administrator account. Changes nothing.

    Reads the environment variables and the database and prints what it finds:
    which administrator variables are configured, whether an administrator
    account exists, whether the configured email matches an account and whether
    that account holds an administrator role. It never creates, promotes,
    modifies or deletes anything, and never prints a password or a hash.
    """
    report = admin_setup.verify_configuration()

    click.echo("FruitVista - administrator verification (read-only)")
    click.echo("=" * 54)

    click.echo("")
    click.echo("Environment variables")
    for _field, name, is_set in report["environment"]:
        click.echo(f"  {name:<26} {'set' if is_set else 'not set'}")
    if report["environment_complete"]:
        click.echo("  -> every administrator field is configured.")
    else:
        click.echo(
            "  -> not configured: "
            + admin_setup.describe_missing_fields(report["missing_fields"])
        )

    click.echo("")
    click.echo("Administrator account")
    administrator = report["administrator"]
    if administrator is None:
        click.echo("  none found in the database")
    else:
        click.echo(f"  id     {administrator['id']}")
        click.echo(f"  email  {administrator['email']}")
        click.echo(f"  role   {role_label(administrator['role'])}")

    click.echo("")
    click.echo("Configured administrator email")
    if not report["configured_email"]:
        click.echo("  no administrator email is configured")
    else:
        click.echo(f"  email    {report['configured_email']}")
        account = report["configured_email_account"]
        if account is None:
            click.echo("  account  not found: no user uses this address")
        else:
            click.echo(f"  account  id {account['id']} (role: {account['role']})")
            click.echo(
                "  role     " + ("correct" if report["configured_email_role_ok"] else "NOT an administrator")
            )
            if not report["configured_email_role_ok"]:
                click.echo(
                    "  note     an existing account is never promoted automatically; "
                    "see the recovery steps in the documentation"
                )

    click.echo("")
    click.echo("Database")
    click.echo(f"  path  {report['database_path']}")
    click.echo(
        "  disk  "
        + (
            "outside the project (can be a persistent disk)"
            if report["database_persistent"]
            else "inside the project (ephemeral on Render - lost on redeploy)"
        )
    )

    click.echo("")
    if report["administrator"] is not None:
        click.echo("Result: OK - an administrator account exists.")
        return

    click.echo("Result: ATTENTION - no administrator account exists.")
    click.echo("Create one with: flask --app app create-admin")
    raise SystemExit(1)
