"""Create the static charts shown in the README from the exported marts.

    python scripts/make_readme_charts.py
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
EXPORTS = ROOT / "exports"
OUT = ROOT / "docs" / "images"

BLUE, ORANGE = "#2a78d6", "#eb6834"
INK, INK_2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"


def style(ax, title, subtitle):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.tick_params(colors=INK_2, labelsize=9, length=0)
    ax.set_axisbelow(True)
    ax.set_title(title, loc="left", fontsize=13, color=INK, fontweight="bold", pad=24)
    ax.text(0, 1.02, subtitle, transform=ax.transAxes, fontsize=9.5, color=INK_2)


def label_end(ax, x, y, text, color):
    ax.plot([x], [y], "o", color=color, markersize=6, markeredgecolor=SURFACE, markeredgewidth=2)
    ax.annotate(text, (x, y), xytext=(8, 0), textcoords="offset points", va="center", fontsize=9, color=INK)


def chart_delinquency_trend():
    df = pd.read_csv(EXPORTS / "mart_portfolio_monthly.csv", parse_dates=["month_start"])
    m = df.groupby("month_start")[["portfolio_balance_gtq", "npl_balance_gtq", "balance_30_plus_gtq"]].sum()
    m = m[m.index >= "2023-01-01"]
    npl = m["npl_balance_gtq"] / m["portfolio_balance_gtq"]
    d30 = m["balance_30_plus_gtq"] / m["portfolio_balance_gtq"]

    fig, ax = plt.subplots(figsize=(9, 4.2), facecolor=SURFACE)
    ax.axvspan(pd.Timestamp("2024-04-01"), pd.Timestamp("2024-12-31"), color="#f0efec", zorder=0)
    ax.text(pd.Timestamp("2024-04-10"), max(d30) * 1.02, "Stress period", fontsize=8.5, color=INK_2, va="bottom")
    ax.axvline(pd.Timestamp("2025-01-01"), color=INK_2, linewidth=1, linestyle=(0, (3, 3)))
    ax.text(pd.Timestamp("2025-01-20"), max(d30) * 0.03, "Stricter underwriting\n(min score 560)", fontsize=8.5, color=INK_2, va="bottom")
    ax.plot(d30.index, d30, color=BLUE, linewidth=2)
    ax.plot(npl.index, npl, color=ORANGE, linewidth=2)
    label_end(ax, d30.index[-1], d30.iloc[-1], f"30+ days past due  {d30.iloc[-1]:.1%}", BLUE)
    label_end(ax, npl.index[-1], npl.iloc[-1], f"NPL (90+ days)  {npl.iloc[-1]:.1%}", ORANGE)
    ax.yaxis.set_major_formatter(mtick.PercentFormatter(1.0, decimals=0))
    ax.set_ylim(0, max(d30) * 1.15)
    ax.set_xlim(m.index[0], m.index[-1] + pd.Timedelta(days=200))
    ticks = pd.date_range(m.index[0], m.index[-1], freq="6MS")
    ax.set_xticks(ticks, [t.strftime("%b %Y") for t in ticks])
    style(ax, "Delinquency ratios", "Share of portfolio balance past due, monthly")
    fig.tight_layout()
    fig.savefig(OUT / "delinquency_trend.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


def chart_vintage_by_policy():
    v = pd.read_csv(EXPORTS / "mart_vintage_curves.csv")
    g = v.groupby(["underwriting_policy", "months_on_book"])[["loans_ever_90_plus", "loans_observed"]].sum()
    g["rate"] = g["loans_ever_90_plus"] / g["loans_observed"]
    g = g.reset_index()

    fig, ax = plt.subplots(figsize=(9, 4.2), facecolor=SURFACE)
    for policy, color, name in [("Legacy policy (min score 500)", ORANGE, "Loans approved 2022-2024"),
                                ("2025 policy (min score 560)", BLUE, "Loans approved 2025-2026")]:
        s = g[g["underwriting_policy"] == policy]
        s = s[s["loans_observed"] >= 400]  # drop the thin tail of young vintages
        ax.plot(s["months_on_book"], s["rate"], color=color, linewidth=2)
        label_end(ax, s["months_on_book"].iloc[-1], s["rate"].iloc[-1],
                  f"{name}  {s['rate'].iloc[-1]:.1%}", color)
    ax.yaxis.set_major_formatter(mtick.PercentFormatter(1.0, decimals=0))
    ax.set_xlabel("Months since disbursement", fontsize=9, color=INK_2)
    ax.set_xlim(0, 46)
    ax.set_ylim(0, None)
    style(ax, "Vintage curves: stricter underwriting is paying off",
          "Cumulative share of loans that ever reached 90+ days past due")
    fig.tight_layout()
    fig.savefig(OUT / "vintage_by_policy.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


def chart_pd_by_score():
    pd_seg = pd.read_csv(EXPORTS / "mart_pd_by_segment.csv")
    s = pd_seg.groupby("score_band")[["defaults", "observations"]].sum()
    s["pd"] = s["defaults"] / s["observations"]
    labels = [b.split(". ")[1] for b in s.index]

    fig, ax = plt.subplots(figsize=(9, 4.2), facecolor=SURFACE)
    bars = ax.bar(labels, s["pd"], color=BLUE, width=0.55)
    for bar, value in zip(bars, s["pd"]):
        ax.annotate(f"{value:.1%}", (bar.get_x() + bar.get_width() / 2, value), xytext=(0, 4),
                    textcoords="offset points", ha="center", fontsize=9, color=INK)
    ax.yaxis.set_major_formatter(mtick.PercentFormatter(1.0, decimals=0))
    ax.set_xlabel("Credit score at origination", fontsize=9, color=INK_2)
    style(ax, "12-month probability of default by credit score",
          "Share of performing loans that reach 90+ days past due within a year")
    fig.tight_layout()
    fig.savefig(OUT / "pd_by_score.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    chart_delinquency_trend()
    chart_vintage_by_policy()
    chart_pd_by_score()
    print(f"Charts saved to {OUT}")
