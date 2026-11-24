"""Load step: save raw extracts as CSV files and load them into SQLite `raw_*` tables."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)


def save_raw_files(tables: dict[str, pd.DataFrame], raw_dir: Path) -> None:
    """Persist each extract as a CSV, the way files would land from source systems."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_csv(raw_dir / f"{name}.csv", index=False)
    log.info("Raw files written to %s", raw_dir)


def load_raw_tables(tables: dict[str, pd.DataFrame], db_path: Path) -> None:
    """Replace each `raw_<name>` table in the warehouse with the latest extract."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        for name, df in tables.items():
            df.to_sql(f"raw_{name}", conn, if_exists="replace", index=False)
            log.info("Loaded raw_%s (%d rows)", name, len(df))
        # Index the join keys so the SQL models run in seconds
        conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_loans_id ON raw_loans (loan_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_perf_loan ON raw_loan_performance (loan_id)")
