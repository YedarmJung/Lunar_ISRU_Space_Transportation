"""Facility-placement oriented plots for the sweep results.

Reads results/sweep/sweep_results.csv + results/sweep/cases/*.json and writes
seminar-ready figures to results/sweep/plots_new/.

Figures
-------
1. maps/depot_map_<case>.png : cislunar schematic map per case
   (facilities sized by capacity + aggregated commodity flows)
2. depot_map_grid.png        : small-multiple facility maps (years x demand)
3. facility_heatmap.png      : DWE / storage capacity per node per case
4. depot_ranking.png         : which node is selected how often / how big
5. capacity_vs_demand.png    : installed capacity trends vs demand
6. cost_breakdown_musd_per_t.png : MUSD/t cost composition per case
"""

import json
import math
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Circle, FancyArrowPatch

SWEEP_DIR = Path("results") / "sweep"
CASE_DIR = SWEEP_DIR / "cases"
OUT_DIR = SWEEP_DIR / "plots_new"

TOL = 0.001  # t (1 kg); below this treated as numerical noise

# ---------------------------------------------------------------- style ----
C_BLUE = "#2a78d6"    # DWE / Prop
C_GREEN = "#008300"   # SWE
C_AQUA = "#1baf7a"    # H2O
C_YELLOW = "#eda100"  # storage
C_ORANGE = "#eb6834"  # payload
C_VIOLET = "#4a3aa7"
C_RED = "#e34948"
C_GRAY = "#8a8a8a"

from plot_style import (HORIZON_COLORS, HORIZON_MARKERS,  # noqa: E402
                        period_linestyle)

plt.rcParams.update({
    "figure.dpi": 150,
    "savefig.dpi": 200,
    "font.size": 10,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.color": "#dddddd",
    "grid.linewidth": 0.6,
    "axes.axisbelow": True,
})

# node layout mimicking the cislunar schematic (Earth left, Moon right)
NODE_POS = {
    "GTO": (2.10, 1.00),
    "GEO": (2.90, -1.40),
    "EML1": (5.50, -0.50),
    "NRHO": (6.90, 1.55),
    "LLO": (6.60, 0.45),
    "Moon": (7.20, -0.40),
}
EARTH_C, EARTH_R = (-0.65, 0.0), 1.15
MOON_C, MOON_R = (7.70, -0.60), 0.72
NODE_ORDER = ["GTO", "GEO", "EML1", "NRHO", "LLO", "Moon"]

COMMODITY_COLOR = {"Prop": C_BLUE, "H2O": C_AQUA, "PL": C_ORANGE}


# ----------------------------------------------------------------- data ----
def load_cases():
    df = pd.read_csv(SWEEP_DIR / "sweep_results.csv")
    df = df[df["feasible"]].copy()
    sols = {}
    for case in df["case"]:
        with (CASE_DIR / f"{case}.json").open(encoding="utf-8") as f:
            sols[case] = json.load(f)
    df = df.sort_values(["period_days", "mission_years", "demand_t_yr"]).reset_index(drop=True)
    return df, sols


def facility_table(sol):
    """Return per-node SWE, DWE, and tank masses in metric tonnes."""
    out = {n: defaultdict(float) for n in NODE_ORDER}
    swe = sol["facilities"]["SWE"]
    if swe.get("installed") and swe["q"] > TOL:
        out[swe["node"]]["SWE"] = swe["q"]
    for node, info in sol["facilities"]["DWE"].items():
        if info.get("installed") and info["q"] > TOL:
            out[node]["DWE"] = info["q"]
    for node, comms in sol.get("storage", {}).items():
        for comm in ("H2O", "Prop"):
            cap = float(comms.get(comm, 0.0))
            if cap > TOL:
                out[node][f"sto_{comm}"] = cap
    return out


def aggregate_flows(sol):
    """Sum departed mass on move arcs in metric tonnes."""
    agg = defaultdict(float)
    for fl in sol["flows"]:
        if fl["kind"] == "hold" or fl["tail"] == fl["head"]:
            continue
        if fl["departed"] > TOL:
            agg[(fl["tail"], fl["head"], fl["commodity"])] += fl["departed"]
    return agg


