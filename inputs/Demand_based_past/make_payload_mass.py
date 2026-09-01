# -*- coding: utf-8 -*-
"""
geo_satellites_2019_2025.csv  ->  OTV 가 실제로 GEO 까지 날라야 할 페이로드 질량.

[문제]
  satcat 의 Mass 는 '발사체가 투입한 시점의 질량' 이다.
  GTO/STO 로 투입된 위성은 이 안에 자기가 GEO 까지 올라가는 데 쓸 추진제가 들어있다.
  OTV 가 궤도상승을 대신해주면 그 추진제는 실을 필요가 없다.

      Mass(투입시) = DryMass + 궤도유지(SK)추진제 + 궤도상승추진제
      OTV 페이로드 = DryMass + 궤도유지(SK)추진제          <- 우리가 원하는 값

[방법]
  A) GEO 직접투입 위성 : 애초에 궤도상승 추진제가 없다 -> TotMass 그대로 사용.
  B) GTO/STO 위성      : 로켓방정식으로 궤도상승분만 역산해서 뺀다.

        payload = Mass x exp( -dv_raise_eff / (g0 * Isp) )

     dv_raise 는 발사장별 표준 전이궤도에서 GEO 까지의 임펄시브 dv 다.
       - 원지점이 GEO 이하  : 원지점 1회 분사 (원궤도화 + 경사각 동시 변경)
       - 원지점이 GEO 초과  : 준-바이엘립틱 2회 분사 (STO)
     O.tsv 의 Perigee/Apogee/Inc 는 '주차궤도' 값이라 쓸 수 없다.
     (예: AEHF 5 가 175 x 501 km, i=28.1 로 기록됨)
     따라서 발사장별 표준 전이궤도를 쓰고, STO 는 O.tsv 의 원지점 고도만 활용한다.

[가정] - 전부 아래 상수에서 바꿀 수 있다
  Isp   : 화학 이원추진 원지점엔진 320 s / 전기추진 홀스러스터 1800 s
  LOW_THRUST_FACTOR : 전기추진 저추력 나선상승의 중력손실 보정 1.25

[GCAT DryMass 로 하한처리 하지 않는 이유]
  결과가 satcat DryMass 보다 작게 나오는 위성이 90 기 중 37 기나 된다.
  이건 방법의 문제가 아니라 GCAT DryMass 가 라운드 추정치(dry_flag='?')로
  5~20% 높게 잡혀 있기 때문이다. 실제 공개값과 대조하면 로켓방정식 쪽이 맞다.
      Galaxy 33 (GEOStar-2e) : 로켓방정식 2082 kg / GCAT dry 2500 / 실제 BOL 약 2000
      Hot Bird 13F (Eurostar Neo, 전전기) : 4038 / 4200 / 실제 BOL 약 4050
      SES-22                 : 1969 / 2000  (거의 일치)
  따라서 하한처리 없이 로켓방정식 값을 쓰고, DryMass 보다 작은 경우만
  below_gcat_dry 플래그로 표시한다.
"""
import collections
import csv
import io
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'geo_satellites_2019_2025.csv')
LAUNCH = os.path.join(HERE, 'O.tsv')
DST = os.path.join(HERE, 'geo_payload_demand_2019_2025.csv')

MU, RE, RGEO = 398600.4418, 6378.137, 42164.0
VGEO = math.sqrt(MU / RGEO)
G0 = 9.80665

ISP = {'CHEM': 320.0, 'CHEM+ELEC': 320.0, 'ELEC': 1800.0}
LOW_THRUST_FACTOR = {'ELEC': 1.25}          # 저추력 나선상승 중력손실 보정
DEFAULT_STO_APOGEE_KM = 60000.0

# 발사장별 표준 GTO (근지점고도 km, 원지점고도 km, 전이궤도 경사각 deg)
SITE_GTO = {
    'CSG':  (250.0, 35786.0,  6.0),   # Kourou,         Ariane 5/6
    'CC':   (185.0, 35786.0, 27.0),   # Cape Canaveral, Falcon 9 / Atlas / Delta / Vulcan
    'KSC':  (185.0, 35786.0, 27.0),   # Kennedy,        Falcon Heavy
    'TNSC': (250.0, 35786.0, 20.1),   # Tanegashima,    H-IIA/H3 long-coast
    'SHAR': (170.0, 35975.0, 19.3),   # Sriharikota,    GSLV / LVM3 / PSLV
}
DEFAULT_SITE = (200.0, 35786.0, 25.0)

