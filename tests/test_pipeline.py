"""Tests: unit tests for the building blocks plus an end-to-end run on a small portfolio."""
import sqlite3
from datetime import date

import numpy as np
import pandas as pd
import pytest

from run_pipeline import run
from src.config import PipelineConfig
from src.extract_fx import build_envelope, fallback_series, parse_rango_response
from src.generate_data import generate_portfolio, monthly_installment

SAMPLE_BANGUAT_RESPONSE = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <TipoCambioRangoResponse xmlns="http://www.banguat.gob.gt/variables/ws/">
      <TipoCambioRangoResult>
        <Vars>
          <Var><moneda>2</moneda><fecha>02/01/2026</fecha><venta>7.66</venta><compra>7.66</compra></Var>
          <Var><moneda>2</moneda><fecha>03/01/2026</fecha><venta>7.67</venta><compra>7.67</compra></Var>
        </Vars>
        <TotalItems>2</TotalItems>
      </TipoCambioRangoResult>
    </TipoCambioRangoResponse>
  </soap:Body>
</soap:Envelope>"""


def test_installment_matches_known_value():
    # Q100,000 at 12% annual for 12 months -> Q8,884.88 per month
    assert monthly_installment(np.array([100_000.0]), np.array([0.12]), np.array([12]))[0] == pytest.approx(8884.88, abs=0.01)


def test_banguat_envelope_uses_ddmmyyyy_dates():
    xml = build_envelope(date(2026, 1, 2), date(2026, 3, 31))
    assert "<fechainit>02/01/2026</fechainit>" in xml
    assert "<fechafin>31/03/2026</fechafin>" in xml


def test_parse_banguat_response():
    df = parse_rango_response(SAMPLE_BANGUAT_RESPONSE)
    assert list(df["rate_date"]) == [date(2026, 1, 2), date(2026, 1, 3)]
    assert list(df["usd_gtq"]) == [7.66, 7.67]


def test_fallback_series_is_plausible():
    df = fallback_series(date(2022, 1, 1), date(2026, 10, 31))
    assert df["usd_gtq"].between(7.4, 8.1).all()
    assert df["rate_date"].is_unique


def test_generator_is_reproducible():
    a = generate_portfolio(200, seed=1)["loan_performance"]
    b = generate_portfolio(200, seed=1)["loan_performance"]
    pd.testing.assert_frame_equal(a, b)


def test_policy_change_enforces_min_score():
    loans = generate_portfolio(1500, seed=3)["loans"]
    after = loans[pd.to_datetime(loans["origination_date"]) >= "2025-01-01"]
    assert (after["credit_score_at_origination"] >= 560).all()


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("run")
    cfg = PipelineConfig(n_loans=800, seed=11, offline=True,
                         db_path=tmp / "wh.db", raw_dir=tmp / "raw", export_dir=tmp / "exports")
    run(cfg)  # raises if any data quality check fails
    return cfg


def test_end_to_end_creates_all_marts(warehouse):
    expected = {"mart_portfolio_monthly", "mart_vintage_curves", "mart_roll_rates", "mart_pd_by_segment",
                "mart_loan_snapshot", "mart_fx_exposure", "mart_kpi_summary"}
    exported = {p.stem for p in warehouse.export_dir.glob("*.csv")}
    assert expected <= exported


def test_portfolio_balance_reconciles(warehouse):
    """The mart total must equal the sum of active balances in the fact table."""
    with sqlite3.connect(warehouse.db_path) as conn:
        mart = conn.execute("SELECT SUM(portfolio_balance_gtq) FROM mart_portfolio_monthly").fetchone()[0]
        fact = conn.execute("SELECT SUM(balance_gtq) FROM fact_loan_monthly WHERE is_active = 1").fetchone()[0]
    assert mart == pytest.approx(fact, rel=1e-6)


def test_roll_rates_sum_to_one(warehouse):
    rr = pd.read_csv(warehouse.export_dir / "mart_roll_rates.csv")
    totals = rr.groupby("from_bucket")["pct_of_from_bucket"].sum()
    assert np.allclose(totals, 1.0, atol=0.01)