def network_edges(sol):
    edges = set()
    for arc in sol["arcs"]:
        if arc["kind"] != "hold" and arc["tail"] != arc["head"]:
            edges.add(tuple(sorted((arc["tail"], arc["head"]))))
    return edges


# ------------------------------------------------------------ map basics ----
def draw_basemap(ax, edges, label_size=9, node_size=14):
    ax.set_xlim(-2.0, 8.7)
    ax.set_ylim(-2.35, 2.35)
    ax.set_aspect("equal")
    ax.axis("off")

    earth = Circle(EARTH_C, EARTH_R, facecolor="#c3d8f0", edgecolor="#6d94c4",
                   linewidth=1.2, zorder=2)
    ax.add_patch(earth)
    moon = Circle(MOON_C, MOON_R, facecolor="#d9d9d9", edgecolor="#9a9a9a",
                  linewidth=1.0, zorder=2)
    ax.add_patch(moon)
    ax.text(EARTH_C[0], EARTH_C[1], "Earth", ha="center", va="center",
            fontsize=label_size, color="#40608c", zorder=3)

    for a, b in edges:
        xa, ya = NODE_POS[a]
        xb, yb = NODE_POS[b]
        ax.plot([xa, xb], [ya, yb], color="#cccccc", linewidth=0.8, zorder=1)

    label_off = {"GEO": (0, -13), "EML1": (0, -13), "Moon": (16, -13),
                 "LLO": (-15, 5), "NRHO": (0, 7), "GTO": (0, 7)}
    for node, (x, y) in NODE_POS.items():
        ax.scatter([x], [y], s=node_size, color="#333333", zorder=6)
        dx, dy = label_off[node]
        scale = label_size / 9.0
        ax.annotate(node, (x, y), xytext=(dx * scale, dy * scale),
                    textcoords="offset points", ha="center",
                    fontsize=label_size, fontweight="bold",
                    color="#222222", zorder=7)


def glyph_area(mass_t, max_mass_t, max_area=2400.0, min_area=60.0):
    if mass_t <= TOL:
        return 0.0
    return min_area + (max_area - min_area) * mass_t / max_mass_t


def draw_facilities(ax, fac, scale_max, area_scale=1.0):
    """SWE = green square, DWE = blue circle, storage = yellow diamond."""
    for node, vals in fac.items():
        x, y = NODE_POS[node]
        if vals.get("DWE"):
            ax.scatter([x], [y],
                       s=glyph_area(vals["DWE"], scale_max["DWE"],
                                    2400.0 * area_scale, 60.0 * area_scale),
                       marker="o", facecolor=C_BLUE, edgecolor="white",
                       linewidth=1.2, alpha=0.85, zorder=4)
        if vals.get("SWE"):
            ax.scatter([x], [y],
                       s=glyph_area(vals["SWE"], scale_max["SWE"],
                                    2400.0 * area_scale, 60.0 * area_scale),
                       marker="s", facecolor=C_GREEN, edgecolor="white",
                       linewidth=1.2, alpha=0.85, zorder=3)
        sto = vals.get("sto_Prop", 0.0) + vals.get("sto_H2O", 0.0)
        if sto > TOL:
            ax.scatter([x], [y],
                       s=glyph_area(sto, scale_max["sto"],
                                    900.0 * area_scale, 40.0 * area_scale),
                       marker="D", facecolor=C_YELLOW, edgecolor="white",
                       linewidth=1.0, alpha=0.95, zorder=5)


def facility_legend_handles(short=False):
    return [
        Line2D([], [], linestyle="", marker="s", markersize=10,
               markerfacecolor=C_GREEN, markeredgecolor="white",
               label="SWE" if short else "SWE (water extraction)"),
        Line2D([], [], linestyle="", marker="o", markersize=10,
               markerfacecolor=C_BLUE, markeredgecolor="white",
               label="DWE" if short else "DWE (electrolysis)"),
        Line2D([], [], linestyle="", marker="D", markersize=8,
               markerfacecolor=C_YELLOW, markeredgecolor="white",
               label="Storage"),
    ]


