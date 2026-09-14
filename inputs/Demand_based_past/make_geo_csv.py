# -*- coding: utf-8 -*-
"""
GCAT satcat.tsv + O.tsv  ->  2019~2025 GEO 위성 목록 CSV (위성 1기 = 1행).

  O.tsv      : 발사 단위 로그. Category 필드로 "GEO 로 향한 발사" 를 고른다.
  satcat.tsv : 물체 단위 카탈로그. 질량(Mass/DryMass/TotMass), 소유국(State),
               제작사(Manufacturer), 상태(Status) 를 여기서 가져온다.

[선정 로직]
1) O.tsv 에서 2019~2025, Category 궤도클래스가 GTO / GEO / STO 인 발사를 고른다.
     GTO = geostationary transfer orbit
     GEO = 정지궤도 직접투입
     STO = supersynchronous transfer orbit (Falcon 9 GEO 통신위성 다수가 여기 분류)
2) 그 발사의 satcat 페이로드(Type='P...') 를 전부 후보로 잡는다.
3) 후보 중 최종 목적지가 GEO 가 아닌 것을 제거한다 (아래 DROP 규칙).
4) 중국/러시아가 끼면 전부 제거한다.

주의: satcat 의 OpOrbit 컬럼은 '최종 목적지' 판정에 쓰지 않는다.
      전기추진으로 궤도상승 중이거나 GCAT 이 갱신하지 않은 정지궤도 위성이
      HEO / VHEO / MEO 로 남아있는 경우가 많다.
      (예: Thuraya 4-NGS, Spainsat NG-1/2, Hot Bird 13F, Eutelsat 36D,
           Inmarsat 6 F1/F2, Astra 1P, Arabsat 6A, Viasat-3 EMEA ...)
      마찬가지로 Perigee/Apogee 도 ODate 시점 값이라 전이궤도인 경우가 있다.

[질량] 단위 kg. satcat.tsv 원본값이며 이 스크립트가 추산한 값이 아니다.
      다만 GCAT 자체가 추정한 값이면 해당 *_flag 가 '?' 다.
  mass_kg      Mass    : 궤도 투입 직후 위성 질량 (추진제 포함, BOL)
  dry_mass_kg  DryMass : 추진제 제외 건조질량
  tot_mass_kg  TotMass : 발사시 총질량 (원지점 킥모터/어댑터 등 포함)
  prop_mass_kg         : mass_kg - dry_mass_kg (파생값)
"""
import argparse
import collections
import csv
import datetime as dt
import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SATCAT = os.path.join(HERE, 'satcat.tsv')
LAUNCH = os.path.join(HERE, 'O.tsv')

YEAR_MIN, YEAR_MAX = 2019, 2025          # main() 에서 CLI 인자로 덮어쓴다
EPOCH = dt.date(YEAR_MIN, 1, 1)
GEO_BOUND_CLASSES = ('GTO', 'GEO', 'STO')

