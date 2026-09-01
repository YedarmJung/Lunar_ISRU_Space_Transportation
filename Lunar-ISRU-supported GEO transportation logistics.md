# Lunar-ISRU-supported GEO Transportation Logistics — 모델 & 코드 설명서

> 이 문서는 **다른 세션의 AI가 이 저장소를 처음 볼 때 연구 전체를 파악**하도록 쓴 단일 기준 문서다.
> **실제 구현된 코드(`.py`)가 ground truth**이며, 아래 표기는 코드의 변수·함수 이름과 1:1로 맞춰져 있다.

---

## 1. 연구 질문

> **GTO→GEO로 payload를 나르는 재사용 OTV의 추진제를, (A) 지구에서 올려 공급하는 게 싼가, (B) 달 ISRU 물에서 만든 추진제를 궤도 depot으로 공급하는 게 싼가?**

핵심 산출물:
- **GEO 인도 kg당 비용** `cost_per_kg`
- **break-even GEO 수요밀도** (t/yr): 어느 수요 이상에서 달 ISRU가 지구공급을 이기는가
- **미션기간 민감도**: 긴 미션일수록 ISRU 초기투자가 상각되어 유리
- (확장) concentrated vs distributed ISRU, depot 위치/개수

기반: **Gkaravela et al.** 의 time-expanded multicommodity network flow + economies-of-scale ISRU logistics MILP. 이를 GTO-GEO OTV 추진제 공급 문제로 특화.

---

## 2. 핵심 모델링 원칙

1. **Payload ≠ Propellant.** GEO로 보내는 화물(위성부품·hydrazine·서비스킷 등)은 전부 `PL`. 달 ISRU는 그 화물을 나르는 **차량의 연료**(`Prop`)만 공급한다. (달이 hydrazine을 만드는 게 아님.)
2. **OTV ≠ RT(Resource Tanker).**
   - **OTV**: GTO→GEO payload 운송(고객측). 달 노드(`Moon`,`LLO`)에는 못 감.
   - **RT**: 달/궤도에서 depot으로 물·추진제 운반(공급측).
3. **시간축 포함 (time-expanded).** depot/탱크의 의미는 공급 타이밍과 수요 타이밍의 차이를 흡수하는 buffer에서 나온다. 저장(Storage)이 없으면 물자 보관/전달 불가.
4. **정상상태 window + 상각 (이 저장소의 최신 방식).** 전체 미션(수년)을 통째로 시뮬하지 않고, 주기적 운영의 **짧은 대표 window**만 풀고 임의 미션기간 `H`로 **상각(amortize)** 한다. (§8에서 상술 — 이 부분이 예전 계획문서와 가장 다름.)
5. **결정론적 MILP.** stochastic/service-level 확장은 향후 과제.

---

## 3. 네트워크 구조

### 3.1 Nodes (`data.py: get_data`)
```
GEO, GTO, EML1, NRHO, LLO, Moon             # 6개
LUNAR_NODES = {Moon, LLO}                    # OTV 진입 금지 (model.py)
```
별도 "Earth" 노드는 없다. 지구 공급은 `GTO`/`Moon` 노드에 **`earth_prop` 주입 + `transfer_cost`(배송비)** 로 표현.

### 3.2 Depot 후보 (`depot_node`)
```
Moon, GEO, GTO, EML1, NRHO, LLO             # 여기에 DWE/저장탱크 설치 가능
```

### 3.3 Commodities
```
PL, H2O, Prop, H2O_Tank, Prop_Tank          # 5개
```
- `PL` payload, `H2O` 달 물, `Prop` 추진제(달산 or 지구산 동일 취급).
- `H2O_Tank`,`Prop_Tank`: **RT가 물자 운반 시 쓰는 탱크**(commodity로 취급, RT가 실어나름).

### 3.4 Arcs (`Arc` dataclass)
- **hold 아크**: 각 노드 자기루프 `(i,i)`, τ=1. 재고 보관 + ISRU 활동 host.
- **move 아크**: `add_two_way`로 양방향, `tau`(1 또는 2)와 `delta_v_km_s` 부여. 각 아크에 `active_times`(사용 가능 시점).
- 이동시간 τ: 지구클러스터 내부 1, 지구↔달 2, 달클러스터 내부 1.
- Δv 출처 태그: `[G]` Gkaravela, `[H]` Hohmann 계산, `[L]` cislunar 문헌, `[~]` 추정.

---

## 4. 결정변수 (실제 코드 이름, `model.py`)