def draw_flows(ax, agg, flow_max):
    """Curved arrows per (tail, head, commodity); width ~ sqrt(mass)."""
    rad_by_comm = {"Prop": 0.18, "H2O": -0.18, "PL": 0.34}
    for (tail, head, comm), mass_t in sorted(agg.items(), key=lambda kv: -kv[1]):
        color = COMMODITY_COLOR.get(comm)
        if color is None:  # tanks etc. -> skip (small logistics returns)
            continue
        p0, p1 = np.array(NODE_POS[tail]), np.array(NODE_POS[head])
        lw = 0.6 + 5.5 * math.sqrt(mass_t / flow_max)
        arrow = FancyArrowPatch(
            p0, p1, connectionstyle=f"arc3,rad={rad_by_comm[comm]}",
            arrowstyle="-|>", mutation_scale=8 + 2.2 * lw,
            linewidth=lw, color=color, alpha=0.75,
            shrinkA=10, shrinkB=12, zorder=8)
        ax.add_patch(arrow)


# ------------------------------------------------------------- figures -----
def plot_case_maps(df, sols):
    out = OUT_DIR / "maps"
    out.mkdir(parents=True, exist_ok=True)

    # shared scales so maps are comparable across cases
    scale_max = {"SWE": TOL, "DWE": TOL, "sto": TOL}
    flow_max = TOL
    for sol in sols.values():
        fac = facility_table(sol)
        for vals in fac.values():
            scale_max["SWE"] = max(scale_max["SWE"], vals.get("SWE", 0.0))
            scale_max["DWE"] = max(scale_max["DWE"], vals.get("DWE", 0.0))
            scale_max["sto"] = max(scale_max["sto"],
                                   vals.get("sto_Prop", 0.0) + vals.get("sto_H2O", 0.0))
        agg = aggregate_flows(sol)
        if agg:
            flow_max = max(flow_max, max(agg.values()))

    paths = []
    for _, row in df.iterrows():
        case = row["case"]
        sol = sols[case]
        fac = facility_table(sol)
        agg = aggregate_flows(sol)

        fig, ax = plt.subplots(figsize=(10.5, 5.4))
        draw_basemap(ax, network_edges(sol))
        draw_facilities(ax, fac, scale_max)
        draw_flows(ax, agg, flow_max)

        flow_handles = [Line2D([], [], color=c, linewidth=2.5, label=k)
                        for k, c in COMMODITY_COLOR.items()]
        leg = ax.legend(handles=flow_handles, loc="upper left", frameon=False,
                        fontsize=8, title="flow (width $\\propto$ mass)",
                        title_fontsize=8)
        ax.add_artist(leg)
        ax.legend(handles=facility_legend_handles(), loc="lower left",
                  frameon=False, fontsize=8,
                  title="marker size $\\propto$ capacity", title_fontsize=8)

        ax.set_title(
            f"{int(row['mission_years'])}-yr horizon, "
            f"{int(row['demand_t_yr'])} t/yr GEO demand, "
            f"{int(row['period_days'])}d cadence   "
            f"(cost {row['cost_musd_per_t']:,.3f} MUSD/t, "
            f"lunar prop share {1 - row['earth_frac']:.0%})",
            fontsize=11)
        fig.tight_layout()
        p = out / f"depot_map_{case}.png"
        fig.savefig(p, bbox_inches="tight")
        plt.close(fig)
        paths.append(p)
    return paths


