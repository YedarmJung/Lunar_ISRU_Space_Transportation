import json
import math
import shutil
from datetime import datetime
from pathlib import Path

from gurobipy import GRB

from data import get_data
from incumbent_flow_plot import IncumbentFlowPlotter
from model import build_model, solve_model
from post_process import generate_plots


RESULT_DIR = Path("results")
DEMAND_PROFILE_PATH = Path("inputs") / "demand_10yr_ramp_20to100t.json"
# Point this at a .mst produced by make_warm_start.py to warm start the solve.
# Leave it as None to start from scratch.
#WARM_START_PATH = Path("results/plots_20260815_143840/start.mst")
GUROBI_PARAMS = {
    "TimeLimit": 14 * 3600,
    "MIPGap": 0.025,
    "MIPFocus": 1,
    "Cuts": 2,
    "Symmetry": 2,
    "Heuristics": 0.2,
#    "Presolve" : 2,
}

TOL = 1e-6


def main():
    run_dir = _create_run_dir()
    solution_path = run_dir / "latest_solution.json"
    input_copy_path = run_dir / DEMAND_PROFILE_PATH.name
    shutil.copy2(DEMAND_PROFILE_PATH, input_copy_path)

    data = get_data(DEMAND_PROFILE_PATH)
    params = {**GUROBI_PARAMS, "LogFile": str(run_dir / "solver.log")}
    model, variables = build_model(data, gurobi_params=params)
    _load_warm_start(model, run_dir)
    incumbent_plotter = IncumbentFlowPlotter(data, variables, run_dir)
    try:
        solve_model(model, callback=incumbent_plotter)
    finally:
        incumbent_plotter.close()

    if model.Status == GRB.INFEASIBLE:
        print("\nINFEASIBLE: GEO payload demand cannot be met.")
        _write_iis(model, run_dir)
        return
    if model.SolCount == 0:
        print(
            f"\nSolver finished with status {model.Status}, but no integer feasible "
            "solution was found."
        )
        return

    solution = build_solution(data, model, variables)
    write_solution(solution, solution_path)

    label = "Optimal" if model.Status == GRB.OPTIMAL else "Best feasible"
    print(f"\n{label} lifecycle cost = {model.ObjVal:,.0f}")
    if model.Status != GRB.OPTIMAL:
        gap = f"{model.MIPGap:.2%}" if math.isfinite(model.MIPGap) else "not available"
        print(f"Solver status = {model.Status}, gap = {gap}")
    print(f"Run artifacts -> {run_dir}")
    print(f"Saved solution -> {solution_path}")
    print(f"Saved input copy -> {input_copy_path}")
    print_summary(solution)

    try:
        plot_paths = generate_plots(solution_path, run_dir)
    except ImportError as exc:
        print(f"\nPlots were not generated (matplotlib missing?): {exc}")
        return

    print("\nSaved plots:")
    for path in plot_paths:
        print(f"  {path}")


def _create_run_dir():
    """Create a Windows-safe, timestamped directory for one solver run."""
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = 0

    while True:
        suffix_text = "" if suffix == 0 else f"_{suffix:02d}"
        run_dir = RESULT_DIR / f"plots_{timestamp}{suffix_text}"
        try:
            run_dir.mkdir(exist_ok=False)
            return run_dir
        except FileExistsError:
            suffix += 1


def _year_for_step(mission, step):
    day = step * mission["days_per_step"]
    year = day // mission["days_per_year"] + 1
    return min(int(year), mission["mission_years"])


