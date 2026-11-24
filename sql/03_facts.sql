-- =====================================================================
-- 03 FACTS
-- fact_loan_monthly: one row per loan per month (grain = loan x month).
-- All money is also expressed in GTQ using the month-end Banguat rate.
-- =====================================================================

DROP TABLE IF EXISTS fact_loan_monthly;
CREATE TABLE fact_loan_monthly AS
SELECT
    p.loan_id,
    CAST(STRFTIME('%Y%m', p.snapshot_month) AS INTEGER) AS month_key,
    p.snapshot_month,
    l.client_id,
    l.product_id,
    l.agency_id,
    l.currency,
    -- months on book: months elapsed since origination
    (CAST(STRFTIME('%Y', p.snapshot_month) AS INTEGER) - CAST(STRFTIME('%Y', l.origination_date) AS INTEGER)) * 12
      + (CAST(STRFTIME('%m', p.snapshot_month) AS INTEGER) - CAST(STRFTIME('%m', l.origination_date) AS INTEGER))
                                                         AS months_on_book,
    p.loan_status,
    p.days_past_due,
    CASE WHEN p.loan_status IN ('Paid Off', 'Charged Off') THEN p.loan_status
         WHEN p.days_past_due = 0  THEN '0. Current'
         WHEN p.days_past_due <= 30 THEN '1. 1-30'
         WHEN p.days_past_due <= 60 THEN '2. 31-60'
         WHEN p.days_past_due <= 90 THEN '3. 61-90'
         ELSE '4. 90+' END                               AS dpd_bucket,
    CASE WHEN p.loan_status IN ('Current', 'Delinquent') THEN 1 ELSE 0 END AS is_active,
    CASE WHEN p.loan_status = 'Delinquent' AND p.days_past_due > 30 THEN 1 ELSE 0 END AS is_30_plus,
    -- Non-performing loan (NPL): more than 90 days past due
    CASE WHEN p.loan_status = 'Delinquent' AND p.days_past_due > 90 THEN 1 ELSE 0 END AS is_npl,
    fx.month_end_rate                                    AS fx_rate,
    p.outstanding_balance,
    ROUND(p.outstanding_balance * CASE WHEN l.currency = 'USD' THEN fx.month_end_rate ELSE 1 END, 2) AS balance_gtq,
    ROUND(p.payment_received   * CASE WHEN l.currency = 'USD' THEN fx.month_end_rate ELSE 1 END, 2) AS payment_gtq,
    ROUND(p.interest_accrued   * CASE WHEN l.currency = 'USD' THEN fx.month_end_rate ELSE 1 END, 2) AS interest_gtq,
    ROUND(p.charge_off_amount  * CASE WHEN l.currency = 'USD' THEN fx.month_end_rate ELSE 1 END, 2) AS charge_off_gtq
FROM stg_loan_performance p
JOIN stg_loans l        ON l.loan_id = p.loan_id
LEFT JOIN dim_fx_monthly fx ON fx.month_key = CAST(STRFTIME('%Y%m', p.snapshot_month) AS INTEGER);

CREATE INDEX idx_fact_month ON fact_loan_monthly (month_key);
CREATE INDEX idx_fact_loan  ON fact_loan_monthly (loan_id, month_key);
