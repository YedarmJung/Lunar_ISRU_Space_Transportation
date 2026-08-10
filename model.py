import gurobipy as gp
from gurobipy import GRB

from math import exp

from build_Q import build_Q
from build_dit import build_dit
from build_cost import BUILD_COST as bc

from data import amort_factor   # ver_time_horizon: amort_factor


def build_and_solve(data, gurobi_params=None, horizon_years=None):   # ver_time_horizon: horizon_years
    nodes = data.nodes
    commodities = data.commodities
    arcs = data.arcs
    vehicles = data.vehicles
    arcs_type = data.arc_type


    T = data.T
    A = range(len(arcs))

    # ver_time_horizon: 정상상태 window / amortization 파라미터
    mission = data.mission
    ss   = mission["steady_start"]   # 정상상태 시작
    seam = mission["seam"]           # seam(= ss 에 glue)
    if horizon_years is None:
        horizon_years = mission["mission_years"]
    AMORT, periods_H = amort_factor(mission, horizon_years)

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
                    arc_components = ["H2O", "Prop", "H2O_Tank", "Prop_Tank"]
                else:
                    arc_components = []

            elif arc.kind == "hold":
                if v == "OTV":
                    arc_components = ["PL", "Prop"]

                elif v == "RT":
                    arc_components = ["H2O", "Prop", "H2O_Tank", "Prop_Tank"]

                elif v == "hold":
                    if arc.tail == "LEO":
                        arc_components = ["PL"]
                    else:
                        arc_components = ["PL", "H2O", "Prop",
                                        "H2O_Tank", "Prop_Tank"]

            for c in arc_components:
                for t in arc.active_times:
                    if t + arc.tau > T - 1:
                        continue
                    dep.append((c, v, a, t))

    x = m.addVars(dep, lb=0, vtype=GRB.CONTINUOUS, name="x")
    xm = m.addVars([(k,v,a,t+arcs[a].tau) for (k,v,a,t) in dep], lb=0, vtype=GRB.CONTINUOUS, name="xm")

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
    y = m.addVars(spacecraft_index, lb=0, vtype=GRB.CONTINUOUS, name="y")

    N_sc = m.addVars(["OTV", "RT"], lb=0, vtype=GRB.INTEGER, name="N_sc")

    #ISRU/storage variables
    E_SWE = ["Moon_SWE"]
    E_Depot = list(data.depot_node)

    E = E_SWE+E_Depot

    DWE = m.addVars(E_Depot, vtype=GRB.BINARY, name="DWE") #DWE 설치 여부
    SWE = m.addVar(vtype=GRB.BINARY, name="SWE") #SWE 설치 여부
    Storage_H2O = m.addVars(E_Depot, lb=0, name="Storage_H2O")
    Storage_Prop = m.addVars(E_Depot, lb=0, name="Storage_Prop") #Storage 사이징

    q = m.addVars(E, lb=0, name="q") #DWE, SWE 사이징
    q_operation = m.addVars(E,range(T), lb=0, name="q_operation")

    # ver_time_horizon: 지구연료를 t=0 일회성 주입(first_prop) 대신 매 스텝 recurring source 로.
    #   ramp 구간(t<ss) = 1회성, 정상상태 블록(ss<=t<seam) = opex(×AMORT).  탱크(first_Tank)는
    #   durable 하드웨어라 그대로 유지.
    earth_prop = m.addVars(["LEO", "Moon"], range(T), lb=0, name="earth_prop")
    first_Tank = m.addVars(["H2O_Tank", "Prop_Tank"], lb=0, name="first_tank")

    '''-----------------------objective-----------------------'''
    # ------------------------------------------------------------
    # Facility build cost
    # ------------------------------------------------------------

    # ver_time_horizon: 목적함수를 capex(1회) + opex(정상상태 블록 ×AMORT)로 재구성.
    #   - 유지보수를 obj_swe/obj_dwe 에서 빼내 opex(정상상태 주기당)로 재분류.
    #   - 지구연료: ramp 구간(t<ss) ×1, 정상상태 블록(ss<=t<seam) ×AMORT, tail(t>=seam) ×1.
    # 이렇게 하면 짧은 window 최적해가 곧 미션-H(horizon_years) 최적해가 된다.

    # ---- capex: 제작 + 배송만 (유지보수 제거) ----  ver_time_horizon
    obj_swe = (
        bc["SWE_fixed"] * SWE
        + (bc["SWE_per_capacity"] + bc["transfer_cost"]["Moon"]) * q["Moon_SWE"]
    )
    obj_dwe = gp.quicksum(
        bc["DWE_fixed"] * DWE[p]
        + (bc["DWE_per_capacity"] + bc["transfer_cost"][p]) * q[p]
        for p in E_Depot
    )

    # Storage cost (both tanks charged manufacture + delivery to the node)
    obj_storage = gp.quicksum(
        (bc["Storage_H2O_per_kg"]+bc["transfer_cost"][p]) * Storage_H2O[p]
        + (bc["Storage_Prop_per_kg"]+bc["transfer_cost"][p]) * Storage_Prop[p]
        for p in E_Depot
    )

    # Spacecraft manufacturing + deployment cost (first_Tank 유지)
    obj_spacecraft = (
        bc["OTV_unit"] * N_sc["OTV"]
        + bc["RT_unit"] * N_sc["RT"]
        # deploy each vehicle to its initial node (OTV->LEO, RT->Moon)
        + bc["transfer_cost"]["LEO"] * vehicles["OTV"]["dry_mass"] * N_sc["OTV"]
        + bc["transfer_cost"]["Moon"] * vehicles["RT"]["dry_mass"] * N_sc["RT"]
        + (bc["transfer_cost"]["Moon"] + bc["Storage_H2O_per_kg"]) * first_Tank["H2O_Tank"]
        + (bc["transfer_cost"]["Moon"] + bc["Storage_Prop_per_kg"]) * first_Tank["Prop_Tank"]
    )

    # ---- opex1: ISRU 유지보수 (Gkaravela 5% 플랜트질량/년)
    # 스텝당 spares 비율 × 정상상태 블록 스텝수 × ×AMORT  = 미션 전체 유지보수.
    maint_ps = bc["ISRU_maint_frac_per_yr"] * (data.mission["days_per_step"] / 365.0)
    block_steps = seam - ss
    obj_maint = AMORT * maint_ps * block_steps * (
        (bc["ISRU_spares_cost_per_kg"] + bc["transfer_cost"]["Moon"]) * q["Moon_SWE"]
        + gp.quicksum((bc["ISRU_spares_cost_per_kg"] + bc["transfer_cost"][p]) * q[p]
                      for p in E_Depot)
    )

    # ---- opex2: 지구연료 (재료비 + 배송비) ----  ver_time_horizon
    obj_earth_ramp = gp.quicksum(          # ramp(setup + roll-in): 1회성 초기재고
        (bc["Ini_Prop_per_kg"] + bc["transfer_cost"][node]) * earth_prop[node, t]
        for node in ["LEO", "Moon"] for t in range(ss)
    )
    obj_earth_steady = AMORT * gp.quicksum(  # 정상상태 블록 [ss, seam): opex
        (bc["Ini_Prop_per_kg"] + bc["transfer_cost"][node]) *earth_prop[node, t]
        for node in ["LEO", "Moon"] for t in range(ss, seam)
    )
    # ver_time_horizon: tail[seam,T) 지구연료는 주입 자체를 막았으므로(위 mass balance) 목적함수에
    # 별도 항이 없다.  (해당 earth_prop 변수는 어떤 제약에도 안 쓰여 최적에서 0.)

    # ---- Total objective ----  ver_time_horizon
    obj = (
        obj_swe + obj_dwe + obj_storage + obj_spacecraft
        + obj_maint + obj_earth_ramp + obj_earth_steady
    )

    m.setObjective(obj, GRB.MINIMIZE)

    '''-----------------------Constraints-----------------------'''
   
    #----------------------------xm value----------------------------
    for (k, v, a, t) in x:
        t_arr = t + arcs[a].tau
        if (k, v, a, t_arr) in xm:
            outside = gp.quicksum(
                Q[v][a][k][c] * x[c, v, a, t]
                for c in commodities
                if (c, v, a, t) in x
            )
            if (v, a, t) in y:
                outside += Q[v][a][k][v] * y[v, a, t]
            m.addConstr(xm[k, v, a, t_arr] == outside, name=f"xm_value_{k}_{v}_{a}_{t}")

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
                    xm[k, v, a, t]
                    for v in arcs_type
                    for a in in_arcs[i]
                    if (k, v, a, t) in xm
                )

                rhs = gp.LinExpr(d_it.get((k, i, t), 0.0))
                # ver_time_horizon: 지구연료 recurring source (LEO/Moon, t<seam 에만 주입).
                #   tail[seam,T) 은 seam 걸친 아크 "도착"용일 뿐이라 주입을 막는다.  seam 은 ss 에
                #   glue 되므로 tail 주입을 허용하면 싼(×1) 연료가 정상상태 시작재고로 역류(누수)한다.
                if k == "Prop" and i in ("LEO", "Moon") and t < seam:
                    rhs += earth_prop[i, t]
                #처음 탱크
                if t == 0 and i == "Moon":
                    rhs += first_Tank.get(k, 0.0)
                
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
                    for c in ["H2O", "Prop", "H2O_Tank", "Prop_Tank"]
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
                    for c in ["H2O", "H2O_Tank", "Prop_Tank"]
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
                    if ("H2O", "hold", a, t) in x and node in Storage_H2O:
                        m.addConstr(
                            x["H2O", "hold", a, t] <= H2O_TANK_RATIO * Storage_H2O[node],
                            name=f"store_H2O_{node}_t{t}",
                        )
                    # Prop 저장 <= Storage_Prop[node]
                    if ("Prop", "hold", a, t) in x and node in Storage_Prop:
                        m.addConstr(
                            x["Prop", "hold", a, t] <= PROP_TANK_RATIO * Storage_Prop[node],
                            name=f"store_Prop_{node}_t{t}",
                        )


    #---------------------ISRU operation------------------------
    for e in E:
        for t in range(T): 
            m.addConstr(q_operation[e, t] <= q[e], name=f"prod_cap_{e}_{t}")

    #---------------------vehicle flow conservation------------------------
    # ---------------- 초기 우주선 배치 노드 ----------------
    INIT_NODE = {"OTV": "LEO", "RT": "Moon"}

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
                    init = N_sc[v]
                else:
                    init = 0

                m.addConstr(arrive + init >= depart,
                            name=f"veh_cons_{v}_{i}_{t}")

    #---------------------ver_time_horizon: 정상상태 seam glue------------------------
    # seam(se)을 ss 에 꿰매어 정상상태 블록 [ss, seam) 이 스스로 반복하게 만든다.
    # seam 을 가로지르는 모든 아크(출발 ss-j == seam-j, j=0..tau-1)의 화물 x 와 우주선 y 를
    # 동일하게 강제 -> 정지재고(hold, τ=1, j=0) + 비행중 화물/우주선(move, τ=2, j=0,1)까지 전부 닫힘.
    # x(출발)를 묶으면 xm(도착)=Q·x 도 자동 일치하므로 도착 변수는 따로 건드리지 않는다.
    for a, arc in enumerate(arcs):
        for j in range(arc.tau):
            ts, te = ss - j, seam - j
            for c in commodities:
                for v in arcs_type:
                    if (c, v, a, ts) in x and (c, v, a, te) in x:
                        m.addConstr(x[c, v, a, ts] == x[c, v, a, te],
                                    name=f"seam_x_{c}_{v}_{a}_{j}")
            for v in ["OTV", "RT"]:
                if (v, a, ts) in y and (v, a, te) in y:
                    m.addConstr(y[v, a, ts] == y[v, a, te],
                                name=f"seam_y_{v}_{a}_{j}")

     #------------------------q<=My------------------------
    # Big-M bounds ISRU plant mass: never larger than what is needed to make the
    # whole mission's propellant on the Moon.  Derived from total payload demand
    # (propellant scales with payload) and DAYS_PER_STEP, so it stays valid AND
    # reasonably tight across the demand-density sweep -- a fixed constant would
    # be too small at high demand and silently cut optimal solutions.
    total_pl_demand = sum(-val for (k, i, t), val in d_it.items()
                          if k == "PL" and val < 0.0)
    op_days = max(1, T - data.mission["setup_steps"]) * data.mission["days_per_step"]
    swe_rate = 0.02917 * 2 / 1.5    # kg water / day per kg SWE (linear productivity)
    # assume up to ~40 kg propellant produced per kg payload (delivered prop plus
    # lunar climb-out overhead), water:propellant ~ 1:1.
    BIG_M = max(30000.0, 40.0 * total_pl_demand / (swe_rate * op_days))

    # SWE: q["Moon_SWE"] <= M * SWE
    for e in E_SWE:
        m.addConstr(q[e] <= BIG_M * SWE, name=f"install_SWE_{e}")

    # DWE: q[node] <= M * DWE[node]
    for e in E_Depot:
        m.addConstr(q[e] <= BIG_M * DWE[e], name=f"install_DWE_{e}")

   

    m.optimize()

    variables = {
        "x": x,
        "xm": xm,
        "y": y,
        "N_sc": N_sc,
        "DWE": DWE,
        "SWE": SWE,
        "Storage_H2O": Storage_H2O,
        "Storage_Prop": Storage_Prop,
        "q": q,
        "q_operation": q_operation,
        "earth_prop": earth_prop,   # ver_time_horizon: first_prop -> earth_prop(node,t)
        "first_Tank": first_Tank,
    }
    return m, variables
