-- =====================================================================
-- 01 STAGING
-- Light cleanup of the raw core-banking extracts: consistent types,
-- trimmed text and derived month columns. One table per source table.
-- =====================================================================

DROP TABLE IF EXISTS stg_clients;
CREATE TABLE stg_clients AS
SELECT
    TRIM(client_id)                 AS client_id,
    CAST(age AS INTEGER)            AS age,
    TRIM(employment_type)           AS employment_type,
    CAST(monthly_income_gtq AS REAL) AS monthly_income_gtq,
    CAST(credit_score AS INTEGER)   AS credit_score,
    TRIM(home_department)           AS home_department
FROM raw_clients;

DROP TABLE IF EXISTS stg_loans;
CREATE TABLE stg_loans AS
SELECT
    TRIM(loan_id)                        AS loan_id,
    TRIM(client_id)                      AS client_id,
    TRIM(product_id)                     AS product_id,
    TRIM(agency_id)                      AS agency_id,
    DATE(origination_date)               AS origination_date,
    DATE(origination_date, 'start of month') AS origination_month,
    UPPER(TRIM(currency))                AS currency,
    CAST(principal_amount AS REAL)       AS principal_amount,
    CAST(annual_rate AS REAL)            AS annual_rate,
    CAST(term_months AS INTEGER)         AS term_months,
    CAST(installment_amount AS REAL)     AS installment_amount,
    CAST(credit_score_at_origination AS INTEGER) AS credit_score_at_origination,
    CAST(dti_at_origination AS REAL)     AS dti_at_origination
FROM raw_loans;

DROP TABLE IF EXISTS stg_loan_performance;
CREATE TABLE stg_loan_performance AS
SELECT
    TRIM(loan_id)                     AS loan_id,
    DATE(snapshot_month)              AS snapshot_month,
    CAST(outstanding_balance AS REAL) AS outstanding_balance,
    CAST(days_past_due AS INTEGER)    AS days_past_due,
    CAST(installment_due AS REAL)     AS installment_due,
    CAST(interest_accrued AS REAL)    AS interest_accrued,
    CAST(payment_received AS REAL)    AS payment_received,
    CAST(charge_off_amount AS REAL)   AS charge_off_amount,
    TRIM(loan_status)                 AS loan_status
FROM raw_loan_performance;

DROP TABLE IF EXISTS stg_fx_rates;
CREATE TABLE stg_fx_rates AS
SELECT
    DATE(rate_date)                   AS rate_date,
    DATE(rate_date, 'start of month') AS rate_month,
    CAST(usd_gtq AS REAL)             AS usd_gtq,
    source
FROM raw_fx_rates
WHERE usd_gtq IS NOT NULL;

CREATE INDEX idx_stg_loans_id ON stg_loans (loan_id);
CREATE INDEX idx_stg_perf_loan ON stg_loan_performance (loan_id);
