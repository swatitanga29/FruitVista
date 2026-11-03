"""Historical CSV import for FruitVista.

The dataset placed in the project folder contains one synthetic row per
day, district and fruit. This module finds the file, checks its columns and
values, and copies the rows into the existing ``daily_records`` table with
``record_source = 'historical'``.

Design decisions worth knowing about:

* **No new columns or duplicate tables.** ``daily_records`` was designed for
  this data and already has every field the CSV provides, so the import only
  adds rows to it. ``import_batches`` records what each run did.
* **Historical and manual rows never mix.** Manual entries use
  ``record_source = 'manual'``; this module only writes ``'historical'``.
* **Duplicates cannot happen.** ``record_date``, ``district_id``, ``fruit_id``
  and ``record_source`` are unique together in the table and rows are inserted
  with ``INSERT OR IGNORE``, so re-importing the same file adds nothing.
* **Chunked and resumable.** The import is run in slices so the page can show
  real progress. Every slice is one transaction that is rolled back on error.
* **Bad rows are skipped, not guessed.** A row with an unreadable date, an
  unknown district or fruit, a category mismatch, a missing value or a
  negative number is left out and counted with the reason why.

The data is synthetic sample data generated for this project. It is labelled
as such in the database and on the import page; it is not a verified record of
real market observations.
"""

import hashlib
import os
import time

import pandas as pd

from config import BASE_DIR
from models import imports as imports_repo
from models.sales import list_districts, list_fruits
from models.imports import IMPORT_COLUMN_ORDER, RECORD_SOURCE

DATA_DIR = os.path.join(BASE_DIR, "data")

DATE_FORMAT = "%Y-%m-%d"

# Where the dataset may live, and how a candidate file is recognised. The
# folder is inspected at run time - the file name is never assumed.
SEARCH_DIRECTORIES = [BASE_DIR, DATA_DIR]
DATASET_NAME_HINTS = ("fruit_data", "historical", "fruitvista")

# Maps a CSV column to the ``daily_records`` column that stores it. Anything
# not listed here is read from the file but not stored: ``year``, ``month``,
# ``month_name``, ``day_of_week``, ``season`` and ``unit`` are derived or
# constant, and ``supplier_id`` has no place in a sales record.
COLUMN_MAP = {
    "record_date": "date",
    "opening_stock": "opening_stock_kg",
    "purchased_quantity": "purchased_quantity_kg",
    "purchase_price": "purchase_price_per_kg_inr",
    "purchase_cost": "purchase_cost_inr",
    "available_stock": "available_stock_kg",
    "estimated_demand": "estimated_demand_kg",
    "quantity_sold": "quantity_sold_kg",
    "selling_price": "sold_price_per_kg_inr",
    "sales_revenue": "sales_revenue_inr",
    "damaged_quantity": "damaged_quantity_kg",
    "wastage_cost": "wastage_cost_inr",
    "closing_stock": "closing_stock_kg",
    "reorder_level": "reorder_level_kg",
    "stock_status": "stock_status",
    "cost_of_goods_sold": "cost_of_goods_sold_inr",
    "operating_expenses": "operating_expenses_inr",
    "gross_profit": "gross_profit_inr",
    "net_profit": "net_profit_inr",
    "profit_margin": "profit_margin_pct",
    "availability_percentage": "availability_pct",
}

# The CSV writes the category in the singular; the fruit catalogue (and the
# database CHECK constraint) uses the plural form.
CATEGORY_MAP = {
    "Dry Fruit": "Dry Fruits",
    "Seasonal Fruit": "Seasonal Fruits",
    "General Fruit": "General Fruits",
}

# Columns that must hold a number. ``record_date`` and ``stock_status`` are
# stored as text, and the two id columns are looked up from the catalogue.
TEXT_COLUMNS = {"record_date", "stock_status"}

DEFAULT_CHUNK_SIZE = 5000
MAX_CHUNK_SIZE = 20000

