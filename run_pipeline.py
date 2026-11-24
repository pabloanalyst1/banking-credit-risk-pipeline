"""Run the full credit-risk pipeline end to end.

    python run_pipeline.py                 # default: 6,000 loans, live Banguat FX rates
    python run_pipeline.py --offline       # no internet: simulated FX rates
    python run_pipeline.py --loans 20000   # bigger portfolio
"""
import argparse
import logging
import time
from datetime import date

import pandas as pd

from src.config import END_MONTH, START_MONTH, PipelineConfig
from src.export import export_marts
from src.extract_fx import get_fx_rates
from src.generate_data import generate_portfolio
from src.load import load_raw_tables, save_raw_files
from src.quality import run_quality_checks
from src.transform import run_sql_models

log = logging.getLogger("pipeline")


def run(cfg: PipelineConfig) -> None:
    t0 = time.perf_counter()

    log.info("1/5 EXTRACT  - core-banking extract (%d loans) and Banguat FX rates", cfg.n_loans)
    tables = generate_portfolio(cfg.n_loans, cfg.seed)
    last_day = (pd.Timestamp(END_MONTH) + pd.offsets.MonthEnd(0)).date()
    tables["fx_rates"] = get_fx_rates(date.fromisoformat(START_MONTH), last_day, offline=cfg.offline)

    log.info("2/5 LOAD     - raw files and raw_* tables")
    save_raw_files(tables, cfg.raw_dir)
    load_raw_tables(tables, cfg.db_path)

    log.info("3/5 TRANSFORM - SQL models")
    run_sql_models(cfg.db_path, cfg.sql_dir)

    log.info("4/5 QUALITY  - data quality checks")
    run_quality_checks(cfg.db_path)

    log.info("5/5 EXPORT   - marts to CSV")
    export_marts(cfg.db_path, cfg.export_dir)

    log.info("Done in %.1f s. Warehouse: %s", time.perf_counter() - t0, cfg.db_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--loans", type=int, default=6000, help="number of loans to simulate")
    parser.add_argument("--seed", type=int, default=42, help="random seed (same seed = same data)")
    parser.add_argument("--offline", action="store_true", help="skip the Banguat API and use simulated FX rates")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    run(PipelineConfig(n_loans=args.loans, seed=args.seed, offline=args.offline))


if __name__ == "__main__":
    main()
