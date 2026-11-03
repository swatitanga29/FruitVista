"""FruitVista - application entry point.

Fresh Insights, Smarter Markets
Fruit Market Sales Analytics and Price Prediction

Implemented so far:
    * Phase 1 - project structure, configuration, SQLite connection, base
      layout, stylesheet and the public homepage.
    * Phase 2 - user registration, sign-in, sign-out, protected dashboard
      access and a reusable ``login_required`` decorator.
    * Phase 3 - role-based access control: a ``role`` column, the
      ``admin_required`` decorator, an administrator dashboard, a regular user
      landing page, a shared profile page and CSRF protection.
    * Phase 4 - administrator sales data entry and the historical CSV import
      (chunked, duplicate-safe, with progress and skip reporting).
    * Phase 5 - administrator sales analytics: summary cards and Chart.js
      charts for the daily, monthly, yearly, fruit, category and district
      views, with filters and server-side aggregation.
    * Phase 6 - administrator inventory management: opening, purchased,
      available and closing stock, damaged quantities, reorder levels,
      recalculated stock status with low-stock alerts, stock purchases and
      stock adjustments kept in their own table, and a stock movement history.
    * Phase 7 - administrator wastage management: total damaged quantity,
      wastage cost and wastage percentage, fruit-wise, district-wise and
      category-wise wastage, daily, monthly and yearly trends, and manual
      wastage entries kept in their own table beside the historical dataset.
    * Phase 8 - administrator reports and export management: Sales, Inventory,
      Wastage and Market Summary reports built from the module that owns each
      figure, with date, district, category, fruit and record-source filters,
      a report preview with charts, and PDF, CSV and Excel downloads.
    * Phase 9 - administrator market insights: plain-language observations
      generated from the same figures Sales Analytics, Inventory Management and
      Wastage Management already publish - best and least-selling fruits,
      revenue leaders, growth, daily, monthly, yearly and seasonal patterns,
      category performance, stock and reorder positions, wastage hot spots and
      a five-district comparison - with the same filters, summary cards, charts
      and insight panels, read-only and labelled as historical or manual.
    * Phase 10 - administrator machine learning and prediction: price, sales
      and demand forecasts built from the stored daily history. Every task
      compares Linear Regression, Random Forest, Gradient Boosting and a
      seasonal time-series baseline on the same chronological split, keeps the
      best performer and saves it as a new model version that an administrator
      trains on demand. Forecasts, the price, sales and demand views, the
      actual-versus-predicted evaluation and the prediction history are all
      labelled as experimental estimates, because the models are trained on the
      synthetic dataset.

Run locally on Windows 11:
    python app.py
"""

import os
from datetime import datetime

from flask import Flask, jsonify, render_template
from flask_wtf.csrf import CSRFError

import account
import admin
import auth
import dashboard
import database
import security
from database import get_db
from config import get_config
from services import admin_setup


def create_app(config_name=None):
    """Build and configure the Flask application."""
    app = Flask(__name__)
    app.config.from_object(get_config(config_name))

    # Database: register the helpers, then create the folder and schema
    # if they do not exist yet. This never deletes existing records.
    database.init_app(app)
    database.bootstrap(app)

    # Optional one-time administrator creation for hosting platforms without a
    # shell. It runs after the schema exists, creates the account only when the
    # ADMIN_* variables are set AND no administrator exists, and returns
    # quietly otherwise so local runs are unaffected.
    if not app.config["TESTING"]:
        with app.app_context():
            admin_setup.seed_from_environment(app)

    # Cross-site request forgery protection for every POST request.
    security.init_app(app)

    # Feature areas are separate blueprints, registered here.
    auth.init_app(app)
    dashboard.init_app(app)
    admin.init_app(app)
    account.init_app(app)

    register_routes(app)
    register_error_handlers(app)

    return app


def register_routes(app):
    """Register the routes that live directly on the application."""

    @app.context_processor
    def inject_project_identity():
        """Make the project identity and coverage available to templates."""
        return {
            "app_name": app.config["APP_NAME"],
            "tagline": app.config["TAGLINE"],
            "theme": app.config["THEME"],
            "districts": app.config["DISTRICTS"],
            "fruit_categories": app.config["FRUIT_CATEGORIES"],
            "total_fruits": sum(len(f) for f in app.config["FRUIT_CATEGORIES"].values()),
            "current_year": datetime.now().year,
        }

    @app.route("/")
    def index():
        """Public FruitVista homepage."""
        return render_template("index.html", page="home")

    @app.get("/health")
    def health():
        """Small JSON endpoint used to verify the app and database are alive."""
        tables = [
            row["name"]
            for row in get_db()
            .execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            )
            .fetchall()
        ]

        return jsonify(
            application=app.config["APP_NAME"],
            tagline=app.config["TAGLINE"],
            status="ok",
            database=os.path.basename(app.config["DATABASE"]),
            database_exists=os.path.exists(app.config["DATABASE"]),
            tables=tables,
        )


def register_error_handlers(app):
    """Show understandable messages instead of raw error pages."""

    @app.errorhandler(403)
    def access_denied(error):
        return (
            render_template(
                "error.html",
                code=403,
                message="You do not have permission to open this page.",
                hint=(
                    "This area is limited to FruitVista administrators. "
                    "If you believe you should have access, please contact an administrator."
                ),
            ),
            403,
        )

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        return (
            render_template(
                "error.html",
                code=400,
                message="Your session expired, so the form was not submitted.",
                hint=(
                    "For your security, forms can only be sent from FruitVista. "
                    "Please go back, reload the page and try again."
                ),
            ),
            400,
        )

    @app.errorhandler(404)
    def page_not_found(error):
        return (
            render_template(
                "error.html",
                code=404,
                message="We could not find the page you were looking for.",
            ),
            404,
        )

    @app.errorhandler(500)
    def internal_error(error):
        return (
            render_template(
                "error.html",
                code=500,
                message="Something went wrong while processing your request.",
            ),
            500,
        )


app = create_app()


if __name__ == "__main__":
    # Bind to every interface so the app is reachable from outside the
    # container, and honour the PORT variable that hosting platforms (Render)
    # inject. Locally PORT is unset and the app keeps running on 5000.
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=app.config["DEBUG"],
    )
