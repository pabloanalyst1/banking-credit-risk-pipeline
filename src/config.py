"""Central configuration for the pipeline."""
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Reporting window: first origination month through the last closed month
START_MONTH = "2022-01-01"
END_MONTH = "2026-10-01"

# Underwriting policy change: minimum credit score raised on this date
POLICY_CHANGE_DATE = "2025-01-01"
MIN_SCORE_BEFORE = 500
MIN_SCORE_AFTER = 560


@dataclass
class PipelineConfig:
    n_loans: int = 6000
    seed: int = 42
    offline: bool = False  # True = skip the Banguat API and use the fallback FX series
    db_path: Path = ROOT / "data" / "warehouse.db"
    raw_dir: Path = ROOT / "data" / "raw"
    export_dir: Path = ROOT / "exports"
    sql_dir: Path = ROOT / "sql"
