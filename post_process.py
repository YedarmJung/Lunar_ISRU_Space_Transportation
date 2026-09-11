import json
import math
from pathlib import Path

from units import MODEL_UNITS, SOLUTION_SCHEMA_VERSION


RESULT_DIR = Path("results/lns2_20260904_150738")
LEGACY_SOLUTION = RESULT_DIR / "latest_solution.json"
LEGACY_PLOT_DIR = RESULT_DIR / "plots"

# top-to-bottom order for the time-expanded plot (Earth cluster -> Moon cluster)
NODE_ORDER = ["GTO", "GEO", "EML1", "NRHO", "LLO", "Moon"]

# 2-D positions for the network flow map
NODE_POS = {
    "GTO": (1.2, 1.0),
    "GEO": (1.2, -1.0),
    "EML1": (3.0, 0.0),
    "NRHO": (4.2, 1.0),
    "LLO": (4.2, -1.0),
    "Moon": (5.4, 0.0),
}

TOL = 1e-6
MIN_FLOW_T = 0.001  # 1 kg, expressed in the model's tonne unit
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
    for path in generate_plots():
        print(f"saved {path}")


def _latest_solution_path():
    candidates = sorted(RESULT_DIR.glob("plots_*/latest_solution.json"))
    if candidates:
        return candidates[-1]
    if LEGACY_SOLUTION.exists():
        return LEGACY_SOLUTION
    raise FileNotFoundError(
        f"No latest_solution.json found under {RESULT_DIR.resolve()}"
    )


def generate_plots(solution_path=None, plot_dir=None):
    _load_matplotlib()

    solution_path = (
        _latest_solution_path() if solution_path is None else Path(solution_path)
    )
    if plot_dir is None:
        plot_dir = (
            solution_path.parent
            if solution_path.parent.name.startswith("plots_")
            else LEGACY_PLOT_DIR
        )
    plot_dir = Path(plot_dir)
    plot_dir.mkdir(parents=True, exist_ok=True)

    with solution_path.open("r", encoding="utf-8") as f:
        solution = json.load(f)
    _require_canonical_units(solution)

    makers = [
        plot_flow_over_time,
        plot_infrastructure_flow_over_time,
        plot_network_flow_map,
        plot_cost_breakdown,
        plot_cost_share,
        plot_demand_profile,
        plot_infrastructure,
        plot_production,
    ]
    paths = [maker(solution, plot_dir) for maker in makers]
    return [str(p) for p in paths if p is not None]


def _load_matplotlib():
    global plt, Line2D
    if "plt" in globals():
        return

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    plt.rcParams["font.family"] = "Calibri"


def generate_flow_plot(solution, plot_dir, filename="flow_over_time.png"):
    """Generate only the time-expanded flow plot from an in-memory solution."""
    _require_canonical_units(solution)
    _load_matplotlib()
    plot_dir = Path(plot_dir)
    plot_dir.mkdir(parents=True, exist_ok=True)
    path = plot_flow_over_time(solution, plot_dir, filename=filename)
    return str(path) if path is not None else None


def _require_canonical_units(solution):
    """Fail clearly instead of silently plotting a legacy kg/USD solution."""
    if solution.get("schema_version") != SOLUTION_SCHEMA_VERSION:
        raise ValueError(
            "Solution uses a legacy unit schema. Re-run the optimizer to create "
            "a schema-v2 solution in t and MUSD before plotting it."
        )
    if solution.get("units") != MODEL_UNITS:
        raise ValueError(
            f"Unexpected solution units: {solution.get('units')!r}; "
            f"expected {MODEL_UNITS!r}."
        )


def _ordered_nodes(nodes):
    ordered = [n for n in NODE_ORDER if n in nodes]
    ordered += [n for n in nodes if n not in ordered]
    return ordered


def _color_map(commodities):
    return {k: plt.cm.tab10.colors[i % 10] for i, k in enumerate(commodities)}


