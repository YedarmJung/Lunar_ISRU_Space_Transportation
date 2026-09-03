"""Sweep runner: GEO payload demand x demand cadence x mission duration.

For each (mission_years, demand density, demand period) it solves the steady-state
window and:
  * APPENDS a summary row to a cumulative CSV the moment the case finishes
    (crash-safe; re-running with different axis values keeps accumulating), and
  * saves that case's FULL solution JSON under results/sweep/cases/ so any single
    case can later be inspected in detail (post_process.generate_plots on it).

Plotting lives in sweep_post_process.py, so you can run the sweep in several
batches and still plot everything accumulated so far at once.

ver_time_horizon: window size is independent of mission_years (horizon enters only
via amortization).  Cadence is a swept axis; setup auto-scales to 1.5 x period.
"""
import csv
from pathlib import Path

from data import get_data
from model import build_and_solve
from run import build_cost_breakdown, build_solution, write_solution


RESULT_DIR = Path("results") / "sweep"
CSV_PATH = RESULT_DIR / "sweep_results.csv"
CASE_DIR = RESULT_DIR / "cases"

# ---- Sweep axes (change between batches; rows just accumulate in the CSV) ----
DEMAND_T_PER_YR = [50,75,100,125,150]               # annual GEO payload demand
DEMAND_PERIOD_DAYS_LIST = [90]   # cadence (days between pulses)
MISSION_YEARS_LIST = [4,6,8,10]            # amortization horizon(s)
SWEEP_DAYS_PER_STEP = 5                         # 5-day grid, n_rollin=1, n_steady=1

GUROBI_PARAMS = {
    "TimeLimit": 1.2*3600,        # per cell; break-even robust to loose gap
    "MIPGap": 0.025,
    "MIPFocus": 2,
    "Cuts": 2,
    "Symmetry": 2,
    "Heuristics": 0.2,
}
TOL = 1e-6

FIELDS = [
    "case", "mission_years", "demand_t_yr", "period_days", "setup_days", "pulse_t",
    "objective_musd", "gap", "total_payload_t", "cost_musd_per_t", "OTV", "RT",
    "SWE_q", "DWE_q", "tank_mass_t", "earth_prop_t", "lunar_prop_t", "earth_frac",
    "lunar_used", "status", "feasible",
]


def case_tag(years, annual_t, period_days):
    return f"y{years}_p{period_days}_d{annual_t}"




def append_row(row):
    """Append one case row; write the header only when the CSV is first created."""
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    new_file = not CSV_PATH.exists()
    with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in FIELDS})


def run_one(years, annual_t_here, period_days):
    # cadence(period_days) 바꾸면 setup(=1.5x)·lead 도 get_data 안에서 자동 스케일.
    data = get_data(days_per_step=SWEEP_DAYS_PER_STEP, demand_period_days=period_days,
                    n_rollin=1, n_steady=1, annual_t=annual_t_here)

    m, var = build_and_solve(data, gurobi_params=GUROBI_PARAMS, horizon_years=years)

    tag = case_tag(years, annual_t_here, period_days)
    setup_days = data.mission["setup_steps"] * data.mission["days_per_step"]
    base = {"case": tag, "mission_years": years, "demand_t_yr": annual_t_here,
            "period_days": period_days, "setup_days": setup_days}
    if m.SolCount == 0:
        return {**base, "feasible": False, "status": int(m.Status)}

    # full solution for later detailed inspection of this specific case
    solution = build_solution(data, m, var, years)
    CASE_DIR.mkdir(parents=True, exist_ok=True)
    write_solution(solution, CASE_DIR / f"{tag}.json")

    _breakdown, amort = build_cost_breakdown(data, var, years)
    ss, seam = data.mission["steady_start"], data.mission["seam"]
    ep_steady = sum(var["earth_prop"][n, t].X
                    for n in ["GTO", "Moon"] for t in range(ss, seam))
    tank_mass = sum(var["Storage_H2O"][p].X + var["Storage_Prop"][p].X
                    for p in data.depot_node)
    # lunar propellant produced by DWE in the steady block (same rate constants as model.py)
    dwe_rate = 0.09722 * 3 / 2.5 * data.mission["days_per_step"]
    lunar_prop = sum((2.0 / 3.0) * dwe_rate * var["q_operation"][p, t].X
                     for p in data.depot_node for t in range(ss, seam))
    denom = ep_steady + lunar_prop
    earth_frac = (ep_steady / denom) if denom > 0 else ""
    swe_q = var["q"]["Moon_SWE"].X
    return {
        **base,
        "pulse_t": data.mission["GEO_demand_PL_t"],
        "objective_musd": m.ObjVal,                           # = total(H) [MUSD]
        "gap": m.MIPGap,
        "total_payload_t": amort["pulse_t"] * amort["periods_H"],
        "cost_musd_per_t": amort["cost_musd_per_t"],
        "OTV": round(var["N_sc"]["OTV"].X),
        "RT": round(var["N_sc"]["RT"].X),
        "SWE_q": round(swe_q, 1),
        "DWE_q": round(sum(var["q"][p].X for p in data.depot_node), 1),
        "tank_mass_t": round(tank_mass, 4),                   # depot 탱크 질량 합 [t]
        "earth_prop_t": round(ep_steady, 4),                  # 정상상태 블록당 지구연료 [t]
        "lunar_prop_t": round(lunar_prop, 4),                 # 정상상태 블록당 달산 추진제 [t]
        "earth_frac": round(earth_frac, 4) if earth_frac != "" else "",  # 지구연료 비율
        "lunar_used": swe_q > TOL,
        "status": int(m.Status),
        "feasible": True,
    }


def main():
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    n = len(MISSION_YEARS_LIST) * len(DEMAND_PERIOD_DAYS_LIST) * len(DEMAND_T_PER_YR)
    k = 0
    for years in MISSION_YEARS_LIST:
        for period_days in DEMAND_PERIOD_DAYS_LIST:
            for annual_t in DEMAND_T_PER_YR:
                k += 1
                print(f"\n{'='*10} [{k}/{n}] {years}yr | {period_days}d cadence | {annual_t}t/yr {'='*10}")
                r = run_one(years, annual_t, period_days)
                append_row(r)          # ver_time_horizon: 케이스 끝나자마자 즉시 저장(누적)
                if r.get("feasible"):
                    print(
                        f"  saved {r['case']}  "
                        f"cost={r['cost_musd_per_t']:,.3f} MUSD/t  "
                        f"lunar={r['lunar_used']}  "
                        f"tank={r['tank_mass_t']:,.3f} t  gap={r['gap']:.1%}"
                    )
                else:
                    print(f"  {r['case']}: no feasible solution (status {r['status']})")

    print(f"\nAppended {n} case(s) to {CSV_PATH}")
    print("Run  python sweep_post_process.py  to (re)plot everything accumulated.")


if __name__ == "__main__":
    main()
