"""Central configuration for FruitVista.

FruitVista - Fresh Insights, Smarter Markets
Fruit Market Sales Analytics and Price Prediction.

Everything that describes the project (name, tagline, districts, fruit
catalogue, database location) is defined here once so that the whole
application - frontend, backend and future reports - stays consistent.
"""

import os
from datetime import timedelta

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE_DIR = os.path.join(BASE_DIR, "database")
DATA_DIR = os.path.join(BASE_DIR, "data")
# Versioned, saved machine-learning models. Kept outside the database so a
# trained model is a plain file that can be inspected, replaced or deleted
# without ever touching a stored record.
MODEL_DIR = os.path.join(BASE_DIR, "ml_models")

# ---------------------------------------------------------------------------
# Project identity (appears on the login page, dashboard, reports and docs)
# ---------------------------------------------------------------------------
APP_NAME = "FruitVista"
TAGLINE = "Fresh Insights, Smarter Markets"
THEME = "Fruit Market Sales Analytics and Price Prediction"

# ---------------------------------------------------------------------------
# Supported districts (Karnataka)
# ---------------------------------------------------------------------------
DISTRICTS = [
    "Bengaluru Urban",
    "Chikkaballapura",
    "Mysuru",
    "Kolar",
    "Vijayapura",
]

# ---------------------------------------------------------------------------
# Supported fruit categories - 30 fruits in total
# ---------------------------------------------------------------------------
FRUIT_CATEGORIES = {
    "Dry Fruits": [
        "Almond",
        "Cashew",
        "Raisins",
        "Dates",
        "Pistachios",
        "Walnuts",
        "Dried Figs",
        "Apricots",
        "Prunes",
        "Dried Cranberries",
    ],
    "Seasonal Fruits": [
        "Mango",
        "Watermelon",
        "Muskmelon",
        "Litchi",
        "Jackfruit",
        "Custard Apple",
        "Strawberry",
        "Orange",
        "Grapes",
        "Pomegranate",
    ],
    "General Fruits": [
        "Apple",
        "Banana",
        "Papaya",
        "Guava",
        "Pineapple",
        "Sweet Lime",
        "Lemon",
        "Coconut",
        "Sapota",
        "Pear",
    ],
}


class Config:
    """Settings shared by every environment."""

    # --- Project identity -------------------------------------------------
    APP_NAME = APP_NAME
    TAGLINE = TAGLINE
    THEME = THEME
    DISTRICTS = DISTRICTS
    FRUIT_CATEGORIES = FRUIT_CATEGORIES

    # --- Security ---------------------------------------------------------
    # The session secret is read from the environment. When it is not set, a
    # development value is used so the project runs out of the box - always set
    # FRUITVISTA_SECRET_KEY before deploying the application.
    SECRET_KEY = os.environ.get("FRUITVISTA_SECRET_KEY", "fruitvista-dev-secret-key")

    # Session cookie hardening: not readable by JavaScript, not sent on
    # cross-site requests. "Remember me" makes the cookie permanent.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False  # set to True behind HTTPS (production)
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)

    # CSRF protection (Flask-WTF) for every state-changing request.
    # The time limit is expressed in seconds (Flask-WTF expects an integer).
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 7200  # two hours

    # --- Database ---------------------------------------------------------
    # One common SQLite database holds historical AND manually entered records.
    DATABASE = os.path.join(DATABASE_DIR, "fruitvista.db")
    SCHEMA_FILE = os.path.join(DATABASE_DIR, "schema.sql")

    # --- Data -------------------------------------------------------------
    # Folder that will hold the synthetic historical CSV dataset.
    DATA_DIR = DATA_DIR

    # --- Machine learning -------------------------------------------------
    # Where the trained, versioned models are saved (one joblib file plus one
    # registry row per model version). A model is only trained when an
    # administrator asks for it, never on a page load.
    ML_MODEL_DIR = MODEL_DIR

    # The training window and the chronological split. The most recent
    # ML_TRAIN_DAYS of stored history are used, and the last ML_TEST_DAYS of
    # that window are held back as a later test period, so a model is never
    # evaluated on a day it was trained on.
    ML_TRAIN_DAYS = 1095      # three years of daily history
    ML_TEST_DAYS = 90         # the held-back, later test period
    ML_MIN_TRAIN_ROWS = 2000  # refuse to train on less than this much history
    # A deterministic sample of the training window is used, so a run finishes
    # in a reasonable time on a laptop. The held-back test period is always used
    # in full. Set to 0 to train on every row.
    ML_MAX_TRAIN_ROWS = 40000

    # How far ahead a forecast may reach, and the default horizon.
    ML_HORIZON_MIN = 1
    ML_HORIZON_MAX = 90
    ML_HORIZON_DEFAULT = 30

    # --- Flask ------------------------------------------------------------
    DEBUG = False
    TESTING = False
    JSON_SORT_KEYS = False


class DevelopmentConfig(Config):
    """Local development on Windows 11."""

    DEBUG = True


class ProductionConfig(Config):
    """Production-ready settings."""

    DEBUG = False
    # Only exchange the session cookie over HTTPS.
    SESSION_COOKIE_SECURE = True

    # The session secret must come from the environment. There is deliberately
    # no fallback value here: get_config() refuses to start when
    # FRUITVISTA_SECRET_KEY is missing, so the development secret can never
    # reach a live deployment.
    SECRET_KEY = os.environ.get("FRUITVISTA_SECRET_KEY")


class TestingConfig(Config):
    """Isolated database used by automated tests."""

    TESTING = True
    DATABASE = os.path.join(DATABASE_DIR, "fruitvista_test.db")
    ML_MODEL_DIR = os.path.join(BASE_DIR, "ml_models_test")


CONFIG_MAP = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def get_config(name=None):
    """Return the configuration class to use.

    The environment can be selected with the ``FRUITVISTA_ENV`` environment
    variable; when it is not set, development settings are used.
    """
    name = (name or os.environ.get("FRUITVISTA_ENV") or "development").lower()
    config_class = CONFIG_MAP.get(name, DevelopmentConfig)

    # Fail fast: never start production with the shared development secret.
    if config_class is ProductionConfig and not os.environ.get("FRUITVISTA_SECRET_KEY"):
        raise RuntimeError(
            "FRUITVISTA_SECRET_KEY is not set. Set it in the environment "
            "before starting FruitVista with FRUITVISTA_ENV=production."
        )

    return config_class