def plot_flow_over_time(solution, plot_dir, filename="flow_over_time.png"):
    nodes = _ordered_nodes(solution["nodes"])
    # include BOTH move and hold arcs (hold = inventory sitting at a node);
    # drop near-zero numerical-noise flows (loose-gap artifacts) so phantom routes vanish.
    flows = [f for f in solution["flows"] if f["departed"] > MIN_FLOW_T]
    if not flows:
        return None

    # Earth-nearest orbit at the bottom, Moon at the top
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
    service_by_event = {
        (event["year"], event["event_id"]): event["served"]
        for event in solution.get("satellite_service", [])
    }
    max_mass = max((event["mass_t"] for event in events), default=1.0)
    if "GEO" in y:
        for event in events:
            size = 140 + 300 * event["mass_t"] / max_mass
            served = service_by_event.get(
                (event["year"], event["event_id"]), True
            )
            if served:
                ax.scatter(event["demand_step"], y["GEO"], marker="*", s=size,
                           color="crimson", edgecolor="black", linewidth=0.6, zorder=6)
            else:
                ax.scatter(event["demand_step"], y["GEO"], marker="x", s=size * 0.7,
                           color="0.55", linewidth=1.4, zorder=6)
    if "GTO" in y:
        for event in events:
            if not service_by_event.get(
                (event["year"], event["event_id"]), True
            ):
                continue
            size = 140 + 300 * event["mass_t"] / max_mass
            ax.scatter(event["supply_step"], y["GTO"], marker="*", s=size, color="gold",
                       edgecolor="black", linewidth=0.6, zorder=6)

    ax.set_yticks([y[node] for node in nodes])
    ax.set_yticklabels(nodes)
    ax.set_xlabel(
        f"mission year (time step = {mis['days_per_step']} days)"
    )
    ax.set_ylabel("node")
    ax.set_title(solution.get("plot_title", "Commodity flow over time"), pad=36)

    cats_present = [c for c in CAT_STYLE if any(_cat(f) == c for f in flows)]
    handles = [Line2D([0], [0], color=CAT_STYLE[c][0], lw=3, linestyle=CAT_STYLE[c][1], label=c)
               for c in cats_present]
    handles += [
        Line2D([0], [0], marker="*", color="w", markerfacecolor="gold",
               markeredgecolor="black", markersize=15, label="PL supply @GTO"),
        Line2D([0], [0], marker="*", color="w", markerfacecolor="crimson",
               markeredgecolor="black", markersize=15, label="PL demand @GEO"),
    ]
    if any(not served for served in service_by_event.values()):
        handles.append(
            Line2D([0], [0], marker="x", color="0.55", linestyle="None",
                   markersize=10, label="unserved satellite")
        )
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

    path = plot_dir / filename
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def plot_infrastructure_flow_over_time(solution, plot_dir):
    """Plot transported Infra, fleet additions, and installed asset stocks."""
    mission = solution["mission"]
    mission_years = mission["mission_years"]
    if mission["days_per_year"] % mission["days_per_step"]:
        raise ValueError("days_per_year must be divisible by days_per_step")
    steps_per_year = mission["days_per_year"] // mission["days_per_step"]
    T = solution["T"]

    category_style = {
        "SWE": {"color": COST_COLORS["SWE"], "offset": -0.18},
        "DWE": {"color": COST_COLORS["DWE"], "offset": -0.06},
        "H2O tank": {"color": COST_COLORS["storage"], "offset": 0.06},
        "Prop tank": {"color": PROP_TANK_COLOR, "offset": 0.18},
    }

    def annual_values(info, annual_key, final_key):
        annual = info.get(annual_key)
        if annual:
            return [float(annual.get(str(year), 0.0))
                    for year in range(1, mission_years + 1)]
        # Compatibility with older result files that only reported final stock.
        final = float(info.get(final_key, 0.0))
        return [final] * mission_years

    stock = {}

    def add_stock(node, category, values):
        if max(values, default=0.0) > TOL:
            stock.setdefault(node, {})[category] = values

    swe = solution.get("facilities", {}).get("SWE", {})
    add_stock(
        swe.get("node", "Moon"),
        "SWE",
        annual_values(swe, "capacity_by_year", "q"),
    )
    for node, info in solution.get("facilities", {}).get("DWE", {}).items():
        add_stock(node, "DWE", annual_values(info, "capacity_by_year", "q"))
    for node, info in solution.get("storage", {}).items():
        add_stock(node, "H2O tank", annual_values(info, "H2O_by_year", "H2O"))
        add_stock(node, "Prop tank", annual_values(info, "Prop_by_year", "Prop"))

    # Portable tanks are delivered as generic Infra, installed at Moon, and
    # then become mobile H2O_Tank / Prop_Tank commodities.  Show their annual
    # installation events without treating them as persistent fixed-facility
    # bands at Moon.
    portable_tank_additions = {}
    first_tank_by_year = solution.get("first_tank", {}).get("by_year", {})
    portable_tank_names = {
        "H2O_Tank": "Portable H2O tank",
        "Prop_Tank": "Portable Prop tank",
    }
    for year_index in range(mission_years):
        annual = first_tank_by_year.get(str(year_index + 1), {})
        breakdown = {
            label: float(annual.get(tank, 0.0))
            for tank, label in portable_tank_names.items()
            if float(annual.get(tank, 0.0)) > TOL
        }
        if breakdown:
            portable_tank_additions["Moon", year_index] = breakdown

    infra_flows = [
        flow for flow in solution.get("flows", [])
        if flow["commodity"] == "Infra" and flow["departed"] > MIN_FLOW_T
    ]
    fleet_additions = solution.get("fleet_additions_by_year", {})
    supply_by_time = (
        solution.get("infrastructure_resupply", {}).get("by_time", {})
    )

    relevant_nodes = set(stock)
    for flow in infra_flows:
        relevant_nodes.update((flow["tail"], flow["head"]))
    for annual in fleet_additions.values():
        if annual.get("OTV", 0):
            relevant_nodes.add("GTO")
        if annual.get("RT", 0):
            relevant_nodes.add("Moon")
    if portable_tank_additions:
        relevant_nodes.add("Moon")
    if not relevant_nodes:
        return None

    nodes = [node for node in NODE_ORDER if node in relevant_nodes]
    nodes += sorted(relevant_nodes - set(nodes))
    lane_spacing = 1.25
    deploy_y = 0.0
    y = {node: (index + 1) * lane_spacing for index, node in enumerate(nodes)}

    all_stock_values = [
        value
        for categories in stock.values()
        for values in categories.values()
        for value in values
        if value > TOL
    ]
    max_stock = max(all_stock_values, default=MIN_FLOW_T)
    max_addition = MIN_FLOW_T
    additions = {}
    addition_breakdown = {}
    for node, categories in stock.items():
        for year_index in range(mission_years):
            total = 0.0
            breakdown = {}
            for category, values in categories.items():
                previous = values[year_index - 1] if year_index else 0.0
                addition = max(0.0, values[year_index] - previous)
                if addition > TOL:
                    breakdown[category] = addition
                    total += addition
            if total > TOL:
                additions[node, year_index] = total
                addition_breakdown[node, year_index] = breakdown
                max_addition = max(max_addition, total)

    for key, breakdown in portable_tank_additions.items():
        addition_breakdown.setdefault(key, {}).update(breakdown)
        additions[key] = additions.get(key, 0.0) + sum(breakdown.values())
    max_addition = max([MIN_FLOW_T, *additions.values()])

    fig, ax = plt.subplots(figsize=(18, 10))
    ax.axhline(deploy_y, color="0.78", lw=0.8, zorder=0)
    for node in nodes:
        ax.axhline(y[node], color="0.9", lw=0.8, zorder=0)
    for boundary in range(mission_years + 1):
        time = boundary * steps_per_year
        ax.axvline(time, color="0.82", ls=":", lw=0.9, zorder=0)

    # Persistent facility state: each colored segment covers the year in which
    # that stock is available; line thickness is proportional to sqrt(dry mass).
    for node, categories in stock.items():
        for category, values in categories.items():
            style = category_style[category]
            yy = y[node] + style["offset"]
            for year_index, value in enumerate(values):
                if value <= TOL:
                    continue
                x0 = year_index * steps_per_year
                x1 = min((year_index + 1) * steps_per_year, T - 1)
                linewidth = 1.2 + 7.2 * math.sqrt(value / max_stock)
                ax.plot(
                    [x0, x1], [yy, yy],
                    color=style["color"], lw=linewidth, alpha=0.72,
                    solid_capstyle="butt", zorder=2,
                )

    max_flow = max(
        (flow["departed"] for flow in infra_flows), default=MIN_FLOW_T
    )
    # Held infrastructure stays visible but subdued behind actual move arcs.
    for flow in infra_flows:
        if flow["kind"] != "hold":
            continue
        linewidth = 0.8 + 3.2 * math.sqrt(flow["departed"] / max_flow)
        ax.plot(
            [flow["time"], flow["arrival_time"]],
            [y[flow["tail"]], y[flow["head"]]],
            color="0.25", ls=":", lw=linewidth, alpha=0.35,
            solid_capstyle="round", zorder=3,
        )

    # RT infrastructure moves, with labels in tonnes.
    route_label_index = {}
    for flow in infra_flows:
        if flow["kind"] != "move":
            continue
        linewidth = 1.2 + 4.2 * math.sqrt(flow["departed"] / max_flow)
        ax.annotate(
            "",
            xy=(flow["arrival_time"], y[flow["head"]]),
            xytext=(flow["time"], y[flow["tail"]]),
            arrowprops={
                "arrowstyle": "-|>",
                "color": "0.08",
                "lw": linewidth,
                "alpha": 0.78,
                "mutation_scale": 9 + 2 * linewidth,
                "shrinkA": 2,
                "shrinkB": 2,
            },
            zorder=5,
        )
        midpoint_x = (flow["time"] + flow["arrival_time"]) / 2
        midpoint_y = (y[flow["tail"]] + y[flow["head"]]) / 2
        route = (flow["tail"], flow["head"])
        label_index = route_label_index.get(route, 0)
        route_label_index[route] = label_index + 1
        label_offset = 0.12 if label_index % 2 == 0 else -0.12
        ax.text(
            midpoint_x + 0.12, midpoint_y + label_offset,
            f"{flow['departed']:.1f} t"
            if flow["departed"] >= 10.0
            else f"{flow['departed']:.2f} t",
            fontsize=7, color="0.12", ha="left",
            va="bottom" if label_offset > 0 else "top",
            bbox={"boxstyle": "round,pad=0.12", "fc": "white",
                  "ec": "none", "alpha": 0.72},
            zorder=7,
        )

    # Earth-to-GTO infrastructure supply and annual spacecraft additions.
    if "GTO" in y:
        for raw_time, raw_mass in supply_by_time.items():
            time, mass = int(raw_time), float(raw_mass)
            if mass <= TOL:
                continue
            ax.annotate(
                "",
                xy=(time, y["GTO"] - 0.02),
                xytext=(time - 0.75, deploy_y + 0.04),
                arrowprops={"arrowstyle": "-|>", "color": "goldenrod",
                            "lw": 2.2, "ls": "--", "mutation_scale": 12},
                zorder=4,
            )
            ax.text(
                time - 0.82, deploy_y - 0.08, f"Infra {mass:.2f} t",
                fontsize=7, color="darkgoldenrod", ha="right", va="top",
            )

    vehicle_style = {
        "OTV": {"target": "GTO", "color": "mediumpurple",
                "offset": -2.05, "label_y": 0.07},
        "RT": {"target": "Moon", "color": "tab:red",
               "offset": -0.65, "label_y": 0.25},
    }
    for raw_year, annual in fleet_additions.items():
        year = int(raw_year)
        deployment_time = (year - 1) * steps_per_year
        for vehicle, style in vehicle_style.items():
            count = int(round(annual.get(vehicle, 0)))
            target = style["target"]
            if count <= 0 or target not in y:
                continue
            origin_x = deployment_time + style["offset"]
            ax.annotate(
                "",
                xy=(deployment_time, y[target]),
                xytext=(origin_x, deploy_y),
                arrowprops={"arrowstyle": "-|>", "color": style["color"],
                            "lw": 1.8, "ls": "--", "mutation_scale": 12,
                            "alpha": 0.9},
                zorder=4,
            )
            ax.text(
                origin_x, deploy_y + style["label_y"], f"{vehicle} +{count}",
                fontsize=8, color=style["color"], ha="center", va="bottom",
            )

    # Installation events and final stock labels make every selected facility
    # visible, including nodes that never receive a post-initial Infra shipment.
    for (node, year_index), mass in additions.items():
        time = year_index * steps_per_year
        is_initial = year_index == 0
        size = 28 + 90 * math.sqrt(mass / max_addition)
        ax.scatter(
            time, y[node], s=size,
            marker="o" if is_initial else "D",
            facecolor="white" if is_initial else "0.12",
            edgecolor="0.12", linewidth=1.0, zorder=8,
        )
        if is_initial:
            label = f"initial {mass:.2f} t"
        else:
            short_name = {
                "SWE": "SWE",
                "DWE": "DWE",
                "H2O tank": "H2O",
                "Prop tank": "Prop",
                "Portable H2O tank": "Portable H2O",
                "Portable Prop tank": "Portable Prop",
            }
            detail = "\n".join(
                f"{short_name[category]} {value:.2f} t"
                for category, value in addition_breakdown[node, year_index].items()
            )
            label = f"+{mass:.2f} t\n{detail}"
        ax.annotate(
            label, (time, y[node]),
            xytext=(4, 7), textcoords="offset points",
            fontsize=7, color="0.15", ha="left", va="bottom",
            bbox={"boxstyle": "round,pad=0.12", "fc": "white",
                  "ec": "none", "alpha": 0.68},
            zorder=9,
        )

    summary_x = T - 1 + 2.5
    for node, categories in stock.items():
        parts = [
            f"{category} {values[-1]:.2f} t"
            for category, values in categories.items()
            if values[-1] > TOL
        ]
        if parts:
            ax.text(
                summary_x, y[node], " | ".join(parts),
                fontsize=7.5, color="0.2", ha="left", va="center",
            )

    legend_handles = [
        Line2D([0], [0], color=style["color"], lw=5, label=category)
        for category, style in category_style.items()
        if any(category in categories for categories in stock.values())
    ]
    legend_handles += [
        Line2D([0], [0], color="0.08", lw=3, marker=">", label="Infra move (RT)"),
        Line2D([0], [0], color="0.25", lw=2, ls=":", label="Infra hold"),
        Line2D([0], [0], color="goldenrod", lw=2, ls="--", label="Infra supply to GTO"),
        Line2D([0], [0], color="mediumpurple", lw=2, ls="--", label="OTV addition"),
        Line2D([0], [0], color="tab:red", lw=2, ls="--", label="RT addition"),
        Line2D([0], [0], marker="D", color="0.12", lw=0,
               markerfacecolor="0.12", label="Installed hardware addition"),
    ]
    ax.legend(
        handles=legend_handles, loc="lower center", bbox_to_anchor=(0.5, 1.01),
        ncol=5, fontsize=8, frameon=False,
    )

    year_ticks = [year * steps_per_year for year in range(mission_years + 1)]
    year_labels = [f"Y{year + 1}" for year in range(mission_years)] + ["End"]
    ax.set_xticks(year_ticks)
    ax.set_xticklabels(year_labels)
    ax.set_yticks([deploy_y] + [y[node] for node in nodes])
    ax.set_yticklabels(["Earth supply / fleet"] + nodes)
    ax.set_xlim(-2.5, T - 1 + 27)
    ax.set_ylim(-0.45, max(y.values()) + 0.72)
    ax.set_xlabel(
        f"mission time (1 step = {mission['days_per_step']} days; "
        "facility bands show stock available during each year)"
    )
    ax.set_ylabel("node")
    ax.set_title("Infrastructure delivery and installed-facility evolution", pad=52)
    ax.grid(axis="x", color="0.94", lw=0.7)
    status = solution.get("status")
    gap = solution.get("mip_gap")
    if status == 2:
        solution_note = "optimal solution"
    elif gap is not None:
        solution_note = f"solver incumbent (status {status}, MIP gap {gap:.2%})"
    else:
        solution_note = f"solver incumbent (status {status})"
    fig.text(
        0.5, 0.012,
        "Band/marker size scales with square root of dry hardware mass; "
        "portable tanks appear in Moon installation markers, not fixed-stock bands; "
        f"labels at right show final fixed stock; {solution_note}.",
        ha="center", va="bottom", fontsize=8, color="0.35",
    )
    fig.tight_layout(rect=(0, 0.035, 1, 0.94))

    path = plot_dir / "infrastructure_flow_over_time.png"
    fig.savefig(path, dpi=180, bbox_inches="tight")
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
            (x0 + x1) / 2, (y0 + y1) / 2, _tonnes(value),
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
    summary_keys = {"total", "physical_total", "objective_total"}
    costs = {
        name: value
        for name, value in solution["cost_breakdown_musd"].items()
        if name not in summary_keys and abs(value) > TOL
    }
    if not costs:
        return None

    labels = list(costs)
    values = [costs[label] for label in labels]

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.bar(range(len(labels)), values, color="slateblue")
    objective_total = solution["cost_breakdown_musd"].get(
        "objective_total", solution["cost_breakdown_musd"]["total"]
    )
    ax.set_title(f"Objective cost breakdown (total = {_money(objective_total)})")
    ax.set_ylabel("cost [MUSD]")
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
    breakdown = solution.get("cost_breakdown_musd", {})
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
    events = solution.get("satellite_service", mission.get("demand_events", []))
    if not events:
        return None

    days_per_year = mission["days_per_year"]
    mission_years = mission["mission_years"]
    event_year = [event["demand_day"] / days_per_year for event in events]
    event_tonnes = [event["mass_t"] for event in events]
    served = [event.get("served", True) for event in events]
    candidate_annual_tonnes = [
        mission["demand_by_year_t"].get(str(year), 0.0)
        for year in range(1, mission_years + 1)
    ]
    served_annual_tonnes = [
        solution.get("lifecycle", {}).get("demand_by_year_t", {}).get(
            str(year), candidate_annual_tonnes[year - 1]
        )
        for year in range(1, mission_years + 1)
    ]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))
    for is_served, color, marker, label in [
        (True, "crimson", "o", "served"),
        (False, "0.55", "x", "unserved"),
    ]:
        xs = [x for x, selected in zip(event_year, served) if selected == is_served]
        ys = [mass for mass, selected in zip(event_tonnes, served) if selected == is_served]
        if xs:
            ax1.scatter(xs, ys, s=55, color=color, marker=marker, label=label)
            for x, mass in zip(xs, ys):
                ax1.vlines(x, 0, mass, color=color, alpha=0.25, lw=1)
    ax1.set_xlabel("mission year (360 days/year)")
    ax1.set_ylabel("satellite mass [t]")
    ax1.set_title("GEO satellite service decisions")
    ax1.set_xlim(0, mission_years)
    ax1.grid(color="0.92")
    ax1.legend(frameon=False)

    years = list(range(1, mission_years + 1))
    ax2.bar(years, candidate_annual_tonnes, color="0.82", label="candidate")
    ax2.bar(years, served_annual_tonnes, width=0.62, color="slateblue", label="served")
    ax2.set_xlabel("mission year")
    ax2.set_ylabel("annual payload [t]")
    ax2.set_title("Candidate and served payload")
    ax2.set_xticks(years)
    ax2.grid(axis="y", color="0.92")
    ax2.legend(frameon=False)
    for year, mass in zip(years, served_annual_tonnes):
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
    ax.set_ylabel("mass [t]")
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
    ax.set_ylabel("operated plant mass [t]")
    ax.legend(fontsize=8, ncol=min(4, len(facilities)))
    ax.grid(color="0.92")
    fig.tight_layout()

    path = plot_dir / "isru_production.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def _tonnes(value):
    if abs(value) >= 100.0:
        return f"{value:,.0f} t"
    if abs(value) >= 10.0:
        return f"{value:,.1f} t"
    return f"{value:,.2f} t"


def _money(value):
    if abs(value) >= 1.0:
        return f"{value:,.1f} MUSD"
    return f"{value:,.3f} MUSD"


if __name__ == "__main__":
    main()
