"""Plot the accumulated sweep CSV (results/sweep/sweep_results.csv).

Reads every row written so far by sweep.py (deduped by case tag -- latest wins),
then draws:
  (1) cost/kg vs demand           (one line per year,cadence)
  (2) cost/kg & tank mass vs cadence   (the cadence study)
  (3) lunar-vs-Earth map over demand x cadence  (one panel per year)

Run anytime -- even if the sweep was done in several separate batches, this plots
everything accumulated.  Detailed per-case inspection: point post_process.py at
results/sweep/cases/<case>.json.
"""
import csv
from collections import defaultdict
from pathlib import Path

from plot_style import horizon_color, period_linestyle


RESULT_DIR = Path("results") / "sweep"
CSV_PATH = RESULT_DIR / "sweep_results.csv"


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load_feasible(csv_path=CSV_PATH):
    """Return feasible rows (numeric), deduped by case tag (last occurrence wins)."""
    if not csv_path.exists():
        raise SystemExit(f"no CSV at {csv_path}; run sweep.py first")
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))

    dedup = {}
    for r in rows:
        key = r.get("case") or (r.get("mission_years"), r.get("period_days"), r.get("demand_t_yr"))
        dedup[key] = r                       # later rows overwrite earlier (re-runs win)

    pts = []
    for r in dedup.values():
        if str(r.get("feasible")).lower() != "true":
            continue
        pts.append({
            "mission_years": _f(r.get("mission_years")),
            "demand_t_yr": _f(r.get("demand_t_yr")),
            "period_days": _f(r.get("period_days")),
            "cost_per_kg": _f(r.get("cost_per_kg")),
            "tank_mass": _f(r.get("tank_mass")),
            "SWE_q": _f(r.get("SWE_q")),
            "earth_prop_kg": _f(r.get("earth_prop_kg")),
            "lunar_prop_kg": _f(r.get("lunar_prop_kg")),
            "earth_frac": _f(r.get("earth_frac")),
            "lunar_used": str(r.get("lunar_used")).lower() == "true",
        })
    return [p for p in pts if p["cost_per_kg"] is not None]


def plot_curves(pts):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    groups = defaultdict(list)
    for r in pts:
        groups[(r["mission_years"], r["period_days"])].append(r)

    fig, ax = plt.subplots(figsize=(9, 6))
    keys = sorted(groups)
    for key in keys:
        yr, pd = key
        pr = sorted(groups[key], key=lambda r: r["demand_t_yr"])
        ax.plot([r["demand_t_yr"] for r in pr], [r["cost_per_kg"] for r in pr],
                marker="o", color=horizon_color(yr), linestyle=period_linestyle(pd),
                label=f"{yr:g}yr, {pd:g}d")
        for r in pr:
            if r["lunar_used"]:
                ax.scatter(r["demand_t_yr"], r["cost_per_kg"], s=150,
                           facecolors="none", edgecolors="seagreen", linewidths=1.8, zorder=3)

    ax.set_xscale("log")
    ax.set_xlabel("GEO payload demand [t/yr]")
    ax.set_ylabel("cost per kg to GEO [$/kg]")
    ax.set_title("Cost/kg vs demand")
    ax.grid(color="0.9", which="both")
    ax.legend(title="mission, cadence", fontsize=8, ncol=2)
    fig.tight_layout()
    path = RESULT_DIR / "cost_per_kg_curves.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    print(f"Saved {path}")


