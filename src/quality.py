"""Data quality checks. Each check is a SQL query that returns the number of bad rows.

If any check fails the pipeline stops before exporting, so the dashboard never shows bad data.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

CHECKS = {
    "Every loan has a known client, product and agency": """
        SELECT COUNT(*) FROM dim_loan l
        LEFT JOIN dim_client c ON c.client_id = l.client_id
        LEFT JOIN dim_product p ON p.product_id = l.product_id
        LEFT JOIN dim_agency a ON a.agency_id = l.agency_id
        WHERE c.client_id IS NULL OR p.product_id IS NULL OR a.agency_id IS NULL""",
    "Loan IDs are unique": """
        SELECT COUNT(*) - COUNT(DISTINCT loan_id) FROM dim_loan""",
    "One fact row per loan per month": """
        SELECT COUNT(*) FROM (SELECT loan_id, month_key FROM fact_loan_monthly
                              GROUP BY loan_id, month_key HAVING COUNT(*) > 1)""",
    "Fact table has the same rows as the raw extract": """
        SELECT ABS((SELECT COUNT(*) FROM fact_loan_monthly) - (SELECT COUNT(*) FROM raw_loan_performance))""",
    "Balances are never negative": """
        SELECT COUNT(*) FROM fact_loan_monthly WHERE balance_gtq < 0""",
    "Every month has an exchange rate": """
        SELECT COUNT(*) FROM fact_loan_monthly WHERE fx_rate IS NULL""",
    "Exchange rates are within a plausible range (6.5-9.0)": """
        SELECT COUNT(*) FROM dim_fx_monthly WHERE month_end_rate NOT BETWEEN 6.5 AND 9.0""",
    "Days past due match the delinquency status": """
        SELECT COUNT(*) FROM fact_loan_monthly
        WHERE (loan_status = 'Current' AND days_past_due <> 0)
           OR (loan_status = 'Delinquent' AND days_past_due = 0)""",
    "Closed loans carry no balance": """
        SELECT COUNT(*) FROM fact_loan_monthly
        WHERE loan_status IN ('Paid Off', 'Charged Off') AND balance_gtq > 0""",
    "No activity after a loan is closed": """
        SELECT COUNT(*) FROM fact_loan_monthly f
        JOIN (SELECT loan_id, MIN(month_key) AS closed_key FROM fact_loan_monthly
              WHERE loan_status IN ('Paid Off', 'Charged Off') GROUP BY loan_id) c
          ON c.loan_id = f.loan_id AND f.month_key > c.closed_key""",
    "Ratios are between 0 and 1": """
        SELECT COUNT(*) FROM mart_portfolio_monthly
        WHERE npl_ratio NOT BETWEEN 0 AND 1 OR delinquency_30_plus_ratio NOT BETWEEN 0 AND 1""",
    "PD is available for every active loan": """
        SELECT COUNT(*) FROM mart_loan_snapshot WHERE pd_12m IS NULL""",
}


class DataQualityError(Exception):
    pass


def run_quality_checks(db_path: Path) -> dict[str, int]:
    """Run all checks, log results and raise DataQualityError if any fail."""
    results = {}
    with sqlite3.connect(db_path) as conn:
        for name, sql in CHECKS.items():
            bad_rows = conn.execute(sql).fetchone()[0] or 0
            results[name] = bad_rows
            log.info("[%s] %s%s", "PASS" if bad_rows == 0 else "FAIL", name,
                     "" if bad_rows == 0 else f" ({bad_rows} rows)")
    failed = {k: v for k, v in results.items() if v}
    if failed:
        raise DataQualityError(f"{len(failed)} data quality check(s) failed: {failed}")
    return results