| 변수 | 타입 | 의미 |
|---|---|---|
| `x[c,v,a,t]` | 연속 ≥0 | 아크 `a`를 시점 `t`에 **출발**하는 mode `v`의 commodity `c` 양 [kg] |
| `xm[c,v,a,t+τ]` | 연속 ≥0 | **도착**량 = `Q·x` (전이행렬 적용, §5) |
| `y[v,a,t]` | 정수 ≥0 | 아크 `a`, 시점 `t`의 mode `v` 운항 대수 (hold 아크 포함 → "머무름"도 표현) |
| `N_sc[v]` | 정수 ≥0 | 함대 규모 (OTV, RT) |
| `SWE` | 이진 | 달 SWE 설치 여부 |
| `DWE[p]` | 이진 | depot `p`에 DWE 설치 여부 |
| `q[e]` | 연속 ≥0 | 플랜트 규모 [kg] (`Moon_SWE` + 각 depot DWE) |
| `q_operation[e,t]` | 연속 ≥0 | 시점 `t`에 가동한 플랜트 질량 (≤ `q[e]`) |
| `Storage_H2O[p]`, `Storage_Prop[p]` | 연속 ≥0 | depot 물/추진제 **탱크 질량** [kg] |
| `earth_prop[node,t]` | 연속 ≥0 | 지구 추진제 주입량 (`GTO`,`Moon`) |
| `first_Tank[H2O/Prop]` | 연속 ≥0 | RT 탱크 초기 배치량 (`Moon`, t=0) |

`arc_type = [OTV, RT, hold]`. `x`는 **출발량**, `xm`은 **도착량**이라는 구분이 중요(추진제는 비행 중 연소로 줄어듦).

---

## 5. 전이행렬 Q — 로켓방정식 (`build_Q.py`)

각 (mode `v`, 아크 `a`)에 대해 `xm = Q·x` 로 도착량 계산. 기본은 항등(도착=출발). 추진제 연소만 특별:

```
mr    = exp(Δv / (Isp·g0))          # 질량비, g0 = 9.80665e-3 km/s²
alpha = 1 − 1/mr
xm[Prop] = (1−alpha)·x[Prop]                     # 살아남은 추진제
         − alpha·Σ_{c≠Prop} x[c]                 # 실은 화물 1kg당 소비
         − alpha·dry_mass·y[v]                    # 차량 건조질량 이동분 소비
```
즉 **아크를 건너면 추진제가 (화물+건조질량)을 밀어낸 만큼 줄어든 채 도착**한다. hold 아크(Δv=None)는 연소 없음. 다른 commodity는 그대로 도착.

---

## 6. 목적함수 — capex + 상각 opex (`model.py`, `run.py: build_cost_breakdown`)

```
AMORT, periods_H = amort_factor(mission, horizon_years)   # data.py
```
- **capex (×1, 1회성)**: SWE 제작+배송, DWE 제작+배송, 저장탱크, 우주선 제작+배송+RT탱크, **ramp 구간(`t<ss`) 지구연료**(초기재고).
- **opex (×AMORT)**: ISRU 유지보수(5%/yr 플랜트질량 × spares $10k/kg), **정상상태 블록(`ss≤t<seam`) 지구연료**.

```
maint_ps  = 0.05 · (days_per_step/365)                    # 스텝당 spares 비율
obj_maint = AMORT · maint_ps · (seam−ss) · Σ_e (spares+transfer)·q[e]
obj_earth = Σ_{t<ss} u(node)·earth_prop      + AMORT · Σ_{ss≤t<seam} u(node)·earth_prop
            └ ramp ×1 ────────┘                └ steady ×AMORT ────────────────┘
u(node) = Ini_Prop_per_kg + transfer_cost[node]
obj = obj_swe + obj_dwe + obj_storage + obj_spacecraft + obj_maint + obj_earth
```
목적값 `m.ObjVal == total(H) == capex + opex_per_period·periods(H)` (검증됨).

**성능지표**: `cost_per_kg = total(H) / (pulse · periods_H)` = GEO 인도 kg당 비용.

---

## 7. 제약조건 (`model.py`)

1. **xm 값**: `xm[k,v,a,t+τ] == Σ_c Q[v][a][k][c]·x[c,v,a,t] + Q[v][a][k][v]·y[v,a,t]`.
2. **질량 균형** (각 `k,i,t`, **free disposal `≤`**):
   `outflow(x 출발) − inflow(xm 도착) ≤ rhs`, 여기서
   - `rhs = d_it[k,i,t]` (외생 수요/공급, `build_dit`)
   - `+ earth_prop[i,t]` if `k=Prop, i∈{GTO,Moon}` (지구연료 주입)
   - `+ first_Tank[k]` if `t=0, i=Moon`
   - **SWE 생산** (i=Moon, k=H2O): `+ 0.02917·(3/2.5)·days · q_operation[Moon_SWE,t]`
   - **DWE 변환** (i∈depot): 물 소비 `− dwe_rate·q_op` (k=H2O), 추진제 생산 `+ (2/3)·dwe_rate·q_op` (k=Prop);
     `dwe_rate = 0.09722·(3/2.5)·days`, `PROP_PER_H2O = 2/3`
