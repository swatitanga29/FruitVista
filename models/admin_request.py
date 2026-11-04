"""Database access for the Secondary Admin access requests (``admin_requests``).

One row follows a request through its whole life:

``pending``  - submitted by a normal user and waiting for a decision,
``approved`` - the permanent administrator approved it and the account was
               promoted to ``secondary_admin``,
``rejected`` - the permanent administrator turned it down; the account stays
               a normal ``user``,
``revoked``  - an approved secondary administrator had their access taken
               away again; the account is a normal ``user`` once more.

Only the permanent administrator's own service (``services.admin_request_service``)
writes to this table, and only with values from the fixed constants below -
never with anything that came from a form or the address bar.

Every query uses ``?`` placeholders; nothing is formatted into the SQL string.
The functions that change a row do **not** commit: they are steps inside a
transaction that the calling service owns, so an approval and the matching
role change are committed together or not at all.
"""

from database import get_db

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_REVOKED = "revoked"

# Every value the ``status`` column may hold.
STATUSES = (STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED, STATUS_REVOKED)

# How each status is described on screen.
STATUS_LABELS = {
    STATUS_PENDING: "Pending",
    STATUS_APPROVED: "Approved",
    STATUS_REJECTED: "Rejected",
    STATUS_REVOKED: "Revoked",
}

# CSS class used for the status chip on each page.
STATUS_CLASSES = {
    STATUS_PENDING: "status-pending",
    STATUS_APPROVED: "status-approved",
    STATUS_REJECTED: "status-rejected",
    STATUS_REVOKED: "status-revoked",
}

# The statuses that count as "already decided", used by the history view.
REVIEWED_STATUSES = (STATUS_APPROVED, STATUS_REJECTED, STATUS_REVOKED)

# The columns every listing joins the applicant's account details through.
_APPLICANT_COLUMNS = (
    "u.full_name AS applicant_name, "
    "u.email     AS applicant_email, "
    "u.mobile    AS applicant_mobile, "
    "u.district  AS applicant_district, "
    "u.role      AS applicant_role"
)


def status_label(status):
    """Return the human-readable name of a status value."""
    return STATUS_LABELS.get(status, status)


def status_class(status):
    """Return the status chip CSS class for a status value."""
    return STATUS_CLASSES.get(status, "status-pending")


def get_request(request_id):
    """Return one request with its applicant's details, or ``None``."""
    return get_db().execute(
        f"SELECT r.*, {_APPLICANT_COLUMNS} "
        "FROM admin_requests r "
        "JOIN users u ON u.id = r.user_id "
        "WHERE r.id = ?",
        (request_id,),
    ).fetchone()


def get_pending_request(user_id):
    """Return the open request belonging to ``user_id``, or ``None``.

    The partial unique index guarantees there is at most one.
    """
    return get_db().execute(
        "SELECT * FROM admin_requests WHERE user_id = ? AND status = ? "
        "ORDER BY id DESC LIMIT 1",
        (user_id, STATUS_PENDING),
    ).fetchone()


def get_latest_request(user_id):
    """Return the most recent request for one account, whatever its status."""
    return get_db().execute(
        "SELECT * FROM admin_requests WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()


def list_for_user(user_id):
    """Return every request an account has ever made, newest first."""
    return get_db().execute(
        "SELECT * FROM admin_requests WHERE user_id = ? ORDER BY id DESC",
        (user_id,),
    ).fetchall()


def list_pending():
    """Return every pending request with the applicant's details, oldest first."""
    return get_db().execute(
        f"SELECT r.*, {_APPLICANT_COLUMNS} "
        "FROM admin_requests r "
        "JOIN users u ON u.id = r.user_id "
        "WHERE r.status = ? "
        "ORDER BY r.created_at ASC, r.id ASC",
        (STATUS_PENDING,),
    ).fetchall()


def list_approved():
    """Return the currently approved requests, newest decision first.

    A revoked request changes to ``revoked``, so it never appears here; the
    list therefore matches the accounts that currently hold the
    ``secondary_admin`` role.
    """
    return get_db().execute(
        f"SELECT r.*, {_APPLICANT_COLUMNS} "
        "FROM admin_requests r "
        "JOIN users u ON u.id = r.user_id "
        "WHERE r.status = ? "
        "ORDER BY r.reviewed_at DESC, r.id DESC",
        (STATUS_APPROVED,),
    ).fetchall()


def list_history():
    """Return every decided request (approved, rejected, revoked), newest first.

    The acting administrator's name is joined in for both the review and the
    revocation, so the history can name who acted and when.
    """
    placeholders = ", ".join("?" for _ in REVIEWED_STATUSES)
    return get_db().execute(
        f"SELECT r.*, {_APPLICANT_COLUMNS}, "
        "       reviewer.full_name AS reviewer_name, "
        "       revoker.full_name  AS revoker_name "
        "FROM admin_requests r "
        "JOIN users u ON u.id = r.user_id "
        "LEFT JOIN users reviewer ON reviewer.id = r.reviewed_by "
        "LEFT JOIN users revoker  ON revoker.id  = r.revoked_by "
        f"WHERE r.status IN ({placeholders}) "
        "ORDER BY COALESCE(r.revoked_at, r.reviewed_at, r.created_at) DESC, r.id DESC",
        REVIEWED_STATUSES,
    ).fetchall()


def count_pending():
    """Return how many requests are still waiting for a decision."""
    row = get_db().execute(
        "SELECT COUNT(*) AS total FROM admin_requests WHERE status = ?",
        (STATUS_PENDING,),
    ).fetchone()
    return row["total"] if row is not None else 0


def insert_request(user_id, reason):
    """Insert a new pending request and return its new id.

    No commit happens here: the caller commits. A duplicate pending request
    raises ``sqlite3.IntegrityError`` from the partial unique index, which the
    service turns into a friendly message.
    """
    cursor = get_db().execute(
        "INSERT INTO admin_requests (user_id, reason, status) VALUES (?, ?, ?)",
        (user_id, reason, STATUS_PENDING),
    )
    return cursor.lastrowid


def mark_reviewed(request_id, status, reviewer_id, note=None):
    """Move a **pending** request to ``approved`` or ``rejected``.

    The ``WHERE status = 'pending'`` clause makes the change conditional, so
    two administrators (or two clicks) can never both decide the same request:
    the second update simply matches no row. Returns ``True`` when the row was
    changed. No commit happens here - the caller controls the transaction.
    """
    if status not in (STATUS_APPROVED, STATUS_REJECTED):
        raise ValueError(f"Unsupported review status: {status}")

    cursor = get_db().execute(
        "UPDATE admin_requests "
        "SET status = ?, "
        "    reviewed_at = datetime('now', 'localtime'), "
        "    reviewed_by = ?, "
        "    review_note = ? "
        "WHERE id = ? AND status = ?",
        (status, reviewer_id, note, request_id, STATUS_PENDING),
    )
    return cursor.rowcount == 1


def mark_revoked(request_id, revoker_id):
    """Move an **approved** request to ``revoked``.

    Conditional on the current status being ``approved``, so a request that was
    already rejected, revoked or is still pending can never be revoked. Returns
    ``True`` when the row was changed. No commit happens here - the caller
    controls the transaction.
    """
    cursor = get_db().execute(
        "UPDATE admin_requests "
        "SET status = ?, "
        "    revoked_at = datetime('now', 'localtime'), "
        "    revoked_by = ? "
        "WHERE id = ? AND status = ?",
        (STATUS_REVOKED, revoker_id, request_id, STATUS_APPROVED),
    )
    return cursor.rowcount == 1
