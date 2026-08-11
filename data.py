import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


@dataclass(frozen=True)
class Arc:
    """A move or holdover arc in the time-expanded network."""

    tail: str
    head: str
    tau: int
    kind: str  # "move" or "hold"
    active_times: Tuple[int, ...]
    delta_v_km_s: Optional[float] = None
    cost: float = 0.0


@dataclass
class NetworkData:
    nodes: list
    commodities: list
    T: int
    arcs: list
    vehicles: dict
    arc_type: list
    mission: dict
    depot_node: list


def periods(T, tau=1):
    """Departure times whose arrival remains inside the mission horizon."""
    return tuple(range(0, T - tau))


def activity_component(facility_name):
    return f"act_{facility_name}"


def add_two_way(arcs, tail, head, tau, active_times, delta_v_km_s=None):
    arcs.append(Arc(tail, head, tau, "move", active_times, delta_v_km_s=delta_v_km_s))
    arcs.append(Arc(head, tail, tau, "move", active_times, delta_v_km_s=delta_v_km_s))


def _load_demand_profile(profile_path):
    path = Path(profile_path)
    with path.open("r", encoding="utf-8") as f:
        profile = json.load(f)

    required = {
        "mission_years",
        "days_per_year",
        "days_per_step",
        "payload_lead_days",
        "events",
    }
    missing = sorted(required - profile.keys())
    if missing:
        raise ValueError(f"Demand profile is missing fields: {', '.join(missing)}")

    mission_years = int(profile["mission_years"])
    days_per_year = int(profile["days_per_year"])
    days_per_step = int(profile["days_per_step"])
    lead_days = int(profile["payload_lead_days"])
    if min(mission_years, days_per_year, days_per_step) <= 0:
        raise ValueError("mission_years, days_per_year, and days_per_step must be positive")

    mission_days = mission_years * days_per_year
    if mission_days % days_per_step != 0:
        raise ValueError("The mission duration must be divisible by days_per_step")
    if lead_days < 0 or lead_days % days_per_step != 0:
        raise ValueError("payload_lead_days must be a nonnegative multiple of days_per_step")

    normalized_events = []
    seen_ids = set()
    for index, raw in enumerate(profile["events"]):
        event_missing = sorted({"year", "event_id", "demand_day", "mass_kg"} - raw.keys())
        if event_missing:
            raise ValueError(
                f"Demand event {index} is missing fields: {', '.join(event_missing)}"
            )

        year = int(raw["year"])
        event_id = str(raw["event_id"])
        demand_day = raw["demand_day"]
        mass_kg = raw["mass_kg"]
        if isinstance(demand_day, bool) or not isinstance(demand_day, (int, float)):
            raise ValueError(f"Demand event {event_id} has a nonnumeric demand_day")
        if int(demand_day) != demand_day:
            raise ValueError(f"Demand event {event_id} demand_day must be an integer")
        demand_day = int(demand_day)
        if year < 1 or year > mission_years:
            raise ValueError(f"Demand event {event_id} has year outside 1..{mission_years}")
        if demand_day < 0 or demand_day > mission_days:
            raise ValueError(
                f"Demand event {event_id} demand_day must be inside 0..{mission_days}"
            )
        if demand_day % days_per_step != 0:
            raise ValueError(
                f"Demand event {event_id} demand_day must be a multiple of {days_per_step}"
            )
        if isinstance(mass_kg, bool) or not isinstance(mass_kg, (int, float)) or mass_kg <= 0:
            raise ValueError(f"Demand event {event_id} mass_kg must be positive")

        unique_id = (year, event_id)
        if unique_id in seen_ids:
            raise ValueError(f"Duplicate demand event id: year={year}, event_id={event_id}")
        seen_ids.add(unique_id)

        demand_step = demand_day // days_per_step
        normalized_events.append(
            {
                "year": year,
                "event_id": event_id,
                "demand_day": demand_day,
                "demand_step": demand_step,
                "supply_day": max(0, demand_day - lead_days),
                "supply_step": max(0, demand_step - lead_days // days_per_step),
                "mass_kg": float(mass_kg),
            }
        )

    normalized_events.sort(key=lambda event: (event["demand_step"], event["year"], event["event_id"]))
    profile["events"] = normalized_events
    profile["profile_path"] = str(path)
    return profile


def get_data(profile_path):
    """Build the full-horizon network from an explicit payload-demand profile."""
    profile = _load_demand_profile(profile_path)

    nodes = ["LEO", "GEO", "GTO", "EML1", "NRHO", "LLO", "Moon"]
    commodities = ["PL", "H2O", "Prop", "H2O_Tank", "Prop_Tank", "Infra"]
    depot_node = ["Moon", "GEO", "GTO", "EML1", "NRHO", "LLO"]

    mission_years = int(profile["mission_years"])
    days_per_year = int(profile["days_per_year"])
    days_per_step = int(profile["days_per_step"])
    mission_days = mission_years * days_per_year
    mission_steps = mission_days // days_per_step
    T = mission_steps + 1

    demand_by_year_kg = {
        str(year): sum(
            event["mass_kg"] for event in profile["events"] if event["year"] == year
        )
        for year in range(1, mission_years + 1)
    }
    mission = {
        "profile_name": profile.get("name", Path(profile_path).stem),
        "profile_path": profile["profile_path"],
        "mission_years": mission_years,
        "days_per_year": days_per_year,
        "days_per_step": days_per_step,
        "mission_days": mission_days,
        "mission_steps": mission_steps,
        "payload_lead_days": int(profile["payload_lead_days"]),
        "PL_supply_lead": int(profile["payload_lead_days"]) // days_per_step,
        "demand_events": profile["events"],
        "demand_by_year_kg": demand_by_year_kg,
        "total_payload_kg": sum(event["mass_kg"] for event in profile["events"]),
    }

    vehicles = {
        "OTV": {
            "payload_cap": 40000.0,
            "propellant_cap": 40000.0,
            "dry_mass": 6000.0,
            "isp_s": 420,
        },
        "RT": {
            "payload_cap": 30000.0,
            "propellant_cap": 20000.0,
            "dry_mass": 4000.0,
            "isp_s": 420.0,
        },
    }
    arc_type = ["OTV", "RT", "hold"]

    arcs = []
    all_hold_times = periods(T, tau=1)
    for node in nodes:
        arcs.append(Arc(node, node, 1, "hold", all_hold_times, 0))

    add_two_way(arcs, "LEO", "GEO", 1, periods(T, 1), delta_v_km_s=4.33)
    add_two_way(arcs, "LEO", "GTO", 1, periods(T, 1), delta_v_km_s=2.86)
    add_two_way(arcs, "LEO", "EML1", 2, periods(T, 2), delta_v_km_s=3.77)
    add_two_way(arcs, "LEO", "NRHO", 2, periods(T, 2), delta_v_km_s=3.6)
    add_two_way(arcs, "LEO", "LLO", 2, periods(T, 2), delta_v_km_s=4.04)
    add_two_way(arcs, "LEO", "Moon", 2, periods(T, 2), delta_v_km_s=5.93)

    add_two_way(arcs, "GEO", "GTO", 1, periods(T, 1), delta_v_km_s=1.47)
    add_two_way(arcs, "GEO", "EML1", 2, periods(T, 2), delta_v_km_s=1.38)
    add_two_way(arcs, "GEO", "NRHO", 2, periods(T, 2), delta_v_km_s=1.47)
    add_two_way(arcs, "GEO", "LLO", 2, periods(T, 2), delta_v_km_s=2.05)
    add_two_way(arcs, "GEO", "Moon", 2, periods(T, 2), delta_v_km_s=3.92)

    add_two_way(arcs, "GTO", "EML1", 2, periods(T, 2), delta_v_km_s=1.31)
    add_two_way(arcs, "GTO", "NRHO", 2, periods(T, 2), delta_v_km_s=1.1)
    add_two_way(arcs, "GTO", "LLO", 2, periods(T, 2), delta_v_km_s=1.58)
    add_two_way(arcs, "GTO", "Moon", 2, periods(T, 2), delta_v_km_s=3.47)

    add_two_way(arcs, "EML1", "NRHO", 1, periods(T, 1), delta_v_km_s=0.2)
    add_two_way(arcs, "EML1", "LLO", 1, periods(T, 1), delta_v_km_s=0.64)
    add_two_way(arcs, "EML1", "Moon", 2, periods(T, 2), delta_v_km_s=2.51)

    add_two_way(arcs, "NRHO", "LLO", 1, periods(T, 1), delta_v_km_s=0.73)
    add_two_way(arcs, "NRHO", "Moon", 1, periods(T, 1), delta_v_km_s=2.6)
    add_two_way(arcs, "LLO", "Moon", 1, periods(T, 1), delta_v_km_s=1.87)

    return NetworkData(
        nodes=nodes,
        commodities=commodities,
        T=T,
        arcs=arcs,
        vehicles=vehicles,
        arc_type=arc_type,
        mission=mission,
        depot_node=depot_node,
    )
