# -*- coding: utf-8 -*-
"""
GEO 위성 데이터셋 대조/보완  (v4 — probe 결과 반영 최종본)
============================================================
probe 로 확인된 사실:
  · GCAT OpOrbit 은 '목적지'가 아니라 '현재/기록 궤도'.
    GEO행 위성이 HEO(GTO 체류) / VHEO(초동기천이) / MEO(전기추진 상승중)
    / '-'(기밀) / GEO/* 로 흩어짐 → OpOrbit 필터는 원리적으로 부적합.
  · 궤도 필터 없이 이름+날짜로만 매칭하면 36/39 적중.
  · DryMass 가 대부분 채워져 있음. 단 Mass==DryMass 인 행은 자리표시자.

설계:
  [보완] 궤도 필터 없이 GCAT 페이로드 전체와 매칭 → 질량/건조질량 취득
  [누락] 궤도 요소(Apogee/Inc) 기반 2단계 후보 추출 → 등급(A/B) 표시
  [검증] MassFlag/DryFlag + Mass==DryMass 자리표시자 탐지

사용법:
    python geo_reconcile_v4.py
    python geo_reconcile_v4.py --probe        # 매칭 상세 재확인
필요: pandas, openpyxl
"""

import argparse
import io
import re
import sys
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher
from urllib.request import Request, urlopen

import pandas as pd

GCAT_SATCAT = "https://planet4589.org/space/gcat/tsv/cat/satcat.tsv"
CELESTRAK_SATCAT = "https://celestrak.org/pub/satcat.csv"

MY_XLSX = "GEO_launches_2024_2026plus_excl_CN_RU.xlsx"
MY_SHEET = "발사데이터"
OUT_XLSX = "GEO_reconciled.xlsx"

YEAR_MIN, YEAR_MAX = 2024, 2026
EXCLUDE_STATES = {"CN", "PRC", "RU", "SU", "CIS", "USSR"}

# --- 누락후보 추출용 궤도 기준 (라벨이 아니라 요소 기반) ---
APOGEE_MIN_A = 30000.0     # km. GTO~GEO 원지점
APOGEE_MIN_B = 18000.0     # km. 전기추진 상승 중간 단계까지 포함
INC_MAX = 35.0             # deg. 몰니야(63deg) 배제
APOGEE_MAX = 200000.0      # km. 심우주 배제

# 라벨이 명시적으로 GEO 계열인 경우는 요소와 무관하게 A등급
GEO_LABELS = ("GEO", "GTO", "GSO", "IGO")

# 이름으로 못 찾는 건에 대한 수동 별칭 (필요시 채우세요)
MANUAL_ALIASES = {
    # "NROL-70": ["USA 353"],
    # "MRV (Mission Robotic Vehicle)": ["Mission Robotic Vehicle"],
}

UA = {"User-Agent": "Mozilla/5.0 (research script)"}


# ==========================================================================
# 다운로드 / 파싱
# ==========================================================================
def fetch_text(url, timeout=180):
    print(f"  다운로드: {url}")
    with urlopen(Request(url, headers=UA), timeout=timeout) as r:
        raw = r.read()
    for enc in ("utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def read_gcat_tsv(url=GCAT_SATCAT):
    lines = fetch_text(url).split("\n")
    hdr = next((i for i, l in enumerate(lines[:200])
                if l.startswith("#") and "\t" in l), None)
    if hdr is None:
        raise RuntimeError("GCAT 헤더 없음")
    cols = [c.strip() for c in lines[hdr].lstrip("#").split("\t")]

    def ok(l):
        s = l.strip()
        return bool(s) and not s.startswith("#") and not set(s) <= set("-\t ")

    df = pd.read_csv(io.StringIO("\n".join(l for l in lines[hdr + 1:] if ok(l))),
                     sep="\t", names=cols, dtype=str,
                     engine="python", on_bad_lines="skip")
    df.columns = [c.strip() for c in df.columns]
    for c in df.columns:
        df[c] = df[c].astype(str).str.strip()
    return df


def read_celestrak():
    return pd.read_csv(io.StringIO(fetch_text(CELESTRAK_SATCAT)), dtype=str)


# ==========================================================================
# 값 정리
# ==========================================================================
def to_num(x):
    if x is None:
        return None
    s = str(x).strip()
    if s in ("", "-", "?", "nan", "None", "N/A"):
        return None
    s = s.replace("~", "").replace("?", "").replace(",", "").replace("+", "")
    m = re.match(r"^(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)$", s)
    if m:
        return (float(m.group(1)) + float(m.group(2))) / 2
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else None


_TIME_TAIL = re.compile(r"\s+\d{3,4}(?::\d{2})?(?::\d{2})?\s*$")
_FMTS = ("%Y %b %d", "%Y-%m-%d", "%Y %b", "%Y/%m/%d", "%Y%m%d", "%Y")


def to_date(x):
    if x is None:
        return pd.NaT
    s = str(x).strip()
    if s in ("", "-", "?", "nan", "None"):
        return pd.NaT
    s = _TIME_TAIL.sub("", s.rstrip("?").strip().split("T")[0].strip()).strip()
    for f in _FMTS:
        try:
            return pd.Timestamp(datetime.strptime(s, f))
        except ValueError:
            continue
    return pd.to_datetime(s, errors="coerce")


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower()) if s is not None else ""


