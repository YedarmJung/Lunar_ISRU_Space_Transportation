def build_dit(data):
    """Exogenous demand/supply vector d_it for the mass-balance constraint.

    Sign convention (matches model.py `outflow - inflow == rhs`):
        positive = supply/source at the node, negative = demand/sink.

    Encoded here:
      * GEO payload demand: an identical pulse every GEO_demand_period steps,
        starting after the setup phase (negative = must be delivered to GEO).
      * LEO payload supply: one large stock injected at t=0.  The LEO holdover
        arc carries the unused surplus forward at no cost, so this behaves as an
        effectively unlimited payload reservoir the OTV draws from as needed.
    """
    d = {}
    m = data.mission

    setup = m["setup_steps"]
    period = m["GEO_demand_period"]
    demand = m["GEO_demand_PL_kg"]
    lead = m["PL_supply_lead"]
    n_pulses = m["n_pulses"]        # ver_time_horizon

    # ver_time_horizon: 정확히 n_pulses 번의 GEO 수요만 생성한다(닫는 슬롯에서 펄스가 튀지
    # 않도록 range(setup, T) 루프 대신 명시적 개수 사용).  펄스 위치는 setup, setup+P, ...
    # (roll-in n_rollin + 정상상태 n_steady + seam 1 = n_pulses).
    for k in range(n_pulses):
        t = setup + k * period
        # GEO 수요 (sink)
        d[("PL", "GEO", t)] = d.get(("PL", "GEO", t), 0.0) - demand
        # LEO 공급 (source), 수요 lead 스텝 전에만 풀림
        t_sup = max(0, t - lead)
        d[("PL", "LEO", t_sup)] = d.get(("PL", "LEO", t_sup), 0.0) + demand

    return d