3. **동시성/용량** (각 아크·시점):
   - OTV: `x[PL] ≤ 40000·y`, `x[Prop] ≤ 40000·y`
   - RT: `Σ실은것 ≤ 40000·y`; `x[Prop] ≤ 40000·y + 1.478·x[Prop_Tank]`; `x[H2O] ≤ 40·x[H2O_Tank]`
   - 저장 hold: `x[H2O,hold] ≤ 40·Storage_H2O[node]`, `x[Prop,hold] ≤ 1.478·Storage_Prop[node]`
4. **ISRU 가동한계**: `q_operation[e,t] ≤ q[e]`.
5. **차량 수 보존** (`≥`, free disposal): `arrive + init ≥ depart`, `init = N_sc[v]` at INIT_NODE(OTV→GTO, RT→Moon) at t=0.
6. **정상상태 seam glue** (§8): `x[c,v,a,ss−j]==x[c,v,a,se−j]`, `y[v,a,ss−j]==y[v,a,se−j]`, `j=0..τ−1`.
7. **설치 Big-M**: `q[e] ≤ BIG_M·(SWE 또는 DWE[e])`.

### 화학/변환 상수
- **물:추진제 = 3:2** (5.5:1 연소 시 잉여 O2 폐기 → 사용가능 추진제/물 = **2/3**), **150% overhead**(생산율에 `3/2.5` 곱).
- **탱크비**: 물 40 kg/kg탱크, 추진제 1.478 kg/kg탱크.
- **차량**(OTV·RT 동일): payload_cap 40000, prop_cap 40000, dry_mass 6000, Isp 420 s.

---

## 8. ★ 정상상태 window + 상각 (이 저장소의 최신 핵심)

운영이 주기적(GEO 펄스가 주기마다 반복)이므로 **전체 미션을 시뮬하지 않는다**. 대신:

### 8.1 Window 구조 (`data.py`, 기본 20일 격자 예)
```
setup(4스텝) | roll-in(1주기) | 정상상태(n_steady=2주기) | seam | tail
[0 .. 3]     | 펄스@4         | 펄스@7(=ss), @10        | @13(=se) | 14,15
```
- `setup_steps`, `period_steps`, `steady_start=ss`, `seam=se`, `n_rollin`, `n_steady`, `n_pulses` 는 `mission` dict에 저장.
- `T = seam + MAX_TAU + 1`. `mission_years`는 **T를 결정하지 않고** 상각 지평 `H`로만 쓰인다.
- GEO 수요는 `build_dit`가 정확히 `n_pulses`개 생성(펄스: `setup, setup+P, …`), GTO 공급은 `lead` 스텝 전에.

### 8.2 왜 이렇게? — 두 가지 필수 장치
1. **지구연료 = recurring source.** 예전엔 `first_prop`로 t=0에 한 번에 주입 → opex 분리 불가. 지금은 `earth_prop[node,t]` 매 스텝 구매.
2. **opex 재가중 (AMORT).** 짧은 window의 최적해는 저-capex(지구)로 편향된다. 그래서 목적함수의 opex를 `AMORT = periods(H)/n_steady` 배 → **tiny window 최적해 = 진짜 미션-H 최적해**. `periods(H) = H·365/period_days`.

### 8.3 cyclic seam glue (정상상태 닫기)
`se`를 `ss`에 **꿰매어** 정상상태 블록 `[ss,se)`이 스스로 반복하게 함:
```
모든 아크에 대해  x[c,v,a, ss−j] == x[c,v,a, se−j],  y도 동일,  j = 0 .. τ−1
```
- `j=0`: seam에서 출발하는 것(τ=1 hold면 **재고 등식** = 정지재고 닫힘).
- `j≥1`: seam을 넘어 **비행 중**인 화물/우주선(τ=2)까지 닫힘.
- **출발변수 x로 묶는다**(도착 xm=Q·x는 자동 일치). `ss`를 2번째 펄스에 둬서 양쪽 seam이 window 내부 → 걸친 아크가 안 잘림.
- 효과: 정상상태 재고 in=out 강제 → "싼 ramp 연료를 정상상태로 흘려 opex 회피"하는 **arbitrage 차단**.

### 8.4 누수 주의 (구현 시 고쳤던 버그)
`tail[seam,T)`은 **seam 걸친 아크의 도착 버퍼일 뿐**. 여기서 지구연료 주입을 허용하면, seam이 ss에 glue돼 **싼(×1) tail 연료가 정상상태 시작재고로 역류**(opex가 0으로). → 그래서 **지구연료 주입은 `t<seam`에서만** (`model.py` 질량균형 `and t < seam`).