def name_variants(*fields):
    out = []
    for f in fields:
        if f is None:
            continue
        s = str(f).strip()
        if s in ("", "-", "nan", "None"):
            continue
        for p in re.split(r"[()/,]", s):
            n = norm(p.replace("→", " "))
            if len(n) >= 3:
                out.append(n)
    seen, res = set(), []
    for n in out:
        if n not in seen:
            seen.add(n)
            res.append(n)
    return res


def name_score(a_list, b_list):
    best = 0
    for a in a_list:
        for b in b_list:
            if a == b:
                return 100
            if len(a) >= 4 and len(b) >= 4 and (a in b or b in a):
                best = max(best, 75)
            else:
                r = SequenceMatcher(None, a, b).ratio()
                if r >= 0.80:
                    best = max(best, int(r * 70))
    return best


# ==========================================================================
# 데이터 준비
# ==========================================================================
def gcat_payloads():
    """2024~2026 GCAT 페이로드 전체 (궤도 필터 없음). 보완의 기준 풀."""
    g = read_gcat_tsv()
    g["_date"] = g["LDate"].map(to_date)
    g["_year"] = g["_date"].dt.year
    g = g[g["_year"].between(YEAR_MIN, YEAR_MAX)]
    g = g[g["Type"].fillna("").str.upper().str.startswith("P")].copy()
    g["_names"] = g.apply(
        lambda r: name_variants(r.get("Name"), r.get("PLName"), r.get("AltNames")),
        axis=1)
    for c, t in (("_apo", "Apogee"), ("_per", "Perigee"), ("_inc", "Inc")):
        g[c] = g[t].map(to_num)
    g["_mass"] = g["Mass"].map(to_num)
    g["_dry"] = g["DryMass"].map(to_num)
    return g.reset_index(drop=True)


def load_mine():
    m = pd.read_excel(MY_XLSX, sheet_name=MY_SHEET)
    done = m[m["상태"] == "발사완료"].copy().reset_index(drop=True)
    done["_names"] = done["위성명"].map(
        lambda s: name_variants(s) + [norm(a) for a in MANUAL_ALIASES.get(s, [])])
    done["_d"] = pd.to_datetime(done["발사일"], errors="coerce")
    return done


def match(mine, ext, day_tol=5):
    pairs, used = {}, set()
    for mi, mr in mine.iterrows():
        cands = []
        for ei, er in ext.iterrows():
            if ei in used:
                continue
            gap = None
            if pd.notna(mr["_d"]) and pd.notna(er["_date"]):
                gap = abs((mr["_d"] - er["_date"]).days)
                if gap > day_tol:
                    continue
            ns = name_score(mr["_names"], er["_names"])
            if ns >= 60 or (ns >= 40 and gap is not None and gap <= 1):
                cands.append((ns + (max(0, 30 - gap * 5) if gap is not None else 0),
                              ei, ns, gap))
        if cands:
            cands.sort(reverse=True)
            _, ei, _, _ = cands[0]
            pairs[mi] = ei
            used.add(ei)
    return pairs, used


def geo_tier(row):
    """궤도 요소 기반 GEO행 후보 등급. A=확실, B=가능, None=제외."""
    lab = str(row.get("OpOrbit", "")).upper()
    if lab.startswith(GEO_LABELS):
        return "A"
    apo, inc = row.get("_apo"), row.get("_inc")
    if apo is None or apo > APOGEE_MAX:
        return None
    if inc is not None and inc > INC_MAX:
        return None                      # 몰니야/HEO 경사궤도 배제
    if apo >= APOGEE_MIN_A:
        return "A"
    if apo >= APOGEE_MIN_B:
        return "B"                       # 전기추진 상승 중간 단계 가능성
    return None


