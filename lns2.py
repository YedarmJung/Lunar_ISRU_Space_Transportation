"""Rolling LNS with vehicle-wide neighborhoods before each time-window sweep."""

import time
from datetime import datetime
from pathlib import Path

from gurobipy import GRB

from data import get_data
from model import build_model
from run import build_solution, write_solution


DEMAND_PROFILE_PATH = Path("inputs") / "demand_hist_9yr_2017_2025.json"
WARM_START_PATH = Path("results") / "plots_20260903_144432" / "start.mst"
RESULT_DIR = Path("results")

TOTAL_TIME = 6 * 3600
IMPROVEMENT_TOL = 1e-6
WINDOW = 12
STEP = 6
WINDOW_TIME = 300
OTV_FREE_TIME = 1200
RT_FREE_TIME = 1200


def make_run_dir():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = RESULT_DIR / f"lns2_{timestamp}"
    suffix = 0
    while run_dir.exists():
        suffix += 1
        run_dir = RESULT_DIR / f"lns2_{timestamp}_{suffix:02d}"
    run_dir.mkdir(parents=True)
    return run_dir


def make_logger(log_path):
    def log(message=""):
        print(message, flush=True)
        with log_path.open("a", encoding="utf-8") as file:
            print(message, file=file, flush=True)

    return log


def save_best(data, model, variables, all_vars, best_values, run_dir):
    """Checkpoint the current best solution and its complete start vector."""
    write_solution(
        build_solution(data, model, variables),
        run_dir / "latest_solution.json",
    )
    model.write(str(run_dir / "final.sol"))
    model.setAttr(GRB.Attr.Start, all_vars, best_values)
    model.update()
    model.write(str(run_dir / "final.mst"))


def main():
    overall_start = time.perf_counter()
    run_dir = make_run_dir()
    log = make_logger(run_dir / "solver.log")

    data = get_data(DEMAND_PROFILE_PATH)
    model, variables = build_model(
        data,
        gurobi_params={
            "TimeLimit": WINDOW_TIME,
            "MIPFocus": 1,
            "OutputFlag": 0,
        },
    )
    model.update()

    if not WARM_START_PATH.exists():
        raise FileNotFoundError(f"Warm start not found: {WARM_START_PATH}")
    model.read(str(WARM_START_PATH))

    model.Params.SolutionLimit = 1
    model.optimize()
    model.Params.SolutionLimit = 2_000_000_000
    if model.SolCount == 0:
        raise RuntimeError("The warm start did not produce a feasible solution.")

    y = variables["y"]
    all_vars = model.getVars()
    original_bounds = {key: (var.LB, var.UB) for key, var in y.items()}
    incumbent_y = {key: int(round(var.X)) for key, var in y.items()}
    best_values = model.getAttr(GRB.Attr.X, all_vars)
    start_obj = model.ObjVal
    best_obj = start_obj
    save_best(data, model, variables, all_vars, best_values, run_dir)

    last_start = max(0, data.T - WINDOW)
    window_starts = list(range(0, last_start + 1, STEP))
    if window_starts[-1] != last_start:
        window_starts.append(last_start)

    # One cycle: RT free -> OTV free -> one complete rolling-window sweep.
    neighborhoods = [
        ("OTV fixed / RT free", "mode", "OTV"),
        ("RT fixed / OTV free", "mode", "RT"),
    ]
    neighborhoods.extend(
        (f"window [{t0}, {t0 + WINDOW})", "window", t0)
        for t0 in window_starts
    )

    log(f"Initial objective: {start_obj:,.5f} MUSD")
    log(
        f"y vars: {len(y):,} | window: {WINDOW} | step: {STEP} | "
        f"cycle: 2 mode + {len(window_starts)} time windows | "
        f"budget: {TOTAL_TIME / 3600:.1f} h"
    )
    log(
        f"time limits: RT free {RT_FREE_TIME / 60:.0f}m | "
        f"OTV free {OTV_FREE_TIME / 60:.0f}m | "
        f"window {WINDOW_TIME / 60:.0f}m"
    )
    log(f"Artifacts: {run_dir}")

    iteration = 0
    improvements = 0

    try:
        while time.perf_counter() - overall_start < TOTAL_TIME:
            label, neighborhood_type, value = neighborhoods[
                iteration % len(neighborhoods)
            ]
            iteration += 1
            iter_start = time.perf_counter()
            free_y = 0

            for key, var in y.items():
                if neighborhood_type == "mode":
                    is_free = key[0] != value
                else:
                    is_free = value <= key[2] < value + WINDOW

                if is_free:
                    var.LB, var.UB = original_bounds[key]
                    free_y += 1
                else:
                    fixed_value = incumbent_y[key]
                    var.LB = fixed_value
                    var.UB = fixed_value

            if neighborhood_type == "mode":
                iteration_time_limit = (
                    RT_FREE_TIME if value == "OTV" else OTV_FREE_TIME
                )
            else:
                iteration_time_limit = WINDOW_TIME

            model.setAttr(GRB.Attr.Start, all_vars, best_values)
            remaining = TOTAL_TIME - (time.perf_counter() - overall_start)
            model.Params.TimeLimit = max(
                0.01,
                min(iteration_time_limit, remaining),
            )
            model.update()
            model.optimize()

            solve_status = model.Status
            candidate_obj = model.ObjVal if model.SolCount > 0 else None
            improved = (
                candidate_obj is not None
                and candidate_obj < best_obj - IMPROVEMENT_TOL
            )
            if improved:
                best_obj = candidate_obj
                incumbent_y = {key: int(round(var.X)) for key, var in y.items()}
                best_values = model.getAttr(GRB.Attr.X, all_vars)
                improvements += 1
                save_best(data, model, variables, all_vars, best_values, run_dir)

            obj_text = "none" if candidate_obj is None else f"{candidate_obj:.5f}"
            change = 100.0 * (best_obj / start_obj - 1.0)
            marker = " *" if improved else ""
            log(
                f"iter {iteration:3d} | {label:<24} | free_y {free_y:4d} | "
                f"obj {obj_text:>10} | best {best_obj:.5f} "
                f"({change:+.3f}%) | "
                f"{time.perf_counter() - iter_start:.0f}s{marker}"
            )
            if solve_status == GRB.INTERRUPTED:
                log("\nInterrupted; the best checkpoint has been kept.")
                break

    except KeyboardInterrupt:
        if model.SolCount > 0 and model.ObjVal < best_obj - IMPROVEMENT_TOL:
            best_obj = model.ObjVal
            incumbent_y = {key: int(round(var.X)) for key, var in y.items()}
            best_values = model.getAttr(GRB.Attr.X, all_vars)
            improvements += 1
            save_best(data, model, variables, all_vars, best_values, run_dir)
        log("\nInterrupted; the best checkpoint has been kept.")

    elapsed = time.perf_counter() - overall_start
    improvement_pct = 100.0 * (start_obj - best_obj) / start_obj
    log("\nLNS2 finished")
    log(f"  objective   : {start_obj:,.5f} -> {best_obj:,.5f} MUSD")
    log(f"  improvement : {improvement_pct:.3f}%")
    log(f"  iterations  : {iteration} ({improvements} improving)")
    log(f"  elapsed     : {elapsed / 3600:.2f} h")
    log(f"  solution    : {run_dir / 'latest_solution.json'}")
    log(f"  MIP start   : {run_dir / 'final.mst'}")


if __name__ == "__main__":
    main()