def plot_map_grid(df, sols, period):
    """df must already be filtered to a single period."""
    years = sorted(df["mission_years"].unique())
    demands = sorted(df["demand_t_yr"].unique())

    scale_max = {"SWE": TOL, "DWE": TOL, "sto": TOL}
    for sol in sols.values():
        for vals in facility_table(sol).values():
            scale_max["SWE"] = max(scale_max["SWE"], vals.get("SWE", 0.0))
            scale_max["DWE"] = max(scale_max["DWE"], vals.get("DWE", 0.0))
            scale_max["sto"] = max(scale_max["sto"],
                                   vals.get("sto_Prop", 0.0) + vals.get("sto_H2O", 0.0))

    fig, axes = plt.subplots(len(years), len(demands),
                             figsize=(3.1 * len(demands), 1.85 * len(years)))
    for i, y in enumerate(years):
        for j, d in enumerate(demands):
            ax = axes[i][j]
            sel = df[(df["mission_years"] == y) & (df["demand_t_yr"] == d)]
            if sel.empty:
                ax.axis("off")
                continue
            case = sel.iloc[0]["case"]
            sol = sols[case]
            draw_basemap(ax, network_edges(sol), label_size=5.5, node_size=5)
            draw_facilities(ax, facility_table(sol), scale_max, area_scale=0.22)
            if i == 0:
                ax.set_title(f"{int(d)} t/yr", fontsize=10)
            if j == 0:
                ax.text(-0.06, 0.5, f"{int(y)} yr", transform=ax.transAxes,
                        rotation=90, va="center", ha="right", fontsize=10,
                        fontweight="bold")
    fig.legend(handles=facility_legend_handles(short=True), loc="lower center",
               ncol=3, frameon=False,
               fontsize=10, bbox_to_anchor=(0.5, -0.015))
    fig.suptitle(f"Facility placement across the sweep — {int(period)}d cadence "
                 "(marker size $\\propto$ capacity)",
                 fontsize=13, y=1.005)
    fig.tight_layout()
    p = OUT_DIR / f"depot_map_grid_p{int(period)}.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


def _case_axis(df):
    """Case ordering + labels grouped by horizon."""
    order = df.sort_values(["mission_years", "demand_t_yr"])
    cases = list(order["case"])
    labels = [f"{int(r.demand_t_yr)}" for r in order.itertuples()]
    groups = []  # (start, end, label) index ranges per horizon
    for y, grp in order.groupby("mission_years", sort=True):
        idx = [cases.index(c) for c in grp["case"]]
        groups.append((min(idx), max(idx), f"{int(y)} yr"))
    return cases, labels, groups


