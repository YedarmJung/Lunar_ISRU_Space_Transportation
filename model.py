import gurobipy as gp
from gurobipy import GRB

from build_Q import build_Q
from build_dit import build_dit
from build_cost import BUILD_COST as bc

def t_to_year(t, data):
    return t//(data.mission["days_per_year"]//data.mission["days_per_step"])

def build_model(data, gurobi_params=None):
    nodes = data.nodes
    commodities = data.commodities
    arcs = data.arcs
    vehicles = data.vehicles
    arcs_type = data.arc_type


    T = data.T
    A = range(len(arcs))

    mission = data.mission

    out_arcs = {i: [a for a in A if arcs[a].tail == i] for i in nodes}
    in_arcs = {i: [a for a in A if arcs[a].head == i] for i in nodes}


    d_it = build_dit(data)
    Q = build_Q(data)
    m = gp.Model("OOS_using_lunar_ISRU")
    for name, value in (gurobi_params or {}).items():
        m.setParam(name, value)

    '''-----------------------Define decision variables-----------------------'''
    
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
                    if arc.tail == "LEO":
                        arc_components = ["PL"]
                    else:
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
    y = m.addVars(spacecraft_index, lb=0, vtype=GRB.INTEGER, name="y")

    N_sc = m.addVars(["OTV", "RT"],range(T), lb=0, vtype=GRB.INTEGER, name="N_sc")

    #ISRU/storage variables
    E_SWE = ["Moon_SWE"]
    E_Depot = list(data.depot_node)

    E = E_SWE+E_Depot

    DWE = m.addVars(E_Depot, vtype=GRB.BINARY, name="DWE") #DWE 설치 여부
    SWE = m.addVar(vtype=GRB.BINARY, name="SWE") #SWE 설치 여부
    Storage_H2O = m.addVars(E_Depot, range(data.mission["mission_years"]), lb=0, name="Storage_H2O")
    Storage_Prop = m.addVars(E_Depot, range(data.mission["mission_years"]), lb=0, name="Storage_Prop") #Storage 사이징

    q = m.addVars(E,range(data.mission["mission_years"]), lb=0, name="q") #DWE, SWE 사이징
    q_operation = m.addVars(E,range(T), lb=0, name="q_operation")

    # Earth-supplied propellant can enter at any point in the full mission.
    earth_prop = m.addVars(["GTO", "Moon"], range(T), lb=0, name="earth_prop")
    first_Tank = m.addVars(["H2O_Tank", "Prop_Tank"],range(data.mission["mission_years"]), lb=0, name="first_tank")

    '''-----------------------objective-----------------------'''
    # ------------------------------------------------------------
    # Facility build cost
    # ------------------------------------------------------------

    # One-time facility construction and delivery.
    obj_swe = (
        bc["SWE_fixed"] * SWE
        + (bc["SWE_per_capacity"] + bc["transfer_cost"]["Moon"]) * q["Moon_SWE", 0]
    )
    obj_dwe = gp.quicksum(
        bc["DWE_fixed"] * DWE[p]
        + (bc["DWE_per_capacity"] + bc["transfer_cost"][p]) * q[p,0]
        for p in E_Depot
    )

    # Storage cost (both tanks charged manufacture + delivery to the node)
    obj_storage = gp.quicksum(
        (bc["Storage_H2O_per_kg"]+bc["transfer_cost"][p]) * Storage_H2O[p,0]
        + (bc["Storage_Prop_per_kg"]+bc["transfer_cost"][p]) * Storage_Prop[p,0]
        for p in E_Depot
    )

    # Spacecraft manufacturing + deployment cost (first_Tank 유지)
    obj_spacecraft = (
        bc["OTV_unit"] * N_sc["OTV",0]
        + bc["RT_unit"] * N_sc["RT",0]
        # deploy each vehicle to its initial node (OTV->LEO, RT->Moon)
        + bc["transfer_cost"]["GTO"] * vehicles["OTV"]["dry_mass"] * N_sc["OTV",0]
        + bc["transfer_cost"]["Moon"] * vehicles["RT"]["dry_mass"] * N_sc["RT",0]
        + (bc["transfer_cost"]["Moon"] + bc["Storage_H2O_per_kg"]) * first_Tank["H2O_Tank",0]
        + (bc["transfer_cost"]["Moon"] + bc["Storage_Prop_per_kg"]) * first_Tank["Prop_Tank",0]
    )

    # Full-mission ISRU maintenance (5% of plant mass per 360-day year).
    mission_duration_years = (
        mission["mission_steps"] * mission["days_per_step"] / mission["days_per_year"]
    )
    maint_factor = bc["ISRU_maint_frac_per_yr"] * mission_duration_years
    obj_maint = maint_factor * (
        (bc["ISRU_spares_cost_per_kg"] + bc["transfer_cost"]["Moon"]) * q["Moon_SWE"]
        + gp.quicksum((bc["ISRU_spares_cost_per_kg"] + bc["transfer_cost"][p]) * q[p]
                      for p in E_Depot)
    )

    # Every kilogram of Earth-supplied propellant is charged exactly once.
    obj_earth_prop = gp.quicksum(
        (bc["Ini_Prop_per_kg"] + bc["transfer_cost"][node]) * earth_prop[node, t]
        for node in ["GTO", "Moon"] for t in range(T)
    )

    obj = (
        obj_swe + obj_dwe + obj_storage + obj_spacecraft
        + obj_maint + obj_earth_prop
    )

    m.setObjective(obj, GRB.MINIMIZE)

    '''-----------------------Constraints-----------------------'''
   
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

                rhs = gp.LinExpr(d_it.get((k, i, t), 0.0))
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

                # 인프라 페이로드 받음
                if t%(data.mission["days_per_year"]//data.mission["days_per_step"]) == 0 and t != 0:
                    if k == "H2O_Tank" and i == "Moon" :
                        rhs += first_Tank[k, t_to_year(t, data)]
                    elif k == "Prop_Tank" and i == "Moon" :
                        rhs += first_Tank[k, t_to_year(t, data)]

                    elif k == "Infra" : 
                        if i == "Moon" :
                            rhs -= (q["Moon_SWE", t_to_year(t, data)] - q["Moon_SWE", t_to_year(t, data)-1]
                            + q[i, t_to_year(t, data)] - q[i, t_to_year(t, data)-1]
                            + first_Tank[k, t_to_year(t, data)] 
                            + first_Tank[k, t_to_year(t, data)]
                            + Storage_H2O[i, t_to_year(t, data)] - Storage_H2O[i, t_to_year(t, data)-1]
                            + Storage_Prop[i, t_to_year(t, data)] - Storage_Prop[i, t_to_year(t, data)-1]
                            )
                        elif i in E_Depot :
                            rhs -= (
                            + q[i, t_to_year(t, data)] - q[i, t_to_year(t, data)-1]
                            + Storage_H2O[i, t_to_year(t, data)] - Storage_H2O[i, t_to_year(t, data)-1]
                            + Storage_Prop[i, t_to_year(t, data)] - Storage_Prop[i, t_to_year(t, data)-1]
                            )

                # 인프라 페이로드 공급
                if (t+6)%(data.mission["days_per_year"]//data.mission["days_per_step"]) == 0 :
                    if k == "Infra" and i == "GTO":
                        rhs += 100000 #대충 큰 수로 할지 요구되는 인프라 무게 총 합으로 할지 고민중

                m.addConstr(outflow - inflow <= rhs, name=f"mass[{k},{i},{t}]")

    #----------------------------concurrency----------------------------
    H2O_TANK_RATIO = 40.0      # kg H2O per kg H2O tank (Gkaravela)
    PROP_TANK_RATIO = 1.478    # kg propellant per kg prop tank (Gkaravela)

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
                    <= vehicles["OTV"]["payload_cap"] * y["OTV", a, t],
                    name=f"OTV_payload_a{a}_t{t}",
                )
            # propellant: Prop <= propellant_cap * y[OTV,a,t]
            if ("Prop", "OTV", a, t) in x and ("OTV", a, t) in y:
                m.addConstr(
                    x["Prop", "OTV", a, t]
                    <= vehicles["OTV"]["propellant_cap"] * y["OTV", a, t],
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
                        <= (vehicles["RT"]["payload_cap"]+vehicles["RT"]["propellant_cap"]) * y["RT", a, t],
                        name=f"RT_totalmass_a{a}_t{t}",
                    )

                # (2)+(4) 추진제 총량 <= 자기탱크 용량 + 운반탱크 용량
                #   Prop <= propellant_cap * y + 1.478 * Prop_Tank
                if ("Prop", "RT", a, t) in x:
                    prop_cap_rhs = vehicles["RT"]["propellant_cap"] * y["RT", a, t]
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
                        <= vehicles["RT"]["payload_cap"] * y["RT", a, t],
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

                # LEO: concurrency 없음
                if node == "LEO":
                    continue

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
                            x["Prop", "hold", a, t] <= PROP_TANK_RATIO * t_to_year(t, data),
                            name=f"store_Prop_{node}_t{t}",
                        )


    #---------------------ISRU operation------------------------
    for e in E:
        for t in range(T):
            m.addConstr(q_operation[e, t] <= q[e, t_to_year(t, data)], name=f"prod_cap_{e}_{t}")
        # The final point has no outgoing interval in which production can be used.
        m.addConstr(q_operation[e, T - 1] == 0, name=f"prod_terminal_{e}")

    #---------------------vehicle flow conservation------------------------
    # ---------------- 초기 우주선 배치 노드 ----------------
    INIT_NODE = {"OTV": "GTO", "RT": "Moon"}

    # ---------------- 우주선 대수 보존 ----------------
    for v in ["OTV", "RT"]:
        for i in nodes:
            for t in range(T):
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
                    init = N_sc[v, t]
                elif t%(data.mission["days_per_year"]//data.mission["days_per_step"]) == 0 and i == INIT_NODE[v]:
                    init = N_sc[v, t_to_year(t, data))]
                else : 
                    init = 0

                m.addConstr(arrive + init >= depart,
                            name=f"veh_cons_{v}_{i}_{t}")

     #------------------------q<=My------------------------
    # Big-M bounds ISRU plant mass: never larger than what is needed to make the
    # whole mission's propellant on the Moon.  Derived from total payload demand
    # (propellant scales with payload) and DAYS_PER_STEP, so it stays valid AND
    # reasonably tight across the demand-density sweep -- a fixed constant would
    # be too small at high demand and silently cut optimal solutions.
    total_pl_demand = sum(-val for (k, i, t), val in d_it.items()
                          if k == "PL" and val < 0.0)
    op_days = max(1, data.mission["mission_days"])
    swe_rate = 0.02917 * 2 / 1.5    # kg water / day per kg SWE (linear productivity)
    # assume up to ~40 kg propellant produced per kg payload (delivered prop plus
    # lunar climb-out overhead), water:propellant ~ 1:1.
    BIG_M = max(30000.0, 40.0 * total_pl_demand / (swe_rate * op_days))

    # SWE: q["Moon_SWE"] <= M * SWE
    for e in E_SWE:
        m.addConstr(q[e, 0] <= BIG_M * SWE, name=f"install_SWE_{e}")

    # DWE: q[node] <= M * DWE[node]
    for e in E_Depot:
        m.addConstr(q[e, 0] <= BIG_M * DWE[e], name=f"install_DWE_{e}")

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
    }
    return m, variables


def solve_model(model):
    """Optimize a model that has already been built."""
    model.optimize()
    return model


def build_and_solve(data, gurobi_params=None):
    """Compatibility wrapper used by the main runner."""
    model, variables = build_model(data, gurobi_params=gurobi_params)
    solve_model(model)
    return model, variables