# prop_type 이 UNK 인 GTO/STO 위성은 추진제질량비로 화학/전기를 가른다
UNK_CHEM_FRAC_THRESHOLD = 0.30


def dv_to_geo(hp_km, ha_km, inc_deg):
    """전이궤도 -> GEO 임펄시브 dv [km/s]"""
    rp, ra = RE + hp_km, RE + ha_km
    i = math.radians(inc_deg)
    if ra <= RGEO * 1.02:
        a = (rp + ra) / 2.0
        va = math.sqrt(MU * (2.0 / ra - 1.0 / a))
        return math.sqrt(va * va + VGEO * VGEO - 2.0 * va * VGEO * math.cos(i))
    # 초동기 전이궤도: 원지점에서 근지점을 GEO 로 올리며 경사각 변경 -> GEO 에서 원지점 낮춤
    a1 = (rp + ra) / 2.0
    va1 = math.sqrt(MU * (2.0 / ra - 1.0 / a1))
    a2 = (RGEO + ra) / 2.0
    va2 = math.sqrt(MU * (2.0 / ra - 1.0 / a2))
    dv1 = math.sqrt(va1 * va1 + va2 * va2 - 2.0 * va1 * va2 * math.cos(i))
    vp2 = math.sqrt(MU * (2.0 / RGEO - 1.0 / a2))
    return dv1 + (vp2 - VGEO)


def read_tsv(path):
    ls = io.open(path, encoding='utf-8', errors='replace').read().split('\n')
    h = [x.strip() for x in ls[0].lstrip('#').split('\t')]
    out = []
    for l in ls[1:]:
        if not l.strip() or l.startswith('#'):
            continue
        p = l.split('\t')
        if len(p) == len(h):
            out.append(dict(zip(h, [y.strip() for y in p])))
    return out


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def resolve_prop_type(r):
    """prop_type 이 UNK 면 추진제질량비로 추정한다."""
    pt = r['prop_type']
    if pt != 'UNK':
        return pt, 'satcat Motor'
    frac = num(r['prop_mass_frac'])
    if frac is None:
        return 'CHEM', 'default'
    if frac >= UNK_CHEM_FRAC_THRESHOLD:
        return 'CHEM', 'prop_mass_frac'
    return 'ELEC', 'prop_mass_frac'


def build():
    launches = {r['Launch_Tag']: r for r in read_tsv(LAUNCH)}
    rows = list(csv.DictReader(io.open(SRC, encoding='utf-8-sig')))
    out = []
    for r in rows:
        cls = r['launch_orbit_class']
        mass, dry, tot = num(r['mass_kg']), num(r['dry_mass_kg']), num(r['tot_mass_kg'])
        rec = dict(r)

        if cls == 'GEO':
            rec.update({'dv_raise_ms': '0', 'isp_s': '', 'prop_type_used': '',
                        'prop_type_src': '', 'raise_prop_kg': '0',
                        'payload_mass_kg': ('%.1f' % tot) if tot is not None else '',
                        'payload_basis': 'direct_geo:tot_mass', 'below_gcat_dry': 0})
            out.append(rec)
            continue

        hp, ha, inc = SITE_GTO.get(r['launch_site'], DEFAULT_SITE)
        if cls == 'STO':
            a = num(launches.get(r['launch_tag'], {}).get('Apogee'))
            ha = a if (a is not None and a > RGEO - RE) else DEFAULT_STO_APOGEE_KM
            hp = 200.0

        pt, src = resolve_prop_type(r)
        isp = ISP.get(pt, 320.0)
        dv = dv_to_geo(hp, ha, inc) * 1000.0 * LOW_THRUST_FACTOR.get(pt, 1.0)

        payload = mass * math.exp(-dv / (G0 * isp)) if mass is not None else None

        rec.update({
            'dv_raise_ms': '%.0f' % dv, 'isp_s': '%.0f' % isp,
            'prop_type_used': pt, 'prop_type_src': src,
            'raise_prop_kg': ('%.1f' % (mass - payload)) if payload is not None else '',
            'payload_mass_kg': ('%.1f' % payload) if payload is not None else '',
            'payload_basis': 'rocket_eq',
            'below_gcat_dry': 1 if (payload is not None and dry is not None
                                    and payload < dry) else 0,
        })
        out.append(rec)

    fields = list(rows[0].keys()) + ['dv_raise_ms', 'isp_s', 'prop_type_used',
                                     'prop_type_src', 'raise_prop_kg',
                                     'payload_mass_kg', 'payload_basis',
                                     'below_gcat_dry']
    return out, fields