# Columns written for every imported row, in the order the rows are built.
# ``district_id`` and ``fruit_id`` are looked up from the catalogue,
# ``record_source`` is a constant, and ``fruit_category`` is only used to
# validate the row. This list must line up with ``IMPORT_COLUMN_ORDER``.
IMPORT_COLUMNS = ["record_date", "district_id", "fruit_id"] + [
    column for column in COLUMN_MAP if column != "record_date"
]
assert len(IMPORT_COLUMNS) == len(IMPORT_COLUMN_ORDER) - 1, "import column list is out of step"


class DatasetError(Exception):
    """Raised when there is no usable dataset to import."""


# Cache of the inspected dataset and the validated rows, keyed by the file's
# size and modification time, so a page refresh does not re-read 265,000 rows
# and the import can continue from where it stopped.
_CACHE = {"signature": None, "summary": None, "plan": None}

# Import runs that are currently in progress, keyed by their batch id.
_RUNS = {}


# ---------------------------------------------------------------------------
# Finding and inspecting the dataset
# ---------------------------------------------------------------------------
def find_dataset_file():
    """Return the CSV dataset in the project folder, or ``None``.

    The folder is inspected rather than the file name assumed: every ``.csv``
    in the project root and in ``data/`` is listed, a name that looks like the
    agreed dataset is preferred, and a single candidate is accepted.
    """
    candidates = []

    for directory in SEARCH_DIRECTORIES:
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if not name.lower().endswith(".csv"):
                continue
            path = os.path.join(directory, name)
            if os.path.isfile(path):
                candidates.append(path)

    if not candidates:
        return None

    for path in candidates:
        file_name = os.path.basename(path).lower()
        if any(hint in file_name for hint in DATASET_NAME_HINTS):
            return path

    return candidates[0] if len(candidates) == 1 else None


def _file_signature(path):
    """Identify a file by size and modification time."""
    stat = os.stat(path)
    return (os.path.abspath(path), stat.st_size, int(stat.st_mtime))


def _file_hash(path, block_size=1024 * 1024):
    """Return a SHA-1 hash of the file contents."""
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(block_size), b""):
            digest.update(block)
    return digest.hexdigest()


def _human_size(byte_count):
    """Format a size in bytes for the page."""
    if byte_count >= 1024 * 1024:
        return f"{byte_count / (1024 * 1024):.1f} MB"
    if byte_count >= 1024:
        return f"{byte_count / 1024:.1f} KB"
    return f"{byte_count} bytes"


def _modified_label(path):
    stamp = time.localtime(os.stat(path).st_mtime)
    return time.strftime("%d %b %Y, %H:%M", stamp)


