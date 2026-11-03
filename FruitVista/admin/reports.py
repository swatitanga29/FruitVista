"""Administrator Reports and Export Management module.

FruitVista builds one report from the selected filters and shows it on the
page. The same report can be downloaded as a PDF, a CSV file or an Excel
workbook. Every figure comes from the module that already owns it - sales from
Sales Analytics, stock from Inventory Management and wastage from Wastage
Management - so a report and its dashboard always agree.

Nothing here writes to the database: a report is read-only from beginning to
end, and generating one never changes a stored record.

Every route is wrapped in ``admin_required``, so the administrator role is
verified on the server for the page and for each download - a regular user is
refused both even when the address is typed by hand.
"""

from datetime import date
from io import BytesIO

from flask import Blueprint, abort, flash, g, render_template, request, send_file

from auth.decorators import admin_required
from services import report_export, reports_service

reports_bp = Blueprint("reports", __name__, url_prefix="/admin/reports")

# The download formats, with their file extension and content type.
EXPORT_FORMATS = {
    "pdf": (
        "pdf",
        "application/pdf",
    ),
    "csv": (
        "csv",
        "text/csv",
    ),
    "xlsx": (
        "xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
}

EXPORT_BUILDERS = {
    "pdf": report_export.pdf_bytes,
    "csv": report_export.csv_bytes,
    "xlsx": report_export.xlsx_bytes,
}


@reports_bp.route("")
@admin_required
def index():
    """Reports page: filter bar, report preview and the download buttons."""
    filters, errors = reports_service.parse_filters(request.args)

    for message in errors:
        flash(message, "error")

    return render_template(
        "admin/reports/dashboard.html",
        page="admin",
        active_module="reports",
        user=g.user,
        filters=filters,
        options=reports_service.filter_options(),
        bounds=reports_service.data_bounds(),
        report=reports_service.build_report(filters),
        # The report type travels in the download address, so it is left out
        # of the query string the download links carry.
        download_query={
            name: value
            for name, value in reports_service.filter_query(filters).items()
            if name != "report_type"
        },
    )


@reports_bp.route("/download/<report_type>/<file_format>")
@admin_required
def download(report_type, file_format):
    """Download the current report as a PDF, a CSV file or an Excel workbook.

    The report type and the filters travel in the address, so the download
    matches exactly the report that is on the page.
    """
    if report_type not in reports_service.REPORT_TYPES:
        abort(404)
    if file_format not in EXPORT_FORMATS:
        abort(404)

    filters, _ = reports_service.parse_filters(request.args)
    filters["report_type"] = report_type

    report = reports_service.build_report(filters)
    content = EXPORT_BUILDERS[file_format](report)

    extension, mimetype = EXPORT_FORMATS[file_format]
    filename = (
        f"FruitVista_{report_type}_report_{date.today().isoformat()}.{extension}"
    )

    return send_file(
        BytesIO(content),
        mimetype=mimetype,
        as_attachment=True,
        download_name=filename,
    )
