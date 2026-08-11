import json
import math
from pathlib import Path


DEFAULT_SOLUTION = Path("results") / "latest_solution.json"
DEFAULT_PLOT_DIR = Path("results") / "plots"

# top-to-bottom order for the time-expanded plot (Earth cluster -> Moon cluster)
NODE_ORDER = ["LEO", "GTO", "GEO", "EML1", "NRHO", "LLO", "Moon"]

# 2-D positions for the network flow map
NODE_POS = {
    "LEO": (0.0, 0.0),
    "GTO": (1.2, 1.0),
    "GEO": (1.2, -1.0),
    "EML1": (3.0, 0.0),
    "NRHO": (4.2, 1.0),
    "LLO": (4.2, -1.0),
    "Moon": (5.4, 0.0),
}

TOL = 1e-6
COST_COLORS = {
    "SWE": "#4C78A8",
    "DWE": "#F58518",
    "storage": "#54A24B",
    "infrastructure_to_GTO": "#BAB0AC",
    "spacecraft": "#B279A2",
    "maintenance": "#E45756",
    "earth_prop": "#72B7B2",
}
PROP_TANK_COLOR = "#2F7D32"


def main():
    for path in generate_plots(DEFAULT_SOLUTION):
        print(f"saved {path}")


def generate_plots(solution_path=DEFAULT_SOLUTION, plot_dir=DEFAULT_PLOT_DIR):
    global plt, Line2D
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    plt.rcParams["font.family"] = "Calibri"

    solution_path = Path(solution_path)
    plot_dir = Path(plot_dir)
    plot_dir.mkdir(parents=True, exist_ok=True)

    with solution_path.open("r", encoding="utf-8") as f:
        solution = json.load(f)

    makers = [
        plot_flow_over_time,
        plot_network_flow_map,
        plot_cost_breakdown,
        plot_cost_share,
        plot_demand_profile,
        plot_infrastructure,
        plot_production,
    ]
    paths = [maker(solution, plot_dir) for maker in makers]
    return [str(p) for p in paths if p is not None]


def _ordered_nodes(nodes):
    ordered = [n for n in NODE_ORDER if n in nodes]
    ordered += [n for n in nodes if n not in ordered]
    return ordered


def _color_map(commodities):
    return {k: plt.cm.tab10.colors[i % 10] for i, k in enumerate(commodities)}


