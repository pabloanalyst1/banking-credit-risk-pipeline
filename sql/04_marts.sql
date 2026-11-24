-- =====================================================================
-- 04 MARTS  (business-ready tables consumed by the dashboard)
-- Ratios are included for convenience, but numerators and denominators
-- are always exported too, so the BI tool can re-aggregate correctly
-- (e.g. NPL ratio = SUM(npl_balance) / SUM(portfolio_balance)).
-- =====================================================================

-- ---------------------------------------------------------------------
-- Portfolio health by month x product x region
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS mart_portfolio_monthly;
CREATE TABLE mart_portfolio_monthly AS
SELECT
    d.month_key,
    d.month_start,
    d.year_quarter,
    p.product_name,
    p.product_category,
    a.region,
    SUM(f.is_active)                                                    AS active_loans,
    ROUND(SUM(CASE WHEN f.is_active = 1 THEN f.balance_gtq ELSE 0 END), 2) AS portfolio_balance_gtq,
    ROUND(SUM(CASE WHEN f.is_30_plus = 1 THEN f.balance_gtq ELSE 0 END), 2) AS balance_30_plus_gtq,
    ROUND(SUM(CASE WHEN f.is_npl = 1 THEN f.balance_gtq ELSE 0 END), 2)    AS npl_balance_gtq,
    SUM(CASE WHEN f.months_on_book = 0 THEN 1 ELSE 0 END)               AS new_loans,
    ROUND(SUM(CASE WHEN f.months_on_book = 0 THEN f.balance_gtq ELSE 0 END), 2) AS disbursed_gtq,
    ROUND(SUM(f.interest_gtq), 2)                                        AS interest_accrued_gtq,
    ROUND(SUM(f.payment_gtq), 2)                                         AS collections_gtq,
    ROUND(SUM(f.charge_off_gtq), 2)                                      AS charge_offs_gtq,
    ROUND(SUM(CASE WHEN f.is_npl = 1 THEN f.balance_gtq ELSE 0 END)
          / NULLIF(SUM(CASE WHEN f.is_active = 1 THEN f.balance_gtq ELSE 0 END), 0), 4) AS npl_ratio,
    ROUND(SUM(CASE WHEN f.is_30_plus = 1 THEN f.balance_gtq ELSE 0 END)
          / NULLIF(SUM(CASE WHEN f.is_active = 1 THEN f.balance_gtq ELSE 0 END), 0), 4) AS delinquency_30_plus_ratio
FROM fact_loan_monthly f
JOIN dim_date d    ON d.month_key = f.month_key
JOIN dim_product p ON p.product_id = f.product_id
JOIN dim_agency a  ON a.agency_id = f.agency_id
GROUP BY d.month_key, d.month_start, d.year_quarter, p.product_name, p.product_category, a.region;


-- ---------------------------------------------------------------------
-- Vintage analysis: % of loans from each origination quarter that have
-- ever reached 90+ days past due (or charge-off), by months on book.
-- Comparing curves shows whether newer loans are better or worse.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS mart_vintage_curves;
CREATE TABLE mart_vintage_curves AS
WITH last_month AS (
    SELECT MAX(snapshot_month) AS ms FROM fact_loan_monthly
),
loan_outcome AS (
    SELECT
        l.loan_id,
        l.vintage_quarter,
        l.vintage_year,
        l.underwriting_policy,
        (CAST(STRFTIME('%Y', lm.ms) AS INTEGER) - CAST(STRFTIME('%Y', l.origination_date) AS INTEGER)) * 12
          + (CAST(STRFTIME('%m', lm.ms) AS INTEGER) - CAST(STRFTIME('%m', l.origination_date) AS INTEGER)) AS max_mob,
        MIN(CASE WHEN f.is_npl = 1 OR f.loan_status = 'Charged Off' THEN f.months_on_book END) AS first_mob_90_plus
    FROM dim_loan l
    JOIN fact_loan_monthly f ON f.loan_id = l.loan_id
    CROSS JOIN last_month lm
    GROUP BY l.loan_id
),
mob(m) AS (
    SELECT 0 UNION ALL SELECT m + 1 FROM mob WHERE m < 36
)
SELECT
    o.vintage_quarter,
    o.vintage_year,
    o.underwriting_policy,
    mob.m                                                           AS months_on_book,
    COUNT(*)                                                        AS loans_observed,
    SUM(CASE WHEN o.first_mob_90_plus <= mob.m THEN 1 ELSE 0 END)   AS loans_ever_90_plus,
    ROUND(1.0 * SUM(CASE WHEN o.first_mob_90_plus <= mob.m THEN 1 ELSE 0 END) / COUNT(*), 4) AS cum_ever_90_plus_rate
