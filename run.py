import json
import math
from pathlib import Path

from gurobipy import GRB

from build_cost import BUILD_COST as bc
from data import get_data
from model import build_and_solve
from post_process import generate_plots


RESULT_DIR = Path("results")
SOLUTION_PATH = RESULT_DIR / "latest_solution.json"
DEMAND_PROFILE_PATH = Path("inputs") / "demand_5yr_50tpy.json"
GUROBI_PARAMS = {
    "TimeLimit": 6 * 3600,
    "MIPGap": 0.025,
    "MIPFocus": 1,
    "Cuts": 2,
    "Symmetry": 2,
    "Heuristics": 0.2,
}

TOL = 1e-6


def main():
    RESULT_DIR.mkdir(exist_ok=True)
    data = get_data(DEMAND_PROFILE_PATH)
    params = {**GUROBI_PARAMS, "LogFile": str(RESULT_DIR / "full_horizon_5yr.log")}
    model, variables = build_and_solve(data, gurobi_params=params)

    if model.Status == GRB.INFEASIBLE:
        print("\nINFEASIBLE: GEO payload demand cannot be met.")
        _write_iis(model)
        return
    if model.SolCount == 0:
        print(
            f"\nSolver finished with status {model.Status}, but no integer feasible "
            "solution was found."
        )
        return

    solution = build_solution(data, model, variables)
    write_solution(solution, SOLUTION_PATH)

    label = "Optimal" if model.Status == GRB.OPTIMAL else "Best feasible"
    print(f"\n{label} lifecycle cost = {model.ObjVal:,.0f}")
    if model.Status != GRB.OPTIMAL:
        gap = f"{model.MIPGap:.2%}" if math.isfinite(model.MIPGap) else "not available"
        print(f"Solver status = {model.Status}, gap = {gap}")
    print(f"Saved solution -> {SOLUTION_PATH}")
    print_summary(solution)

    try:
        plot_paths = generate_plots(SOLUTION_PATH)
    except ImportError as exc:
        print(f"\nPlots were not generated (matplotlib missing?): {exc}")
        return

    print("\nSaved plots:")
    for path in plot_paths:
        print(f"  {path}")


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

    fleet = {name: int(round(gvar.X)) for name, gvar in variables["N_sc"].items()}
    facilities = {
        "SWE": {
            "node": "Moon",
            "installed": variables["SWE"].X > 0.5,
            "q": variables["q"]["Moon_SWE"].X,
        },
        "DWE": {
            node: {
                "installed": variables["DWE"][node].X > 0.5,
                "q": variables["q"][node].X,
            }
            for node in data.depot_node
            if variables["q"][node].X > TOL or variables["DWE"][node].X > 0.5
        },
    }
    storage = {
        node: {
            "H2O": variables["Storage_H2O"][node].X,
            "Prop": variables["Storage_Prop"][node].X,
        }
        for node in data.depot_node
        if (
            variables["Storage_H2O"][node].X > TOL
            or variables["Storage_Prop"][node].X > TOL
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
    first_tank = {name: gvar.X for name, gvar in variables["first_Tank"].items()}
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
        "facilities": facilities,
        "storage": storage,
        "production": production,
        "earth_prop": earth_prop,
        "first_tank": first_tank,
        "cost_breakdown": breakdown,
        "lifecycle": lifecycle,
    }


def build_cost_breakdown(data, variables):
    transfer = bc["transfer_cost"]
    vehicles = data.vehicles
    mission = data.mission

    swe = bc["SWE_fixed"] * variables["SWE"].X + (
        bc["SWE_per_capacity"] + transfer["Moon"]
    ) * variables["q"]["Moon_SWE"].X
    dwe = sum(
        bc["DWE_fixed"] * variables["DWE"][node].X
        + (bc["DWE_per_capacity"] + transfer[node]) * variables["q"][node].X
        for node in data.depot_node
    )
    storage = sum(
        (bc["Storage_H2O_per_kg"] + transfer[node])
        * variables["Storage_H2O"][node].X
        + (bc["Storage_Prop_per_kg"] + transfer[node])
        * variables["Storage_Prop"][node].X
        for node in data.depot_node
    )
    spacecraft = (
        bc["OTV_unit"] * variables["N_sc"]["OTV"].X
        + bc["RT_unit"] * variables["N_sc"]["RT"].X
        + transfer["LEO"] * vehicles["OTV"]["dry_mass"] * variables["N_sc"]["OTV"].X
        + transfer["Moon"] * vehicles["RT"]["dry_mass"] * variables["N_sc"]["RT"].X
        + (transfer["Moon"] + bc["Storage_H2O_per_kg"])
        * variables["first_Tank"]["H2O_Tank"].X
        + (transfer["Moon"] + bc["Storage_Prop_per_kg"])
        * variables["first_Tank"]["Prop_Tank"].X
    )

    mission_duration_years = (
        mission["mission_steps"] * mission["days_per_step"] / mission["days_per_year"]
    )
    maintenance = bc["ISRU_maint_frac_per_yr"] * mission_duration_years * (
        (bc["ISRU_spares_cost_per_kg"] + transfer["Moon"])
        * variables["q"]["Moon_SWE"].X
        + sum(
            (bc["ISRU_spares_cost_per_kg"] + transfer[node])
            * variables["q"][node].X
            for node in data.depot_node
        )
    )
    earth_prop = sum(
        (bc["Ini_Prop_per_kg"] + transfer[node])
        * variables["earth_prop"][node, time].X
        for node in ["GTO", "Moon"]
        for time in range(data.T)
    )

    capex = swe + dwe + storage + spacecraft
    total = capex + maintenance + earth_prop
    total_payload = mission["total_payload_kg"]
    cost_per_kg = total / total_payload if total_payload > 0 else float("nan")
    breakdown = {
        "SWE": swe,
        "DWE": dwe,
        "storage": storage,
        "spacecraft": spacecraft,
        "maintenance": maintenance,
        "earth_prop": earth_prop,
        "total": total,
    }
    lifecycle = {
        "mission_years": mission["mission_years"],
        "mission_days": mission["mission_days"],
        "total_payload_kg": total_payload,
        "demand_by_year_kg": mission["demand_by_year_kg"],
        "capex_total": capex,
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


def _write_iis(model):
    try:
        model.computeIIS()
        iis_path = RESULT_DIR / "infeasible.ilp"
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

    earth_prop = solution["earth_prop"]
    print(f"\nEarth propellant total: {earth_prop['total']:,.1f} kg")
    for year, value in earth_prop["by_year"].items():
        print(f"  year {year}: {value:,.1f} kg")

    lifecycle = solution["lifecycle"]
    print(f"\nFull-horizon lifecycle ({lifecycle['mission_years']} years):")
    print(f"  payload to GEO : {lifecycle['total_payload_kg']:,.1f} kg")
    print(f"  capex          : {lifecycle['capex_total']:,.0f}")
    print(f"  maintenance    : {lifecycle['maintenance_total']:,.0f}")
    print(f"  Earth propellant: {lifecycle['earth_prop_total']:,.0f}")
    print(f"  total          : {lifecycle['total']:,.0f}")
    print(f"  cost per kg    : {lifecycle['cost_per_kg']:,.0f}")


if __name__ == "__main__":
    main()