# ==========================================================================
# probe
# ==========================================================================
def probe():
    mine, g = load_mine(), gcat_payloads()
    pairs, _ = match(mine, g)
    print(f"\n매칭 {len(pairs)}/{len(mine)}\n")
    print(f"{'내 위성명':<32}{'GCAT':<24}{'OpOrbit':<9}{'Apo':>8}{'Per':>8}"
          f"{'Inc':>6}{'Mass':>7}{'MF':>4}{'Dry':>7}{'DF':>4}")
    print("-" * 108)
    for mi, gi in sorted(pairs.items()):
        r = g.loc[gi]
        print(f"{str(mine.at[mi,'위성명'])[:31]:<32}{str(r['Name'])[:23]:<24}"
              f"{str(r['OpOrbit'])[:8]:<9}{str(r['Apogee'])[:7]:>8}"
              f"{str(r['Perigee'])[:7]:>8}{str(r['Inc'])[:5]:>6}"
              f"{str(r['Mass'])[:6]:>7}{str(r['MassFlag'])[:3]:>4}"
              f"{str(r['DryMass'])[:6]:>7}{str(r['DryFlag'])[:3]:>4}")
    print("\n[미매칭] — 같은 날짜(±2일) GCAT 페이로드를 함께 표시")
    for mi, mr in mine.iterrows():
        if mi in pairs:
            continue
        print(f"\n  ▶ {mr['발사일']}  {mr['위성명']}")
        if pd.notna(mr["_d"]):
            near = g[(g["_date"] - mr["_d"]).abs() <= pd.Timedelta(days=2)]
            for _, r in near.iterrows():
                print(f"      후보: {r['Name']:<28} {r['OpOrbit']:<9} "
                      f"{r['State']:<6} apo={r['Apogee']}")


