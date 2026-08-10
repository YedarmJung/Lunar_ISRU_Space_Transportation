import json
from pathlib import Path

from gurobipy import GRB

from data import get_data, amort_factor   # ver_time_horizon: amort_factor
from build_cost import BUILD_COST as bc
from model import build_and_solve
from post_process import generate_plots


RESULT_DIR = Path("results")
SOLUTION_PATH = RESULT_DIR / "latest_solution.json"
GUROBI_PARAMS = {
    "TimeLimit": 8*3600,      # 1시간이면 충분
    "MIPGap": 0.01,         # 5% 오면 멈춤 (충분히 좋은 해)
    "MIPFocus": 2,          # 하한 개선에 집중
    "Cuts": 2,              # 절단면 강화 -> 하한 up
    "Symmetry": 2,          # 대칭 공략
    "Heuristics": 0.2,      # 좋은 incumbent 빨리 찾기
}

TOL = 1e-6


def main():
    data = get_data(mission_years = 10, days_per_step=5, demand_period_days=60, n_rollin=1, n_steady=1, annual_t=100)
    horizon_years = data.mission["mission_years"]   # ver_time_horizon: amortization 지평
    m, var = build_and_solve(data, gurobi_params=GUROBI_PARAMS, horizon_years=horizon_years)

    RESULT_DIR.mkdir(exist_ok=True)

    if m.Status == GRB.INFEASIBLE:
        print("\nINFEASIBLE: GEO payload demand cannot be met.")
        _write_iis(m)
        return
    if m.SolCount == 0:
        print(f"\nSolver finished with status {m.Status}, no feasible solution.")
        return

    solution = build_solution(data, m, var, horizon_years)   # ver_time_horizon
    write_solution(solution, SOLUTION_PATH)

    label = "Optimal" if m.Status == GRB.OPTIMAL else "Best feasible"
    print(f"\n{label} total cost = {m.ObjVal:,.0f}")
    if m.Status != GRB.OPTIMAL:
        print(f"Solver status = {m.Status}, gap = {m.MIPGap:.2%}")
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


