import json
import math
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from gurobipy import GRB

from data import get_data
from incumbent_flow_plot import IncumbentFlowPlotter
from model import build_model, solve_model
from post_process import generate_plots
from units import MODEL_UNITS, SOLUTION_SCHEMA_VERSION


RESULT_DIR = Path("results")
DEMAND_PROFILE_PATH = Path("inputs/Demand_Scenarios") / "base_10yr_lam13_k0.88_2119.json"
# Point this at a .mst produced by make_warm_start.py to warm start the solve.
# Leave it as None to start from scratch.
#WARM_START_PATH = Path("results/plots_20260902_163755_9yr/start.mst")
GUROBI_PARAMS = {
    "TimeLimit": 10 * 3600,
    #"MIPGap": 0.025,
    #"MIPFocus": 1,
    #"Cuts": 2,
    #"Symmetry": 2,
    #"Heuristics": 0.2,
    #"Presolve" : 2,
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
    print(f"\n{label} optimization objective = {model.ObjVal:,.3f} MUSD")
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
                "kind": arc.kind,
                "tau": arc.tau,
                "time": time,
                "arrival_time": time + arc.tau,
                "count": int(round(count)),
            }
        )

    mission_years = data.mission["mission_years"]
    years = range(mission_years)
    final_year = mission_years - 1

    satellite_service = [
        {
            **event,
            "served": variables["service"][event["year"], event["event_id"]].X
            > 0.5,
        }
        for event in data.mission["demand_events"]
    ]

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
    breakdown, lifecycle = build_cost_breakdown(
        data, variables, satellite_service
    )

    return {
        "schema_version": SOLUTION_SCHEMA_VERSION,
        "units": MODEL_UNITS,
        "status": int(model.Status),
        "objective_musd": model.ObjVal,
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
        "satellite_service": satellite_service,
        "earth_prop": earth_prop,
        "first_tank": first_tank,
        "infrastructure_resupply": infrastructure_resupply,
        "cost_breakdown_musd": breakdown,
        "lifecycle": lifecycle,
    }


def build_cost_breakdown(data, variables, satellite_service):
    mission = data.mission
    breakdown = {
        name: float(expression.getValue())
        for name, expression in variables["cost_terms"].items()
    }
    unserved_penalty = breakdown["unserved_penalty"]
    physical_total = sum(
        value
        for name, value in breakdown.items()
        if name != "unserved_penalty"
    )
    objective_total = physical_total + unserved_penalty

    operating_keys = {"maintenance", "earth_prop", "unserved_penalty"}
    capex = sum(
        value
        for name, value in breakdown.items()
        if name not in operating_keys
    )
    maintenance = breakdown["maintenance"]
    earth_prop = breakdown["earth_prop"]
    candidate_payload_t = mission["total_payload_t"]
    served_payload = sum(
        event["mass_t"] for event in satellite_service if event["served"]
    )
    unserved_payload = candidate_payload_t - served_payload
    served_by_year = {
        str(year): sum(
            event["mass_t"]
            for event in satellite_service
            if event["served"] and event["year"] == year
        )
        for year in range(1, mission["mission_years"] + 1)
    }
    cost_musd_per_t = physical_total / served_payload if served_payload > 0 else None

    breakdown["physical_total"] = physical_total
    breakdown["objective_total"] = objective_total
    breakdown["total"] = physical_total
    lifecycle = {
        "mission_years": mission["mission_years"],
        "mission_days": mission["mission_days"],
        "candidate_satellites": len(satellite_service),
        "served_satellites": sum(event["served"] for event in satellite_service),
        "candidate_payload_t": candidate_payload_t,
        "served_payload_t": served_payload,
        "unserved_payload_t": unserved_payload,
        "total_payload_t": served_payload,
        "candidate_demand_by_year_t": mission["demand_by_year_t"],
        "demand_by_year_t": served_by_year,
        "capex_musd": capex,
        "infrastructure_to_GTO_musd": breakdown["infrastructure_to_GTO"],
        "maintenance_musd": maintenance,
        "earth_prop_musd": earth_prop,
        "unserved_penalty_musd_per_t": variables["unserved_penalty_musd_per_t"],
        "unserved_penalty_musd": unserved_penalty,
        "physical_total_musd": physical_total,
        "optimization_objective_musd": objective_total,
        "total_musd": physical_total,
        "cost_musd_per_t": cost_musd_per_t,
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
    # Gurobi on Windows may reject paths containing non-ASCII characters.
    # Let Python copy the file to an ASCII-only temporary path first.
    with tempfile.TemporaryDirectory(prefix="gurobi_mst_") as temp_dir:
        temp_start = Path(temp_dir) / "start.mst"
        shutil.copyfile(start_path, temp_start)
        model.read(str(temp_start))
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
    print(f"  installed={swe['installed']}, size q={swe['q']:,.3f} t")

    print("\nDWE (electrolysis) by node:")
    if not solution["facilities"]["DWE"]:
        print("  none")
    for node, info in solution["facilities"]["DWE"].items():
        print(f"  {node}: installed={info['installed']}, q={info['q']:,.3f} t")

    print("\nStorage (tank mass) by node:")
    if not solution["storage"]:
        print("  none")
    for node, info in solution["storage"].items():
        print(
            f"  {node}: H2O tank={info['H2O']:,.3f} t, "
            f"Prop tank={info['Prop']:,.3f} t"
        )

    infra = solution["infrastructure_resupply"]
    print(
        "\nPost-initial infrastructure launched to GTO: "
        f"{infra['total_to_GTO']:,.3f} t"
    )
    for year, value in infra["by_installation_year"].items():
        if value > TOL:
            print(f"  for mission year {year}: {value:,.3f} t")

    earth_prop = solution["earth_prop"]
    print(f"\nEarth propellant total: {earth_prop['total']:,.3f} t")
    for year, value in earth_prop["by_year"].items():
        print(f"  year {year}: {value:,.3f} t")

    lifecycle = solution["lifecycle"]
    print(f"\nFull-horizon lifecycle ({lifecycle['mission_years']} years):")
    print(
        f"  satellites     : {lifecycle['served_satellites']} / "
        f"{lifecycle['candidate_satellites']} served"
    )
    print(f"  candidate payload: {lifecycle['candidate_payload_t']:,.3f} t")
    print(f"  payload to GEO : {lifecycle['served_payload_t']:,.3f} t")
    print(f"  unserved payload: {lifecycle['unserved_payload_t']:,.3f} t")
    print(f"  capex          : {lifecycle['capex_musd']:,.3f} MUSD")
    print(
        "    infra to GTO  : "
        f"{lifecycle['infrastructure_to_GTO_musd']:,.3f} MUSD"
    )
    print(f"  maintenance    : {lifecycle['maintenance_musd']:,.3f} MUSD")
    print(f"  Earth propellant: {lifecycle['earth_prop_musd']:,.3f} MUSD")
    print(f"  physical total : {lifecycle['physical_total_musd']:,.3f} MUSD")
    print(f"  unserved penalty: {lifecycle['unserved_penalty_musd']:,.3f} MUSD")
    print(
        "  optimization objective: "
        f"{lifecycle['optimization_objective_musd']:,.3f} MUSD"
    )
    if lifecycle["cost_musd_per_t"] is None:
        print("  cost per served tonne: n/a (no served payload)")
    else:
        print(
            "  cost per served tonne: "
            f"{lifecycle['cost_musd_per_t']:,.3f} MUSD/t"
        )


if __name__ == "__main__":
    main()