def load_dataset():
    """Read the CSV into a DataFrame, using the cache when nothing changed."""
    path = find_dataset_file()
    if path is None:
        raise DatasetError(
            "No CSV dataset was found in the project folder. Place the "
            "historical dataset (.csv) in the FruitVista folder and reload."
        )

    signature = _file_signature(path)
    if _CACHE["signature"] == signature and _CACHE["summary"] is not None:
        return _CACHE["dataframe"], _CACHE["summary"]

    try:
        frame = pd.read_csv(path)
    except Exception as error:  # unreadable or malformed file
        raise DatasetError(f"The dataset could not be read: {error}") from error

    missing = sorted(
        source for source in COLUMN_MAP.values() if source not in frame.columns
    )
    if missing:
        raise DatasetError(
            "The dataset is missing these required columns: " + ", ".join(missing)
        )
    if "district" not in frame.columns or "fruit_name" not in frame.columns:
        raise DatasetError(
            "The dataset must contain the 'district' and 'fruit_name' columns."
        )

    unmapped = [
        column
        for column in frame.columns
        if column not in COLUMN_MAP.values()
        and column not in {"district", "fruit_name", "fruit_category"}
    ]

    dates = pd.to_datetime(frame[COLUMN_MAP["record_date"]], format=DATE_FORMAT, errors="coerce")

    summary = {
        "file_name": os.path.basename(path),
        "file_path": path,
        "file_hash": _file_hash(path),
        "file_size": _human_size(os.path.getsize(path)),
        "modified_at": _modified_label(path),
        "row_count": int(len(frame)),
        "column_count": int(len(frame.columns)),
        "columns": list(frame.columns),
        "mapped_columns": [
            {"source": source, "target": target} for target, source in COLUMN_MAP.items()
        ],
        "stored_column_count": len(IMPORT_COLUMN_ORDER),
        "unmapped_columns": unmapped,
        "table": "daily_records",
        "record_source": RECORD_SOURCE,
        "dataset_label": imports_repo.DATASET_LABEL,
        "date_from": dates.min().strftime(DATE_FORMAT) if dates.notna().any() else "-",
        "date_to": dates.max().strftime(DATE_FORMAT) if dates.notna().any() else "-",
        "days": int(dates.dt.normalize().nunique()),
        "districts": sorted(frame["district"].dropna().unique().tolist()),
        "fruits": sorted(frame["fruit_name"].dropna().unique().tolist()),
        "categories": sorted(frame["fruit_category"].dropna().unique().tolist())
        if "fruit_category" in frame.columns
        else [],
    }

    _CACHE.update({"signature": signature, "dataframe": frame, "summary": summary, "plan": None})
    return frame, summary


def dataset_summary():
    """Return the dataset description, or ``None`` when there is no dataset."""
    try:
        _, summary = load_dataset()
    except DatasetError:
        return None
    return summary


# ---------------------------------------------------------------------------
# Validating the dataset
# ---------------------------------------------------------------------------
def _catalogue():
    """Return the district and fruit lookups used to validate every row."""
    districts = {
        row["name"]: row["id"] for row in list_districts()
    }
    fruits = {
        row["name"]: (row["id"], row["category"]) for row in list_fruits()
    }
    return districts, fruits


def _reason_list(mask, reason, frame, limit=3):
    """Summarise one validation failure: how many rows, and a few examples."""
    indexes = frame.index[mask]
    count = int(len(indexes))
    examples = []

    for position in list(indexes)[:limit]:
        row = frame.loc[position]
        # +2 turns the zero-based row into a spreadsheet line number (line 1
        # is the header).
        examples.append(
            f"line {int(position) + 2}: {row.get(COLUMN_MAP['record_date'])} / "
            f"{row.get('district')} / {row.get('fruit_name')}"
        )

    return {"reason": reason, "count": count, "examples": examples}