def build_solution(data, model, variables):
    x = variables["x"]
    xm = variables["xm"]
    y = variables["y"]

    flows = []
    for (commodity, mode, arc_id, time), gvar in x.items():
        value = gvar.X
        if value <= TOL:
            continue
        arc = data.arcs[arc_id]
        arrival_time = time + arc.tau
        flows.append(
            {
                "commodity": commodity,
                "mode": mode,
                "arc": arc_id,
                "tail": arc.tail,
                "head": arc.head,
                "kind": arc.kind,
                "tau": arc.tau,
                "time": time,
                "arrival_time": arrival_time,
                "departed": value,
                "arrived": (
                    xm[commodity, mode, arc_id, arrival_time].getValue()
                    if (commodity, mode, arc_id, arrival_time) in xm
                    else None
                ),
            }
        )

    trips = []
    for (mode, arc_id, time), gvar in y.items():
        count = gvar.X
        if count <= TOL:
            continue
        arc = data.arcs[arc_id]
        trips.append(
            {
                "mode": mode,
                "arc": arc_id,
                "tail": arc.tail,
                "head": arc.head,
                "time": time,
                "arrival_time": time + arc.tau,
                "count": int(round(count)),
            }
        )

    mission_years = data.mission["mission_years"]
    years = range(mission_years)
    final_year = mission_years - 1

    fleet_by_year = {
        str(year + 1): {
            name: int(round(variables["N_sc"][name, year].X))
            for name in data.vehicles
        }
        for year in years
    }
    fleet = {
        name: sum(fleet_by_year[str(year + 1)][name] for year in years)
        for name in data.vehicles
    }

    def stock_by_year(stock, name):
        return {
            str(year + 1): stock[name, year].X
            for year in years
        }

    def additions_by_year(stock, name):
        return {
            str(year + 1): (
                stock[name, year].X
                if year == 0
                else stock[name, year].X - stock[name, year - 1].X
            )
            for year in years
        }

    facilities = {
        "SWE": {
            "node": "Moon",
            "installed": variables["q"]["Moon_SWE", final_year].X > TOL,
            "q": variables["q"]["Moon_SWE", final_year].X,
            "capacity_by_year": stock_by_year(variables["q"], "Moon_SWE"),
            "additions_by_year": additions_by_year(variables["q"], "Moon_SWE"),
        },
        "DWE": {
            node: {
                "installed": variables["q"][node, final_year].X > TOL,
                "q": variables["q"][node, final_year].X,
                "capacity_by_year": stock_by_year(variables["q"], node),
                "additions_by_year": additions_by_year(variables["q"], node),
            }
            for node in data.depot_node
            if variables["q"][node, final_year].X > TOL
        },
    }
    storage = {
        node: {
            "H2O": variables["Storage_H2O"][node, final_year].X,
            "Prop": variables["Storage_Prop"][node, final_year].X,
            "H2O_by_year": stock_by_year(variables["Storage_H2O"], node),
            "Prop_by_year": stock_by_year(variables["Storage_Prop"], node),
            "H2O_additions_by_year": additions_by_year(
                variables["Storage_H2O"], node
            ),
            "Prop_additions_by_year": additions_by_year(
                variables["Storage_Prop"], node
            ),
        }
        for node in data.depot_node
        if (
            variables["Storage_H2O"][node, final_year].X > TOL
            or variables["Storage_Prop"][node, final_year].X > TOL
        )
    }
    production = [
        {"facility": facility, "time": time, "operated_mass": gvar.X}
        for (facility, time), gvar in variables["q_operation"].items()
        if gvar.X > TOL
    ]

    earth_prop_by_node = {
        node: sum(variables["earth_prop"][node, time].X for time in range(data.T))
        for node in ["GTO", "Moon"]
    }
    earth_prop_by_year = {
        str(year): sum(
            variables["earth_prop"][node, time].X
            for node in ["GTO", "Moon"]
            for time in range(data.T)
            if _year_for_step(data.mission, time) == year
        )
        for year in range(1, data.mission["mission_years"] + 1)
    }
    earth_prop = {
        "total": sum(earth_prop_by_node.values()),
        "by_node": earth_prop_by_node,
        "by_year": earth_prop_by_year,
    }
    first_tank_by_year = {
        str(year + 1): {
            tank: variables["first_Tank"][tank, year].X
            for tank in ["H2O_Tank", "Prop_Tank"]
        }
        for year in years
    }
    first_tank = {
        "total": {
            tank: sum(
                variables["first_Tank"][tank, year].X for year in years
            )
            for tank in ["H2O_Tank", "Prop_Tank"]
        },
        "by_year": first_tank_by_year,
    }
    infra_supply_by_time = {
        str(time): gvar.X
        for time, gvar in variables["infra_supply"].items()
    }
    infra_supply_by_year = {
        str(year): sum(
            gvar.X
            for time, gvar in variables["infra_supply"].items()
            if _year_for_step(data.mission, time) == year
        )
        for year in range(1, mission_years + 1)
    }
    steps_in_year = data.mission["days_per_year"] // data.mission["days_per_step"]
    resupply_lead_steps = steps_in_year // 2
    infra_supply_by_installation_year = {
        str((time + resupply_lead_steps) // steps_in_year + 1): gvar.X
        for time, gvar in variables["infra_supply"].items()
    }
    infrastructure_resupply = {
        "total_to_GTO": sum(gvar.X for gvar in variables["infra_supply"].values()),
        "by_time": infra_supply_by_time,
        "by_year": infra_supply_by_year,
        "by_installation_year": infra_supply_by_installation_year,
    }
    breakdown, lifecycle = build_cost_breakdown(data, variables)

    return {
        "status": int(model.Status),
        "objective": model.ObjVal,
        "mip_gap": model.MIPGap if math.isfinite(model.MIPGap) else None,
        "T": data.T,
        "nodes": data.nodes,
        "commodities": data.commodities,
        "modes": data.arc_type,
        "mission": data.mission,
        "arcs": [
            {
                "id": arc_id,
                "tail": arc.tail,
                "head": arc.head,
                "tau": arc.tau,
                "kind": arc.kind,
                "delta_v_km_s": arc.delta_v_km_s,
            }
            for arc_id, arc in enumerate(data.arcs)
        ],
        "flows": flows,
        "trips": trips,
        "fleet": fleet,
        "fleet_additions_by_year": fleet_by_year,
        "facilities": facilities,
        "storage": storage,
        "production": production,
        "earth_prop": earth_prop,
        "first_tank": first_tank,
        "infrastructure_resupply": infrastructure_resupply,
        "cost_breakdown": breakdown,
        "lifecycle": lifecycle,
    }


def build_cost_breakdown(data, variables):
    mission = data.mission
    breakdown = {
        name: float(expression.getValue())
        for name, expression in variables["cost_terms"].items()
    }
    total = sum(breakdown.values())
    breakdown["total"] = total

    operating_keys = {"maintenance", "earth_prop"}
    capex = sum(
        value
        for name, value in breakdown.items()
        if name not in operating_keys and name != "total"
    )
    maintenance = breakdown["maintenance"]
    earth_prop = breakdown["earth_prop"]
    total_payload = mission["total_payload_kg"]
    cost_per_kg = total / total_payload if total_payload > 0 else None
    lifecycle = {
        "mission_years": mission["mission_years"],
        "mission_days": mission["mission_days"],
        "total_payload_kg": total_payload,
        "demand_by_year_kg": mission["demand_by_year_kg"],
        "capex_total": capex,
        "infrastructure_to_GTO_total": breakdown["infrastructure_to_GTO"],
        "maintenance_total": maintenance,
        "earth_prop_total": earth_prop,
        "total": total,
        "cost_per_kg": cost_per_kg,
    }
    return breakdown, lifecycle


def write_solution(solution, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(solution, f, indent=2)


def _load_warm_start(model, run_dir):
    """Read the MIP start named by WARM_START_PATH, or start from scratch.

    A missing WARM_START_PATH (commented out or deleted) counts as no warm
    start, so the constant can simply be removed to force a cold start.
    """
    warm_start = globals().get("WARM_START_PATH")
    if warm_start is None:
        print("Warm start: none (solving from scratch)")
        return

    start_path = Path(warm_start)
    if not start_path.exists():
        # Silently cold starting here would waste a multi-hour run.
        raise FileNotFoundError(
            f"WARM_START_PATH is set but the file does not exist: {start_path}"
        )

    model.update()
    model.read(str(start_path))
    shutil.copy2(start_path, run_dir / start_path.name)
    print(f"Warm start: {start_path}")


def _write_iis(model, output_dir):
    try:
        model.computeIIS()
        iis_path = Path(output_dir) / "infeasible.ilp"
        model.write(str(iis_path))
        print(f"Wrote IIS (conflicting constraints) -> {iis_path}")
    except Exception as exc:  # noqa: BLE001
        print(f"Could not compute IIS: {exc}")


def print_summary(solution):
    print("\nFleet:")
    for name, count in solution["fleet"].items():
        print(f"  {name}: {count}")

    print("\nSWE (lunar water extraction):")
    swe = solution["facilities"]["SWE"]
    print(f"  installed={swe['installed']}, size q={swe['q']:,.1f} kg")

    print("\nDWE (electrolysis) by node:")
    if not solution["facilities"]["DWE"]:
        print("  none")
    for node, info in solution["facilities"]["DWE"].items():
        print(f"  {node}: installed={info['installed']}, q={info['q']:,.1f} kg")

    print("\nStorage (tank mass) by node:")
    if not solution["storage"]:
        print("  none")
    for node, info in solution["storage"].items():
        print(f"  {node}: H2O tank={info['H2O']:,.1f} kg, Prop tank={info['Prop']:,.1f} kg")

    infra = solution["infrastructure_resupply"]
    print(f"\nPost-initial infrastructure launched to GTO: {infra['total_to_GTO']:,.1f} kg")
    for year, value in infra["by_installation_year"].items():
        if value > TOL:
            print(f"  for mission year {year}: {value:,.1f} kg")

    earth_prop = solution["earth_prop"]
    print(f"\nEarth propellant total: {earth_prop['total']:,.1f} kg")
    for year, value in earth_prop["by_year"].items():
        print(f"  year {year}: {value:,.1f} kg")

    lifecycle = solution["lifecycle"]
    print(f"\nFull-horizon lifecycle ({lifecycle['mission_years']} years):")
    print(f"  payload to GEO : {lifecycle['total_payload_kg']:,.1f} kg")
    print(f"  capex          : {lifecycle['capex_total']:,.0f}")
    print(f"    infra to GTO  : {lifecycle['infrastructure_to_GTO_total']:,.0f}")
    print(f"  maintenance    : {lifecycle['maintenance_total']:,.0f}")
    print(f"  Earth propellant: {lifecycle['earth_prop_total']:,.0f}")
    print(f"  total          : {lifecycle['total']:,.0f}")
    if lifecycle["cost_per_kg"] is None:
        print("  cost per kg    : n/a (no payload demand)")
    else:
        print(f"  cost per kg    : {lifecycle['cost_per_kg']:,.0f}")


if __name__ == "__main__":
    main()