def plot_facility_heatmap(df, sols, period):
    """df must already be filtered to a single period."""
    cases, labels, groups = _case_axis(df)
    nodes = NODE_ORDER[::-1]  # Moon top row, GTO bottom row

    panels = [
        ("DWE capacity (t)", lambda v: v.get("DWE", 0.0)),
        ("Prop storage capacity (t)", lambda v: v.get("sto_Prop", 0.0)),
        ("H2O storage capacity (t)", lambda v: v.get("sto_H2O", 0.0)),
    ]
    fig, axes = plt.subplots(len(panels), 1, figsize=(11.5, 8.6), sharex=True)

    for ax, (title, getter) in zip(axes, panels):
        M = np.zeros((len(nodes), len(cases)))
        for j, case in enumerate(cases):
            fac = facility_table(sols[case])
            for i, node in enumerate(nodes):
                M[i, j] = getter(fac[node])
        vmax = M.max() if M.max() > 0 else 1.0
        im = ax.imshow(M, aspect="auto", cmap="Blues", vmin=0, vmax=vmax)
        ax.set_yticks(range(len(nodes)), nodes, fontsize=9)
        ax.set_xticks(range(len(cases)), labels, fontsize=8)
        ax.grid(False)
        for i in range(len(nodes)):
            for j in range(len(cases)):
                if M[i, j] >= 0.05:
                    dark = M[i, j] > 0.6 * vmax
                    ax.text(j, i, f"{M[i, j]:.1f}", ha="center", va="center",
                            fontsize=7, color="white" if dark else "#1a3a5c")
        first_panel = ax is axes[0]
        for s, e, glabel in groups:
            if s > 0:
                ax.axvline(s - 0.5, color="#666666", linewidth=1.0)
            if first_panel:
                ax.text((s + e) / 2, -0.9, glabel, ha="center", va="bottom",
                        fontsize=9, fontweight="bold")
        ax.set_title(title, fontsize=11, loc="left",
                     pad=30 if first_panel else 8)
        fig.colorbar(im, ax=ax, fraction=0.025, pad=0.01)

    axes[-1].set_xlabel("GEO demand (t/yr), grouped by mission horizon", fontsize=10)
    fig.suptitle(f"Where does the optimizer place capacity?  ({int(period)}d cadence)",
                 fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    p = OUT_DIR / f"facility_heatmap_p{int(period)}.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


def plot_depot_ranking(df, sols):
    nodes = NODE_ORDER
    n_cases = len(df)
    freq = {k: defaultdict(int) for k in ("DWE", "sto")}
    cap = {k: defaultdict(list) for k in ("DWE", "sto")}
    for case in df["case"]:
        fac = facility_table(sols[case])
        for node, vals in fac.items():
            if vals.get("DWE", 0.0) > TOL:
                freq["DWE"][node] += 1
                cap["DWE"][node].append(vals["DWE"])
            sto = vals.get("sto_Prop", 0.0) + vals.get("sto_H2O", 0.0)
            if sto > TOL:
                freq["sto"][node] += 1
                cap["sto"][node].append(sto)

    ypos = np.arange(len(nodes))  # GTO bottom, Moon top
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.5, 4.2), sharey=True)

    h = 0.38
    ax1.barh(ypos + h / 2, [100 * freq["DWE"][n] / n_cases for n in nodes],
             height=h, color=C_BLUE, label="DWE")
    ax1.barh(ypos - h / 2, [100 * freq["sto"][n] / n_cases for n in nodes],
             height=h, color=C_YELLOW, label="Storage")
    ax1.set_yticks(ypos, nodes)
    ax1.set_xlabel(f"% of cases selected  (n={n_cases})")
    ax1.set_title("How often is each node used?", loc="left", fontsize=11)
    ax1.legend(frameon=False, fontsize=9)
    ax1.set_xlim(0, 105)

    def _mean(vals):
        return np.mean(vals) if vals else 0.0

    ax2.barh(ypos + h / 2, [_mean(cap["DWE"][n]) for n in nodes],
             height=h, color=C_BLUE)
    ax2.barh(ypos - h / 2, [_mean(cap["sto"][n]) for n in nodes],
             height=h, color=C_YELLOW)
    ax2.set_xlabel("mean installed capacity when selected (t)")
    ax2.set_title("How big, when it is used?", loc="left", fontsize=11)

    for ax in (ax1, ax2):
        ax.grid(axis="y", visible=False)
    fig.suptitle("Depot suitability by node (all sweep cases)", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    p = OUT_DIR / "depot_ranking.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


def plot_capacity_vs_demand(df, sols):
    years = sorted(df["mission_years"].unique())

    periods = sorted(df["period_days"].unique())
    metrics = {
        "SWE capacity (t)": lambda fac: sum(v.get("SWE", 0.0) for v in fac.values()),
        "DWE @ Moon (t)": lambda fac: fac["Moon"].get("DWE", 0.0),
        "DWE @ LLO (t)": lambda fac: fac["LLO"].get("DWE", 0.0),
        "Total storage (t)": lambda fac: sum(
            v.get("sto_Prop", 0.0) + v.get("sto_H2O", 0.0) for v in fac.values()),
    }
    fig, axes = plt.subplots(1, len(metrics), figsize=(3.2 * len(metrics), 3.4),
                             sharex=True)
    for ax, (title, getter) in zip(axes, metrics.items()):
        for y in years:
            for p_days in periods:
                sub = df[(df["mission_years"] == y)
                         & (df["period_days"] == p_days)].sort_values("demand_t_yr")
                if sub.empty:
                    continue
                vals = [getter(facility_table(sols[c])) for c in sub["case"]]
                ax.plot(sub["demand_t_yr"], vals, marker=HORIZON_MARKERS[y],
                        markersize=5, linewidth=2, alpha=0.85,
                        color=HORIZON_COLORS[y], linestyle=period_linestyle(p_days))
        ax.set_title(title, fontsize=10, loc="left")
        ax.set_xlabel("GEO demand (t/yr)")
        ax.set_xticks(sorted(df["demand_t_yr"].unique()))
    axes[0].set_ylabel("installed capacity (t)")
    handles = [Line2D([], [], color=HORIZON_COLORS[y], marker=HORIZON_MARKERS[y],
                      linewidth=2, label=f"{int(y)} yr") for y in years]
    handles += [Line2D([], [], color="#555555", linestyle=period_linestyle(p),
                       linewidth=2, label=f"{int(p)}d cadence") for p in periods]
    axes[-1].legend(handles=handles, frameon=False, fontsize=9,
                    loc="center left", bbox_to_anchor=(1.02, 0.5))
    fig.suptitle("Installed ISRU capacity vs demand "
                 "(color = horizon, linestyle = cadence)", fontsize=13)
    fig.tight_layout(rect=(0, 0, 0.97, 0.93))
    p = OUT_DIR / "capacity_vs_demand.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


def plot_cost_breakdown(df, sols, period):
    """df must already be filtered to a single period."""
    years = sorted(df["mission_years"].unique())
    comps = [
        ("SWE", lambda cb: cb["SWE"], C_GREEN),
        ("DWE", lambda cb: cb["DWE"], C_BLUE),
        ("Storage", lambda cb: cb["storage"], C_YELLOW),
        (
            "Infrastructure to GTO",
            lambda cb: cb["infrastructure_to_GTO"],
            C_AQUA,
        ),
        ("Spacecraft", lambda cb: cb["spacecraft"], C_VIOLET),
        ("Earth propellant", lambda cb: cb["earth_prop"], C_RED),
        ("Maintenance", lambda cb: cb["maintenance"], C_GRAY),
        ("Unserved penalty", lambda cb: cb["unserved_penalty"], C_ORANGE),
    ]
    fig, axes = plt.subplots(1, len(years), figsize=(3.1 * len(years), 4.0),
                             sharey=True)
    for ax, y in zip(axes, years):
        sub = df[df["mission_years"] == y].sort_values("demand_t_yr")
        x = np.arange(len(sub))
        bottom = np.zeros(len(sub))
        for label, getter, color in comps:
            vals = np.array([
                getter(sols[c]["cost_breakdown_musd"]) / p
                for c, p in zip(sub["case"], sub["total_payload_t"])])
            ax.bar(x, vals, bottom=bottom, color=color,
                   width=0.7, label=label, edgecolor="white", linewidth=0.5)
            bottom += vals
        for xi, tot in zip(x, bottom):
            ax.text(xi, tot + 0.4, f"{tot:.2f}",
                    ha="center", fontsize=8)
        ax.set_xticks(x, [f"{int(d)}" for d in sub["demand_t_yr"]])
        ax.set_title(f"{int(y)}-yr horizon", fontsize=10)
        ax.set_xlabel("demand (t/yr)")
        ax.grid(axis="x", visible=False)
    axes[0].set_ylabel("cost per delivered tonne [MUSD/t]")
    axes[-1].legend(frameon=False, fontsize=8, loc="upper right")
    fig.suptitle(f"Cost composition per delivered tonne ({int(period)}d cadence)",
                 fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    p = OUT_DIR / f"cost_breakdown_musd_per_t_p{int(period)}.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df, sols = load_cases()
    paths = []
    for period in sorted(df["period_days"].unique()):
        sub = df[df["period_days"] == period]
        paths.append(plot_map_grid(sub, sols, period))
        paths.append(plot_facility_heatmap(sub, sols, period))
        paths.append(plot_cost_breakdown(sub, sols, period))
    paths.append(plot_depot_ranking(df, sols))
    paths.append(plot_capacity_vs_demand(df, sols))
    paths.extend(plot_case_maps(df, sols))
    for p in paths:
        print(f"saved {p}")


if __name__ == "__main__":
    main()