FROM loan_outcome o
JOIN mob ON mob.m <= o.max_mob          -- only months each vintage has actually lived through
GROUP BY o.vintage_quarter, o.vintage_year, o.underwriting_policy, mob.m
HAVING COUNT(*) >= 30;                  -- hide statistically thin points


-- ---------------------------------------------------------------------
-- Roll rates (last 12 months): where loans in each delinquency bucket
-- end up one month later. Core input for collections strategy.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS mart_roll_rates;
CREATE TABLE mart_roll_rates AS
WITH transitions AS (
    SELECT
        month_key,
        dpd_bucket                                                         AS from_bucket,
        LEAD(dpd_bucket) OVER (PARTITION BY loan_id ORDER BY month_key)    AS to_bucket,
        balance_gtq
    FROM fact_loan_monthly
),
window_start AS (
    SELECT month_key FROM dim_date ORDER BY month_key DESC LIMIT 1 OFFSET 12
)
SELECT
    from_bucket,
    to_bucket,
    COUNT(*)                     AS transitions,
    ROUND(SUM(balance_gtq), 2)   AS balance_gtq,
    ROUND(1.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY from_bucket), 4) AS pct_of_from_bucket
FROM transitions
WHERE to_bucket IS NOT NULL
  AND from_bucket NOT IN ('Paid Off', 'Charged Off')
  AND month_key >= (SELECT month_key FROM window_start)
GROUP BY from_bucket, to_bucket;


-- ---------------------------------------------------------------------
-- 12-month probability of default (PD) by product x score band.
-- Pooled method: every month a performing loan is observed counts as one
-- observation; it "defaults" if it reaches 90+ DPD or is charged off in
-- the following 12 months. Small segments are blended toward the
-- product-level PD (credibility weighting, k = 30 observations).
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS mart_pd_by_segment;
CREATE TABLE mart_pd_by_segment AS
WITH idx AS (
    SELECT f.*, (f.month_key / 100) * 12 + (f.month_key % 100) AS month_index
    FROM fact_loan_monthly f
),
first_default AS (
    SELECT loan_id, MIN(month_index) AS default_index
    FROM idx
    WHERE is_npl = 1 OR loan_status = 'Charged Off'
    GROUP BY loan_id
),
last_obs AS (
    SELECT MAX(month_index) - 12 AS max_obs_index FROM idx
),
observations AS (
    SELECT
        i.product_id,
        l.score_band,
        CASE WHEN fd.default_index > i.month_index AND fd.default_index <= i.month_index + 12
             THEN 1 ELSE 0 END AS defaulted_12m
    FROM idx i
    JOIN dim_loan l ON l.loan_id = i.loan_id
    LEFT JOIN first_default fd ON fd.loan_id = i.loan_id
    WHERE i.is_active = 1 AND i.is_npl = 0
      AND i.month_index <= (SELECT max_obs_index FROM last_obs)
),
segment AS (
    SELECT product_id, score_band, COUNT(*) AS observations, SUM(defaulted_12m) AS defaults
    FROM observations GROUP BY product_id, score_band
),
product_level AS (
    SELECT product_id, 1.0 * SUM(defaults) / SUM(observations) AS product_pd
    FROM segment GROUP BY product_id
)
SELECT
    s.product_id,
    p.product_name,
    s.score_band,
    s.observations,
    s.defaults,
    ROUND(1.0 * s.defaults / s.observations, 4)                       AS raw_pd_12m,
    ROUND((s.defaults + 30 * pl.product_pd) / (s.observations + 30), 4) AS pd_12m,
    p.lgd_assumption                                                  AS lgd
FROM segment s
JOIN product_level pl ON pl.product_id = s.product_id
JOIN dim_product p    ON p.product_id = s.product_id;


