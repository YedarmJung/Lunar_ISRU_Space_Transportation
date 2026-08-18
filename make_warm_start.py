"""Build a Gurobi MIP start (.mst) from a saved latest_solution.json.

Only the integer variables (y, N_sc, SWE, DWE) are written.  The continuous
variables are deliberately left undefined so Gurobi fixes the integers and
re-solves the LP for the rest.  That reproduces the source objective while
recomputing the continuous values cleanly, which also avoids carrying over the
tiny bound violations Gurobi reported for the source solution.

Set SOLUTION_PATH below to the run you want to warm start from, then:
    python make_warm_start.py
The paths can still be overridden on the command line if needed:
    python make_warm_start.py <solution.json> -o results/start.mst
"""

import argparse
import json
from pathlib import Path

from data import get_data
from model import build_model


# The saved solution to build the MIP start from.
SOLUTION_PATH = Path("results") / "plots_20260815_143840" / "latest_solution.json"
# Where to write the .mst.  None -> start.mst next to the solution above.
OUTPUT_PATH = None


def _load_solution(path):
    with Path(path).open(encoding="utf-8") as f:
        return json.load(f)


def _resolve_profile(solution, override):
    """The start is only valid for the profile the solution was produced from."""
    if override is not None:
        return Path(override)
    profile_path = solution.get("mission", {}).get("profile_path")
    if not profile_path:
        raise SystemExit(
            "Solution has no mission.profile_path; pass --profile explicitly."
        )
    path = Path(profile_path)
    if not path.exists():
        raise SystemExit(
            f"Demand profile recorded in the solution is missing: {path}\n"
            "Pass --profile with the correct path."
        )
    return path


def apply_integer_start(variables, solution):
    """Set .Start on every integer variable; leave continuous ones undefined."""
    y = variables["y"]
    n_sc = variables["N_sc"]
    swe = variables["SWE"]
    dwe = variables["DWE"]

    # A complete integer assignment lets Gurobi finish the start with a single
    # LP solve instead of a sub-MIP, so the zero trips must be written too.
    for gvar in y.values():
        gvar.Start = 0.0

    trips = solution.get("trips", [])
    for trip in trips:
        key = (trip["mode"], trip["arc"], trip["time"])
        if key not in y:
            raise SystemExit(
                f"Trip {key} is not a variable in the rebuilt model.\n"
                "The solution and the demand profile do not match."
            )
        y[key].Start = float(trip["count"])

    fleet_by_year = solution.get("fleet_additions_by_year", {})
    if not fleet_by_year:
        raise SystemExit("Solution has no fleet_additions_by_year.")
    for (name, year), gvar in n_sc.items():
        # build_solution writes these under str(year + 1).
        entry = fleet_by_year.get(str(year + 1))
        if entry is None or name not in entry:
            raise SystemExit(
                f"fleet_additions_by_year is missing {name} for year {year + 1}."
            )
        gvar.Start = float(entry[name])

    facilities = solution.get("facilities", {})
    # Keep the value locally: a freshly added Var cannot be read back until update().
    swe_start = 1.0 if facilities.get("SWE", {}).get("installed") else 0.0
    swe.Start = swe_start

    # build_solution only records depots that were actually built; the rest are 0.
    installed_depots = set(facilities.get("DWE", {}))
    for node, gvar in dwe.items():
        gvar.Start = 1.0 if node in installed_depots else 0.0

    return {
        "y": len(y),
        "trips_nonzero": len(trips),
        "N_sc": len(n_sc),
        "DWE_installed": len(installed_depots),
        "SWE_installed": bool(swe_start),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "solution", nargs="?", default=None,
        help="path to a latest_solution.json (default: SOLUTION_PATH)",
    )
    parser.add_argument(
        "-o", "--output", default=None,
        help="output .mst path (default: OUTPUT_PATH, else <solution dir>/start.mst)",
    )
    parser.add_argument(
        "--profile", default=None,
        help="demand profile to rebuild the model from "
             "(default: the one recorded in the solution)",
    )
    args = parser.parse_args()

    solution_path = Path(args.solution) if args.solution else Path(SOLUTION_PATH)
    if not solution_path.exists():
        raise SystemExit(f"Solution file not found: {solution_path}")

    solution = _load_solution(solution_path)
    profile_path = _resolve_profile(solution, args.profile)
    output_path = Path(
        args.output or OUTPUT_PATH or (solution_path.parent / "start.mst")
    )

    print(f"Solution : {solution_path}")
    print(f"Profile  : {profile_path}")
    print(f"Objective: {solution.get('objective', float('nan')):,.0f}")

    data = get_data(profile_path)
    if solution.get("T") not in (None, data.T):
        raise SystemExit(
            f"Time horizon mismatch: solution T={solution['T']}, "
            f"rebuilt model T={data.T}. Wrong profile?"
        )

    # OutputFlag 0 keeps the rebuild quiet; the model is never optimized here.
    model, variables = build_model(data, gurobi_params={"OutputFlag": 0})
    counts = apply_integer_start(variables, solution)
    model.update()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    model.write(str(output_path))

    print(
        f"\nInteger start: {counts['trips_nonzero']:,} nonzero trips of "
        f"{counts['y']:,} y vars, {counts['N_sc']} N_sc vars, "
        f"SWE={counts['SWE_installed']}, {counts['DWE_installed']} DWE installed"
    )
    print(f"Wrote MIP start -> {output_path}")
    print("\nTo use it, set WARM_START_PATH in run.py:")
    print(f'    WARM_START_PATH = Path("{output_path.as_posix()}")')


if __name__ == "__main__":
    main()