def plot_flow_over_time(solution, plot_dir):
    nodes = _ordered_nodes(solution["nodes"])
    # include BOTH move and hold arcs (hold = inventory sitting at a node);
    # drop near-zero numerical-noise flows (loose-gap artifacts) so phantom routes vanish.
    MIN_KG = 1.0
    flows = [f for f in solution["flows"] if f["departed"] > MIN_KG]
    if not flows:
        return None

    # Earth-nearest (LEO) at the bottom, Moon at the top
    y = {node: idx for idx, node in enumerate(nodes)}

    # number of vehicles on each move arc/time (model tracks counts, not identities)
    trip_count = {(tr["mode"], tr["arc"], tr["time"]): tr["count"]
                  for tr in solution.get("trips", [])}

    # ---- line category: OTV / PL / RT-prop / water / hold_prop / tank ----
    #   Prop split by carrier/role (OTV moving it, RT hauling it, or held);
    #   PL and water share one style whether moving or held.
    def _cat(f):
        c, kind, mode = f["commodity"], f["kind"], f["mode"]
        if c == "PL":
            return "PL"
        if c == "H2O":
            return "water"
        if c == "Prop":
            return "hold_prop" if kind == "hold" else ("OTV" if mode == "OTV" else "RT-prop")
        return "tank"                                # H2O_Tank / Prop_Tank
    CAT_STYLE = {                                    # (color, linestyle)
        "OTV":       ("tab:orange",  "-"),
        "PL":        ("crimson",     ":"),
        "water":     ("dodgerblue",  "--"),
        "RT-prop":   ("red",         "-"),
        "hold_prop": ("saddlebrown", "-"),
        "tank":      ("0.6",         "-"),
    }
    # offset by COMMODITY so a move arc's endpoint meets the hold arc of the same
    # commodity -> continuous path; different commodities sit side by side, packed tight.
    comms = sorted({f["commodity"] for f in flows})
    nc = len(comms)
    SPAN = 0.10   # total vertical spread around a node -> packed tight (may touch)
    comm_off = {k: (i - (nc - 1) / 2) * (SPAN / max(1, nc - 1)) for i, k in enumerate(comms)}

    fig, ax = plt.subplots(figsize=(16, 9))
    for node in nodes:
        ax.axhline(y[node], color="0.9", lw=0.8, zorder=0)

    mis = solution["mission"]
    steps_per_year = mis["days_per_year"] // mis["days_per_step"]
    for year in range(1, mis["mission_years"] + 1):
        xline = year * steps_per_year
        ax.axvline(xline, color="0.75", ls=":", lw=1.0, zorder=1)
        ax.text(xline, -0.55, f"Y{year}", fontsize=8, color="0.45",
                ha="center", va="top")

    for f in flows:
        cat = _cat(f)
        color, ls = CAT_STYLE[cat]
        off = comm_off[f["commodity"]]
        if f["kind"] == "hold":
            lw = 3.0                                      # inventory hold (horizontal)
            alpha = 1.0
            zorder = 3
        else:
            n_veh = trip_count.get((f["mode"], f["arc"], f["time"]), 1)
            lw = 2.2 + 0.8 * min(n_veh, 6)                # thick; grows with #vehicles
            alpha = 0.9
            zorder = 4                                    # moves drawn on top of holds
        if cat == "tank":
            lw = min(lw, 1.4)                             # subdue tanks
        ax.plot(
            [f["time"], f["arrival_time"]],
            [y[f["tail"]] + off, y[f["head"]] + off],
            color=color,
            lw=lw,
            alpha=alpha,
            linestyle=ls,
            solid_capstyle="round",
            dash_capstyle="round",
            zorder=zorder,
        )

    # Payload supply/demand events from the explicit input profile.
    events = mis["demand_events"]
    max_mass = max((event["mass_kg"] for event in events), default=1.0)
    if "GEO" in y:
        for event in events:
            size = 140 + 300 * event["mass_kg"] / max_mass
            ax.scatter(event["demand_step"], y["GEO"], marker="*", s=size, color="crimson",
                       edgecolor="black", linewidth=0.6, zorder=6)
    if "GTO" in y:
        for event in events:
            size = 140 + 300 * event["mass_kg"] / max_mass
            ax.scatter(event["supply_step"], y["GTO"], marker="*", s=size, color="gold",
                       edgecolor="black", linewidth=0.6, zorder=6)

    ax.set_yticks([y[node] for node in nodes])
    ax.set_yticklabels(nodes)
    ax.set_xlabel(
        f"mission year (time step = {mis['days_per_step']} days)"
    )
    ax.set_ylabel("node")
    ax.set_title("Commodity flow over time", pad=36)

    cats_present = [c for c in CAT_STYLE if any(_cat(f) == c for f in flows)]
    handles = [Line2D([0], [0], color=CAT_STYLE[c][0], lw=3, linestyle=CAT_STYLE[c][1], label=c)
               for c in cats_present]
    handles += [
        Line2D([0], [0], marker="*", color="w", markerfacecolor="gold",
               markeredgecolor="black", markersize=15, label="PL supply @GTO"),
        Line2D([0], [0], marker="*", color="w", markerfacecolor="crimson",
               markeredgecolor="black", markersize=15, label="PL demand @GEO"),
    ]
    ax.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 1.005), ncol=len(handles), fontsize=8)

    ax.grid(axis="x", color="0.93", lw=0.8)
    ax.set_xlim(-0.5, solution["T"] - 0.5)
    year_ticks = [year * steps_per_year for year in range(mis["mission_years"] + 1)]
    ax.set_xticks(year_ticks)
    ax.set_xticklabels(
        [f"{year}\nstep {year * steps_per_year}\nday {year * mis['days_per_year']}"
         for year in range(mis["mission_years"] + 1)]
    )
    fig.tight_layout()

    path = plot_dir / "flow_over_time.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def plot_network_flow_map(solution, plot_dir):
    edge_value = {}
    for f in solution["flows"]:
        if f["kind"] != "move" or f["departed"] <= TOL:
            continue
        key = (f["tail"], f["head"])
        edge_value[key] = edge_value.get(key, 0.0) + f["departed"]

    edge_value = {k: v for k, v in edge_value.items() if k[0] in NODE_POS and k[1] in NODE_POS}
    if not edge_value:
        return None

    max_value = max(edge_value.values())
    fig, ax = plt.subplots(figsize=(13, 7))

    for (tail, head), value in edge_value.items():
        x0, y0 = NODE_POS[tail]
        x1, y1 = NODE_POS[head]
        width = 0.6 + 5.0 * value / max_value
        ax.annotate(
            "",
            xy=(x1, y1),
            xytext=(x0, y0),
            arrowprops={
                "arrowstyle": "->",
                "lw": width,
                "color": "steelblue",
                "alpha": 0.5,
                "shrinkA": 14,
                "shrinkB": 14,
            },
            zorder=2,
        )
        ax.text(
            (x0 + x1) / 2, (y0 + y1) / 2, _kg(value),
            fontsize=7, ha="center", va="center",
            bbox={"boxstyle": "round,pad=0.18", "fc": "white", "ec": "0.85", "alpha": 0.85},
            zorder=4,
        )

    for node, (x, y) in NODE_POS.items():
        if node not in solution["nodes"]:
            continue
        ax.scatter(x, y, s=1000, color="white", edgecolor="0.25", linewidth=1.3, zorder=5)
        ax.text(x, y, node, ha="center", va="center", fontsize=9, zorder=6)

    dwe_nodes = [n for n, info in solution["facilities"]["DWE"].items() if info["installed"]]
    storage_nodes = list(solution["storage"].keys())
    for node in dwe_nodes:
        if node in NODE_POS:
            x, y = NODE_POS[node]
            ax.scatter(x, y - 0.28, marker="s", s=120, color="seagreen", edgecolor="white", linewidth=0.8, zorder=7)
    for node in storage_nodes:
        if node in NODE_POS:
            x, y = NODE_POS[node]
            ax.scatter(x, y + 0.28, marker="^", s=120, color="mediumpurple", edgecolor="white", linewidth=0.8, zorder=7)

    legend = []
    if dwe_nodes:
        legend.append(Line2D([0], [0], marker="s", color="w", markerfacecolor="seagreen", label="DWE", markersize=9))
    if storage_nodes:
        legend.append(Line2D([0], [0], marker="^", color="w", markerfacecolor="mediumpurple", label="storage", markersize=9))
    if solution["facilities"]["SWE"]["installed"]:
        legend.append(Line2D([0], [0], marker="*", color="w", markerfacecolor="orange", label="SWE @ Moon", markersize=12))
        mx, my = NODE_POS["Moon"]
        ax.scatter(mx, my - 0.28, marker="*", s=180, color="orange", edgecolor="white", linewidth=0.8, zorder=7)
    if legend:
        ax.legend(handles=legend, loc="lower left", frameon=True, fontsize=8)

    ax.set_title("Aggregate departed mass flow map (move arcs)")
    ax.set_axis_off()
    ax.set_xlim(-0.8, 6.2)
    ax.set_ylim(-1.9, 1.9)
    fig.tight_layout()

    path = plot_dir / "network_flow_map.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def plot_cost_breakdown(solution, plot_dir):
    costs = {
        name: value
        for name, value in solution["cost_breakdown"].items()
        if name != "total" and abs(value) > TOL
    }
    if not costs:
        return None

    labels = list(costs)
    values = [costs[label] for label in labels]

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.bar(range(len(labels)), values, color="slateblue")
    ax.set_title(f"Objective cost breakdown (total = {_money(solution['cost_breakdown']['total'])})")
    ax.set_ylabel("cost [$]")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=25, ha="right")
    ax.grid(axis="y", color="0.9")
    for i, value in enumerate(values):
        ax.text(i, value, _money(value), ha="center", va="bottom", fontsize=8)
    fig.tight_layout()

    path = plot_dir / "cost_breakdown.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def plot_cost_share(solution, plot_dir):
    breakdown = solution.get("cost_breakdown", {})
    costs = {
        "SWE": breakdown.get("SWE", 0.0),
        "DWE": breakdown.get("DWE", 0.0),
        "storage": breakdown.get("storage", 0.0),
        "infrastructure_to_GTO": breakdown.get("infrastructure_to_GTO", 0.0),
        "spacecraft": breakdown.get("spacecraft", 0.0),
        "maintenance": breakdown.get("maintenance", 0.0),
        "earth_prop": breakdown.get("earth_prop", 0.0),
    }
    labels = list(costs)
    values = [max(0.0, costs[label]) for label in labels]
    total = sum(values)
    if total <= TOL:
        return None

    colors = [COST_COLORS[label] for label in labels]
    fig, ax = plt.subplots(figsize=(11, 6.5))
    wedges, label_texts, autotexts = ax.pie(
        values,
        labels=labels,
        colors=colors,
        startangle=90,
        counterclock=False,
        autopct=lambda pct: f"{pct:.1f}%",
        pctdistance=0.72,
        labeldistance=1.08,
        wedgeprops={"edgecolor": "white", "linewidth": 1.2},
    )
    for text in label_texts:
        text.set_fontsize(15)
    for text in autotexts:
        text.set_fontsize(16)
        text.set_weight("bold")

    legend_labels = [f"{label}: {_money(value)}" for label, value in zip(labels, values)]
    ax.legend(
        wedges,
        legend_labels,
        loc="center left",
        bbox_to_anchor=(1.02, 0.5),
        frameon=False,
        fontsize=15,
    )
    ax.set_title(f"Lifecycle cost share (total = {_money(total)})", fontsize=18)
    ax.set_aspect("equal")
    fig.tight_layout()

    path = plot_dir / "cost_share_pie.png"
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_demand_profile(solution, plot_dir):
    mission = solution.get("mission", {})
    events = mission.get("demand_events", [])
    if not events:
        return None

    days_per_year = mission["days_per_year"]
    mission_years = mission["mission_years"]
    event_year = [event["demand_day"] / days_per_year for event in events]
    event_tonnes = [event["mass_kg"] / 1000.0 for event in events]
    annual_tonnes = [
        mission["demand_by_year_kg"].get(str(year), 0.0) / 1000.0
        for year in range(1, mission_years + 1)
    ]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))
    ax1.scatter(event_year, event_tonnes, s=55, color="crimson", edgecolor="black", linewidth=0.4)
    for x, mass in zip(event_year, event_tonnes):
        ax1.vlines(x, 0, mass, color="crimson", alpha=0.25, lw=1)
    ax1.set_xlabel("mission year (360 days/year)")
    ax1.set_ylabel("event payload [t]")
    ax1.set_title("Irregular GEO payload events")
    ax1.set_xlim(0, mission_years)
    ax1.grid(color="0.92")

    years = list(range(1, mission_years + 1))
    ax2.bar(years, annual_tonnes, color="slateblue")
    ax2.set_xlabel("mission year")
    ax2.set_ylabel("annual payload [t]")
    ax2.set_title("Annual GEO payload total (reported, not constrained)")
    ax2.set_xticks(years)
    ax2.grid(axis="y", color="0.92")
    for year, mass in zip(years, annual_tonnes):
        ax2.text(year, mass, f"{mass:g}", ha="center", va="bottom", fontsize=8)

    fig.tight_layout()
    path = plot_dir / "demand_profile.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def plot_infrastructure(solution, plot_dir):
    labels, values, colors = [], [], []

    swe = solution["facilities"]["SWE"]
    if swe["q"] > TOL:
        labels.append("SWE@Moon")
        values.append(swe["q"])
        colors.append(COST_COLORS["SWE"])
    for node, info in solution["facilities"]["DWE"].items():
        if info["q"] > TOL:
            labels.append(f"DWE@{node}")
            values.append(info["q"])
            colors.append(COST_COLORS["DWE"])
    for node, info in solution["storage"].items():
        if info["H2O"] > TOL:
            labels.append(f"H2O tank@{node}")
            values.append(info["H2O"])
            colors.append(COST_COLORS["storage"])
        if info["Prop"] > TOL:
            labels.append(f"Prop tank@{node}")
            values.append(info["Prop"])
            colors.append(PROP_TANK_COLOR)

    if not labels:
        return None

    fig, ax = plt.subplots(figsize=(max(9, 0.5 * len(labels)), 5))
    ax.bar(range(len(labels)), values, color=colors)
    ax.set_title("Selected infrastructure mass")
    ax.set_ylabel("mass [kg]")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax.grid(axis="y", color="0.9")
    fig.tight_layout()

    path = plot_dir / "infrastructure.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def plot_production(solution, plot_dir):
    production = solution["production"]
    if not production:
        return None

    facilities = sorted({p["facility"] for p in production})
    T = solution["T"]
    times = list(range(T))
    series = {
        e: [
            sum(p["operated_mass"] for p in production if p["facility"] == e and p["time"] == t)
            for t in times
        ]
        for e in facilities
    }

    fig, ax = plt.subplots(figsize=(12, 5))
    for i, e in enumerate(facilities):
        ax.plot(times, series[e], marker="o", ms=3, lw=1.2, color=plt.cm.tab10.colors[i % 10], label=e)

    ax.set_title("ISRU operated mass over time (q_operation)")
    mission = solution["mission"]
    steps_per_year = mission["days_per_year"] // mission["days_per_step"]
    year_ticks = [year * steps_per_year for year in range(mission["mission_years"] + 1)]
    ax.set_xticks(year_ticks)
    ax.set_xticklabels([str(year) for year in range(mission["mission_years"] + 1)])
    ax.set_xlabel(
        f"mission year (time step = {mission['days_per_step']} days)"
    )
    ax.set_ylabel("operated plant mass [kg]")
    ax.legend(fontsize=8, ncol=min(4, len(facilities)))
    ax.grid(color="0.92")
    fig.tight_layout()

    path = plot_dir / "isru_production.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def _kg(value):
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.1f}k"
    return f"{value:.0f}"


def _money(value):
    if abs(value) >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if abs(value) >= 1_000:
        return f"${value / 1_000:.1f}k"
    return f"${value:.0f}"


if __name__ == "__main__":
    main()
