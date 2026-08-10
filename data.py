from dataclasses import dataclass
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
    return tuple(range(0, T - tau))


def amort_factor(mission, horizon_years):
    """정상상태 블록(n_steady 주기)의 비용을 지평 H(년)로 환산하는 배율.

    AMORT = periods(H) / n_steady,  periods(H) = H*365 / period_days.
    목적함수의 opex 항에 AMORT 를 곱하면 짧은 window 최적해가 곧 미션-H 최적해가 된다.
    """
    period_days = mission["period_steps"] * mission["days_per_step"]
    periods_H = horizon_years * 365.0 / period_days
    return periods_H / mission["n_steady"], periods_H


def activity_component(facility_name):
    return f"act_{facility_name}"


def add_two_way(arcs, tail, head, tau, active_times, delta_v_km_s=None, isp_s=420.0):
    arcs.append(Arc(tail, head, tau, "move", active_times, delta_v_km_s=delta_v_km_s))
    arcs.append(Arc(head, tail, tau, "move", active_times, delta_v_km_s=delta_v_km_s))


def get_data(mission_years=10, days_per_step=5, n_rollin=1, n_steady=1,
             demand_period_days=60, setup_days=None, annual_t=100):   # ver_time_horizon: n_rollin/n_steady + cadence sweep
    nodes = [
        "LEO",
        "GEO",
        "GTO",
        "EML1",
        "NRHO",
        "LLO",
        "Moon"
    ]

    # Candidate ISRU facilities.  Each facility gets an activity column in x
    # with the deterministic name act_{facility_name}.

    commodities = ["PL", "H2O", "Prop", "H2O_Tank", "Prop_Tank"] # 끝 두개는 RT 가 물자를 운반할 때 쓰는 탱크임

    depot_node = [
        "Moon",
        "GEO",
        "GTO",
        "EML1",
        "NRHO",
        "LLO",
    ]

    DAYS_PER_STEP = days_per_step
    MISSION_YEARS = mission_years

    # Mission timing is defined in DAYS and converted to step counts, so changing
    # DAYS_PER_STEP automatically rescales the setup phase, demand cadence, and
    # payload lead time.  max(1, .) keeps every count at >= 1 step.
    # ver_time_horizon: cadence(수요 간격)를 sweep 가능하게 파라미터화.
    DEMAND_PERIOD_DAYS = demand_period_days                     # GEO payload pulse cadence
    SUPPLY_LEAD_DAYS = round(2.0/3.0 * DEMAND_PERIOD_DAYS)      # ~2/3 period 전에 공급(=40 @ 60d)
    if setup_days is None:
        setup_days = 1.5 * DEMAND_PERIOD_DAYS                  # setup >= 1.5 x 수요간격 (ISRU 사전적재)
    SETUP_DAYS = setup_days                                     # no-demand setup phase

    # ver_time_horizon: T 를 "미션 전체 길이"가 아니라 정상상태 window 로 구성한다.
    #   [0, ss)      setup + roll-in(n_rollin 주기): 전이 흡수 (비용 ×1, ramp)
    #   [ss, seam)   정상상태 블록(n_steady 주기): amortization 대상 (비용 ×AMORT)
    #   seam         seam(= ss 에 glue), [seam, T) 는 seam 걸친 τ=2 아크 도착 tail
    # mission_years 는 이제 amortization 지평 H 로만 쓰이고 T 를 결정하지 않는다.
    setup_steps  = max(1, round(SETUP_DAYS / DAYS_PER_STEP))
    period_steps = max(1, round(DEMAND_PERIOD_DAYS / DAYS_PER_STEP))
    MAX_TAU = 2
    steady_start = setup_steps + n_rollin * period_steps    # ver_time_horizon: ss (2번째 펄스)
    seam         = steady_start + n_steady * period_steps   # ver_time_horizon: se (ss 에 glue)
    T = seam + MAX_TAU + 1                                   # ver_time_horizon: 도착 tail 확보]
    all_hold_times = periods(T, tau=1)

    #payload
    GEO_DEMAND_PL_KG = annual_t *1000.0 * period_steps * days_per_step / 365

    mission = {
        "days_per_step": DAYS_PER_STEP,
        "mission_years": MISSION_YEARS,        # ver_time_horizon: amortization 지평 H (T 와 무관)
        "setup_steps": setup_steps,
        "GEO_demand_period": period_steps,     # (기존 이름 유지: build_dit/sweep 호환)
        "period_steps": period_steps,          # ver_time_horizon
        "n_rollin": n_rollin,                  # ver_time_horizon
        "n_steady": n_steady,                  # ver_time_horizon
        "steady_start": steady_start,          # ver_time_horizon: ss
        "seam": seam,                          # ver_time_horizon: se
        "n_pulses": n_rollin + n_steady + 1,   # ver_time_horizon: 총 GEO 수요 횟수(seam 포함)
        "GEO_demand_PL_kg": GEO_DEMAND_PL_KG,
        "PL_supply_lead": max(1, round(SUPPLY_LEAD_DAYS / DAYS_PER_STEP)),
    }

    # Fixed vehicle designs.  None means the scenario still needs a final value.
    vehicles = {
        "OTV": {
            "payload_cap": 40000.0,
            "propellant_cap": 40000.0,
            "dry_mass": 6000.0,
            "isp_s": 420,
        },
#        "RT": {
#            "payload_cap": 40000.0,
#            "propellant_cap": 40000.0,
#            "dry_mass": 6000.0,
#            "isp_s": 420.0,
#        },
        "RT": {
            "payload_cap": 30000.0,
            "propellant_cap": 20000.0,
            "dry_mass": 4000.0,
            "isp_s": 420.0,
        },
    }

    arc_type = [
        "OTV",
        "RT",
        "hold"
    ]

    arcs = []

    # Holdover arcs carry inventory and host ISRU activity components.
    for node in nodes:
        arcs.append(Arc(node, node, 1, "hold", all_hold_times, 0))

    add_two_way(arcs, "LEO", "GEO", 1, periods(T, 1), delta_v_km_s=4.33)
    add_two_way(arcs, "LEO", "GTO", 1, periods(T, 1), delta_v_km_s=2.86)
    add_two_way(arcs, "LEO", "EML1", 2, periods(T, 2), delta_v_km_s=3.77)
    add_two_way(arcs, "LEO", "NRHO", 2, periods(T, 2), delta_v_km_s=3.6) #3.95
    add_two_way(arcs, "LEO", "LLO", 2, periods(T, 2), delta_v_km_s=4.04)
    add_two_way(arcs, "LEO", "Moon", 2, periods(T,2), delta_v_km_s=5.93)

    add_two_way(arcs, "GEO", "GTO", 1, periods(T, 1), delta_v_km_s=1.47)
    add_two_way(arcs, "GEO", "EML1", 2, periods(T, 2), delta_v_km_s=1.38)
    add_two_way(arcs, "GEO", "NRHO", 2, periods(T, 2), delta_v_km_s=1.47) #1.47
    add_two_way(arcs, "GEO", "LLO", 2, periods(T, 2), delta_v_km_s=2.05)
    add_two_way(arcs, "GEO", "Moon", 2, periods(T,2), delta_v_km_s=3.92)

    add_two_way(arcs, "GTO", "EML1", 2, periods(T, 2),  delta_v_km_s=1.31)
    add_two_way(arcs, "GTO", "NRHO", 2, periods(T, 2), delta_v_km_s=1.1) #1.1
    add_two_way(arcs, "GTO", "LLO", 2, periods(T, 2), delta_v_km_s=1.58)
    add_two_way(arcs, "GTO", "Moon", 2, periods(T,2), delta_v_km_s=3.47)

    add_two_way(arcs, "EML1", "NRHO", 1, periods(T, 1), delta_v_km_s=0.2)
    add_two_way(arcs, "EML1", "LLO", 1, periods(T, 1), delta_v_km_s=0.64)
    add_two_way(arcs, "EML1", "Moon", 2, periods(T,2), delta_v_km_s=2.51)

    add_two_way(arcs, "NRHO", "LLO", 1, periods(T, 1), delta_v_km_s=0.73)
    add_two_way(arcs, "NRHO", "Moon", 1, periods(T,1), delta_v_km_s=2.6)

    add_two_way(arcs, "LLO", "Moon", 1, periods(T,1), delta_v_km_s=1.87)


    return NetworkData(
        nodes=nodes,
        commodities=commodities,
        T=T,
        arcs=arcs,
        vehicles=vehicles,
        arc_type = arc_type,
        mission=mission,
        depot_node = depot_node
    )
