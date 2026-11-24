"""Export step: write every mart table to CSV so Tableau (or any BI tool) can read it."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)


def export_marts(db_path: Path, export_dir: Path) -> list[Path]:
    export_dir.mkdir(parents=True, exist_ok=True)
    written = []
    with sqlite3.connect(db_path) as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'mart_%' ORDER BY name")]
        for table in tables:
            path = export_dir / f"{table}.csv"
            pd.read_sql(f"SELECT * FROM {table}", conn).to_csv(path, index=False)
            written.append(path)
            log.info("Exported %s", path.name)
    return written