MONTHS = {m: i + 1 for i, m in enumerate(
    ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'])}

# ---- 중국/러시아 제외 -------------------------------------------------------
EXCLUDE_STATES = {'CN', 'RU', 'SU'}
EXCLUDE_MANUFACTURERS = {
    'CAST', 'SAST', 'CASC', 'CALT', 'DFH', 'CGWIC', 'CASIC', 'SHMIT',   # 중국
    'RESH', 'NPOL', 'ISS-R', 'NPOM', 'KHRU', 'LAVOCH', 'VNIIEM', 'PO POLYOT',
    'RKKE', 'RKTS', 'NPOPM',                                              # 러시아
}
LV_COUNTRY = [
    ('Chang Zheng', 'CN'), ('Kuaizhou', 'CN'), ('Jielong', 'CN'), ('Zhuque', 'CN'),
    ('Kinetica', 'CN'), ('Ceres', 'CN'), ('Gravity', 'CN'), ('Tianlong', 'CN'),
    ('Proton', 'RU'), ('Angara', 'RU'), ('Soyuz', 'RU'), ('Zenit', 'RU'), ('Rokot', 'RU'),
    ('Ariane', 'EU'), ('Vega', 'EU'),
    ('Falcon', 'US'), ('Atlas', 'US'), ('Delta', 'US'), ('Vulcan', 'US'),
    ('New Glenn', 'US'), ('Antares', 'US'), ('Electron', 'US'), ('SLS', 'US'),
    ('Minotaur', 'US'), ('Pegasus', 'US'), ('LauncherOne', 'US'), ('Alpha', 'US'),
    ('H-II', 'JP'), ('H3', 'JP'), ('Epsilon', 'JP'),
    ('PSLV', 'IN'), ('GSLV', 'IN'), ('LVM3', 'IN'), ('SSLV', 'IN'),
    ('Nuri', 'KR'), ('KSLV', 'KR'), ('Shavit', 'IL'),
]
EXCLUDE_LV_COUNTRIES = {'CN', 'RU'}

# ---- 최종 목적지가 GEO 가 아닌 페이로드 제거 --------------------------------
# 심우주로 이탈 (GEO/GTO 를 경유만 함)
DROP_STATUS = {'DSO', 'DSA', 'AR'}   # AR = 로켓에 부착된 채 분리 실패
# GEO 도달 실패 후 전이궤도에 좌초
DROP_OPORBIT = {'GTO'}
DROP_JCAT = {
    # ---- 1996-2015 ----
    'S25023': 'TEAMSAT: Ariane 502 시험발사 부착 탑재물 (Status=AO)',
    'S25024': 'Maqsat-B: Ariane 502 질량 모사체 (Status=AO)',
    'S25025': 'YES: Ariane 502 동반 교육위성, HEO',
    'S25068': 'Equator-S: 564x67070 km HEO 자기권 과학위성',
    'S25175': 'Kakehashi(COMETS): H-II 2단 이상으로 472x17715 km 좌초',
    'S25724': 'Milstar 3: Titan IV/Centaur 실패로 1094x5152 km 좌초',
    'S25727': 'Orion 3: Delta III 2단 실패로 LEO 좌초',
    'S27815': 'USERS REM: 재진입 캡슐, LEO',
    'S28500': 'Demosat: Delta IV Heavy 시험 질량모사체, GEO 미도달',
    'S32384': 'GPS IIR-18(M): MEO 항법위성',
    'S32711': 'GPS IIR-19(M): MEO 항법위성',
    'S38752': 'Van Allen Probe A (RBSP): HEO 과학위성',
    'S38753': 'Van Allen Probe B (RBSP): HEO 과학위성',
    # ---- 2016-2025 ----
    'S41896': 'Arase(ERG): 426x32258 km HEO 자기권 과학위성, GEO 아님',
    'S43229': 'PODSAT: 180x22165 km 에 머문 GTO 시험탑재체, 궤도상승 안 함',
    'S43241': 'GSAT-6A: 3차 원지점분사 후 교신두절, 25977x36370 km 좌초 (전손)',
    'S62256': 'PROBA-3 CSC: HEO 편대비행 미션, GEO 아님',
    'S62258': 'PROBA-3 OSC: HEO 편대비행 미션, GEO 아님',
    'S48619': 'TDO 3: MEO 캘리브레이션 타겟(20 kg), 재진입',
    'S48620': 'TDO 4: MEO 캘리브레이션 타겟(20 kg), 재진입',
}
# OpOrbit=GTO 로 남아있지만 실제로는 GEO 에서 운용중인 위성 (DROP_OPORBIT 규칙 예외)
KEEP_JCAT = {
    'S39237': 'Amos 4: GCAT OpOrbit=GTO(2860x35792) 미갱신, 실제 65E GEO 운용',
    'S39206': 'MUOS 2: GCAT OpOrbit=GTO(3800x35781) 미갱신, 실제 GEO 운용',
}
# 참고 메모
NOTES = {
    'S44625': 'MEV-1: Intelsat 901 에 도킹, GEO 운용 (GCAT OpOrbit 은 VHEO 로 미갱신)',
    'S46113': 'MEV-2: Intelsat 10-02 에 도킹',
    'S67480': 'NTS-3 부속 물체, GCAT 에 궤도정보 미기재',
}

# ---- 원지점 엔진(satcat Motor) -> 추진방식 분류 ---------------------------
# satcat 의 Motor 컬럼 값만 사용한다. 값이 없으면 UNK 로 두고 추정하지 않는다.
MOTOR_PREFIX_TYPE = [
    ('XPS/PPS', 'ELEC'), ('PPS-', 'ELEC'), ('SPT', 'ELEC'),   # Safran / Fakel 홀추력기
    ('XIPS', 'ELEC'), ('BHT', 'ELEC'), ('T6', 'ELEC'),        # Boeing XIPS / Busek / QinetiQ
    ('EOR', 'ELEC'), ('EP', 'ELEC'),                          # electric orbit raising
    ('BT-4/AJ-EP', 'CHEM+ELEC'),                              # 화학 원지점분사 + 전기 위치유지
    ('R-4D', 'CHEM'), ('S400', 'CHEM'), ('S-400', 'CHEM'),    # 이원추진 원지점엔진
    ('BT-4', 'CHEM'), ('IHI BT-4', 'CHEM'), ('Leros', 'CHEM'),
    ('ISRO LAM', 'CHEM'), ('LAPS', 'CHEM'), ('DFH', 'CHEM'), ('LAE', 'CHEM'),
    ('IHI', 'CHEM'), ('2x Leros', 'CHEM'), ('X/R-4D', 'CHEM'),
    ('Star ', 'CHEM'), ('TE-M-', 'CHEM'),                    # 고체 원지점 킥모터
    ('AOCS', 'RCS'),                                          # 원지점엔진 없이 RCS 만
]


def prop_type(motor):
    m = motor.replace('?', '').strip()
    if not m or m in ('-', 'UNK'):
        return 'UNK'
    for pre, t in MOTOR_PREFIX_TYPE:
        if m.startswith(pre):
            return t
    return 'UNK'


FIELDS = [
    'jcat', 'satcat', 'launch_tag', 'piece',
    'name', 'pl_name',
    'launch_date', 'year', 'doy', 'day_since_epoch',
    'state', 'owner', 'manufacturer', 'bus', 'motor', 'prop_type',
    'mass_kg', 'mass_flag', 'dry_mass_kg', 'dry_flag', 'tot_mass_kg', 'tot_flag',
    'prop_mass_kg', 'prop_mass_frac', 'mass_is_estimate',
    'op_orbit', 'status',
    'length_m', 'diameter_m', 'span_m', 'shape',
    'launch_orbit_class', 'lv_type', 'lv_country', 'launch_site', 'launch_agency',
    'note',
]


def read_tsv(path):
    with io.open(path, encoding='utf-8', errors='replace') as f:
        lines = f.read().split('\n')
    hdr = [h.strip() for h in lines[0].lstrip('#').split('\t')]
    out = []
    for l in lines[1:]:
        if not l.strip() or l.startswith('#'):
            continue
        p = l.split('\t')
        if len(p) == len(hdr):
            out.append(dict(zip(hdr, [x.strip() for x in p])))
    return out


def parse_date(s):
    """'2019 Feb  5 2101:07' / '2019 Jun 20' -> date"""
    tok = s.replace('?', '').split()
    if len(tok) < 3:
        return None
    try:
        return dt.date(int(tok[0]), MONTHS[tok[1][:3]], int(tok[2]))
    except (ValueError, KeyError):
        return None


def num(s):
    if s is None:
        return None
    s = s.strip()
    if not s or s in ('-', '?'):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fmt(v):
    if v is None:
        return ''
    return ('%.3f' % v).rstrip('0').rstrip('.')


def orbit_class(cat):
    tok = cat.split()
    return tok[1] if len(tok) > 1 else ''


def lv_country(lv):
    for pre, cc in LV_COUNTRY:
        if lv.startswith(pre):
            return cc
    return '??'


def is_cn_ru(sat, lv):
    if sat['State'] in EXCLUDE_STATES:
        return True
    for part in sat['Manufacturer'].replace('?', '').replace('+', '/').split('/'):
        if part.strip() in EXCLUDE_MANUFACTURERS:
            return True
    return lv_country(lv) in EXCLUDE_LV_COUNTRIES


def not_geo_reason(s):
    if s['JCAT'] in KEEP_JCAT:
        return None
    if s['JCAT'] in DROP_JCAT:
        return DROP_JCAT[s['JCAT']]
    if s['Status'] == 'AR':
        return '로켓에서 분리 실패, 부착된 채 좌초 (Status=AR)'
    if s['Status'] in DROP_STATUS:
        return '심우주 이탈 (Status=%s)' % s['Status']
    if s['OpOrbit'] in DROP_OPORBIT:
        return 'GEO 도달 실패, 전이궤도 좌초 (OpOrbit=GTO, Status=%s)' % s['Status']
    if s['Status'] == 'R' and not s['OpOrbit'].startswith('GEO'):
        return 'GEO 미도달 후 재진입 (Status=R, OpOrbit=%s)' % s['OpOrbit']
    return None


def to_record(s, lr, d):
    m, dry, tot = num(s['Mass']), num(s['DryMass']), num(s['TotMass'])
    lv = lr.get('LV_Type', '')
    return {
        'jcat': s['JCAT'], 'satcat': s['Satcat'],
        'launch_tag': s['Launch_Tag'], 'piece': s['Piece'],
        'name': s['Name'], 'pl_name': s['PLName'],
        'launch_date': d.isoformat(), 'year': d.year,
        'doy': d.timetuple().tm_yday, 'day_since_epoch': (d - EPOCH).days,
        'state': s['State'], 'owner': s['Owner'],
        'manufacturer': s['Manufacturer'], 'bus': s['Bus'], 'motor': s['Motor'],
        'prop_type': prop_type(s['Motor']),
        'mass_kg': fmt(m), 'mass_flag': s['MassFlag'],
        'dry_mass_kg': fmt(dry), 'dry_flag': s['DryFlag'],
        'tot_mass_kg': fmt(tot), 'tot_flag': s['TotFlag'],
        'prop_mass_kg': fmt(m - dry) if (m is not None and dry is not None) else '',
        'prop_mass_frac': ('%.3f' % ((m - dry) / m)) if (m and dry is not None) else '',
        'mass_is_estimate': 1 if '?' in (s['MassFlag'] + s['DryFlag'] + s['TotFlag']) else 0,
        'op_orbit': s['OpOrbit'], 'status': s['Status'],
        'length_m': s['Length'], 'diameter_m': s['Diameter'],
        'span_m': s['Span'], 'shape': s['Shape'],
        'launch_orbit_class': orbit_class(lr.get('Category', '')),
        'lv_type': lv, 'lv_country': lv_country(lv) if lv else '',
        'launch_site': lr.get('Launch_Site', ''),
        'launch_agency': lr.get('Agency', ''),
        'note': KEEP_JCAT.get(s['JCAT'], '') or NOTES.get(s['JCAT'], ''),
    }


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start-year', type=int, default=2019)
    ap.add_argument('--end-year', type=int, default=2025)
    return ap.parse_args()


def main(start_year=None, end_year=None):
    global YEAR_MIN, YEAR_MAX, EPOCH
    if start_year is not None:
        YEAR_MIN = start_year
    if end_year is not None:
        YEAR_MAX = end_year
    EPOCH = dt.date(YEAR_MIN, 1, 1)
    dst = os.path.join(HERE, 'geo_satellites_%d_%d.csv' % (YEAR_MIN, YEAR_MAX))

    launches = {}
    for r in read_tsv(LAUNCH):
        d = parse_date(r['Launch_Date'])
        if d and YEAR_MIN <= d.year <= YEAR_MAX and orbit_class(r['Category']) in GEO_BOUND_CLASSES:
            launches[r['Launch_Tag']] = r

    keep, drop_cnru, drop_nongeo = [], [], []
    for s in read_tsv(SATCAT):
        lr = launches.get(s['Launch_Tag'])
        if lr is None or not s['Type'].startswith('P'):
            continue
        d = parse_date(s['LDate']) or parse_date(lr['Launch_Date'])
        if d is None:
            continue
        reason = not_geo_reason(s)
        if reason:
            drop_nongeo.append((s, reason))
            continue
        if is_cn_ru(s, lr.get('LV_Type', '')):
            drop_cnru.append(s)
            continue
        keep.append(to_record(s, lr, d))

    keep.sort(key=lambda x: (x['launch_date'], x['launch_tag'], x['jcat']))

    with io.open(dst, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for rec in keep:
            w.writerow(rec)

    print('GEO 지향 발사 %d 건 -> 페이로드 후보에서' % len(launches))
    print('  최종목적지 GEO 아님 제외 : %d' % len(drop_nongeo))
    for s, why in sorted(drop_nongeo, key=lambda x: x[0]['LDate']):
        print('      %-8s %-26s %-6s  %s' % (s['JCAT'], s['Name'][:26], s['State'], why))
    print('  중국/러시아 제외         : %d' % len(drop_cnru))
    print('  => %s : %d satellites' % (os.path.basename(dst), len(keep)))
    print()

    span = YEAR_MAX - YEAR_MIN + 1
    print('%-6s %5s %10s %10s %10s' % ('year', 'n', 'mass_t', 'dry_t', 'tot_t'))
    for y in range(YEAR_MIN, YEAR_MAX + 1):
        sub = [x for x in keep if x['year'] == y]
        print('%-6d %5d %10.1f %10.1f %10.1f' % (
            y, len(sub),
            sum(float(x['mass_kg']) for x in sub if x['mass_kg']) / 1000.0,
            sum(float(x['dry_mass_kg']) for x in sub if x['dry_mass_kg']) / 1000.0,
            sum(float(x['tot_mass_kg']) for x in sub if x['tot_mass_kg']) / 1000.0))
    tm = sum(float(x['mass_kg']) for x in keep if x['mass_kg']) / 1000.0
    td = sum(float(x['dry_mass_kg']) for x in keep if x['dry_mass_kg']) / 1000.0
    tt = sum(float(x['tot_mass_kg']) for x in keep if x['tot_mass_kg']) / 1000.0
    print('%-6s %5d %10.1f %10.1f %10.1f' % ('TOTAL', len(keep), tm, td, tt))
    print('%-6s %5.1f %10.1f %10.1f %10.1f' % ('/yr', len(keep) / float(span),
                                               tm / span, td / span, tt / span))
    print('\nGCAT 추정치(flag=?) 포함 행 : %d / %d' % (
        sum(1 for x in keep if x['mass_is_estimate']), len(keep)))

    print('\n--- 추진방식(satcat Motor 기준) ---')
    for k, v in sorted(collections.Counter(x['prop_type'] for x in keep).items()):
        sub = [x for x in keep if x['prop_type'] == k]
        fr = [float(x['prop_mass_frac']) for x in sub if x['prop_mass_frac']]
        print('  %-10s %3d 기   추진제질량비 %s' % (
            k, v, ('%.2f ~ %.2f (중앙 %.2f)' % (min(fr), max(fr), sorted(fr)[len(fr) // 2]))
            if fr else '-'))

    reconcile(launches, keep)
    return dst


def reconcile(launches, keep):
    """O.tsv OrbPay(발사체 탑재체 총질량) 와 satcat 위성질량 합을 대조한다."""
    sats = collections.defaultdict(list)
    for s in read_tsv(SATCAT):
        if s['Launch_Tag'] in launches and s['Type'].startswith('P'):
            sats[s['Launch_Tag']].append(s)

    kept_tags = set(x['launch_tag'] for x in keep)
    out = []
    for t in sorted(launches):
        orbpay = num(launches[t]['OrbPay'])
        if orbpay is None:
            continue
        orbpay *= 1000.0
        grp = sats.get(t, [])
        sm = sum(num(s['Mass']) or 0.0 for s in grp)
        st = sum(num(s['TotMass']) or 0.0 for s in grp)
        out.append({
            'launch_tag': t, 'mission': launches[t]['Mission'],
            'launch_date': launches[t]['Launch_Date'],
            'lv_type': launches[t]['LV_Type'],
            'n_payloads': len(grp),
            'orbpay_kg': fmt(orbpay),
            'sum_mass_kg': fmt(sm), 'sum_totmass_kg': fmt(st),
            'diff_kg': fmt(st - orbpay),
            'diff_pct': '%.1f' % (100.0 * (st - orbpay) / orbpay) if orbpay else '',
            'in_final_csv': 1 if t in kept_tags else 0,
        })

    path = os.path.join(HERE, 'mass_reconciliation_%d_%d.csv' % (YEAR_MIN, YEAR_MAX))
    with io.open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        for r in out:
            w.writerow(r)

    def within(p):
        return sum(1 for r in out
                   if abs(float(r['sum_totmass_kg']) - float(r['orbpay_kg']))
                   <= p * float(r['orbpay_kg']))

    print('\n--- OrbPay(O.tsv) vs sum(TotMass)(satcat) 대조 : %d 발사 ---' % len(out))
    print('    완전일치 %d / 1%%이내 %d / 5%%이내 %d' % (
        sum(1 for r in out if abs(float(r['sum_totmass_kg']) - float(r['orbpay_kg'])) < 1.0),
        within(0.01), within(0.05)))
    big = [r for r in out if r['in_final_csv'] == 1
           and abs(float(r['sum_totmass_kg']) - float(r['orbpay_kg'])) > 0.05 * float(r['orbpay_kg'])]
    print('    최종 CSV 에 포함된 발사 중 5%% 초과 불일치 : %d 건' % len(big))
    for r in sorted(big, key=lambda x: -abs(float(x['diff_kg']))):
        print('      %-10s %-26s OrbPay=%7s sumTot=%7s diff=%+8s (%s%%) n=%d' % (
            r['launch_tag'], r['mission'][:26], r['orbpay_kg'], r['sum_totmass_kg'],
            r['diff_kg'], r['diff_pct'], r['n_payloads']))
    print('    -> %s' % os.path.basename(path))


if __name__ == '__main__':
    _a = parse_args()
    main(_a.start_year, _a.end_year)