# ==========================================================================
# 본 실행
# ==========================================================================
def main():
    print("=" * 72)
    print("GEO 데이터셋 대조/보완 (v4)")
    print("=" * 72)

    mine = load_mine()
    print(f"\n내 엑셀 발사완료: {len(mine)}건")

    g = gcat_payloads()
    print(f"GCAT {YEAR_MIN}~{YEAR_MAX} 페이로드: {len(g)}건")

    # ---------- 1. 보완: 궤도 필터 없이 전체와 매칭 ----------
    pairs, used = match(mine, g)
    print(f"GCAT 매칭: {len(pairs)}/{len(mine)}건")

    enr = mine.drop(columns=["_names", "_d"]).copy()
    newc = ["COSPAR", "GCAT_Name", "GCAT_질량", "MassFlag", "GCAT_건조질량",
            "DryFlag", "GCAT_OpOrbit", "GCAT_Apogee", "GCAT_Perigee",
            "GCAT_Inc", "GCAT_Bus", "질량신뢰_판정"]
    for c in newc:
        enr[c] = None

    for mi, gi in pairs.items():
        r = g.loc[gi]
        enr.at[mi, "COSPAR"] = r["Piece"]
        enr.at[mi, "GCAT_Name"] = r["Name"]
        enr.at[mi, "GCAT_질량"] = r["_mass"]
        enr.at[mi, "MassFlag"] = r["MassFlag"]
        enr.at[mi, "GCAT_건조질량"] = r["_dry"]
        enr.at[mi, "DryFlag"] = r["DryFlag"]
        enr.at[mi, "GCAT_OpOrbit"] = r["OpOrbit"]
        enr.at[mi, "GCAT_Apogee"] = r["_apo"]
        enr.at[mi, "GCAT_Perigee"] = r["_per"]
        enr.at[mi, "GCAT_Inc"] = r["_inc"]
        enr.at[mi, "GCAT_Bus"] = r["Bus"]

        # 자리표시자 판정: Mass == DryMass 이면 GCAT이 모른다는 뜻
        m_, d_ = r["_mass"], r["_dry"]
        flags = f"{r['MassFlag']}{r['DryFlag']}".strip().replace("-", "")
        if m_ is not None and d_ is not None and abs(m_ - d_) < 1e-6:
            enr.at[mi, "질량신뢰_판정"] = "자리표시자 의심 (Mass=Dry)"
        elif flags:
            enr.at[mi, "질량신뢰_판정"] = f"GCAT 플래그: {flags}"
        elif m_ is not None:
            enr.at[mi, "질량신뢰_판정"] = "정상"

    good = enr["질량신뢰_판정"] == "정상"

    fill_m = enr[enr["총질량_kg"].isna() & enr["GCAT_질량"].notna()][
        ["발사일", "위성명", "GCAT_질량", "MassFlag", "질량신뢰_판정"]]
    fill_d = enr[enr["건조질량_kg"].isna() & enr["GCAT_건조질량"].notna()][
        ["발사일", "위성명", "GCAT_건조질량", "DryFlag", "질량신뢰_판정"]]
    fill_d_ok = fill_d[fill_d["질량신뢰_판정"] == "정상"]

    both = enr[enr["총질량_kg"].notna() & enr["GCAT_질량"].notna()].copy()
    if len(both):
        both["차이_%"] = ((both["GCAT_질량"].astype(float)
                          - both["총질량_kg"].astype(float)).abs()
                         / both["총질량_kg"].astype(float) * 100).round(1)
        mism = both[both["차이_%"] > 5][
            ["발사일", "위성명", "총질량_kg", "GCAT_질량", "차이_%",
             "질량신뢰도", "질량신뢰_판정"]].sort_values("차이_%", ascending=False)
    else:
        mism = pd.DataFrame()

    unmatched = enr[~enr.index.isin(pairs)][
        ["발사일", "위성명", "발사체", "국가_운용"]]

    # ---------- 2. 누락 탐지: 궤도 요소 기반 등급 ----------
    g["_tier"] = g.apply(geo_tier, axis=1)
    pool = g[g["_tier"].notna()
             & ~g["State"].fillna("").str.upper().isin(EXCLUDE_STATES)].copy()
    print(f"GEO행 후보 풀: A={sum(pool['_tier']=='A')} B={sum(pool['_tier']=='B')}")

    miss = pool[~pool.index.isin(used)][
        ["_tier", "Piece", "Name", "PLName", "_date", "OpOrbit",
         "_apo", "_per", "_inc", "State", "Owner", "_mass", "_dry", "Bus"]
    ].sort_values(["_tier", "_date"])
    miss.columns = ["등급", "COSPAR", "이름", "PL이름", "발사일", "OpOrbit",
                    "원지점", "근지점", "경사각", "국가", "운용자",
                    "질량", "건조질량", "버스"]

    # ---------- 3. CelesTrak 보조 ----------
    c = read_celestrak()
    for col in ("PERIOD", "APOGEE", "PERIGEE", "INCLINATION"):
        c[col] = pd.to_numeric(c[col], errors="coerce")
    c["_date"] = c["LAUNCH_DATE"].map(to_date)
    ct = c[(c["APOGEE"] >= APOGEE_MIN_B)
           & (c["APOGEE"] <= APOGEE_MAX)
           & (c["INCLINATION"] <= INC_MAX)
           & (c["OBJECT_TYPE"].fillna("") == "PAY")
           & c["_date"].dt.year.between(YEAR_MIN, YEAR_MAX)
           & ~c["OWNER"].fillna("").str.upper().isin(EXCLUDE_STATES)
           & (c["DECAY_DATE"].isna()
              | (c["DECAY_DATE"].astype(str).str.strip() == ""))].copy()
    ct = ct.reset_index(drop=True)
    ct["_names"] = ct["OBJECT_NAME"].map(name_variants)
    p2, u2 = match(mine, ct)
    miss_c = ct[~ct.index.isin(u2)][
        ["OBJECT_ID", "OBJECT_NAME", "_date", "OWNER",
         "PERIOD", "APOGEE", "PERIGEE", "INCLINATION"]].sort_values("_date")
    print(f"CelesTrak 후보 {len(ct)}건, 매칭 {len(p2)}건")

    print(f"\n총질량 보완 가능        : {len(fill_m)}건")
    print(f"건조질량 보완 가능      : {len(fill_d)}건 (그중 신뢰 {len(fill_d_ok)}건)")
    print(f"질량 불일치(>5%)        : {len(mism)}건")
    print(f"자리표시자 의심         : {sum(enr['질량신뢰_판정']=='자리표시자 의심 (Mass=Dry)')}건")
    print(f"미매칭 내 행            : {len(unmatched)}건")
    print(f"누락후보 GCAT           : {len(miss)}건 (A={sum(miss['등급']=='A')})")
    print(f"누락후보 CelesTrak      : {len(miss_c)}건")

    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as w:
        enr.to_excel(w, sheet_name="대조결과", index=False)
        fill_d.to_excel(w, sheet_name="건조질량보완", index=False)
        fill_m.to_excel(w, sheet_name="총질량보완", index=False)
        (mism if len(mism) else pd.DataFrame({"비고": ["없음"]})
         ).to_excel(w, sheet_name="질량불일치", index=False)
        unmatched.to_excel(w, sheet_name="미매칭_내행", index=False)
        miss.to_excel(w, sheet_name="누락후보_GCAT", index=False)
        miss_c.to_excel(w, sheet_name="누락후보_CelesTrak", index=False)
    print(f"\n저장: {OUT_XLSX}")
    print("\n※ '건조질량보완' 은 질량신뢰_판정이 '정상'인 행만 쓰세요.")
    print("  '자리표시자 의심'은 GCAT이 값을 모를 때 넣는 임시값입니다.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    a = ap.parse_args()
    try:
        probe() if a.probe else main()
    except Exception as e:
        print(f"\n오류: {type(e).__name__}: {e}", file=sys.stderr)
        raise