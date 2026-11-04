"""Secondary Admin Management - the permanent administrator's approval area.

This page is where the permanent administrator reviews requests from normal
users who want administrator access, and where an approved secondary
administrator can have that access taken away again.

Every route is wrapped in ``permanent_admin_required``, not merely
``admin_required``: a secondary administrator is refused here with a 403 even
though they can use the rest of the administrator area. That is what enforces
"a secondary administrator cannot approve, reject or revoke another
administrator" on the server, so hiding the menu link is only ever a
convenience.
"""

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from auth.decorators import permanent_admin_required
from services import admin_request_service

secondary_admins_bp = Blueprint(
    "secondary_admins", __name__, url_prefix="/admin/secondary-admins"
)


@secondary_admins_bp.route("")
@permanent_admin_required
def index():
    """Secondary Admin Management: pending, approved and history."""
    return render_template(
        "admin/secondary_admins.html",
        page="admin",
        active_module="secondary-admins",
        user=g.user,
        **admin_request_service.build_management_view(),
    )


@secondary_admins_bp.route("/<int:request_id>/approve", methods=["POST"])
@permanent_admin_required
def approve(request_id):
    """Approve one pending request and promote the applicant.

    ``g.user`` is the permanent administrator taking the action; the service
    records who and when, and refuses a request that is no longer pending.
    """
    ok, message = admin_request_service.approve_request(request_id, g.user)
    flash(message, "success" if ok else "error")
    return redirect(url_for("secondary_admins.index"))


@secondary_admins_bp.route("/<int:request_id>/reject", methods=["POST"])
@permanent_admin_required
def reject(request_id):
    """Reject one pending request. The applicant stays a normal user."""
    note = request.form.get("note")
    ok, message = admin_request_service.reject_request(request_id, g.user, note)
    flash(message, "success" if ok else "error")
    return redirect(url_for("secondary_admins.index"))


@secondary_admins_bp.route("/<int:request_id>/revoke", methods=["POST"])
@permanent_admin_required
def revoke(request_id):
    """Revoke an approved secondary administrator's access.

    The account and all of its data are kept; only the role is lowered.
    """
    ok, message = admin_request_service.revoke_request(request_id, g.user)
    flash(message, "success" if ok else "error")
    return redirect(url_for("secondary_admins.index"))
