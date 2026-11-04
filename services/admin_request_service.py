"""Business logic for the Secondary Admin access-request workflow.

This module is the only place that decides whether a request may be submitted,
approved, rejected or revoked. Every rule is checked here, on the server, so
hiding a button in the browser can never grant a permission:

* a normal user may submit one request at a time,
* only the permanent administrator may approve, reject or revoke,
* an approval and the role change it triggers are written in one transaction,
* an account is never downgraded by accident - each role change is conditional
  on the role the account is expected to hold, so the permanent administrator
  can never be changed by this workflow.

The database steps themselves live in :mod:`models.admin_request` and
:mod:`models.user`; the transaction is owned here so the two tables are always
updated together.
"""

import sqlite3

from database import get_db
from models import admin_request as request_repo
from models import user as user_repo
from models.user import ROLE_SECONDARY_ADMIN, ROLE_USER
from services.validators import clean_text

# The reason must say something useful but must stay a sensible length.
REASON_MIN = 10
REASON_MAX = 500

# Friendly messages reused by the routes and the templates.
MESSAGE_NOT_PERMANENT = "Only the permanent administrator can manage administrator requests."
MESSAGE_ALREADY_DECIDED = "This request has already been reviewed by another administrator."
MESSAGE_NOT_ELIGIBLE = "This account can no longer be approved as a secondary administrator."


def validate_reason(value):
    """Validate the justification typed by the applicant.

    Returns ``(cleaned_reason, error_message)``. ``cleaned_reason`` is ``None``
    when the input is rejected.
    """
    reason = clean_text(value)

    if not reason:
        return None, "Please explain why you are requesting administrator access."
    if len(reason) < REASON_MIN:
        return None, f"Please write at least {REASON_MIN} characters explaining your request."
    if len(reason) > REASON_MAX:
        return None, f"The reason must be {REASON_MAX} characters or fewer."

    return reason, None


def submit_request(user, reason):
    """Create a pending access request for a signed-in normal user.

    Returns ``(request_id, errors)``. ``request_id`` is ``None`` when nothing
    was created, and ``errors`` is a dictionary of readable messages. The role
    is never read from the form: only an account that is currently a normal
    ``user`` may submit a request.
    """
    errors = {}

    if user is None:
        return None, {"form": "Please sign in again."}

    if user["role"] != ROLE_USER:
        return None, {
            "form": "Administrator accounts cannot request administrator access."
        }

    clean_reason, error = validate_reason(reason)
    if error:
        errors["reason"] = error
        return None, errors

    # A friendly message first, so the common case never reaches the database.
    if request_repo.get_pending_request(user["id"]) is not None:
        errors["form"] = (
            "You already have a request waiting for review. "
            "Please wait for the administrator's decision."
        )
        return None, errors

    try:
        request_id = request_repo.insert_request(user["id"], clean_reason)
        get_db().commit()
    except sqlite3.IntegrityError:
        # The partial unique index refused a second pending row: another submit
        # arrived a moment earlier. Report it exactly like the check above.
        get_db().rollback()
        errors["form"] = (
            "You already have a request waiting for review. "
            "Please wait for the administrator's decision."
        )
        return None, errors

    return request_id, errors


def approve_request(request_id, reviewer):
    """Approve a pending request and promote its owner to secondary administrator.

    Returns ``(ok, message)``. The request is re-read and its status is
    re-checked here, and the actual status change is conditional on
    ``status = 'pending'``, so two approvals can never both succeed. The
    request update and the role change are committed together.
    """
    if not user_repo.is_permanent_admin(reviewer):
        return False, MESSAGE_NOT_PERMANENT

    request = request_repo.get_request(request_id)
    if request is None:
        return False, "That request could not be found."

    if request["status"] != request_repo.STATUS_PENDING:
        return False, MESSAGE_ALREADY_DECIDED

    db = get_db()
    try:
        if not request_repo.mark_reviewed(
            request_id, request_repo.STATUS_APPROVED, reviewer["id"]
        ):
            db.rollback()
            return False, MESSAGE_ALREADY_DECIDED

        # Promote the applicant. ``expected_role=ROLE_USER`` keeps this honest:
        # if the account is no longer a plain user (already an administrator, or
        # changed by another action) the whole transaction is rolled back and
        # the permanent administrator can never be touched.
        if not user_repo.set_role(
            request["user_id"], ROLE_SECONDARY_ADMIN, expected_role=ROLE_USER
        ):
            db.rollback()
            return False, MESSAGE_NOT_ELIGIBLE

        db.commit()
    except Exception:
        db.rollback()
        raise

    return True, f"{request['applicant_name']}'s request was approved."