-- ---------------------------------------------------------------------
-- Loan-level snapshot for the latest month, with expected loss:
--   EL = EAD (balance) x PD x LGD      (PD = 100% for loans already NPL)
-- This is a simplified version of the IFRS 9 / Basel approach.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS mart_loan_snapshot;
CREATE TABLE mart_loan_snapshot AS
SELECT
    f.loan_id,
    f.snapshot_month,
    p.product_name,
    p.product_category,
    a.agency_name,
    a.department,
    a.region,
    c.employment_type,
    c.age_band,
    c.income_band,
    l.vintage_quarter,
    l.underwriting_policy,
    l.score_band,
    l.dti_band,
    l.currency,
    l.annual_rate,
    l.term_months,
    f.months_on_book,
    f.loan_status,
    f.dpd_bucket,
    f.days_past_due,
    f.is_npl,
    f.balance_gtq                                                AS ead_gtq,
    CASE WHEN f.is_npl = 1 THEN 1.0 ELSE COALESCE(pd.pd_12m, ppd.product_pd, ppd.overall_pd) END           AS pd_12m,
    p.lgd_assumption                                             AS lgd,
    ROUND(f.balance_gtq * (CASE WHEN f.is_npl = 1 THEN 1.0 ELSE COALESCE(pd.pd_12m, ppd.product_pd, ppd.overall_pd) END) * p.lgd_assumption, 2)
                                                                 AS expected_loss_gtq
FROM fact_loan_monthly f
JOIN dim_loan l     ON l.loan_id = f.loan_id
JOIN dim_product p  ON p.product_id = f.product_id
JOIN dim_agency a   ON a.agency_id = f.agency_id
JOIN dim_client c   ON c.client_id = f.client_id
LEFT JOIN mart_pd_by_segment pd ON pd.product_id = l.product_id AND pd.score_band = l.score_band
-- Fallback for segments with no history: product-level PD, then portfolio-level PD
LEFT JOIN (
    SELECT dp.product_id,
           (SELECT 1.0 * SUM(defaults) / SUM(observations) FROM mart_pd_by_segment s WHERE s.product_id = dp.product_id) AS product_pd,
           (SELECT 1.0 * SUM(defaults) / SUM(observations) FROM mart_pd_by_segment) AS overall_pd
    FROM dim_product dp
) ppd ON ppd.product_id = l.product_id
WHERE f.month_key = (SELECT MAX(month_key) FROM fact_loan_monthly)
  AND f.is_active = 1;