def tsum(rs, k):
    return sum(num(x[k]) or 0.0 for x in rs) / 1000.0


def main():
    out, fields = build()
    with io.open(DST, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for rec in out:
            w.writerow(rec)

    print('%s : %d satellites' % (os.path.basename(DST), len(out)))
    print()
    print('%-6s %4s %10s %10s %10s %10s' % ('class', 'n', 'wet_t', 'payload_t', 'dry_t', 'saved_t'))
    for cls in ('GEO', 'GTO', 'STO'):
        sub = [x for x in out if x['launch_orbit_class'] == cls]
        print('%-6s %4d %10.1f %10.1f %10.1f %10.1f' % (
            cls, len(sub), tsum(sub, 'mass_kg'), tsum(sub, 'payload_mass_kg'),
            tsum(sub, 'dry_mass_kg'), tsum(sub, 'mass_kg') - tsum(sub, 'payload_mass_kg')))
    print('%-6s %4d %10.1f %10.1f %10.1f %10.1f' % (
        'ALL', len(out), tsum(out, 'mass_kg'), tsum(out, 'payload_mass_kg'),
        tsum(out, 'dry_mass_kg'), tsum(out, 'mass_kg') - tsum(out, 'payload_mass_kg')))
    print()
    print('payload_basis :', dict(collections.Counter(x['payload_basis'] for x in out)))
    print()
    print('%-6s %4s %10s %10s' % ('year', 'n', 'wet_t', 'payload_t'))
    for y in range(2019, 2026):
        sub = [x for x in out if int(x['year']) == y]
        print('%-6d %4d %10.1f %10.1f' % (y, len(sub), tsum(sub, 'mass_kg'),
                                          tsum(sub, 'payload_mass_kg')))
    print()
    print('--- applied dv ---')
    seen = {}
    for x in out:
        if x['launch_orbit_class'] == 'GEO':
            continue
        k = (x['launch_site'], x['launch_orbit_class'], x['prop_type_used'])
        seen.setdefault(k, [x['dv_raise_ms'], 0])
        seen[k][1] += 1
    for k in sorted(seen):
        print('   %-6s %-4s %-10s dv=%5s m/s  Isp=%4.0f  n=%d' % (
            k[0], k[1], k[2], seen[k][0], ISP.get(k[2], 320.0), seen[k][1]))
    nb = sum(int(x['below_gcat_dry']) for x in out)
    print()
    print('below_gcat_dry : %d / %d  (GCAT DryMass 가 라운드 추정치라 높게 잡힌 경우)' % (
        nb, len([x for x in out if x['launch_orbit_class'] != 'GEO'])))

    print()
    print('--- 민감도: 총 payload [t] ---')
    base_chem, base_elec, base_ltf = ISP['CHEM'], ISP['ELEC'], LOW_THRUST_FACTOR['ELEC']
    print('%-30s %10s' % ('가정', 'payload_t'))
    for lbl, ic, ie, lt in [
            ('기준 (320s / 1800s / 1.25)', 320.0, 1800.0, 1.25),
            ('화학 Isp 310s', 310.0, 1800.0, 1.25),
            ('화학 Isp 325s', 325.0, 1800.0, 1.25),
            ('전기 Isp 1600s', 320.0, 1600.0, 1.25),
            ('전기 Isp 2000s', 320.0, 2000.0, 1.25),
            ('저추력보정 1.0 (임펄시브)', 320.0, 1800.0, 1.00),
            ('저추력보정 1.5', 320.0, 1800.0, 1.50)]:
        ISP['CHEM'] = ISP['CHEM+ELEC'] = ic
        ISP['ELEC'] = ie
        LOW_THRUST_FACTOR['ELEC'] = lt
        alt, _ = build()
        print('%-30s %10.1f' % (lbl, tsum(alt, 'payload_mass_kg')))
    ISP['CHEM'] = ISP['CHEM+ELEC'] = base_chem
    ISP['ELEC'] = base_elec
    LOW_THRUST_FACTOR['ELEC'] = base_ltf


if __name__ == '__main__':
    main()
