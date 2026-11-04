"""Session and role helpers that protect FruitVista pages.

Three decorators guard routes:

* ``login_required``            - any signed-in account,
* ``admin_required``            - any administrator (permanent or secondary),
* ``permanent_admin_required``  - the original full-authority administrator only.

Every decorator checks the server-side session and the role stored in the
database on every request. Navigation links are hidden as a convenience, but
access is always decided here, not by what the browser displays. This is what
enforces the rule that only the permanent administrator may approve, reject or
revoke secondary administrators.
"""

from functools import wraps
from urllib.parse import urlsplit

from flask import abort, g, redirect, request, session, url_for

import models.user as user_repo


def load_logged_in_user():
    """Load the signed-in user before each request.

    Runs before every request and stores the account on ``flask.g``. When a
    session points at an account that no longer exists, the stale session is
    discarded so the visitor is treated as anonymous.
    """
    user_id = session.get("user_id")
    g.user = user_repo.get_user_by_id(user_id) if user_id else None

    if user_id and g.user is None:
        session.clear()


def login_required(view):
    """Send anonymous visitors to the login page, remembering where they were."""

    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if g.get("user") is None:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)

    return wrapped_view


def admin_required(view):
    """Allow any administrator (permanent or secondary).

    Anonymous visitors are redirected to the administrator sign-in page;
    signed-in accounts without an administrator role receive an access-denied
    (403) response.
    """

    @wraps(view)
    def wrapped_view(*args, **kwargs):
        user = g.get("user")

        if user is None:
            return redirect(url_for("admin.login", next=request.full_path.rstrip("?")))

        if not user_repo.is_admin(user):
            abort(403)

        return view(*args, **kwargs)

    return wrapped_view


def permanent_admin_required(view):
    """Allow the original, full-authority administrator only.

    This guards the Secondary Admin Management area. A secondary
    administrator is refused with a 403 even though they may use the rest of
    the administrator area, so they can never approve, reject or revoke other
    administrators. Anonymous visitors go to the administrator sign-in page.
    """

    @wraps(view)
    def wrapped_view(*args, **kwargs):
        user = g.get("user")

        if user is None:
            return redirect(url_for("admin.login", next=request.full_path.rstrip("?")))

        if not user_repo.is_permanent_admin(user):
            abort(403)

        return view(*args, **kwargs)

    return wrapped_view


def landing_endpoint(user):
    """Return the endpoint a signed-in account should be sent to after login."""
    return "admin.index" if user_repo.is_admin(user) else "dashboard.index"


def redirect_target(default=None):
    """Return the requested local path, or ``default`` when it is unsafe.

    Only same-site paths are accepted, which blocks "open redirect" attempts
    such as ``/login?next=https://example.com``.
    """
    target = request.args.get("next") or request.form.get("next")

    if not target:
        return default

    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//"):
        return default

    return target
