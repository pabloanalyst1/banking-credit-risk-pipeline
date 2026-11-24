-- =====================================================================
-- 02 DIMENSIONS  (star schema: the "who / what / where / when")
-- =====================================================================

-- Calendar at month grain. month_key (YYYYMM) is the join key used by facts.
DROP TABLE IF EXISTS dim_date;
CREATE TABLE dim_date AS
SELECT DISTINCT
    CAST(STRFTIME('%Y%m', snapshot_month) AS INTEGER)          AS month_key,
    snapshot_month                                             AS month_start,
    CAST(STRFTIME('%Y', snapshot_month) AS INTEGER)            AS year,
    (CAST(STRFTIME('%m', snapshot_month) AS INTEGER) + 2) / 3  AS quarter,
    STRFTIME('%Y', snapshot_month) || '-Q'
        || ((CAST(STRFTIME('%m', snapshot_month) AS INTEGER) + 2) / 3) AS year_quarter,
    CAST(STRFTIME('%m', snapshot_month) AS INTEGER)            AS month_num,
    CASE STRFTIME('%m', snapshot_month)
        WHEN '01' THEN 'Jan' WHEN '02' THEN 'Feb' WHEN '03' THEN 'Mar' WHEN '04' THEN 'Apr'
        WHEN '05' THEN 'May' WHEN '06' THEN 'Jun' WHEN '07' THEN 'Jul' WHEN '08' THEN 'Aug'
        WHEN '09' THEN 'Sep' WHEN '10' THEN 'Oct' WHEN '11' THEN 'Nov' ELSE 'Dec'
    END                                                        AS month_name
FROM stg_loan_performance;

DROP TABLE IF EXISTS dim_product;
CREATE TABLE dim_product AS
SELECT product_id, product_name, product_category, CAST(lgd_assumption AS REAL) AS lgd_assumption
FROM raw_products;

DROP TABLE IF EXISTS dim_agency;
CREATE TABLE dim_agency AS
SELECT agency_id, agency_name, department, region
FROM raw_agencies;

DROP TABLE IF EXISTS dim_client;
CREATE TABLE dim_client AS
SELECT
    c.client_id,
    c.age,
    CASE WHEN c.age < 30 THEN '21-29' WHEN c.age < 40 THEN '30-39'
         WHEN c.age < 50 THEN '40-49' WHEN c.age < 60 THEN '50-59' ELSE '60+' END AS age_band,
    c.employment_type,
    c.monthly_income_gtq,
    CASE WHEN c.monthly_income_gtq < 5000 THEN '1. < Q5k'
         WHEN c.monthly_income_gtq < 10000 THEN '2. Q5k-10k'
         WHEN c.monthly_income_gtq < 20000 THEN '3. Q10k-20k'
         ELSE '4. Q20k+' END                                AS income_band,
    c.home_department,
    (SELECT a.region FROM dim_agency a WHERE a.department = c.home_department LIMIT 1) AS home_region
FROM stg_clients c;

-- Monthly FX: average rate and month-end (closing) rate.
-- Balances are converted at the closing rate, as on a bank balance sheet.
DROP TABLE IF EXISTS dim_fx_monthly;
CREATE TABLE dim_fx_monthly AS
WITH ranked AS (
    SELECT rate_month, rate_date, usd_gtq, source,
           ROW_NUMBER() OVER (PARTITION BY rate_month ORDER BY rate_date DESC) AS rn
    FROM stg_fx_rates
)
SELECT
    CAST(STRFTIME('%Y%m', rate_month) AS INTEGER) AS month_key,
    rate_month                                   AS month_start,
    ROUND(AVG(usd_gtq), 5)                       AS avg_rate,
    ROUND(MAX(CASE WHEN rn = 1 THEN usd_gtq END), 5) AS month_end_rate,
    MAX(source)                                  AS source
FROM ranked
GROUP BY rate_month;

-- One row per loan with its origination attributes (a "loan" dimension).
DROP TABLE IF EXISTS dim_loan;
CREATE TABLE dim_loan AS
SELECT
    l.loan_id,
    l.client_id,
    l.product_id,
    l.agency_id,
    l.origination_date,
    CAST(STRFTIME('%Y%m', l.origination_month) AS INTEGER) AS origination_month_key,
    STRFTIME('%Y', l.origination_month) || '-Q'
        || ((CAST(STRFTIME('%m', l.origination_month) AS INTEGER) + 2) / 3) AS vintage_quarter,
    CAST(STRFTIME('%Y', l.origination_month) AS INTEGER) AS vintage_year,
    l.currency,
    l.principal_amount,
    ROUND(l.principal_amount * CASE WHEN l.currency = 'USD' THEN fx.month_end_rate ELSE 1 END, 2) AS principal_gtq,
    l.annual_rate,
    l.term_months,
    l.installment_amount,
    l.credit_score_at_origination,
    CASE WHEN l.credit_score_at_origination < 560 THEN '1. <560'
         WHEN l.credit_score_at_origination < 620 THEN '2. 560-619'
         WHEN l.credit_score_at_origination < 680 THEN '3. 620-679'
         WHEN l.credit_score_at_origination < 740 THEN '4. 680-739'
         ELSE '5. 740+' END                                AS score_band,
    l.dti_at_origination,
    CASE WHEN l.dti_at_origination < 0.20 THEN '1. <20%'
         WHEN l.dti_at_origination < 0.35 THEN '2. 20-35%'
         WHEN l.dti_at_origination < 0.50 THEN '3. 35-50%'
         ELSE '4. 50%+' END                                AS dti_band,
    CASE WHEN l.origination_date >= '2025-01-01' THEN '2025 policy (min score 560)'
         ELSE 'Legacy policy (min score 500)' END          AS underwriting_policy
FROM stg_loans l
LEFT JOIN dim_fx_monthly fx
       ON fx.month_key = CAST(STRFTIME('%Y%m', l.origination_month) AS INTEGER);

CREATE INDEX idx_dim_fx_month ON dim_fx_monthly (month_key);
CREATE INDEX idx_dim_loan_id ON dim_loan (loan_id);
