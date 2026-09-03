"""Create hourly flow plots from newly improved Gurobi incumbents."""

import threading
import time

from gurobipy import GRB

from post_process import generate_flow_plot
from units import MODEL_UNITS, SOLUTION_SCHEMA_VERSION


TOL = 1e-6


class IncumbentFlowPlotter:
    """Keep callbacks light and render the latest incumbent off the solver thread."""

    def __init__(self, data, variables, plot_dir, interval_seconds=3600):
        self.data = data
        self.plot_dir = plot_dir
        self.interval_seconds = interval_seconds
        self.x_items = list(variables["x"].items())
        self.y_items = list(variables["y"].items())
        self.service_items = list(variables["service"].items())
        self.best_objective = None
        self.latest_snapshot = None
        self.snapshot_version = 0
        self.plotted_version = 0
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.worker = threading.Thread(target=self._plot_hourly, daemon=True)
        self.worker.start()

    def __call__(self, model, where):
        if where != GRB.Callback.MIPSOL:
            return

        try:
            objective = model.cbGet(GRB.Callback.MIPSOL_OBJ)
            if (
                self.best_objective is not None
                and objective >= self.best_objective - TOL
            ):
                return

            x_values = model.cbGetSolution([var for _, var in self.x_items])
            y_values = model.cbGetSolution([var for _, var in self.y_items])
            service_values = model.cbGetSolution(
                [var for _, var in self.service_items]
            )
            snapshot = self._build_snapshot(
                x_values, y_values, service_values, objective
            )
            with self.lock:
                self.best_objective = objective
                self.latest_snapshot = snapshot
                self.snapshot_version += 1
        except Exception as exc:  # keep plotting failures from stopping Gurobi
            print(f"\nCould not capture incumbent for hourly plot: {exc}")

    def close(self):
        self.stop_event.set()
        self.worker.join()

    def _plot_hourly(self):
        start_time = time.monotonic()
        elapsed_hours = 1
        while True:
            deadline = start_time + elapsed_hours * self.interval_seconds
            wait_seconds = max(0.0, deadline - time.monotonic())
            if self.stop_event.wait(wait_seconds):
                return

            with self.lock:
                if self.snapshot_version == self.plotted_version:
                    elapsed_hours += 1
                    continue
                snapshot = self.latest_snapshot
                self.plotted_version = self.snapshot_version

            snapshot = {
                **snapshot,
                "plot_title": (
                    "Commodity flow over time — "
                    f"incumbent at {elapsed_hours} h "
                    f"(objective {snapshot['objective_musd']:,.3f} MUSD)"
                ),
            }
            filename = f"flow_over_time_incumbent_{elapsed_hours:02d}h.png"
            try:
                path = generate_flow_plot(snapshot, self.plot_dir, filename)
                if path is not None:
                    print(f"\nSaved hourly incumbent flow plot -> {path}")
            except Exception as exc:  # plotting must never terminate optimization
                print(f"\nCould not generate hourly incumbent flow plot: {exc}")
            elapsed_hours += 1

    def _build_snapshot(self, x_values, y_values, service_values, objective):
        flows = []
        for ((commodity, mode, arc_id, time), _), value in zip(
            self.x_items, x_values
        ):
            if value <= TOL:
                continue
            arc = self.data.arcs[arc_id]
            flows.append(
                {
                    "commodity": commodity,
                    "mode": mode,
                    "arc": arc_id,
                    "tail": arc.tail,
                    "head": arc.head,
                    "kind": arc.kind,
                    "time": time,
                    "arrival_time": time + arc.tau,
                    "departed": value,
                }
            )

        trips = []
        for ((mode, arc_id, time), _), value in zip(self.y_items, y_values):
            if value <= TOL:
                continue
            arc = self.data.arcs[arc_id]
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
                    "count": int(round(value)),
                }
            )

        service_by_event = {
            event_key: value > 0.5
            for (event_key, _), value in zip(self.service_items, service_values)
        }
        satellite_service = [
            {
                **event,
                "served": service_by_event[event["year"], event["event_id"]],
            }
            for event in self.data.mission["demand_events"]
        ]

        return {
            "schema_version": SOLUTION_SCHEMA_VERSION,
            "units": MODEL_UNITS,
            "objective_musd": objective,
            "T": self.data.T,
            "nodes": self.data.nodes,
            "commodities": self.data.commodities,
            "mission": self.data.mission,
            "flows": flows,
            "trips": trips,
            "satellite_service": satellite_service,
        }