def reject_request(request_id, reviewer, note=None):
    """Reject a pending request, leaving the applicant a normal user.

    Returns ``(ok, message)``. No role change is made; only the request status
    is recorded, together with the acting administrator and the time.
    """
    if not user_repo.is_permanent_admin(reviewer):
        return False, MESSAGE_NOT_PERMANENT

    request = request_repo.get_request(request_id)
    if request is None:
        return False, "That request could not be found."

    if request["status"] != request_repo.STATUS_PENDING:
        return False, MESSAGE_ALREADY_DECIDED

    db = get_db()
    try:
        if not request_repo.mark_reviewed(
            request_id, request_repo.STATUS_REJECTED, reviewer["id"], note
        ):
            db.rollback()
            return False, MESSAGE_ALREADY_DECIDED
        db.commit()
    except Exception:
        db.rollback()
        raise

    return True, f"{request['applicant_name']}'s request was rejected."


def revoke_request(request_id, reviewer):
    """Revoke an approved secondary administrator, returning them to a normal user.

    Returns ``(ok, message)``. The account itself and all of its data are kept;
    only the role is lowered, and only when the account is still a secondary
    administrator. The permanent administrator can never be revoked this way.
    """
    if not user_repo.is_permanent_admin(reviewer):
        return False, MESSAGE_NOT_PERMANENT

    request = request_repo.get_request(request_id)
    if request is None:
        return False, "That request could not be found."

    if request["status"] != request_repo.STATUS_APPROVED:
        return False, "Only an approved secondary administrator can have access revoked."

    db = get_db()
    try:
        if not request_repo.mark_revoked(request_id, reviewer["id"]):
            db.rollback()
            return False, "This account's access has already been revoked."

        # ``expected_role=ROLE_SECONDARY_ADMIN`` means the update only applies
        # to an account that really is a secondary administrator. A permanent
        # administrator, a normal user or an already-revoked account is left
        # exactly as it is.
        if not user_repo.set_role(
            request["user_id"], ROLE_USER, expected_role=ROLE_SECONDARY_ADMIN
        ):
            db.rollback()
            return False, MESSAGE_NOT_ELIGIBLE

        db.commit()
    except Exception:
        db.rollback()
        raise

    return True, f"{request['applicant_name']}'s administrator access was revoked."


def build_management_view():
    """Everything the Secondary Admin Management page needs.

    Returns the three groups the page shows - pending requests, the currently
    approved secondary administrators and the full decision history - with the
    display labels each row needs already worked out.
    """
    pending = [
        {
            "id": row["id"],
            "name": row["applicant_name"],
            "email": row["applicant_email"],
            "mobile": row["applicant_mobile"],
            "district": row["applicant_district"],
            "reason": row["reason"],
            "created_at": row["created_at"],
        }
        for row in request_repo.list_pending()
    ]

    approved = [
        {
            "id": row["id"],
            "name": row["applicant_name"],
            "email": row["applicant_email"],
            "district": row["applicant_district"],
            "approved_at": row["reviewed_at"],
            "status": request_repo.status_label(row["status"]),
            "status_class": request_repo.status_class(row["status"]),
        }
        for row in request_repo.list_approved()
    ]

    history = []
    for row in request_repo.list_history():
        if row["status"] == request_repo.STATUS_REVOKED:
            action_date = row["revoked_at"] or row["reviewed_at"]
            actor = row["revoker_name"]
        else:
            action_date = row["reviewed_at"]
            actor = row["reviewer_name"]

        history.append(
            {
                "id": row["id"],
                "name": row["applicant_name"],
                "email": row["applicant_email"],
                "district": row["applicant_district"],
                "status": request_repo.status_label(row["status"]),
                "status_class": request_repo.status_class(row["status"]),
                "action_date": action_date,
                "actor": actor or "Unknown",
                "reason": row["reason"],
            }
        )

    return {
        "pending": pending,
        "approved": approved,
        "history": history,
        "pending_count": len(pending),
        "approved_count": len(approved),
        "history_count": len(history),
    }


def build_user_view(user):
    """Describe a signed-in user's own request situation for the dashboard.

    Returns the current pending request (if any), the most recent decision and
    whether the form should be offered at all.
    """
    if user is None:
        return {"can_request": False, "pending": None, "latest": None}

    pending_row = request_repo.get_pending_request(user["id"])
    latest_row = request_repo.get_latest_request(user["id"])

    def describe(row):
        if row is None:
            return None
        return {
            "id": row["id"],
            "reason": row["reason"],
            "status": request_repo.status_label(row["status"]),
            "status_key": row["status"],
            "status_class": request_repo.status_class(row["status"]),
            "created_at": row["created_at"],
            "reviewed_at": row["reviewed_at"],
        }

    return {
        "can_request": user["role"] == ROLE_USER and pending_row is None,
        "pending": describe(pending_row),
        "latest": describe(latest_row),
    }
