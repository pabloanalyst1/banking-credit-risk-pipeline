"""Generate a realistic, fully synthetic loan portfolio for a fictional Guatemalan bank.

Real loan-level banking data is confidential, so this module simulates what a core-banking
system would export: clients, agencies, products, loans and a monthly performance snapshot
for every loan (balance, days past due, payments, status).

The simulation is driven by risk factors a credit analyst would expect to see:
  * credit score and debt-to-income at origination
  * product type and region
  * loan seasoning (risk peaks around months 6-12 on book)
  * a macro stress period (Apr-Dec 2024) and January seasonality
  * an underwriting policy change in Jan 2025 (minimum score 500 -> 560)
Delinquency moves between 30-day buckets with a roll-rate (Markov) model; loans 180+ days
past due are charged off.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import END_MONTH, MIN_SCORE_AFTER, MIN_SCORE_BEFORE, POLICY_CHANGE_DATE, START_MONTH

APPROX_FX = 7.8  # only used to size USD loans and compute DTI; reporting uses Banguat rates

# id, name, category, mix share, GTQ amount range, annual rate range, terms, base monthly miss prob, USD share, LGD
PRODUCTS = [
    ("P01", "Personal Loan", "Consumer", 0.35, (5_000, 80_000), (0.16, 0.26), [12, 24, 36, 48, 60], 0.030, 0.00, 0.65),
    ("P02", "Microcredit", "Consumer", 0.20, (1_500, 15_000), (0.24, 0.36), [6, 12, 18, 24], 0.070, 0.00, 0.75),
    ("P03", "Auto Loan", "Consumer", 0.15, (60_000, 300_000), (0.10, 0.15), [36, 48, 60, 72], 0.015, 0.25, 0.35),
    ("P04", "Mortgage", "Mortgage", 0.05, (250_000, 900_000), (0.07, 0.10), [120, 180, 240], 0.009, 0.40, 0.20),
    ("P05", "SME Working Capital", "Commercial", 0.25, (50_000, 600_000), (0.11, 0.18), [12, 24, 36, 48], 0.022, 0.30, 0.50),
]

# department, region, regional risk multiplier
DEPARTMENTS = [
    ("Guatemala", "Metropolitana", 0.90), ("Sacatepéquez", "Central", 0.95), ("Escuintla", "Central", 1.05),
    ("Quetzaltenango", "Occidente", 1.00), ("San Marcos", "Occidente", 1.15), ("Huehuetenango", "Occidente", 1.20),
    ("Zacapa", "Oriente", 1.10), ("Chiquimula", "Oriente", 1.20), ("Jutiapa", "Oriente", 1.15),
    ("Alta Verapaz", "Norte", 1.30), ("Petén", "Norte", 1.25),
    ("Retalhuleu", "Suroccidente", 1.05), ("Suchitepéquez", "Suroccidente", 1.10),
]
# Weight of each department in the client base (the capital concentrates most banking activity)
DEPT_WEIGHTS = np.array([40, 5, 6, 8, 4, 4, 3, 3, 3, 4, 3, 3, 3], dtype=float)

# Roll-rate model for loans already delinquent. Index = missed payments (1..5)
P_ROLL_FORWARD = {1: 0.30, 2: 0.40, 3: 0.45, 4: 0.40, 5: 0.40}
P_CURE = {1: 0.45, 2: 0.25, 3: 0.10, 4: 0.05, 5: 0.03}
CHARGE_OFF_STATE = 6  # 6 missed payments ~ 180 days past due
P_PREPAY = 0.008      # monthly probability that a current loan is paid off early


def _months(start: str, end: str) -> pd.DatetimeIndex:
    return pd.date_range(start, end, freq="MS")


def macro_factor(month: pd.Timestamp) -> float:
    """Economy-wide risk multiplier: stress in Apr-Dec 2024, post-holiday bump each January."""
    factor = 1.0
    if pd.Timestamp("2024-04-01") <= month <= pd.Timestamp("2024-12-01"):
        factor *= 1.0 + 0.5 * min(1.0, (month.month - 3) / 4)  # ramps up to 1.5x
    elif month >= pd.Timestamp("2025-01-01"):
        factor *= 0.95
    if month.month == 1:
        factor *= 1.15
    return factor


def seasoning_factor(mob: np.ndarray) -> np.ndarray:
    """Risk by months on book: low at first, peaks in months 6-12, declines for seasoned loans."""
    return np.select([mob < 3, mob < 6, mob <= 12, mob <= 24], [0.5, 1.0, 1.35, 1.0], default=0.75)


def monthly_installment(principal: np.ndarray, annual_rate: np.ndarray, term: np.ndarray) -> np.ndarray:
    """French amortization: fixed payment P * r / (1 - (1 + r)^-n)."""
    r = annual_rate / 12
    return principal * r / (1 - (1 + r) ** (-term))


def generate_reference_tables() -> tuple[pd.DataFrame, pd.DataFrame]:
    products = pd.DataFrame(
        [(p[0], p[1], p[2], p[9]) for p in PRODUCTS],
        columns=["product_id", "product_name", "product_category", "lgd_assumption"],
    )
    agencies = []
    n = 1
    for dept, region, _ in DEPARTMENTS:
        branches = ["Zona 1", "Zona 10", "Zona 12", "Mixco", "Villa Nueva"] if dept == "Guatemala" else ["Centro"]
        for branch in branches:
            agencies.append((f"AG{n:02d}", f"{dept} - {branch}", dept, region))
            n += 1
    agencies = pd.DataFrame(agencies, columns=["agency_id", "agency_name", "department", "region"])
    return products, agencies


def generate_clients(n_clients: int, rng: np.random.Generator) -> pd.DataFrame:
    dept_idx = rng.choice(len(DEPARTMENTS), size=n_clients, p=DEPT_WEIGHTS / DEPT_WEIGHTS.sum())
    employment = rng.choice(["Salaried", "Self-employed", "Business owner"], size=n_clients, p=[0.55, 0.28, 0.17])
    income = rng.lognormal(mean=np.log(9_000), sigma=0.6, size=n_clients)
    income[employment == "Business owner"] *= 2.2
    income[np.array([DEPARTMENTS[i][0] for i in dept_idx]) == "Guatemala"] *= 1.25
    score = np.clip(rng.normal(665, 75, size=n_clients), 400, 850)
    return pd.DataFrame({
        "client_id": [f"C{i:06d}" for i in range(1, n_clients + 1)],
        "age": rng.integers(21, 71, size=n_clients),
        "employment_type": employment,
        "monthly_income_gtq": income.round(2),
        "credit_score": score.round().astype(int),
        "home_department": [DEPARTMENTS[i][0] for i in dept_idx],
    })


def generate_loans(n_loans: int, clients: pd.DataFrame, agencies: pd.DataFrame,
                   rng: np.random.Generator) -> pd.DataFrame:
    months = _months(START_MONTH, END_MONTH)
    # Origination volume grows over time (the bank is expanding)
    weights = np.linspace(1.0, 2.2, len(months))
    orig_months = rng.choice(months, size=n_loans, p=weights / weights.sum())
    orig_months = pd.DatetimeIndex(np.sort(orig_months))

    shares = np.array([p[3] for p in PRODUCTS])
    product_idx = rng.choice(len(PRODUCTS), size=n_loans, p=shares / shares.sum())

    policy_date = pd.Timestamp(POLICY_CHANGE_DATE)
    scores = clients["credit_score"].to_numpy()
    is_owner = (clients["employment_type"] == "Business owner").to_numpy()

    rows = []
    for i in range(n_loans):
        p = PRODUCTS[product_idx[i]]
        min_score = MIN_SCORE_AFTER if orig_months[i] >= policy_date else MIN_SCORE_BEFORE
        eligible = scores >= min_score
        if p[0] == "P05":  # SME loans go to business owners
            eligible &= is_owner
        client_pos = rng.choice(np.flatnonzero(eligible))
        client = clients.iloc[client_pos]

        dept_agencies = agencies.loc[agencies["department"] == client["home_department"], "agency_id"].to_numpy()
        currency = "USD" if rng.random() < p[8] else "GTQ"
        amount_gtq = np.exp(rng.uniform(np.log(p[4][0]), np.log(p[4][1])))
        amount = amount_gtq / APPROX_FX if currency == "USD" else amount_gtq
        # Better scores get lower rates within the product's range
        score_pos = (client["credit_score"] - 400) / 450
        rate = p[5][1] - (p[5][1] - p[5][0]) * score_pos + rng.normal(0, 0.005)
        rows.append({
            "loan_id": f"L{i + 1:07d}",
            "client_id": client["client_id"],
            "product_id": p[0],
            "agency_id": rng.choice(dept_agencies),
            "origination_date": (orig_months[i] + pd.Timedelta(days=int(rng.integers(0, 28)))).date(),
            "currency": currency,
            "principal_amount": round(amount, 2),
            "annual_rate": round(float(np.clip(rate, p[5][0] - 0.01, p[5][1] + 0.01)), 4),
            "term_months": int(rng.choice(p[6])),
            "credit_score_at_origination": int(client["credit_score"]),
            "monthly_income_gtq": float(client["monthly_income_gtq"]),
        })

    loans = pd.DataFrame(rows)
    loans["installment_amount"] = monthly_installment(
        loans["principal_amount"].to_numpy(), loans["annual_rate"].to_numpy(), loans["term_months"].to_numpy()
    ).round(2)
    installment_gtq = loans["installment_amount"] * np.where(loans["currency"] == "USD", APPROX_FX, 1.0)
    loans["dti_at_origination"] = (installment_gtq / loans["monthly_income_gtq"]).round(4)
    return loans.drop(columns="monthly_income_gtq")


def simulate_performance(loans: pd.DataFrame, departments: np.ndarray, rng: np.random.Generator) -> pd.DataFrame:
    """Month-by-month simulation of every loan until it is paid off, charged off or the window ends."""
    months = _months(START_MONTH, END_MONTH)
    n = len(loans)
    product_lookup = {p[0]: p for p in PRODUCTS}
    region_mult = {d[0]: d[2] for d in DEPARTMENTS}

    orig_month = pd.to_datetime(loans["origination_date"]).dt.to_period("M").dt.to_timestamp().to_numpy()
    principal = loans["principal_amount"].to_numpy(dtype=float)
    rate_m = loans["annual_rate"].to_numpy() / 12
    installment = loans["installment_amount"].to_numpy(dtype=float)
    term = loans["term_months"].to_numpy()

    score = loans["credit_score_at_origination"].to_numpy()
    dti = loans["dti_at_origination"].to_numpy()
    base_p = loans["product_id"].map(lambda x: product_lookup[x][7]).to_numpy()
    # Risk that doesn't change month to month: product, score, debt-to-income and region
    static_risk = (base_p
                   * np.exp(-(score - 650) / 90)
                   * (1 + np.clip(dti - 0.35, 0, None) * 2)
                   * np.array([region_mult[d] for d in departments]))

    balance = principal.copy()
    state = np.zeros(n, dtype=int)  # consecutive missed payments
    paid_count = np.zeros(n, dtype=int)
    active = np.zeros(n, dtype=bool)
    closed = np.zeros(n, dtype=bool)

    records = []
    for month in months:
        month64 = np.datetime64(month)
        new = (orig_month == month64) & ~closed
        seasoned = active & ~new
        idx = np.flatnonzero(seasoned)

        payment = np.zeros(n)
        interest = np.zeros(n)
        status = np.full(n, "", dtype=object)
        charge_off = np.zeros(n)

        if idx.size:
            mob = ((month.year - pd.DatetimeIndex(orig_month[idx]).year) * 12
                   + (month.month - pd.DatetimeIndex(orig_month[idx]).month)).to_numpy()
            interest[idx] = balance[idx] * rate_m[idx]
            u = rng.random(idx.size)
            s = state[idx]

            # Current loans: early payoff, missed payment, or a regular payment
            p_miss = np.clip(static_risk[idx] * seasoning_factor(mob) * macro_factor(month), 0, 0.6)
            prepay = (s == 0) & (u < P_PREPAY)
            miss_from_current = (s == 0) & ~prepay & (u < P_PREPAY + p_miss)
            pay_current = (s == 0) & ~prepay & ~miss_from_current

            # Delinquent loans: roll forward, cure (pay all arrears) or pay only this month
            roll_p = np.array([P_ROLL_FORWARD.get(k, 0) for k in s]) * min(macro_factor(month), 1.3)
            cure_p = np.array([P_CURE.get(k, 0) for k in s])
            roll = (s > 0) & (u < roll_p)
            cure = (s > 0) & ~roll & (u < roll_p + cure_p)
            stay = (s > 0) & ~roll & ~cure

            inst = installment[idx]
            pay = np.zeros(idx.size)
            pay[prepay] = balance[idx][prepay] + interest[idx][prepay]
            pay[pay_current] = inst[pay_current]
            pay[cure] = inst[cure] * (s[cure] + 1)
            pay[stay] = inst[stay]
            pay = np.minimum(pay, balance[idx] + interest[idx])  # never pay more than owed

            new_balance = np.maximum(balance[idx] + interest[idx] - pay, 0)
            new_state = s.copy()
            new_state[miss_from_current] = 1
            new_state[roll] = s[roll] + 1
            new_state[cure] = 0

            paid_count[idx] += (pay_current | cure | stay).astype(int)
            balance[idx] = np.round(new_balance, 2)
            state[idx] = new_state
            payment[idx] = pay

            st = np.where(new_state == 0, "Current", "Delinquent").astype(object)
            finished = (new_balance < 1) | ((paid_count[idx] >= term[idx]) & (new_state == 0))
            st[finished] = "Paid Off"
            payment[idx[finished]] += balance[idx[finished]]  # final payment settles any remainder
            co = new_state >= CHARGE_OFF_STATE
            st[co] = "Charged Off"
            charge_off[idx[co]] = balance[idx[co]]
            balance[idx[co | finished]] = 0
            status[idx] = st
            closed[idx[co | finished]] = True
            active[idx[co | finished]] = False

        # Loans disbursed this month: first snapshot at full principal
        status[new] = "Current"
        active[new] = True

        reported = new | seasoned
        r_idx = np.flatnonzero(reported)
        dpd = np.where(state[r_idx] == 0, 0, (state[r_idx] - 1) * 30 + rng.integers(1, 31, size=r_idx.size))
        dpd = np.where(status[r_idx] == "Charged Off", 180 + rng.integers(0, 30, size=r_idx.size), dpd)
        dpd = np.where(status[r_idx] == "Paid Off", 0, dpd)
        records.append(pd.DataFrame({
            "loan_id": loans["loan_id"].to_numpy()[r_idx],
            "snapshot_month": month.date(),
            "outstanding_balance": balance[r_idx].round(2),
            "days_past_due": dpd,
            "installment_due": np.where(new[r_idx], 0, installment[r_idx]).round(2),
            "interest_accrued": interest[r_idx].round(2),
            "payment_received": payment[r_idx].round(2),
            "charge_off_amount": charge_off[r_idx].round(2),
            "loan_status": status[r_idx],
        }))

    return pd.concat(records, ignore_index=True)


def generate_portfolio(n_loans: int = 6000, seed: int = 42) -> dict[str, pd.DataFrame]:
    """Generate every raw table. Same seed -> same data, so results are reproducible."""
    rng = np.random.default_rng(seed)
    products, agencies = generate_reference_tables()
    clients = generate_clients(max(int(n_loans * 0.8), 50), rng)
    loans = generate_loans(n_loans, clients, agencies, rng)
    departments = loans["client_id"].map(clients.set_index("client_id")["home_department"]).to_numpy()
    performance = simulate_performance(loans, departments, rng)
    return {
        "clients": clients,
        "agencies": agencies,
        "products": products,
        "loans": loans,
        "loan_performance": performance,
    }