### 8.5 검증 (full-mission 대비)
동일 비용모델·격자에서 6개 (미션년×수요) 케이스 비교: **cost_per_kg가 +0.4%~+2.8%로 일치**. config(Earth/lunar)도 break-even 경계 밖에선 완전 일치. 즉 **10시간짜리 full-mission을 몇 초로 대체**하면서 결과 보존.

---

## 9. 비용 계수 (`build_cost.py`)

```
transfer_cost[$/kg]: GTO 8,000 · GEO 16,000 · EML1 12,000 · NRHO 12,000 · LLO 13,500 · Moon 36,000
  (Bennett/Kornuta 앵커, EML1/NRHO/LLO는 GEO~Moon 사이 추정)
SWE/DWE_per_capacity: 10,000 $/kg (Gkaravela)
ISRU_maint_frac_per_yr: 0.05,  ISRU_spares_cost_per_kg: 10,000
Storage_H2O 800, Storage_Prop 1,869 $/kg탱크
OTV_unit, RT_unit: 150,000,000 $
Ini_Prop_per_kg: 1.045 $/kg  ((5.5·0.15+5.97)/6.5, LO2/LH2 5.5:1)
```
`transfer_cost`는 "지구→노드 1kg 배송비" — 하드웨어/연료를 그 노드에 놓는 비용에 더해진다.

---

## 10. 코드 흐름

```
data.py          get_data(mission_years, days_per_step, n_rollin=1, n_steady=2) -> NetworkData
                   · nodes/commodities/arcs/vehicles/mission/depot_node
                   · amort_factor(mission, H) -> (AMORT, periods_H)
build_dit.py     build_dit(data) -> d_it{(k,i,t):값}   (GEO 수요 sink, GTO 공급 source, n_pulses개)
build_Q.py       build_Q(data) -> Q[v][a][row][col]    (로켓방정식 전이행렬)
build_cost.py    BUILD_COST 딕셔너리 (위 계수)
model.py         build_and_solve(data, gurobi_params, horizon_years) -> (m, variables)
                   · 변수 생성 → 목적함수(capex+상각opex) → 제약(§7) → m.optimize()
run.py           main(): get_data → build_and_solve → build_solution(JSON)
                   · build_cost_breakdown(data,var,H) -> (breakdown, amort)   # capex/opex/total(H)/$·kg
                   · write results/latest_solution.json → print_summary → generate_plots
post_process.py  generate_plots(solution) -> flow_over_time, network_flow_map,
                   cost_breakdown, amortized_cost, infrastructure, isru_production
sweep.py         2D 스윕 (mission_years × demand_t_yr), 각 셀 = 고정 window를 horizon=years로 상각
                   · run_one(years, t) → plot_curves / plot_breakeven (break-even map)
```

### 실행
```
python run.py           # 단일 케이스 (기본 6yr / 20일 / 75 t/yr), 결과+플롯
python sweep.py         # 수요×미션기간 스윕 → cost/kg 곡선 + break-even map
```
Gurobi 필요(이 저장소 검증은 Python 3.9 + gurobipy 12). 파라미터: `run.py: GUROBI_PARAMS` (MIPGap 등). window가 작아 각 solve는 몇 초·gap→0.

### 산출물 (`results/`)
- `latest_solution.json`: 전체 해 (flows, trips, fleet, facilities, storage, production, earth_prop, cost_breakdown, **amortization**).
- `plots/`: flow_over_time(색=차량-commodity), network_flow_map, cost_breakdown, **amortized_cost**(total(H)·$/kg vs H), infrastructure, isru_production.
- `sweep/`: `sweep2d_results.csv`, `cost_per_kg_curves.png`, `breakeven_map.png`.

---

## 11. 대표 결과 (검증 시점)

| H (yr) | SWE_q | OTV | RT | capex($B) | opex/pd($M) | $/kg | 승자 |
|---:|---:|---:|---:|---:|---:|---:|:--|
| 3 | 0 | 2 | 0 | 0.76 | 205 | 19,958 | Earth |
| 6 | 28,568 | 3 | 2 | 3.36 | 95 | 15,162 | lunar |
| 10 | 37,882 | 3 | 3 | 4.09 | 78 | 11,732 | lunar |

→ **짧은 미션=지구, 긴 미션=달, break-even ~4–5년**, $/kg는 H↑에 감소(capex 상각). 수요밀도↑도 달에 유리.

---


## 12. 참고문헌 (`references/`)
- **Gkaravela** — Distributed Space Resource Logistics under Economies of Scale (주 framework)
- **Bennett** — GTO market for lunar-sourced propellant (Δv·비용 앵커)
- **Kornuta** — Commercial lunar propellant architecture (배송비 앵커)
- **Blair** — Lunar ice mining 경제성 (Δv 테이블 검증)
- **Charania** — lunar ISRU propellant services 시장
- **Chen & Ho (2017/2020)** — integrated space logistics MINLP / Mars ISRU
