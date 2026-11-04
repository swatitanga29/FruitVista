-- ---------------------------------------------------------------------------
-- FruitVista - foundation database schema
-- ---------------------------------------------------------------------------
-- This script is safe to run repeatedly:
--   * every statement uses "IF NOT EXISTS"
--   * nothing is dropped, truncated or overwritten
--   * existing historical and manual records are always preserved
--
-- Phase 1 creates only the foundation tables. Feature-specific tables are
-- added later through new, additive migrations.
-- ---------------------------------------------------------------------------

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Migration log - keeps track of which schema changes have been applied
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO schema_migrations (version, description)
VALUES (1, 'Foundation schema: districts, fruits and daily sales/inventory records');

-- ---------------------------------------------------------------------------
-- District master table (the five supported Karnataka districts)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS districts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    state      TEXT NOT NULL DEFAULT 'Karnataka',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- Fruit master table (30 fruits across three categories)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fruits (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    category   TEXT NOT NULL CHECK (
                   category IN ('Dry Fruits', 'Seasonal Fruits', 'General Fruits')
               ),
    unit       TEXT NOT NULL DEFAULT 'kg',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- Daily sales and inventory records
--
-- One row = one fruit, in one district, on one day.
--   record_source = 'historical'  -> imported from the synthetic CSV dataset
--   record_source = 'manual'      -> entered by an administrator or vendor
--
-- The UNIQUE constraint prevents duplicate imports while still allowing a
-- manual entry to exist alongside a historical record for the same day.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS daily_records (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    record_date             TEXT    NOT NULL,               -- ISO date: YYYY-MM-DD
    district_id             INTEGER NOT NULL,
    fruit_id                INTEGER NOT NULL,
    record_source           TEXT    NOT NULL DEFAULT 'manual'
                                CHECK (record_source IN ('historical', 'manual')),

    -- Inventory
    opening_stock           REAL NOT NULL DEFAULT 0,
    purchased_quantity      REAL NOT NULL DEFAULT 0,
    purchase_price          REAL NOT NULL DEFAULT 0,
    purchase_cost           REAL NOT NULL DEFAULT 0,
    available_stock         REAL NOT NULL DEFAULT 0,
    estimated_demand        REAL NOT NULL DEFAULT 0,
    quantity_sold           REAL NOT NULL DEFAULT 0,
    selling_price           REAL NOT NULL DEFAULT 0,
    sales_revenue           REAL NOT NULL DEFAULT 0,
    damaged_quantity        REAL NOT NULL DEFAULT 0,
    wastage_cost            REAL NOT NULL DEFAULT 0,
    closing_stock           REAL NOT NULL DEFAULT 0,
    reorder_level           REAL NOT NULL DEFAULT 0,
    stock_status            TEXT,

    -- Financials
    cost_of_goods_sold      REAL NOT NULL DEFAULT 0,
    operating_expenses      REAL NOT NULL DEFAULT 0,
    gross_profit            REAL NOT NULL DEFAULT 0,
    net_profit              REAL NOT NULL DEFAULT 0,
    profit_margin           REAL NOT NULL DEFAULT 0,
    availability_percentage REAL NOT NULL DEFAULT 0,

    created_at              TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at              TEXT NOT NULL DEFAULT (datetime('now')),

    FOREIGN KEY (district_id) REFERENCES districts (id) ON DELETE RESTRICT,
    FOREIGN KEY (fruit_id)    REFERENCES fruits (id)    ON DELETE RESTRICT,
    UNIQUE (record_date, district_id, fruit_id, record_source)
);

CREATE INDEX IF NOT EXISTS idx_daily_records_date     ON daily_records (record_date);
CREATE INDEX IF NOT EXISTS idx_daily_records_district ON daily_records (district_id);
CREATE INDEX IF NOT EXISTS idx_daily_records_fruit    ON daily_records (fruit_id);
CREATE INDEX IF NOT EXISTS idx_daily_records_source   ON daily_records (record_source);

-- ---------------------------------------------------------------------------
-- Migration 2 - registered FruitVista users
-- ---------------------------------------------------------------------------
-- Additive only. The Phase 1 tables above and every stored record stay exactly
-- as they are; running this script again changes nothing.
--
--   password_hash  -> Werkzeug password hash, never a plain-text password
--   email          -> case-insensitive, enforced unique by the index below
--   role           -> 'user' (default), 'secondary_admin' or 'permanent_admin';
--                     only ever set by the server. 'admin' is the legacy value
--                     for the original administrator and is still accepted so
--                     an older database keeps working (see migration 9).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name     TEXT    NOT NULL,
    email         TEXT    COLLATE NOCASE NOT NULL,
    mobile        TEXT    NOT NULL,
    district      TEXT    NOT NULL,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'user'
                      CHECK (role IN ('user', 'admin', 'permanent_admin', 'secondary_admin')),
    last_login_at TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique ON users (email COLLATE NOCASE);

INSERT OR IGNORE INTO schema_migrations (version, description)
VALUES (2, 'Registered users table for FruitVista authentication');

-- ---------------------------------------------------------------------------
-- Migration 3 - role-based access control
-- ---------------------------------------------------------------------------
-- Existing databases created before this migration receive the column through
-- ``database.apply_migrations()`` (SQLite cannot add a column with
-- "CREATE TABLE IF NOT EXISTS"). Fresh databases already have it above.
-- ---------------------------------------------------------------------------
INSERT OR IGNORE INTO schema_migrations (version, description)
VALUES (3, 'Role column for regular user and administrator accounts');

-- ---------------------------------------------------------------------------
-- Migration 5 - historical CSV import tracking
-- ---------------------------------------------------------------------------
-- The imported market data itself is stored in ``daily_records``, the same
-- table the Sales Entry module writes to, separated by ``record_source``.
-- This table only records what happened during an import run: which file was
-- read, how many rows were imported, skipped or already present, and whether
-- the run finished. It is what makes a second import of the same file safe and
-- reportable, and it never changes or replaces an existing record.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS import_batches (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name      TEXT    NOT NULL,
    file_hash      TEXT    NOT NULL,
    dataset_label  TEXT    NOT NULL DEFAULT 'Synthetic historical dataset',
    total_rows     INTEGER NOT NULL DEFAULT 0,
    imported_rows  INTEGER NOT NULL DEFAULT 0,
    duplicate_rows INTEGER NOT NULL DEFAULT 0,
    skipped_rows   INTEGER NOT NULL DEFAULT 0,
    status         TEXT    NOT NULL DEFAULT 'running'
                       CHECK (status IN ('running', 'completed', 'failed')),
    notes          TEXT,
    started_at     TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    finished_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_import_batches_started ON import_batches (started_at DESC);

INSERT OR IGNORE INTO schema_migrations (version, description)
VALUES (5, 'Historical CSV import tracking table');

-- ---------------------------------------------------------------------------
-- Migration 6 - stock purchases and stock adjustments
-- ---------------------------------------------------------------------------
-- One row = one manual stock movement: either a purchase of fruit that arrived
-- in a district, or a stock adjustment (fruit found or written off).
--
-- Stock movements are kept in their own table and are never written into
-- ``daily_records``. That table holds the synthetic historical dataset and the
-- manual sales entries the Sales Entry module owns; adding purchase rows there
-- would mix three different things together and could be counted twice.
--
-- Instead, the Inventory module reads the two sources side by side and adds
-- the manual movements on top of the historical stock, so:
--   * an imported historical row is never changed or overwritten,
--   * a manual sales record is never changed or overwritten,
--   * a purchase or adjustment can never be counted twice, because it exists
--     in exactly one place.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stock_movements (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    movement_date TEXT    NOT NULL,               -- ISO date: YYYY-MM-DD
    district_id   INTEGER NOT NULL,
    fruit_id      INTEGER NOT NULL,
    movement_type TEXT    NOT NULL
                      CHECK (movement_type IN ('purchase', 'adjustment')),

    -- A purchase adds stock and always carries a price per kilogram, so both
    -- values must be greater than zero. An adjustment may add stock (fruit
    -- found during a count) or remove it (fruit written off), so it is simply
    -- non-zero, and it must say why it was made.
    quantity      REAL    NOT NULL,
    unit_price    REAL    NOT NULL DEFAULT 0,
    amount        REAL    NOT NULL DEFAULT 0,
    reason        TEXT,

    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),

    CHECK (
        (movement_type = 'purchase'   AND quantity > 0 AND unit_price > 0)
        OR
        (movement_type = 'adjustment' AND quantity <> 0)
    ),
    CHECK (
        movement_type <> 'adjustment'
        OR (reason IS NOT NULL AND TRIM(reason) <> '')
    ),

    FOREIGN KEY (district_id) REFERENCES districts (id) ON DELETE RESTRICT,
    FOREIGN KEY (fruit_id)    REFERENCES fruits (id)    ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_stock_movements_date     ON stock_movements (movement_date);
CREATE INDEX IF NOT EXISTS idx_stock_movements_district ON stock_movements (district_id);
CREATE INDEX IF NOT EXISTS idx_stock_movements_fruit    ON stock_movements (fruit_id);
CREATE INDEX IF NOT EXISTS idx_stock_movements_type     ON stock_movements (movement_type);

INSERT OR IGNORE INTO schema_migrations (version, description)
VALUES (6, 'Manual stock purchases and stock adjustments for inventory management');

-- ---------------------------------------------------------------------------
-- Migration 7 - manually recorded wastage
-- ---------------------------------------------------------------------------
-- One row = one wastage entry an administrator recorded: a damaged quantity of
-- one fruit, in one district, on one day, with the cost per kilogram it was
-- valued at and the reason it was damaged.
--
-- The imported synthetic dataset already carries its own damaged quantity and
-- wastage cost, so manual wastage is kept in its own table and is never written
-- into ``daily_records``. That table holds the historical dataset (the CSV
-- headers ``damaged_quantity_kg`` and ``wastage_cost_inr`` are imported into
-- ``damaged_quantity`` and ``wastage_cost``) and it holds the manual sales
-- entries the Sales Entry module owns. Adding wastage rows there would mix
-- three different things together and could be counted twice.
--
-- The UNIQUE constraint stops the same day, district and fruit being entered
-- twice, so wastage for one position is corrected by editing the existing row
-- instead of adding a second one.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wastage_records (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    wastage_date     TEXT    NOT NULL,               -- ISO date: YYYY-MM-DD
    district_id      INTEGER NOT NULL,
    fruit_id         INTEGER NOT NULL,

    -- The damaged quantity and the cost per kilogram are both entered by the
    -- administrator and both must be greater than zero. The wastage cost is
    -- calculated on the server as damaged quantity x cost per kilogram.
    damaged_quantity REAL    NOT NULL,
    cost_per_kg      REAL    NOT NULL,
    wastage_cost     REAL    NOT NULL DEFAULT 0,
    reason           TEXT    NOT NULL,

    created_at       TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),

    CHECK (damaged_quantity > 0),
    CHECK (cost_per_kg > 0),
    CHECK (wastage_cost >= 0),

    FOREIGN KEY (district_id) REFERENCES districts (id) ON DELETE RESTRICT,
    FOREIGN KEY (fruit_id)    REFERENCES fruits (id)    ON DELETE RESTRICT,
    UNIQUE (wastage_date, district_id, fruit_id)
);

CREATE INDEX IF NOT EXISTS idx_wastage_records_date     ON wastage_records (wastage_date);
CREATE INDEX IF NOT EXISTS idx_wastage_records_district ON wastage_records (district_id);
CREATE INDEX IF NOT EXISTS idx_wastage_records_fruit    ON wastage_records (fruit_id);

INSERT OR IGNORE INTO schema_migrations (version, description)
VALUES (7, 'Manual wastage entries for the wastage management module');

-- ---------------------------------------------------------------------------
-- Migration 8 - machine learning model registry and prediction history
-- ---------------------------------------------------------------------------
-- Two new tables, both additive. No existing table is touched, so the
-- historical dataset, the manual sales, the stock movements and the wastage
-- records are preserved exactly as they are.
--
-- ``ml_models`` is the registry of the models an administrator trained. The
-- trained estimator itself is written to a file by the training pipeline and
-- the path to that file is stored here together with the evaluation results,
-- the version, the chronological train and test periods and the date the model
-- was trained. The web page reads the active row instead of training again, so
-- a page load never retrains a model.
--
-- ``ml_predictions`` is the prediction history: one row per forecast an
-- administrator chose to keep. Forecasts are never written back into
-- ``daily_records`` and never presented as recorded market data.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ml_models (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    -- What the model predicts: the selling price per kg, the quantity sold in
    -- kg, or the estimated demand in kg.
    model_key       TEXT    NOT NULL
                        CHECK (model_key IN ('price', 'quantity', 'demand')),
    version         INTEGER NOT NULL,
    algorithm       TEXT    NOT NULL,
    algorithm_label TEXT    NOT NULL,

    -- The saved estimator (joblib) and the exact feature order it was trained
    -- on, so a stored model can always be matched to its inputs.
    artifact_path   TEXT    NOT NULL,
    feature_count   INTEGER NOT NULL DEFAULT 0,

    -- The chronological split the model was evaluated on. The test period
    -- always comes after the training period, so no future day is used to
    -- train the model that predicts it.
    train_rows      INTEGER NOT NULL DEFAULT 0,
    test_rows       INTEGER NOT NULL DEFAULT 0,
    train_start     TEXT,
    train_end       TEXT,
    test_start      TEXT,
    test_end        TEXT,

    -- The observed range of the target, kept so a prediction can be held
    -- inside the range the data actually supports.
    target_min      REAL,
    target_max      REAL,

    -- Evaluation on the held-out, later test period.
    mae             REAL,
    rmse            REAL,
    r2              REAL,

    -- Exactly one active model per model_key (enforced by the partial index
    -- below), so there is never any doubt which model a page is using.
    is_active       INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),

    -- The comparison of every candidate algorithm on the same test period, as
    -- JSON text, so the dashboard can show why the winner was chosen.
    candidates      TEXT,

    dataset_label   TEXT    NOT NULL DEFAULT 'Synthetic historical dataset',
    trained_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    notes           TEXT,

    UNIQUE (model_key, version)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ml_models_active
    ON ml_models (model_key) WHERE is_active = 1;
CREATE INDEX IF NOT EXISTS idx_ml_models_key ON ml_models (model_key, version DESC);

CREATE TABLE IF NOT EXISTS ml_predictions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id          INTEGER,
    prediction_type   TEXT    NOT NULL
                          CHECK (prediction_type IN ('price', 'quantity', 'demand')),
    district_id       INTEGER,
    fruit_id          INTEGER,

    -- How far ahead the forecast reaches and the window it covers.
    horizon_days      INTEGER NOT NULL DEFAULT 0,
    start_date        TEXT,
    end_date          TEXT,

    predicted         REAL,
    predicted_revenue REAL,
    interval_low      REAL,
    interval_high     REAL,

    created_at        TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    notes             TEXT,

    FOREIGN KEY (model_id)   REFERENCES ml_models (id) ON DELETE SET NULL,
    FOREIGN KEY (district_id) REFERENCES districts (id) ON DELETE SET NULL,
    FOREIGN KEY (fruit_id)    REFERENCES fruits (id)    ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_ml_predictions_created
    ON ml_predictions (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ml_predictions_type
    ON ml_predictions (prediction_type);

INSERT OR IGNORE INTO schema_migrations (version, description)
VALUES (8, 'Machine learning model registry and prediction history');

-- ---------------------------------------------------------------------------
-- Migration 9 - secondary administrator approval and management
-- ---------------------------------------------------------------------------
-- One row = one request by a normal user to become a Secondary Administrator,
-- together with the decision the Permanent Administrator made on it. No other
-- table is touched, so every existing account and record is preserved exactly
-- as it is.
--
--   status       -> 'pending'  the request is waiting for a decision
--                   'approved' the user was promoted to secondary_admin
--                   'rejected' the request was declined, the user stays 'user'
--                   'revoked'  an approved secondary administrator was demoted
--   reviewed_at / reviewed_by -> when and by whom the request was approved or
--                                rejected (the acting permanent administrator)
--   revoked_at / revoked_by   -> when and by whom access was later revoked
--   review_note               -> optional note recorded with a decision
--
-- The user's own role lives in ``users.role``; this table is the request and
-- its audit trail, so approving, rejecting and revoking are all reportable.
--
-- The partial unique index below enforces, at the database level, that one
-- account can have at most one *pending* request at a time. Combined with the
-- conditional "only while pending" update in the service layer, two
-- administrators clicking Approve at the same moment can never both succeed.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin_requests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    reason      TEXT    NOT NULL,

    status      TEXT    NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'rejected', 'revoked')),

    created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    reviewed_at TEXT,
    reviewed_by INTEGER,
    review_note TEXT,
    revoked_at  TEXT,
    revoked_by  INTEGER,

    FOREIGN KEY (user_id)     REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY (reviewed_by) REFERENCES users (id) ON DELETE SET NULL,
    FOREIGN KEY (revoked_by)  REFERENCES users (id) ON DELETE SET NULL
);

-- At most one pending request per account, so a duplicate can never be stored
-- even if two submits arrive together.
CREATE UNIQUE INDEX IF NOT EXISTS idx_admin_requests_pending
    ON admin_requests (user_id) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_admin_requests_user   ON admin_requests (user_id);
CREATE INDEX IF NOT EXISTS idx_admin_requests_status ON admin_requests (status);

INSERT OR IGNORE INTO schema_migrations (version, description)
VALUES (9, 'Secondary administrator access requests and their approval audit trail');
