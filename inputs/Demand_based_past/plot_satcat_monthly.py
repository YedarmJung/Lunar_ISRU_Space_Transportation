# -*- coding: utf-8 -*-
"""satcat.tsv -> 월별 궤도 투입 페이로드 질량 그래프 (전 기간).

  대상 : Type 이 'P'(payload) 로 시작하는 물체 전체
         로켓 상단(R)/파편(D)/부품(C) 은 제외한다.
         파편은 발사된 것이 아니라 이후 파손으로 생긴 것이라 모질량과 중복이고,
         실제로 satcat 상 파편 질량 합은 5 t 로 무시할 수준이다.
  질량 : TotMass [kg]  (발사시 총질량)
  x축  : LDate 의 연-월
"""
import argparse
import collections
import io
import os

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

import make_geo_csv as G          # CN/RU 제작사 목록 재사용

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'satcat.tsv')

BAR_COLOR = '#4C72B0'
ROLL = 12                      # 이동평균 창 [개월]

MONTHS = {m: i + 1 for i, m in enumerate(
    ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'])}


def read_tsv(path):
    ls = io.open(path, encoding='utf-8', errors='replace').read().split('\n')
    h = [x.strip() for x in ls[0].lstrip('#').split('\t')]
    out = []
    for l in ls[1:]:
        if not l.strip() or l.startswith('#'):
            continue
        q = l.split('\t')
        if len(q) == len(h):
            out.append(dict(zip(h, [y.strip() for y in q])))
    return out


def num(x):
    x = x.strip()
    if not x or x in ('-', '?'):
        return None
    try:
        return float(x)
    except ValueError:
        return None


def ym(ldate):
    """'2019 Feb  5 2101:07' -> (2019, 2)"""
    tok = ldate.replace('?', '').split()
    if len(tok) < 2 or not tok[0].isdigit():
        return None
    mo = MONTHS.get(tok[1][:3])
    if mo is None:
        return None
    return int(tok[0]), mo


# 셔틀 궤도선: GCAT 은 Type='PH' 페이로드로 등재하지만 실제로는 자체 주엔진을 단
# 재사용 발사체 2단이고 궤도에 배달되는 화물이 아니다. Type='R'(로켓 상단)을 뺀 것과
# 같은 논리로 제외한다. Soyuz/Dragon/Progress 등은 별도 로켓이 쏘는 진짜 페이로드라 남긴다.
SHUTTLE_ORBITERS = ('Columbia', 'Challenger', 'Discovery', 'Atlantis', 'Endeavour',
                    'Enterprise')


def is_shuttle_orbiter(s):
    if not s['Type'].startswith('PH'):
        return False
    return s['Name'].split('(')[0].strip() in SHUTTLE_ORBITERS


def is_cnru_owned(s):
    """위성의 국적/제작사 기준. 발사체 국적은 보지 않는다."""
    if s['State'] in ('CN', 'RU', 'SU'):
        return True
    for part in s['Manufacturer'].replace('?', '').replace('+', '/').split('/'):
        if part.strip() in G.EXCLUDE_MANUFACTURERS:
            return True
    return False


# GEO 판정: 아래 둘의 합집합. 어느 한쪽만 쓰면 빠지는 위성이 생긴다.
#   (A) 발사 궤도클래스가 GTO/GEO/STO   -> 전기추진으로 상승중이라 satcat OpOrbit 이
#       아직 HEO/VHEO/MEO 인 최신 위성을 잡는다 (217기 487 t).
#   (B) satcat OpOrbit 이 GEO 로 시작    -> 셔틀이 LEO 에서 방출하고 PAM/IUS 상단이
#       GTO 로 올린 1982~85년 통신위성을 잡는다. GCAT 은 셔틀 발사를 궤도선 기준
#       (LEO)으로 분류해서 (A)에서 통째로 누락된다 (51기 132 t, 중 36기가 셔틀).
# 최종 목적지가 GEO 가 아닌 것(심우주 이탈/전이궤도 좌초/로켓 미분리)은 제거한다.
DROP_STATUS = {'DSO', 'DSA', 'AR'}


def is_geo_payload(s, tags):
    if not (s['Launch_Tag'] in tags or s['OpOrbit'].startswith('GEO')):
        return False
    if s['Status'] in DROP_STATUS:
        return False
    if s['OpOrbit'] == 'GTO':          # GEO 도달 실패, 전이궤도 좌초
        return False
    return True


# MEO 판정. GEO 와 달리 발사 궤도클래스(MEO/MTO)는 쓰지 않는다 - LEO/HEO 물체를
# 49기나 끌고 들어오면서 정작 놓치는 MEO 위성은 없기 때문이다.
#   - OpOrbit 이 MEO 로 시작
#   - GEO 지향 발사분은 제외: AEHF, Viasat-3 EMEA, Eutelsat 172B 등 GEO 위성이
#     전기추진 상승중이라 OpOrbit 이 MEO 로 남아있다.
#   - Status AO/AR : 상단에 부착된 밸러스트/시험체 (Blue Ring Pathfinder 34 t,
#     Kosmos-382 19 t 등). 자유비행 위성이 아니다.
#   - Type PH*/PP* : 유인 우주선 및 그 시제기 (Apollo 9 LM, Korabl'-Sputnik, Mercury).
def is_meo_payload(s, geo_tags):
    if not s['OpOrbit'].startswith('MEO'):
        return False
    if s['Launch_Tag'] in geo_tags:
        return False
    if s['Status'] in ('AO', 'AR', 'DSO', 'DSA'):
        return False
    if s['Type'].split()[0].startswith(('PH', 'PP')):
        return False
    return True


def geo_bound_tags():
    """O.tsv 에서 궤도클래스가 GTO/GEO/STO 인 발사 태그."""
    out = set()
    for r in G.read_tsv(G.LAUNCH):
        tok = r['Category'].split()
        if len(tok) > 1 and tok[1] in ('GTO', 'GEO', 'STO'):
            out.add(r['Launch_Tag'])
    return out


def collect(exclude_cnru=False, no_shuttle=False, geo_only=False, meo_only=False):
    sat = read_tsv(SRC)
    pay = [s for s in sat if s['Type'].startswith('P')]
    if geo_only:
        tags = geo_bound_tags()
        pay = [s for s in pay if is_geo_payload(s, tags)]
    if meo_only:
        tags = geo_bound_tags()
        pay = [s for s in pay if is_meo_payload(s, tags)]
    if no_shuttle:
        pay = [s for s in pay if not is_shuttle_orbiter(s)]
    if exclude_cnru:
        pay = [s for s in pay if not is_cnru_owned(s)]
    agg = collections.defaultdict(float)
    cnt = collections.Counter()
    bad = 0
    for s in pay:
        k = ym(s['LDate'])
        if k is None:
            bad += 1
            continue
        agg[k] += (num(s['TotMass']) or 0.0) / 1000.0
        cnt[k] += 1
    return pay, agg, cnt, bad


def plot_monthly(agg, cnt):
    keys = sorted(agg)
    y0, m0 = keys[0]
    y1, m1 = keys[-1]
    n = (y1 - y0) * 12 + (m1 - m0) + 1
    idx = [(y0 + (m0 - 1 + i) // 12, (m0 - 1 + i) % 12 + 1) for i in range(n)]
    mass = [agg.get(k, 0.0) for k in idx]
    nsat = [cnt.get(k, 0) for k in idx]
    x = list(range(n))

    roll = []
    for i in range(n):
        lo = max(0, i - ROLL + 1)
        roll.append(sum(mass[lo:i + 1]) / float(i - lo + 1))

    fig, ax = plt.subplots(figsize=(15, 6.0))
    ax.bar(x, mass, width=1.0, color=BAR_COLOR, linewidth=0, zorder=3)
    ax.plot(x, roll, color='#1a1a1a', lw=1.1, zorder=4,
            label='%d-month moving average' % ROLL)

    ticks, labels = [], []
    for yy in range(((y0 // 10) + 1) * 10, y1 + 1, 10):
        ticks.append((yy - y0) * 12 + (1 - m0))
        labels.append(str(yy))
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels)
    ax.set_xlim(-2, n + 1)
    ax.set_ylim(0, max(mass) * 1.06)
    ax.set_xlabel('launch month', fontsize=11)
    ax.set_ylabel('payload mass launched  [t / month]', fontsize=11)
    ax.set_title('Payload mass launched to orbit per month, %d–%d' % (y0, y1),
                 fontsize=14, pad=12)
    ax.legend(loc='upper left', fontsize=9.5, frameon=False)
    return fig, ax, idx, mass, nsat, (y0, m0, y1, m1, n)


def plot_yearly(agg, cnt):
    last_y, last_m = max(agg)
    years = sorted(set(y for y, _ in agg))
    y0, y1 = years[0], years[-1]
    xs = list(range(y0, y1 + 1))
    mass = [sum(v for (yy, _), v in agg.items() if yy == y) for y in xs]
    nsat = [sum(v for (yy, _), v in cnt.items() if yy == y) for y in xs]

    # 마지막 해는 카탈로그가 연중까지만 채워져 있어 부분값이다
    partial = last_m < 12
    colors = [BAR_COLOR] * len(xs)
    if partial:
        colors[-1] = '#A8BEDC'

    fig, ax = plt.subplots(figsize=(15, 6.0))
    ax.bar(xs, mass, width=0.8, color=colors, linewidth=0, zorder=3)
    ax.set_xticks(range(((y0 // 10) + 1) * 10, y1 + 1, 10))
    ax.set_xlim(y0 - 1, y1 + 1)
    ax.set_ylim(0, max(mass) * 1.10)
    ax.set_xlabel('launch year', fontsize=11)
    ax.set_ylabel('payload mass  [t / year]', fontsize=11)
    ax.set_title('Payload mass launched to orbit per year, %d–%d' % (y0, y1),
                 fontsize=14, pad=12)

    return fig, ax, xs, mass, nsat, (y0, y1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--by', choices=['month', 'year'], default='month')
    ap.add_argument('--exclude-cnru', action='store_true',
                    help='중국/러시아/구소련 소유 위성 제외')
    ap.add_argument('--no-shuttle', action='store_true',
                    help='스페이스셔틀 궤도선 제외 (발사체 하드웨어)')
    ap.add_argument('--geo-only', action='store_true',
                    help='GTO/GEO/STO 로 향한 발사의 페이로드만')
    ap.add_argument('--meo-only', action='store_true',
                    help='MEO 운용 인공위성만 (유인/부착물 제외)')
    args = ap.parse_args()

    pay, agg, cnt, bad = collect(args.exclude_cnru, args.no_shuttle,
                                 args.geo_only, args.meo_only)
    sfx = (('_geo' if args.geo_only else '')
           + ('_meo' if args.meo_only else '')
           + ('_noshuttle' if args.no_shuttle else '')
           + ('_nocnru' if args.exclude_cnru else ''))

    if args.by == 'month':
        fig, ax, idx, mass, nsat, meta = plot_monthly(agg, cnt)
        dst = os.path.join(HERE, 'satcat_monthly_mass%s.png' % sfx)
        unit = 'month'
        lab = lambda i: '%d-%02d' % idx[i]
    else:
        fig, ax, idx, mass, nsat, meta = plot_yearly(agg, cnt)
        dst = os.path.join(HERE, 'satcat_yearly_mass%s.png' % sfx)
        unit = 'year'
        lab = lambda i: '%d' % idx[i]

    note = []
    if args.geo_only:
        note.append('GEO-bound only')
    if args.meo_only:
        note.append('MEO satellites only')
    if args.no_shuttle:
        note.append('Shuttle orbiters excluded')
    if args.exclude_cnru:
        note.append('CN/RU/SU satellites excluded')

    ax.grid(axis='y', alpha=0.25, zorder=1)
    ax.set_axisbelow(True)
    for out in (dst, dst.replace('.png', '.pdf')):
        fig.savefig(out, dpi=300, bbox_inches='tight', facecolor='white')
        print('saved:', out)

    n = len(mass)
    print()
    print('payload %d기, 질량 합 %.0f t, %d개 %s' % (len(pay), sum(mass), n, unit))
    print('LDate 파싱 실패 : %d' % bad)
    print()
    print('--- 질량 상위 8 ---')
    for i in sorted(range(n), key=lambda i: -mass[i])[:8]:
        print('   %-8s %8.1f t  (%d기)' % (lab(i), mass[i], nsat[i]))
    print()
    print('--- 최근 12 ---')
    for i in range(max(0, n - 12), n):
        print('   %-8s %8.1f t  (%d기)' % (lab(i), mass[i], nsat[i]))


if __name__ == '__main__':
    main()