def build_plan_from_frame(frame, districts, fruits):
    """Validate a dataset and return the rows that can be imported.

    Kept separate from :func:`build_plan` so the validation rules can be
    checked against a small hand-made table as well as the real file.
    Returns ``(valid_frame, skip_report)``.
    """
    today = pd.Timestamp.today().normalize()

    parsed_dates = pd.to_datetime(frame[COLUMN_MAP["record_date"]], format=DATE_FORMAT, errors="coerce")
    date_ok = parsed_dates.notna() & (parsed_dates <= today)

    district_ids = frame["district"].map(districts)
    district_ok = district_ids.notna()

    fruit_ids = frame["fruit_name"].map(lambda name: (fruits.get(name) or (None, None))[0])
    fruit_categories = frame["fruit_name"].map(lambda name: (fruits.get(name) or (None, None))[1])
    fruit_ok = fruit_ids.notna()

    if "fruit_category" in frame.columns:
        csv_categories = frame["fruit_category"].map(
            lambda value: CATEGORY_MAP.get(value, value)
        )
        category_ok = fruit_ok & (csv_categories == fruit_categories)
    else:
        category_ok = fruit_ok

    numeric = {
        column: pd.to_numeric(frame[source], errors="coerce")
        for column, source in COLUMN_MAP.items()
        if column not in TEXT_COLUMNS
    }

    numeric_ok = pd.Series(True, index=frame.index)
    non_negative_ok = pd.Series(True, index=frame.index)
    for series in numeric.values():
        numeric_ok &= series.notna()
        # Values are filled before the comparison so a missing number is
        # reported as "missing" and not as "negative".
        non_negative_ok &= series.fillna(0) >= 0

    checks = [
        (date_ok, "Unreadable date, or a date after today"),
        (district_ok, "District is not one of the five supported districts"),
        (fruit_ok, "Fruit is not one of the 30 approved fruits"),
        (category_ok, "Fruit category does not match the fruit catalogue"),
        (numeric_ok, "Missing or non-numeric value in a numeric column"),
        (non_negative_ok, "Negative value in a numeric column"),
    ]

    valid = pd.Series(True, index=frame.index)
    for mask, _ in checks:
        valid &= mask

    # Report the first problem that applies to each bad row: the list is read
    # backwards so the highest-priority check is written last.
    reasons = pd.Series("Unspecified data problem", index=frame.index, dtype=object)
    for mask, reason in reversed(checks):
        reasons = reasons.mask(~mask, reason)

    # Each skipped row is counted once, under its first (highest-priority)
    # problem, so the reason counts add up to the number of skipped rows.
    skip_report = []
    for _, reason in checks:
        first_failure = reasons == reason
        if first_failure.any():
            skip_report.append(_reason_list(first_failure, reason, frame))
    skip_report.sort(key=lambda item: item["count"], reverse=True)

    valid_frame = pd.DataFrame(
        {
            "record_date": parsed_dates[valid].dt.strftime(DATE_FORMAT),
            "district_id": district_ids[valid].astype(int),
            "fruit_id": fruit_ids[valid].astype(int),
            "stock_status": _clean_status(frame.loc[valid, COLUMN_MAP["stock_status"]]),
            **{column: series[valid] for column, series in numeric.items()},
        }
    ).reset_index(drop=True)

    return valid_frame, skip_report


def _clean_status(values):
    """Return the stock status as plain text, or ``None`` when it is blank.

    SQLite binds Python ``str`` and ``None``; pandas missing markers are
    converted here so an empty cell becomes a real NULL.
    """
    return [
        text.strip() if isinstance(text, str) and text.strip() else None
        for text in values.tolist()
    ]


def build_plan():
    """Build (and cache) the validated import plan for the current dataset."""
    # ``load_dataset`` clears the cached plan whenever the file has changed,
    # so a plan that is still present describes the dataset we just loaded.
    load_dataset()

    if _CACHE.get("plan") is not None:
        return _CACHE["plan"]

    frame = _CACHE["dataframe"]
    summary = _CACHE["summary"]
    districts, fruits = _catalogue()
    valid_frame, skip_report = build_plan_from_frame(frame, districts, fruits)

    plan = {
        "summary": summary,
        "arrays": [valid_frame[column].to_numpy() for column in IMPORT_COLUMNS],
        "importable_rows": int(len(valid_frame)),
        "skipped_rows": int(summary["row_count"]) - int(len(valid_frame)),
        "skipped_reasons": skip_report,
        "known_districts": sorted(districts),
        "known_fruits": sorted(fruits),
        "districts_in_file": summary["districts"],
        "fruits_in_file": summary["fruits"],
    }

    _CACHE["plan"] = plan
    return plan


# ---------------------------------------------------------------------------
# Running the import
# ---------------------------------------------------------------------------
def start_import():
    """Open a new import run and return everything the page needs to begin."""
    plan = build_plan()
    summary = plan["summary"]

    batch_id = imports_repo.create_batch(summary["file_name"], summary["file_hash"])
    _RUNS[batch_id] = {
        "plan": plan,
        "cursor": 0,
        "imported": 0,
        "duplicates": 0,
        "batch_id": batch_id,
    }

    return {
        "ok": True,
        "run_id": batch_id,
        "file_name": summary["file_name"],
        "file_hash": summary["file_hash"],
        "dataset_label": summary["dataset_label"],
        "csv_rows": int(summary["row_count"]),
        "total_rows": plan["importable_rows"],
        "skipped_rows": plan["skipped_rows"],
        "skipped_reasons": plan["skipped_reasons"],
        "table": summary["table"],
        "record_source": summary["record_source"],
        "stored_before": imports_repo.count_rows(RECORD_SOURCE),
    }