-- ---------------------------------------------------------------------
-- FX exposure: USD-denominated balances and the GTQ effect of exchange
-- rate moves (revaluation = last month's USD balance x change in rate).
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS mart_fx_exposure;
CREATE TABLE mart_fx_exposure AS
WITH monthly AS (
    SELECT
        f.month_key,
        MAX(f.snapshot_month)                                                         AS month_start,
        MAX(f.fx_rate)                                                                AS month_end_rate,
        ROUND(SUM(CASE WHEN f.is_active = 1 AND f.currency = 'USD' THEN f.outstanding_balance ELSE 0 END), 2) AS usd_balance,
        ROUND(SUM(CASE WHEN f.is_active = 1 AND f.currency = 'USD' THEN f.balance_gtq ELSE 0 END), 2)         AS usd_balance_in_gtq,
        ROUND(SUM(CASE WHEN f.is_active = 1 THEN f.balance_gtq ELSE 0 END), 2)                                AS total_balance_gtq
    FROM fact_loan_monthly f
    GROUP BY f.month_key
)
SELECT
    m.*,
    fx.avg_rate,
    fx.source                                                            AS fx_source,
    ROUND(m.usd_balance_in_gtq / NULLIF(m.total_balance_gtq, 0), 4)      AS usd_share,
    ROUND(LAG(m.usd_balance) OVER (ORDER BY m.month_key)
          * (m.month_end_rate - LAG(m.month_end_rate) OVER (ORDER BY m.month_key)), 2) AS fx_revaluation_gtq
FROM monthly m
LEFT JOIN dim_fx_monthly fx ON fx.month_key = m.month_key;


-- ---------------------------------------------------------------------
-- Headline KPIs: latest month vs previous month vs same month last year
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS mart_kpi_summary;
CREATE TABLE mart_kpi_summary AS
WITH monthly AS (
    SELECT
        month_key,
        SUM(active_loans)                                         AS active_loans,
        SUM(portfolio_balance_gtq)                                AS portfolio_balance_gtq,
        SUM(npl_balance_gtq) / NULLIF(SUM(portfolio_balance_gtq), 0)     AS npl_ratio,
        SUM(balance_30_plus_gtq) / NULLIF(SUM(portfolio_balance_gtq), 0) AS delinquency_30_plus_ratio,
        SUM(disbursed_gtq)                                        AS disbursed_gtq,
        SUM(charge_offs_gtq)                                      AS charge_offs_gtq
    FROM mart_portfolio_monthly
    GROUP BY month_key
),
ranked AS (
    SELECT *,
           SUM(charge_offs_gtq) OVER (ORDER BY month_key ROWS BETWEEN 11 PRECEDING AND CURRENT ROW) AS charge_offs_12m_gtq,
           ROW_NUMBER() OVER (ORDER BY month_key DESC) - 1 AS months_ago
    FROM monthly
),
picked AS (
    SELECT
        MAX(CASE WHEN months_ago = 0  THEN month_key END) AS current_month,
        MAX(CASE WHEN months_ago = 0  THEN active_loans END) AS al_0,  MAX(CASE WHEN months_ago = 1 THEN active_loans END) AS al_1,  MAX(CASE WHEN months_ago = 12 THEN active_loans END) AS al_12,
        MAX(CASE WHEN months_ago = 0  THEN portfolio_balance_gtq END) AS pb_0, MAX(CASE WHEN months_ago = 1 THEN portfolio_balance_gtq END) AS pb_1, MAX(CASE WHEN months_ago = 12 THEN portfolio_balance_gtq END) AS pb_12,
        MAX(CASE WHEN months_ago = 0  THEN npl_ratio END) AS npl_0,    MAX(CASE WHEN months_ago = 1 THEN npl_ratio END) AS npl_1,    MAX(CASE WHEN months_ago = 12 THEN npl_ratio END) AS npl_12,
        MAX(CASE WHEN months_ago = 0  THEN delinquency_30_plus_ratio END) AS d30_0, MAX(CASE WHEN months_ago = 1 THEN delinquency_30_plus_ratio END) AS d30_1, MAX(CASE WHEN months_ago = 12 THEN delinquency_30_plus_ratio END) AS d30_12,
        MAX(CASE WHEN months_ago = 0  THEN disbursed_gtq END) AS disb_0, MAX(CASE WHEN months_ago = 1 THEN disbursed_gtq END) AS disb_1, MAX(CASE WHEN months_ago = 12 THEN disbursed_gtq END) AS disb_12,
        MAX(CASE WHEN months_ago = 0  THEN charge_offs_12m_gtq END) AS co_0, MAX(CASE WHEN months_ago = 1 THEN charge_offs_12m_gtq END) AS co_1, MAX(CASE WHEN months_ago = 12 THEN charge_offs_12m_gtq END) AS co_12
    FROM ranked
)
SELECT current_month, 1 AS sort_order, 'Active loans' AS kpi, al_0 AS current_value, al_1 AS previous_month, al_12 AS same_month_last_year FROM picked
UNION ALL SELECT current_month, 2, 'Portfolio balance (GTQ)',          ROUND(pb_0, 2),  ROUND(pb_1, 2),  ROUND(pb_12, 2)  FROM picked
UNION ALL SELECT current_month, 3, 'NPL ratio (90+ DPD)',              ROUND(npl_0, 4), ROUND(npl_1, 4), ROUND(npl_12, 4) FROM picked
UNION ALL SELECT current_month, 4, '30+ delinquency ratio',            ROUND(d30_0, 4), ROUND(d30_1, 4), ROUND(d30_12, 4) FROM picked
UNION ALL SELECT current_month, 5, 'Disbursements in month (GTQ)',     ROUND(disb_0, 2), ROUND(disb_1, 2), ROUND(disb_12, 2) FROM picked
UNION ALL SELECT current_month, 6, 'Charge-offs, trailing 12m (GTQ)',  ROUND(co_0, 2),  ROUND(co_1, 2),  ROUND(co_12, 2)  FROM picked
UNION ALL SELECT (SELECT current_month FROM picked), 7, 'Expected loss (GTQ)',
                 ROUND((SELECT SUM(expected_loss_gtq) FROM mart_loan_snapshot), 2), NULL, NULL
UNION ALL SELECT (SELECT current_month FROM picked), 8, 'Expected loss / portfolio',
                 ROUND((SELECT SUM(expected_loss_gtq) / SUM(ead_gtq) FROM mart_loan_snapshot), 4), NULL, NULL;