def build_solution(data, m, var, horizon_years=None):   # ver_time_horizon: horizon_years
    if horizon_years is None:
        horizon_years = data.mission["mission_years"]
    x = var["x"]
    xm = var["xm"]
    y = var["y"]

    flows = []
    for (c, v, a, t), gvar in x.items():
        value = gvar.X
        if value <= TOL:
            continue
        arc = data.arcs[a]
        flows.append(
            {
                "commodity": c,
                "mode": v,
                "arc": a,
                "tail": arc.tail,
                "head": arc.head,
                "kind": arc.kind,
                "tau": arc.tau,
                "time": t,
                "arrival_time": t + arc.tau,
                "departed": value,
                "arrived": xm[c, v, a, t + arc.tau].X if (c, v, a, t + arc.tau) in xm else None,
            }
        )

    trips = []
    for (v, a, t), gvar in y.items():
        count = gvar.X
        if count <= TOL:
            continue
        arc = data.arcs[a]
        trips.append(
            {
                "mode": v,
                "arc": a,
                "tail": arc.tail,
                "head": arc.head,
                "time": t,
                "arrival_time": t + arc.tau,
                "count": round(count),
            }
        )

    fleet = {vname: round(gvar.X) for vname, gvar in var["N_sc"].items()}

    swe_installed = var["SWE"].X > 0.5
    facilities = {
        "SWE": {
            "node": "Moon",
            "installed": swe_installed,
            "q": var["q"]["Moon_SWE"].X,
        },
        "DWE": {
            p: {"installed": var["DWE"][p].X > 0.5, "q": var["q"][p].X}
            for p in data.depot_node
            if var["q"][p].X > TOL or var["DWE"][p].X > 0.5
        },
    }

    storage = {
        p: {"H2O": var["Storage_H2O"][p].X, "Prop": var["Storage_Prop"][p].X}
        for p in data.depot_node
        if var["Storage_H2O"][p].X > TOL or var["Storage_Prop"][p].X > TOL
    }

    production = []
    for (e, t), gvar in var["q_operation"].items():
        value = gvar.X
        if value <= TOL:
            continue
        production.append({"facility": e, "time": t, "operated_mass": value})

    # ver_time_horizon: earth_prop(node,t) 를 ramp / 정상상태블록 / tail 로 집계
    ss = data.mission["steady_start"]
    seam = data.mission["seam"]
    ep = var["earth_prop"]
    ep_ramp = sum(ep[n, t].X for n in ["LEO", "Moon"] for t in range(ss))
    ep_steady = sum(ep[n, t].X for n in ["LEO", "Moon"] for t in range(ss, seam))
    ep_tail = sum(ep[n, t].X for n in ["LEO", "Moon"] for t in range(seam, data.T))
    earth_prop = {"ramp": ep_ramp, "steady_block": ep_steady, "tail": ep_tail}
    first_tank = {k: gvar.X for k, gvar in var["first_Tank"].items()}

    breakdown, amort = build_cost_breakdown(data, var, horizon_years)   # ver_time_horizon

    return {
        "status": int(m.Status),
        "objective": m.ObjVal,
        "mip_gap": m.MIPGap if m.SolCount else None,
        "T": data.T,
        "nodes": data.nodes,
        "commodities": data.commodities,
        "modes": data.arc_type,
        "mission": data.mission,
        "arcs": [
            {
                "id": a,
                "tail": arc.tail,
                "head": arc.head,
                "tau": arc.tau,
                "kind": arc.kind,
                "delta_v_km_s": arc.delta_v_km_s,
            }
            for a, arc in enumerate(data.arcs)
        ],
        "flows": flows,
        "trips": trips,
        "fleet": fleet,
        "facilities": facilities,
        "storage": storage,
        "production": production,
        "earth_prop": earth_prop,          # ver_time_horizon: first_prop -> earth_prop 집계
        "first_tank": first_tank,
        "cost_breakdown": breakdown,       # ver_time_horizon: 미션-총액 항목(bar용)
        "amortization": amort,             # ver_time_horizon: capex/opex/total(H)/$·kg
    }


