"""
scripts/ingest.py

Loads the Retail Transaction Dataset CSV into a local SQLite database.

Usage:
    python scripts/ingest.py [--csv PATH] [--db PATH] [--reset]

Environment variables (override defaults):
    DATABASE_PATH   path to the SQLite file (default: data/retail.db)
    CSV_PATH        path to the source CSV file

Options:
    --csv PATH    Path to the CSV file (default: $CSV_PATH or data/Retail_Transaction_Dataset.csv)
    --db  PATH    Path to the SQLite database (default: $DATABASE_PATH or data/retail.db)
    --reset       Drop and recreate the transactions table before loading
"""

import argparse
import csv
import os
import sqlite3
import sys
from pathlib import Path


# ── Column name normalisation map ──────────────────────────────────────────────
COLUMN_MAP = {
    "CustomerID":        "customer_id",
    "ProductID":         "product_id",
    "Quantity":          "quantity",
    "Price":             "price",
    "TransactionDate":   "transaction_date",
    "PaymentMethod":     "payment_method",
    "StoreLocation":     "store_location",
    "ProductCategory":   "product_category",
    "DiscountApplied(%)":"discount_pct",
    "TotalAmount":       "total_amount",
}

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS transactions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id      TEXT    NOT NULL,
    product_id       TEXT    NOT NULL,
    quantity         INTEGER,
    price            REAL,
    transaction_date TEXT,
    payment_method   TEXT,
    store_location   TEXT,
    product_category TEXT,
    discount_pct     REAL,
    total_amount     REAL
);
"""

CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_customer_id ON transactions (customer_id);",
    "CREATE INDEX IF NOT EXISTS idx_product_id  ON transactions (product_id);",
    "CREATE INDEX IF NOT EXISTS idx_transaction_date ON transactions (transaction_date);",
    "CREATE INDEX IF NOT EXISTS idx_product_category ON transactions (product_category);",
]

INSERT_SQL = """
INSERT INTO transactions
    (customer_id, product_id, quantity, price, transaction_date,
     payment_method, store_location, product_category, discount_pct, total_amount)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""


def _safe_int(value: str) -> int | None:
    try:
        return int(float(value.strip()))
    except (ValueError, AttributeError):
        return None


def _safe_float(value: str) -> float | None:
    try:
        return float(value.strip())
    except (ValueError, AttributeError):
        return None


def _safe_str(value: str) -> str | None:
    v = value.strip() if value else None
    return v if v else None


def ingest(csv_path: str, db_path: str, reset: bool = False) -> None:
    csv_file = Path(csv_path)
    if not csv_file.exists():
        print(f"[ERROR] CSV file not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] Opening database: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")

    if reset:
        print("[INFO] --reset flag set: dropping existing transactions table")
        conn.execute("DROP TABLE IF EXISTS transactions;")
        conn.commit()

    conn.execute(CREATE_TABLE_SQL)
    for idx_sql in CREATE_INDEXES_SQL:
        conn.execute(idx_sql)
    conn.commit()

    # ── Check if table already has data ────────────────────────────────────────
    existing = conn.execute("SELECT COUNT(*) FROM transactions;").fetchone()[0]
    if existing > 0 and not reset:
        print(
            f"[INFO] Table already contains {existing:,} rows. "
            "Use --reset to reload. Exiting."
        )
        conn.close()
        return

    print(f"[INFO] Loading CSV: {csv_path}")

    inserted = 0
    skipped = 0
    batch: list[tuple] = []
    BATCH_SIZE = 5_000

    with open(csv_file, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)

        # Validate expected headers
        if reader.fieldnames is None:
            print("[ERROR] CSV has no header row.", file=sys.stderr)
            sys.exit(1)

        missing = set(COLUMN_MAP.keys()) - set(reader.fieldnames)
        if missing:
            print(f"[ERROR] Missing expected columns: {missing}", file=sys.stderr)
            sys.exit(1)

        for raw_row in reader:
            # Skip completely empty rows
            if not any(v.strip() for v in raw_row.values() if v):
                skipped += 1
                continue

            customer_id = _safe_str(raw_row["CustomerID"])
            product_id  = _safe_str(raw_row["ProductID"])

            if not customer_id or not product_id:
                skipped += 1
                continue

            row = (
                customer_id,
                product_id,
                _safe_int(raw_row["Quantity"]),
                _safe_float(raw_row["Price"]),
                _safe_str(raw_row["TransactionDate"]),
                _safe_str(raw_row["PaymentMethod"]),
                _safe_str(raw_row["StoreLocation"]),
                _safe_str(raw_row["ProductCategory"]),
                _safe_float(raw_row["DiscountApplied(%)"]),
                _safe_float(raw_row["TotalAmount"]),
            )
            batch.append(row)

            if len(batch) >= BATCH_SIZE:
                conn.executemany(INSERT_SQL, batch)
                conn.commit()
                inserted += len(batch)
                print(f"[INFO]   ... {inserted:,} rows inserted", end="\r")
                batch.clear()

    if batch:
        conn.executemany(INSERT_SQL, batch)
        conn.commit()
        inserted += len(batch)

    conn.close()

    print(f"\n[INFO] Done. Inserted: {inserted:,}  Skipped: {skipped:,}")
    print(f"[INFO] Database written to: {db_path}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest retail CSV into SQLite.")
    parser.add_argument(
        "--csv",
        default=os.getenv("CSV_PATH", "data/Retail_Transaction_Dataset.csv"),
        help="Path to the source CSV file",
    )
    parser.add_argument(
        "--db",
        default=os.getenv("DATABASE_PATH", "data/retail.db"),
        help="Path to the target SQLite database",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop and recreate the transactions table before loading",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    ingest(csv_path=args.csv, db_path=args.db, reset=args.reset)