def run_import_chunk(run_id, chunk_size=DEFAULT_CHUNK_SIZE):
    """Import the next slice of rows. Called repeatedly until ``done``."""
    state = _RUNS.get(int(run_id))
    if state is None:
        return {
            "ok": False,
            "error": "This import is no longer running. Start it again to continue.",
        }

    chunk_size = max(1, min(int(chunk_size), MAX_CHUNK_SIZE))
    plan = state["plan"]
    total = plan["importable_rows"]
    start = state["cursor"]
    end = min(start + chunk_size, total)

    if end > start:
        arrays = plan["arrays"]
        # ``arrays`` follows IMPORT_COLUMNS; the single value missing per row
        # is the constant ``record_source``.
        values = [array[start:end].tolist() for array in arrays]

        try:
            inserted = imports_repo.insert_historical_rows(
                [
                    (row[0], row[1], row[2], RECORD_SOURCE) + tuple(row[3:])
                    for row in zip(*values)
                ]
            )
        except Exception as error:  # the transaction was rolled back
            imports_repo.fail_batch(
                state["batch_id"],
                plan["summary"]["row_count"],
                state["imported"],
                state["duplicates"],
                plan["skipped_rows"],
                f"Import stopped at row {start}: {error}",
            )
            _RUNS.pop(int(run_id), None)
            return {
                "ok": False,
                "error": f"The import stopped and was rolled back: {error}",
                "imported": state["imported"],
                "processed": start,
                "total": total,
            }

        state["cursor"] = end
        state["imported"] += inserted
        state["duplicates"] += (end - start) - inserted

    processed = state["cursor"]
    done = processed >= total

    result = {
        "ok": True,
        "run_id": int(run_id),
        "total": total,
        "csv_rows": int(plan["summary"]["row_count"]),
        "processed": processed,
        "imported": state["imported"],
        "duplicate_rows": state["duplicates"],
        "skipped_rows": plan["skipped_rows"],
        "percent": round(processed / total * 100, 1) if total else 100.0,
        "done": done,
    }

    if done:
        imports_repo.finish_batch(
            state["batch_id"],
            plan["summary"]["row_count"],
            state["imported"],
            state["duplicates"],
            plan["skipped_rows"],
            (
                f"{state['imported']:,} historical rows imported into "
                f"daily_records; {state['duplicates']:,} already present; "
                f"{plan['skipped_rows']:,} rows skipped."
            ),
        )
        result["summary"] = import_summary(state["batch_id"], plan, state)
        _RUNS.pop(int(run_id), None)

    return result


def import_summary(batch_id, plan, state):
    """Describe a finished import run for the page and the report."""
    summary = plan["summary"]
    return {
        "batch_id": batch_id,
        "file_name": summary["file_name"],
        "file_hash": summary["file_hash"],
        "dataset_label": summary["dataset_label"],
        "csv_rows": int(summary["row_count"]),
        "imported_rows": state["imported"],
        "duplicate_rows": state["duplicates"],
        "skipped_rows": plan["skipped_rows"],
        "skipped_reasons": plan["skipped_reasons"],
        "table": summary["table"],
        "record_source": summary["record_source"],
        "historical_rows_in_db": imports_repo.count_rows(RECORD_SOURCE),
        "manual_rows_in_db": imports_repo.count_rows("manual"),
        "coverage": [dict(row) for row in imports_repo.coverage_by_district()],
    }


def recent_batches(limit=10):
    """Return the most recent import runs for the page."""
    return imports_repo.list_batches(limit)