def build_cost_breakdown(data, var, horizon_years):   # ver_time_horizon: 재작성 (capex/opex 분리 + amortize)
    transfer = bc["transfer_cost"]
    vehicles = data.vehicles
    mission = data.mission
    ss, seam = mission["steady_start"], mission["seam"]
    AMORT, periods_H = amort_factor(mission, horizon_years)

    # ---- capex (1회) ----  ver_time_horizon
    swe = bc["SWE_fixed"] * var["SWE"].X + (
        bc["SWE_per_capacity"] + transfer["Moon"]) * var["q"]["Moon_SWE"].X
    dwe = sum(
        bc["DWE_fixed"] * var["DWE"][p].X
        + (bc["DWE_per_capacity"] + transfer[p]) * var["q"][p].X
        for p in data.depot_node
    )
    storage = sum(
        (bc["Storage_H2O_per_kg"] + transfer[p]) * var["Storage_H2O"][p].X
        + (bc["Storage_Prop_per_kg"] + transfer[p]) * var["Storage_Prop"][p].X
        for p in data.depot_node
    )
    spacecraft = (
        bc["OTV_unit"] * var["N_sc"]["OTV"].X
        + bc["RT_unit"] * var["N_sc"]["RT"].X
        + transfer["LEO"] * vehicles["OTV"]["dry_mass"] * var["N_sc"]["OTV"].X
        + transfer["Moon"] * vehicles["RT"]["dry_mass"] * var["N_sc"]["RT"].X
        + (transfer["Moon"] + bc["Storage_H2O_per_kg"])
        * var["first_Tank"]["H2O_Tank"].X
        + (transfer["Moon"] + bc["Storage_Prop_per_kg"])
        * var["first_Tank"]["Prop_Tank"].X
    )

    def _u(node):
        return bc["Ini_Prop_per_kg"] + transfer[node]
    ep = var["earth_prop"]
    earth_ramp = sum(_u(n) * ep[n, t].X for n in ["LEO", "Moon"] for t in range(ss))
    earth_tail = sum(_u(n) * ep[n, t].X for n in ["LEO", "Moon"] for t in range(seam, data.T))
    capex = swe + dwe + storage + spacecraft + earth_ramp + earth_tail

    # ---- opex (정상상태 블록당 -> 주기당) ----  ver_time_horizon
    maint_ps = bc["ISRU_maint_frac_per_yr"] * (mission["days_per_step"] / 365.0)
    block_steps = seam - ss
    maint_block = maint_ps * block_steps * (
        (bc["ISRU_spares_cost_per_kg"] + transfer["Moon"]) * var["q"]["Moon_SWE"].X
        + sum((bc["ISRU_spares_cost_per_kg"] + transfer[p]) * var["q"][p].X
              for p in data.depot_node)
    )
    earth_block = sum(_u(n) * ep[n, t].X for n in ["LEO", "Moon"] for t in range(ss, seam))
    opex_per_period = (maint_block + earth_block) / mission["n_steady"]

    total_H = capex + opex_per_period * periods_H
    pulse = mission["GEO_demand_PL_kg"]
    payload_H = pulse * periods_H
    cost_per_kg = total_H / payload_H if payload_H > 0 else float("nan")

    # bar chart 용: 모두 "미션-총액 $" 로 환산해 total 로 합산되게 한다.  ver_time_horizon
    breakdown = {
        "SWE": swe,
        "DWE": dwe,
        "storage": storage,
        "spacecraft": spacecraft,
        "earth_prop_ramp": earth_ramp,
        "earth_prop_tail": earth_tail,
        "maintenance_over_H": (maint_block / mission["n_steady"]) * periods_H,
        "earth_prop_over_H": (earth_block / mission["n_steady"]) * periods_H,
    }
    breakdown["total"] = sum(breakdown.values())   # == total_H == m.ObjVal

    amort = {
        "horizon_years": horizon_years,
        "capex_total": capex,
        "opex_per_period": opex_per_period,
        "periods_H": periods_H,
        "period_days": mission["period_steps"] * mission["days_per_step"],
        "pulse_kg": pulse,
        "n_steady": mission["n_steady"],
        "total": total_H,
        "cost_per_kg": cost_per_kg,
    }
    return breakdown, amort


def write_solution(solution, path):
    with path.open("w", encoding="utf-8") as f:
        json.dump(solution, f, indent=2)


def _write_iis(m):
    """On infeasibility, compute and dump an irreducible infeasible subsystem."""
    try:
        m.computeIIS()
        iis_path = RESULT_DIR / "infeasible.ilp"
        m.write(str(iis_path))
        print(f"Wrote IIS (conflicting constraints) -> {iis_path}")
    except Exception as exc:  # noqa: BLE001
        print(f"Could not compute IIS: {exc}")


def print_summary(solution):
    print("\nFleet:")
    for vname, n in solution["fleet"].items():
        print(f"  {vname}: {n}")

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

    # ver_time_horizon: Earth propellant (ramp / 정상상태 블록 / tail)
    print("\nEarth propellant [kg]:")
    ep = solution["earth_prop"]
    am = solution["amortization"]
    print(f"  ramp (setup+roll-in): {ep['ramp']:,.1f}")
    print(f"  steady block ({am['n_steady']} periods): {ep['steady_block']:,.1f}")
    print(f"  tail (roll-out): {ep['tail']:,.1f}")

    # ver_time_horizon: amortized 비용 (지평 H)
    print(f"\nAmortized cost (horizon H = {am['horizon_years']:g} yr):")
    print(f"  capex (one-time) : {am['capex_total']:,.0f}")
    print(f"  opex per period  : {am['opex_per_period']:,.0f}")
    print(f"  periods over H   : {am['periods_H']:.1f}")
    print(f"  total(H)         : {am['total']:,.0f}")
    print(f"  cost per kg->GEO : {am['cost_per_kg']:,.0f}")

    print("\nCost breakdown (mission-total $):")
    for name, value in solution["cost_breakdown"].items():
        print(f"  {name}: {value:,.0f}")


if __name__ == "__main__":
    main()
