"""Administrator Sales Entry module.

Administrators record the fruit sales that happen in their market. The
records are written to the shared ``daily_records`` table with
``record_source = 'manual'``; imported historical data is never listed,
changed or deleted from here.

Every route is wrapped in ``admin_required``, so the role is verified on the
server for each request - hiding the navigation link is only a convenience.
"""

from datetime import date

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

from auth.decorators import admin_required
from models import sales as sales_repo
from services import sales_service

sales_bp = Blueprint("sales", __name__, url_prefix="/admin/sales")


def _format_number(value):
    """Render a stored number without pointless trailing zeros."""
    return f"{float(value):g}"


def _empty_form():
    """A blank Sales Entry form, dated today."""
    return {
        "record_date": date.today().isoformat(),
        "district_id": "",
        "category": "",
        "fruit_id": "",
        "quantity_sold": "",
        "selling_price": "",
    }


def _form_from_record(record):
    """Fill the form with the values of a saved record."""
    return {
        "record_date": record["record_date"],
        "district_id": str(record["district_id"]),
        "category": record["fruit_category"],
        "fruit_id": str(record["fruit_id"]),
        "quantity_sold": _format_number(record["quantity_sold"]),
        "selling_price": _format_number(record["selling_price"]),
    }


@sales_bp.route("", methods=["GET", "POST"])
@admin_required
def entry():
    """Sales Entry: record one day of sales for a fruit in a district."""
    districts = sales_repo.list_districts()
    fruits = sales_repo.list_fruits()
    categories = sales_repo.list_categories()

    form = _empty_form()
    errors = {}

    if request.method == "POST":
        form = sales_service.read_sales_form(request.form)
        cleaned, errors = sales_service.validate_sales_form(request.form, districts, fruits)

        if not errors:
            record_id, save_errors = sales_service.save_new_record(cleaned)

            if not save_errors:
                flash(
                    "Sales record saved: "
                    f"{cleaned['fruit_name']} in {cleaned['district_name']} "
                    f"on {cleaned['record_date']}.",
                    "success",
                )
                return redirect(url_for("sales.records"))

            errors = save_errors

    return render_template(
        "admin/sales/form.html",
        page="admin",
        active_module="sales-entry",
        user=g.user,
        mode="create",
        form=form,
        errors=errors,
        today=date.today().isoformat(),
        districts=districts,
        fruits=fruits,
        categories=categories,
        record=None,
    )


@sales_bp.route("/records")
@admin_required
def records():
    """Sales Records: list, search and filter the manually entered sales."""
    districts = sales_repo.list_districts()
    fruits = sales_repo.list_fruits()
    categories = sales_repo.list_categories()

    filters, _ = sales_service.read_filters(request.args, districts, fruits)

    rows = sales_repo.list_records(
        date_from=filters["date_from"] or None,
        date_to=filters["date_to"] or None,
        district_id=filters["district_id"] or None,
        category=filters["category"] or None,
        fruit_id=filters["fruit_id"] or None,
    )

    total_quantity = sum(row["quantity_sold"] for row in rows)
    total_revenue = sum(row["sales_revenue"] for row in rows)

    return render_template(
        "admin/sales/records.html",
        page="admin",
        active_module="sales-records",
        user=g.user,
        rows=rows,
        filters=filters,
        districts=districts,
        fruits=fruits,
        categories=categories,
        total_quantity=total_quantity,
        total_revenue=total_revenue,
    )


@sales_bp.route("/records/<int:record_id>/edit", methods=["GET", "POST"])
@admin_required
def edit(record_id):
    """Edit one manually entered sales record."""
    record = sales_repo.get_record(record_id)
    if record is None:
        abort(404)

    districts = sales_repo.list_districts()
    fruits = sales_repo.list_fruits()
    categories = sales_repo.list_categories()

    form = _form_from_record(record)
    errors = {}

    if request.method == "POST":
        form = sales_service.read_sales_form(request.form)
        cleaned, errors = sales_service.validate_sales_form(request.form, districts, fruits)

        if not errors:
            saved, save_errors = sales_service.save_existing_record(record_id, cleaned)

            if saved:
                flash(
                    "Sales record updated: "
                    f"{cleaned['fruit_name']} in {cleaned['district_name']} "
                    f"on {cleaned['record_date']}.",
                    "success",
                )
                return redirect(url_for("sales.records"))

            errors = save_errors

    return render_template(
        "admin/sales/form.html",
        page="admin",
        active_module="sales-records",
        user=g.user,
        mode="edit",
        form=form,
        errors=errors,
        today=date.today().isoformat(),
        districts=districts,
        fruits=fruits,
        categories=categories,
        record=record,
    )


@sales_bp.route("/records/<int:record_id>/delete", methods=["POST"])
@admin_required
def delete(record_id):
    """Delete one manually entered sales record, after confirmation in the page."""
    record = sales_repo.get_record(record_id)
    if record is None:
        abort(404)

    sales_service.delete_record(record_id)
    flash(
        "Sales record deleted: "
        f"{record['fruit_name']} in {record['district_name']} "
        f"on {record['record_date']}.",
        "success",
    )

    # Keep the current filters so the administrator stays on the same view.
    _, query = sales_service.read_filters(
        request.form, sales_repo.list_districts(), sales_repo.list_fruits()
    )
    return redirect(url_for("sales.records", **query))
