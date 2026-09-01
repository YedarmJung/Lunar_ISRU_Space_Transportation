"""Build a Gurobi MIP start (.mst) from a saved latest_solution.json.

Only the integer variables (y, N_sc, SWE, DWE, service) are written.  The continuous
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
import shutil
import tempfile
from pathlib import Path

from data import get_data
from model import build_model


# The saved solution to build the MIP start from.
SOLUTION_PATH = Path("results") / "plots_20260828_161439" / "latest_solution.json"
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


def _write_mip_start(model, output_path):
    """Write through an ASCII-only temp path for Gurobi on Windows.

    Some Gurobi/Python combinations cannot write model files directly to a
    path containing non-ASCII characters. Python's file copy handles the final
    destination normally, so keep the path passed to Gurobi ASCII-only.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gurobi_mst_") as temp_dir:
        temp_path = Path(temp_dir) / "start.mst"
        model.write(str(temp_path))
        shutil.copyfile(temp_path, output_path)


def _arc_identity(arc):
    return (arc.tail, arc.head, arc.kind, arc.tau)


def _saved_arc_identity(trip, solution_arcs):
    """Return an arc identity that remains stable when arc IDs are renumbered."""
    saved_arc = solution_arcs.get(trip.get("arc"), {})
    tail = trip.get("tail", saved_arc.get("tail"))
    head = trip.get("head", saved_arc.get("head"))
    kind = trip.get("kind", saved_arc.get("kind"))
    tau = trip.get("tau", saved_arc.get("tau"))
    if tau is None and trip.get("arrival_time") is not None:
        tau = trip["arrival_time"] - trip["time"]
    if kind is None and tail is not None and head is not None:
        kind = "hold" if tail == head else "move"
    if None in (tail, head, kind, tau):
        raise SystemExit(
            f"Trip {trip} has no complete arc metadata. Re-export the source "
            "solution before building a warm start."
        )
    return (tail, head, kind, int(tau))


def apply_integer_start(data, variables, solution):
    """Set .Start on every integer variable; leave continuous ones undefined.

    Saved numeric arc IDs are deliberately not reused: removing a node changes
    every later ID. Trips are matched by endpoints, kind, and travel time, and
    trips touching nodes absent from the rebuilt network are discarded.
    """
    y = variables["y"]
    n_sc = variables["N_sc"]
    swe = variables["SWE"]
    dwe = variables["DWE"]
    service = variables["service"]

    # A complete integer assignment lets Gurobi finish the start with a single
    # LP solve instead of a sub-MIP, so the zero trips must be written too.
    for gvar in y.values():
        gvar.Start = 0.0

    current_arc_ids = {}
    for arc_id, arc in enumerate(data.arcs):
        identity = _arc_identity(arc)
        if identity in current_arc_ids:
            raise SystemExit(f"Rebuilt model has duplicate arc identity: {identity}")
        current_arc_ids[identity] = arc_id

    solution_arcs = {
        arc["id"]: arc for arc in solution.get("arcs", [])
    }
    trips = solution.get("trips", [])
    applied_trips = 0
    skipped_removed_trips = 0
    for trip in trips:
        identity = _saved_arc_identity(trip, solution_arcs)
        tail, head, _kind, _tau = identity
        if tail not in data.nodes or head not in data.nodes:
            skipped_removed_trips += 1
            continue

        arc_id = current_arc_ids.get(identity)
        if arc_id is None:
            raise SystemExit(
                f"Trip arc {identity} is not in the rebuilt model.\n"
                "The solution and the demand profile do not match."
            )
        key = (trip["mode"], arc_id, trip["time"])
        if key not in y:
            raise SystemExit(
                f"Trip {key} is not a variable in the rebuilt model.\n"
                "The solution and the demand profile do not match."
            )
        y[key].Start = float(trip["count"])
        applied_trips += 1

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

    service_by_event = {
        (event["year"], event["event_id"]): event.get("served", True)
        for event in solution.get("satellite_service", [])
    }
    for event_key, gvar in service.items():
        # Legacy solutions served every event and have no satellite_service list.
        gvar.Start = 1.0 if service_by_event.get(event_key, True) else 0.0

    return {
        "y": len(y),
        "trips_nonzero": applied_trips,
        "trips_skipped_removed_nodes": skipped_removed_trips,
        "N_sc": len(n_sc),
        "DWE_installed": len(installed_depots),
        "SWE_installed": bool(swe_start),
        "service": len(service),
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
    counts = apply_integer_start(data, variables, solution)
    model.update()

    _write_mip_start(model, output_path)

    print(
        f"\nInteger start: {counts['trips_nonzero']:,} nonzero trips of "
        f"{counts['y']:,} y vars, {counts['N_sc']} N_sc vars, "
        f"SWE={counts['SWE_installed']}, {counts['DWE_installed']} DWE installed"
    )
    if counts["trips_skipped_removed_nodes"]:
        print(
            "Skipped "
            f"{counts['trips_skipped_removed_nodes']:,} source trips touching "
            "nodes that are not in the rebuilt network."
        )
    print(f"Wrote MIP start -> {output_path}")
    print("\nTo use it, set WARM_START_PATH in run.py:")
    print(f'    WARM_START_PATH = Path("{output_path.as_posix()}")')


if __name__ == "__main__":
    main()