def plot_cadence_effect(pts):
    """cost/kg and depot tank mass vs demand cadence."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    groups = defaultdict(list)
    for r in pts:
        groups[(r["mission_years"], r["demand_t_yr"])].append(r)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))
    keys = sorted(groups)
    cmap = plt.cm.plasma
    for i, key in enumerate(keys):
        yr, dt = key
        pr = sorted(groups[key], key=lambda r: r["period_days"])
        color = cmap(i / max(1, len(keys) - 1))
        lbl = f"{yr:g}yr, {dt:g}t/yr"
        ax1.plot([r["period_days"] for r in pr], [r["cost_per_kg"] for r in pr],
                 "-o", color=color, label=lbl)
        ax2.plot([r["period_days"] for r in pr], [r["tank_mass"] for r in pr],
                 "-o", color=color, label=lbl)

    ax1.set_xlabel("demand cadence [days between pulses]")
    ax1.set_ylabel("cost per kg to GEO [$/kg]")
    ax1.set_title("Cost/kg vs cadence")
    ax1.grid(color="0.92")
    ax1.legend(fontsize=7, ncol=2)

    ax2.set_xlabel("demand cadence [days between pulses]")
    ax2.set_ylabel("total depot tank mass [kg]")
    ax2.set_title("Depot tank mass vs cadence")
    ax2.grid(color="0.92")

    fig.tight_layout()
    path = RESULT_DIR / "cadence_effect.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    print(f"Saved {path}")


def plot_earth_vs_lunar(pts):
    """How much of the propellant is still Earth-sourced despite lunar ISRU.

    Left: Earth propellant fraction = earth / (earth + lunar) [%] vs demand.
    Right: absolute Earth vs lunar propellant per steady period [kg] vs demand.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    usable = [r for r in pts if r.get("earth_frac") is not None]
    if not usable:
        return
    groups = defaultdict(list)
    for r in usable:
        groups[(r["mission_years"], r["period_days"])].append(r)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))
    keys = sorted(groups)
    for key in keys:
        yr, pd = key
        pr = sorted(groups[key], key=lambda r: r["demand_t_yr"])
        color = horizon_color(yr)
        ls = period_linestyle(pd)
        lbl = f"{yr:g}yr, {pd:g}d"
        ax1.plot([r["demand_t_yr"] for r in pr], [100 * r["earth_frac"] for r in pr],
                 marker="o", color=color, linestyle=ls, label=lbl)
        # absolute: Earth (filled circle) vs lunar (open square, faded)
        ax2.plot([r["demand_t_yr"] for r in pr], [r["earth_prop_kg"] for r in pr],
                 marker="o", color=color, linestyle=ls, label=f"{lbl} Earth")
        ax2.plot([r["demand_t_yr"] for r in pr], [r["lunar_prop_kg"] for r in pr],
                 marker="s", markerfacecolor="none", color=color, linestyle=ls, alpha=0.5)

    ax1.axhline(50, ls=":", color="0.5", lw=1)
    ax1.set_xscale("log")
    ax1.set_ylim(0, 100)
    ax1.set_xlabel("GEO payload demand [t/yr]")
    ax1.set_ylabel("Earth propellant fraction [%]")
    ax1.set_title("Earth share of propellant")
    ax1.grid(color="0.92", which="both")
    ax1.legend(fontsize=7, ncol=2)

    ax2.set_xscale("log")
    ax2.set_xlabel("GEO payload demand [t/yr]")
    ax2.set_ylabel("propellant per steady period [kg]")
    ax2.set_title("Earth (filled ●) vs lunar (open □, faded) propellant")
    ax2.grid(color="0.92", which="both")

    fig.tight_layout()
    path = RESULT_DIR / "earth_vs_lunar_propellant.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    print(f"Saved {path}")


def plot_breakeven(pts):
    """Lunar-vs-Earth map over demand x cadence (one panel per mission year)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    yrs = sorted({r["mission_years"] for r in pts})
    fig, axes = plt.subplots(1, len(yrs), figsize=(5.5 * len(yrs), 5), squeeze=False)
    for ax, yr in zip(axes[0], yrs):
        for r in [p for p in pts if p["mission_years"] == yr]:
            ax.scatter(r["demand_t_yr"], r["period_days"], s=170,
                       color=("seagreen" if r["lunar_used"] else "blue"),
                       edgecolor="black", linewidth=0.6)
        ax.set_xscale("log")
        ax.set_xlabel("GEO payload demand [t/yr]")
        ax.set_ylabel("demand cadence [days]")
        ax.set_title(f"Lunar (green) vs Earth (blue) — {yr:g} yr")
        ax.grid(color="0.9", which="both")
    axes[0][0].scatter([], [], color="seagreen", edgecolor="black", label="lunar wins")
    axes[0][0].scatter([], [], color="blue", edgecolor="black", label="Earth-only")
    axes[0][0].legend()
    fig.tight_layout()
    path = RESULT_DIR / "breakeven_map.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    print(f"Saved {path}")


def main():
    pts = load_feasible()
    if not pts:
        print("no feasible rows to plot yet.")
        return
    print(f"plotting {len(pts)} feasible case(s) from {CSV_PATH}")
    plot_curves(pts)
    plot_cadence_effect(pts)
    plot_earth_vs_lunar(pts)
    plot_breakeven(pts)


if __name__ == "__main__":
    main()
