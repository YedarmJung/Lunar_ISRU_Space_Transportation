import gurobipy as gp
from gurobipy import GRB
from math import exp

from build_Q import G0_KM_S2, build_Q
from build_dit import build_dit
from build_cost import BUILD_COST as bc


INFRA_RESUPPLY_CAP_T = 100.0
FLEET_CAP = {"OTV": 1, "RT": 2}

def steps_per_year(data):
    days_per_year = data.mission["days_per_year"]
    days_per_step = data.mission["days_per_step"]
    if days_per_year % days_per_step != 0:
        raise ValueError("days_per_year must be divisible by days_per_step")
    return days_per_year // days_per_step


def t_to_year(t, data):
    year = t // steps_per_year(data)
    return min(year, data.mission["mission_years"] - 1)

def build_model(data, gurobi_params=None):
    nodes = data.nodes
    commodities = data.commodities
    arcs = data.arcs
    vehicles = data.vehicles
    arcs_type = data.arc_type


    T = data.T
    A = range(len(arcs))

    mission = data.mission
    mission_years = mission["mission_years"]
    final_year = mission_years - 1
    year_indices = range(mission_years)
    steps_in_year = steps_per_year(data)
    installation_step_to_year = {
        year * steps_in_year: year for year in range(1, mission_years)
    }
    resupply_lead_steps = steps_in_year // 2
    infra_supply_time_by_year = {
        year: year * steps_in_year - resupply_lead_steps
        for year in range(1, mission_years)
    }
    infra_supply_times = tuple(infra_supply_time_by_year.values())


    out_arcs = {i: [a for a in A if arcs[a].tail == i] for i in nodes}
    in_arcs = {i: [a for a in A if arcs[a].head == i] for i in nodes}


    Q = build_Q(data)
    m = gp.Model("OOS_using_lunar_ISRU")
    for name, value in (gurobi_params or {}).items():
        m.setParam(name, value)

    '''-----------------------Define decision variables-----------------------'''

    service_index = [
        (event["year"], event["event_id"])
        for event in mission["demand_events"]
    ]
    service = m.addVars(service_index, vtype=GRB.BINARY, name="service")
    d_it = build_dit(data, service)
    
    LUNAR_NODES = {"Moon", "LLO"}
    dep=[]
    #flow variables
    for a, arc in enumerate(arcs):
        # 1) arc 종류에 따라 허용 mode 선택
        if arc.kind == "move":
            allowed_modes = ["OTV", "RT"]
        elif arc.kind == "hold":
            allowed_modes = ["OTV", "RT", "hold"]
        else:
            continue
        for v in allowed_modes:
            if v == "OTV" and (arc.tail in LUNAR_NODES or arc.head in LUNAR_NODES):
                continue 
            # 2) mode별 허용 commodity 선택
            if arc.kind == "move":
                if v == "OTV":
                    # OTV: payload와 propellant만 허용한다고 가정
                    arc_components = ["PL", "Prop"]
                elif v == "RT":
                    # RT: 물과 propellant만 운반, 필요하면 Hw도 허용 가능
                    arc_components = ["H2O", "Prop", "H2O_Tank", "Prop_Tank", "Infra"]
                else:
                    arc_components = []

            elif arc.kind == "hold":
                if v == "OTV":
                    arc_components = ["PL", "Prop"]

                elif v == "RT":
                    arc_components = ["H2O", "Prop", "H2O_Tank", "Prop_Tank","Infra"]

                elif v == "hold":
                    arc_components = ["PL", "H2O", "Prop",
                                    "H2O_Tank", "Prop_Tank","Infra"]

            for c in arc_components:
                for t in arc.active_times:
                    if t + arc.tau > T - 1:
                        continue
                    dep.append((c, v, a, t))

    x = m.addVars(dep, lb=0, vtype=GRB.CONTINUOUS, name="x")

    spacecraft_index=[]
    #spacecraft variables
    for a, arc in enumerate(arcs):
        allowed_modes = ["OTV", "RT"]
        for v in allowed_modes:
            if v == "OTV" and (arc.tail in LUNAR_NODES or arc.head in LUNAR_NODES):
                continue
            for t in arc.active_times:
                if t + arc.tau > T - 1:
                    continue

                spacecraft_index.append((v, a, t))
    y = m.addVars(spacecraft_index, lb=0, ub=2, vtype=GRB.INTEGER, name="y")

    # 각 년도에 새로 배치될 vehicles
    N_sc = m.addVars(
        ["OTV", "RT"], year_indices, lb=0, ub=5, vtype=GRB.INTEGER, name="N_sc"
    )

    #ISRU/storage variables
    E_SWE = ["Moon_SWE"]
    E_Depot = list(data.depot_node)

    E = E_SWE+E_Depot

    DWE = m.addVars(E_Depot, vtype=GRB.BINARY, name="DWE") #DWE 설치 여부
    SWE = m.addVar(vtype=GRB.BINARY, name="SWE") #SWE 설치 여부
    Storage_H2O = m.addVars(E_Depot, year_indices, lb=0, name="Storage_H2O")
    Storage_Prop = m.addVars(E_Depot, year_indices, lb=0, name="Storage_Prop") #Storage 사이징

    q = m.addVars(E, year_indices, lb=0, name="q") #DWE, SWE 사이징
    q_operation = m.addVars(E,range(T), lb=0, name="q_operation")

    # 지구 보급 연료
    earth_prop = m.addVars(["GTO", "Moon"], range(T), lb=0, name="earth_prop")

    #지구 보급 탱크
    first_Tank = m.addVars(
        ["H2O_Tank", "Prop_Tank"], year_indices, lb=0, name="first_tank"
    )

    #인프라 지구 추가 보급
    infra_supply = m.addVars(
        infra_supply_times,
        lb=0,
        ub=INFRA_RESUPPLY_CAP_T,
        name="infra_supply_GTO",
    )

    '''-----------------------objective-----------------------'''
    # ------------------------------------------------------------
    # Facility build cost
    # ------------------------------------------------------------

    #총 제작 코스트 + 첫 배치 코스트
    obj_swe = (
        bc["SWE_fixed_musd"] * SWE
        + bc["SWE_musd_per_t"] * q["Moon_SWE", final_year]
        + bc["transfer_musd_per_t"]["Moon"] * q["Moon_SWE", 0]
    )
    obj_dwe = gp.quicksum(
        bc["DWE_fixed_musd"] * DWE[p]
        + bc["DWE_musd_per_t"] * q[p, final_year]
        + bc["transfer_musd_per_t"][p] * q[p, 0]
        for p in E_Depot
    )


    obj_storage = gp.quicksum(
        bc["Storage_H2O_musd_per_t"] * Storage_H2O[p, final_year]
        + bc["transfer_musd_per_t"][p] * Storage_H2O[p, 0]
        + bc["Storage_Prop_musd_per_t"] * Storage_Prop[p, final_year]
        + bc["transfer_musd_per_t"][p] * Storage_Prop[p, 0]
        for p in E_Depot
    )

    #추가 인프라 GTO 보내는 비용
    obj_infra_to_gto = bc["transfer_musd_per_t"]["GTO"] * gp.quicksum(
        infra_supply[t] for t in infra_supply_times
    )

    # Every annual vehicle/tank addition pays manufacturing and deployment;
    obj_tank = (
    # Initial H2O tank: manufacturing + direct Moon delivery
        (
            bc["Storage_H2O_musd_per_t"]
            + bc["transfer_musd_per_t"]["Moon"]
        )
        * first_Tank["H2O_Tank", 0]

        # Initial propellant tank: manufacturing + direct Moon delivery
        + (
            bc["Storage_Prop_musd_per_t"]
            + bc["transfer_musd_per_t"]["Moon"]
        )
        * first_Tank["Prop_Tank", 0]

        # Later tanks: manufacturing only.
        # GTO delivery is already charged through obj_infra_to_gto.
        + gp.quicksum(
            bc["Storage_H2O_musd_per_t"]
            * first_Tank["H2O_Tank", year]

            + bc["Storage_Prop_musd_per_t"]
            * first_Tank["Prop_Tank", year]

            for year in range(1, mission_years)
        )
    )

    obj_vehicle = (
    # All OTVs are externally delivered to GTO
        gp.quicksum(
            (
                bc["OTV_unit_musd"]
                + bc["transfer_musd_per_t"]["GTO"]
                * vehicles["OTV"]["dry_mass_t"]
            )
            * N_sc["OTV", year]
            for year in year_indices
        )

        # Initial RT is predeployed at Moon
        + (
            bc["RT_unit_musd"]
            + bc["transfer_musd_per_t"]["Moon"]
            * vehicles["RT"]["dry_mass_t"]
        )
        * N_sc["RT", 0]

        # Later RTs are delivered to GTO
        + gp.quicksum(
            (
                bc["RT_unit_musd"]
                + bc["transfer_musd_per_t"]["GTO"]
                * vehicles["RT"]["dry_mass_t"]
            )
            * N_sc["RT", year]
            for year in range(1, mission_years)
        )
    )

    obj_spacecraft = obj_vehicle + obj_tank

    # Maintenance is charged on the stock that actually exists in each year.
    # A facility installed in year y therefore pays maintenance only from y on.
    obj_maint = bc["ISRU_maint_frac_per_yr"] * gp.quicksum(
        (bc["ISRU_spares_musd_per_t"] + bc["transfer_musd_per_t"]["Moon"])
        * q["Moon_SWE", year]
        + gp.quicksum(
            (bc["ISRU_spares_musd_per_t"] + bc["transfer_musd_per_t"][p])
            * q[p, year]
            for p in E_Depot
        )
        for year in year_indices
    )

    # Every tonne of Earth-supplied propellant is charged exactly once.
    obj_earth_prop = gp.quicksum(
        (bc["initial_prop_musd_per_t"] + bc["transfer_musd_per_t"][node])
        * earth_prop[node, t]
        for node in ["GTO", "Moon"] for t in range(T)
    )

    # Rejecting a satellite is penalized by the cost of loading the additional
    # Earth-supplied propellant its payload would require for GTO -> GEO.

    unserved_penalty_musd_per_t = 0.92 * (
        bc["initial_prop_musd_per_t"] + bc["transfer_musd_per_t"]["GTO"]
    )
    obj_unserved_penalty = gp.quicksum(
        event["mass_t"]
        * unserved_penalty_musd_per_t
        * (1 - service[event["year"], event["event_id"]])
        for event in mission["demand_events"]
    )

    cost_terms = {
        "SWE": obj_swe,
        "DWE": obj_dwe,
        "storage": obj_storage,
        "infrastructure_to_GTO": obj_infra_to_gto,
        "spacecraft": obj_spacecraft,
        "maintenance": obj_maint,
        "earth_prop": obj_earth_prop,
        "unserved_penalty": obj_unserved_penalty,
    }
    obj = gp.quicksum(cost_terms.values())

    m.setObjective(obj, GRB.MINIMIZE)

    '''-----------------------Constraints-----------------------'''

    # The GTO payload in each resupply window is exactly the dry mass installed

    for year, supply_time in infra_supply_time_by_year.items():
        previous_year = year - 1
        installed_mass = (
            q["Moon_SWE", year] - q["Moon_SWE", previous_year]
            + gp.quicksum(
                q[p, year] - q[p, previous_year]
                + Storage_H2O[p, year] - Storage_H2O[p, previous_year]
                + Storage_Prop[p, year] - Storage_Prop[p, previous_year]
                for p in E_Depot
            )
            + first_Tank["H2O_Tank", year]
            + first_Tank["Prop_Tank", year]
        )
        m.addConstr(
            infra_supply[supply_time] == installed_mass,
            name=f"infra_supply_matches_additions_{year}",
        )
   
    #----------------------------xm value----------------------------

    arrival_expr ={}
    for (k, v, a, t) in x:
        t_arr = t + arcs[a].tau

        expr = gp.LinExpr()

        for c in commodities:
            if (c,v,a,t) in x:
                coeff = Q[v][a][k][c]

                if coeff != 0.0:
                    expr.addTerms(coeff, x[c,v,a,t])

        if (v,a,t) in y:
            coeff = Q[v][a][k][v]
            if coeff != 0.0:
                expr.addTerms(coeff, y[v,a,t])

        arrival_expr[k,v,a,t_arr] = expr
        if k == "Prop" and arcs[a].kind == "move":
            m.addConstr(
                expr >= 0.0,
                name=f"arrival_prop_nonnegative_{v}_{a}_{t}",
            )


    #----------------------------flow conservation constraints----------------------------
    for k in commodities:
        for i in nodes:
            for t in range(T):
                outflow = gp.quicksum(
                    x[k, v, a, t]
                    for v in arcs_type
                    for a in out_arcs[i]
                    if (k, v, a, t) in x
                )
                inflow = gp.quicksum(
                    arrival_expr[k,v,a,t]
                    for v in arcs_type
                    for a in in_arcs[i]
                    if (k, v, a, t) in arrival_expr
                )

                rhs = gp.LinExpr()
                rhs += d_it.get((k, i, t), 0.0)
                if k == "Prop" and i in ("GTO", "Moon"):
                    rhs += earth_prop[i, t]
                #처음 탱크
                if t == 0 and i == "Moon":
                    rhs += first_Tank.get((k,0),0)
                
                #SWE 생산물 (물 생산; 150% overhead -> Gkaravela 10.5 * 3/2.5)
                if i == "Moon" and k =="H2O":
                    rhs += 0.02917*3/2.5*data.mission["days_per_step"]*q_operation["Moon_SWE", t]

                #DWE 생산물 (depot 노드: 물 소비 -> 추진제 생산)
                #  물:추진제 = 3:2 (5.5:1 연소 시 잉여 O2 폐기), 150% overhead
                if i in E_Depot:
                    dwe_rate = 0.09722*3/2.5*data.mission["days_per_step"]  # 물 처리량/스텝
                    PROP_PER_H2O = 2.0/3.0                                   # 사용가능 추진제 / 물
                    if k == "H2O":
                        rhs -= dwe_rate * q_operation[i, t]
                    elif k == "Prop":
                        rhs += PROP_PER_H2O * dwe_rate * q_operation[i, t]

                # 첫 년도는 이미 배치 되어있는걸로
                # 그 이후엔 GTO로 페이로드 공급
                installation_year = installation_step_to_year.get(t)
                if installation_year is not None:
                    if i == "Moon" and k in ("H2O_Tank", "Prop_Tank"):
                        rhs += first_Tank[k, installation_year]

                    if k == "Infra" and i in E_Depot:
                        previous_year = installation_year - 1
                        installed_mass = (
                            q[i, installation_year] - q[i, previous_year]
                            + Storage_H2O[i, installation_year]
                            - Storage_H2O[i, previous_year]
                            + Storage_Prop[i, installation_year]
                            - Storage_Prop[i, previous_year]
                        )
                        if i == "Moon":
                            installed_mass += (
                                q["Moon_SWE", installation_year]
                                - q["Moon_SWE", previous_year]
                            )
                            installed_mass += (
                                first_Tank["H2O_Tank", installation_year]
                                + first_Tank["Prop_Tank", installation_year]
                            )
                        rhs -= installed_mass

                # Infrastructure is launched from Earth into GTO and then must
                # use the RT network to reach its installation node.
                if k == "Infra" and i == "GTO" and t in infra_supply:
                    rhs += infra_supply[t]

                m.addConstr(outflow - inflow <= rhs, name=f"mass[{k},{i},{t}]")

    #----------------------------concurrency----------------------------
    H2O_TANK_RATIO = 40.0      # t H2O per t H2O tank (dimensionless ratio)
    PROP_TANK_RATIO = 1.478    # t propellant per t prop tank (dimensionless ratio)

    for a, arc in enumerate(arcs):
        for t in arc.active_times:
            if t + arc.tau > T - 1:
                continue

            # ---------------- move arcs ----------------
            # ----- OTV -----
            # payload: PL <= payload_cap * y[OTV,a,t]
            if ("PL", "OTV", a, t) in x and ("OTV", a, t) in y:
                m.addConstr(
                    x["PL", "OTV", a, t]
                    <= vehicles["OTV"]["payload_cap_t"] * y["OTV", a, t],
                    name=f"OTV_payload_a{a}_t{t}",
                )
            # propellant: Prop <= propellant_cap * y[OTV,a,t]
            if ("Prop", "OTV", a, t) in x and ("OTV", a, t) in y:
                m.addConstr(
                    x["Prop", "OTV", a, t]
                    <= vehicles["OTV"]["propellant_cap_t"] * y["OTV", a, t],
                    name=f"OTV_prop_a{a}_t{t}",
                )

                # ----- RT -----
            if ("RT", a, t) in y:
                # (1) 전체 무게: 싣는 모든 것의 합 <= payload_cap * y + propellant_cap * y
                total_terms = [
                    x[c, "RT", a, t]
                    for c in ["H2O", "Prop", "H2O_Tank", "Prop_Tank", "Infra"]
                    if (c, "RT", a, t) in x
                ]
                if total_terms:
                    m.addConstr(
                        gp.quicksum(total_terms)
                        <= (
                            vehicles["RT"]["payload_cap_t"]
                            + vehicles["RT"]["propellant_cap_t"]
                        ) * y["RT", a, t],
                        name=f"RT_totalmass_a{a}_t{t}",
                    )

                # (2)+(4) 추진제 총량 <= 자기탱크 용량 + 운반탱크 용량
                #   Prop <= propellant_cap * y + 1.478 * Prop_Tank
                if ("Prop", "RT", a, t) in x:
                    prop_cap_rhs = vehicles["RT"]["propellant_cap_t"] * y["RT", a, t]
                    if ("Prop_Tank", "RT", a, t) in x:
                        prop_cap_rhs += PROP_TANK_RATIO * x["Prop_Tank", "RT", a, t]
                    m.addConstr(
                        x["Prop", "RT", a, t] <= prop_cap_rhs,
                        name=f"RT_prop_total_a{a}_t{t}",
                    )

                # 탱크와 물 자체가 차지하는 페이로드 H2O + H2O_Tank + Prop_Tank <= payload cap * y
                total_terms = [
                    x[c, "RT", a, t]
                    for c in ["H2O", "H2O_Tank", "Prop_Tank", "Infra"]
                    if (c, "RT", a, t) in x
                ]
                if total_terms:
                    m.addConstr(
                        gp.quicksum(total_terms)
                        <= vehicles["RT"]["payload_cap_t"] * y["RT", a, t],
                        name=f"RT_payload_mass_a{a}_t{t}",
                    )

            # (3) 운반 물 <= 40 * 운반 물탱크   (y 무관, 순수 부등식)
            if ("H2O", "RT", a, t) in x and ("H2O_Tank", "RT", a, t) in x:
                m.addConstr(
                    x["H2O", "RT", a, t]
                    <= H2O_TANK_RATIO * x["H2O_Tank", "RT", a, t],
                    name=f"RT_H2Otank_a{a}_t{t}",
                )

            # ---------------- hold arcs ----------------
            if arc.kind == "hold":
                node = arc.tail  # hold arc: tail == head

                # depot/Moon: storage 사이징 변수로 상한
                if node in E_Depot:
                    # H2O 저장 <= Storage_H2O[node]
                    if ("H2O", "hold", a, t) in x and node in E_Depot:
                        m.addConstr(
                            x["H2O", "hold", a, t] <= H2O_TANK_RATIO * Storage_H2O[node, t_to_year(t, data)],
                            name=f"store_H2O_{node}_t{t}",
                        )
                    # Prop 저장 <= Storage_Prop[node]
                    if ("Prop", "hold", a, t) in x and node in E_Depot:
                        m.addConstr(
                            x["Prop", "hold", a, t]
                            <= PROP_TANK_RATIO * Storage_Prop[node, t_to_year(t, data)],
                            name=f"store_Prop_{node}_t{t}",
                        )


    #---------------------ISRU operation------------------------
    for e in E:
        for t in range(T - 1):
            m.addConstr(q_operation[e, t] <= q[e, t_to_year(t, data)], name=f"prod_cap_{e}_{t}")
        # The final point has no outgoing interval in which production can be used.
        m.addConstr(q_operation[e, T - 1] == 0, name=f"prod_terminal_{e}")

    #---------------------vehicle flow conservation------------------------
    # ---------------- 초기 우주선 배치 노드 ----------------
    INIT_NODE = {"OTV": "GTO", "RT": "Moon"}

    # ---------------- 우주선 대수 보존 ----------------
    for v in ["OTV", "RT"]:
        for i in nodes:
            # At the terminal point vehicles may leave the modeled horizon.
            for t in range(T - 1):
                # i로 도착: in_arc를 통해 (출발시점 + tau == t)인 우주선
                arrive = gp.quicksum(
                    y[v, a, t - arcs[a].tau]
                    for a in in_arcs[i]
                    if t - arcs[a].tau >= 0 and (v, a, t - arcs[a].tau) in y
                )
                # i에서 출발: out_arc를 t에 출발
                depart = gp.quicksum(
                    y[v, a, t]
                    for a in out_arcs[i]
                    if (v, a, t) in y
                )
                # 초기 배치: t=0에 INIT_NODE[v]에 N_sc[v]대
                if t == 0 and i == INIT_NODE[v]:
                    init = N_sc[v, 0]
                elif t in infra_supply_time_by_year.values() and i == INIT_NODE["OTV"]: #이후 배치는 무조건 가장 가까운 궤도에서
                    init = N_sc[v, (t + resupply_lead_steps) // steps_in_year]
                else : 
                    init = 0

                m.addConstr(arrive + init == depart,
                            name=f"veh_cons_{v}_{i}_{t}")


    total_pl_demand_t = sum(
        event["mass_t"] for event in mission["demand_events"]
    )
    op_days = max(1, data.mission["mission_days"])
    swe_rate = 0.02917 * 2 / 1.5    # t water/day per t SWE (dimensionless mass ratio)

    BIG_M_T = max(30.0, 40.0 * total_pl_demand_t / (swe_rate * op_days))


    for e in E:
        for year in range(1, mission_years):
            m.addConstr(
                q[e, year] >= q[e, year - 1],
                name=f"facility_stock_nondec_{e}_{year}",
            )
    for e in E_Depot:
        for year in range(1, mission_years):
            m.addConstr(
                Storage_H2O[e, year] >= Storage_H2O[e, year - 1],
                name=f"H2O_storage_stock_nondec_{e}_{year}",
            )
            m.addConstr(
                Storage_Prop[e, year] >= Storage_Prop[e, year - 1],
                name=f"Prop_storage_stock_nondec_{e}_{year}",
            )

    # Link every year's installed stock to the one-time site decision.
    for e in E_SWE:
        for year in year_indices:
            m.addConstr(
                q[e, year] <= BIG_M_T,
                #q[e, year] <= BIG_M_T * SWE,
                name=f"install_SWE_{e}_{year}",
            )

    for e in E_Depot:
        for year in year_indices:
            m.addConstr(
                q[e, year] <= BIG_M_T,
                #q[e, year] <= BIG_M_T * DWE[e],
                name=f"install_DWE_{e}_{year}",
            )

    variables = {
        "x": x,
        "xm": arrival_expr,
        "y": y,
        "N_sc": N_sc,
        "DWE": DWE,
        "SWE": SWE,
        "Storage_H2O": Storage_H2O,
        "Storage_Prop": Storage_Prop,
        "q": q,
        "q_operation": q_operation,
        "earth_prop": earth_prop,
        "first_Tank": first_Tank,
        "infra_supply": infra_supply,
        "service": service,
        "unserved_penalty_musd_per_t": unserved_penalty_musd_per_t,
        "cost_terms": cost_terms,
    }
    return m, variables


def solve_model(model, callback=None):
    """Optimize a model that has already been built."""
    model.optimize(callback)
    return model


def build_and_solve(data, gurobi_params=None):
    """Compatibility wrapper used by the main runner."""
    model, variables = build_model(data, gurobi_params=gurobi_params)
    solve_model(model)
    return model, variables
