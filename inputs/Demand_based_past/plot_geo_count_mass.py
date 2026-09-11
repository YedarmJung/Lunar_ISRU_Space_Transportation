# -*- coding: utf-8 -*-
"""GEO 페이로드의 건수와 건당 평균질량을 시간축으로 그린다.

  대상 : plot_satcat_monthly.is_geo_payload 를 통과한 GEO 위성
         (발사 궤도클래스 GTO/GEO/STO  또는  satcat OpOrbit=GEO 의 합집합,
          심우주 이탈/전이궤도 좌초/로켓 미분리는 제외)

  통합 : 같은 발사에 실린 개별질량 MERGE_MAX_KG 이하 위성이 2기 이상이면
         하나의 '건'으로 묶고 질량은 합산한다. demand JSON 과 같은 규칙.
         ESPA 급 군용 부속위성이나 Astranis 묶음발사가 건수를 부풀리는 것을 막는다.

  질량 : satcat TotMass [kg]

  사용법 : py -3 plot_geo_count_mass.py [--by year|month] [--exclude-cnru]
"""
import argparse
import collections
import io
import os

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

import make_geo_csv as G
import plot_satcat_monthly as P

HERE = os.path.dirname(os.path.abspath(__file__))

MERGE_MAX_KG = 500.0
MERGE_MIN_COUNT = 2

BAR_COLOR = '#9DB8D6'        # 건수 (연한 파랑)
LINE_COLOR = '#C44E52'       # 건당 평균질량 (빨강)

MONTH_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
              'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


def units(exclude_cnru):
    """GEO 위성을 읽어 소형 동반발사분을 통합한 '건' 목록으로 돌려준다."""
    sat = G.read_tsv(P.SRC)
    tags = P.geo_bound_tags()
    pay = [s for s in sat if s['Type'].startswith('P') and P.is_geo_payload(s, tags)]
    if exclude_cnru:
        pay = [s for s in pay if not P.is_cnru_owned(s)]

    by_launch = collections.defaultdict(list)
    for s in pay:
        k = P.ym(s['LDate'])
        if k is None:
            continue
        by_launch[(s['Launch_Tag'], k)].append(s)

    out, merged_sats, merged_units = [], 0, 0
    for (tag, k), grp in by_launch.items():
        small = [s for s in grp if (G.num(s['TotMass']) or 0.0) <= MERGE_MAX_KG]
        if len(small) >= MERGE_MIN_COUNT:
            out.append((k, sum(G.num(s['TotMass']) or 0.0 for s in small)))
            merged_sats += len(small)
            merged_units += 1
            grp = [s for s in grp if s not in small]
        for s in grp:
            out.append((k, G.num(s['TotMass']) or 0.0))
    return out, len(pay), merged_sats, merged_units


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--by', choices=['year', 'month'], default='year')
    ap.add_argument('--exclude-cnru', action='store_true')
    args = ap.parse_args()

    us, n_sat, m_sat, m_unit = units(args.exclude_cnru)

    cnt = collections.Counter()
    mass = collections.defaultdict(float)
    for (y, mo), kg in us:
        key = y if args.by == 'year' else (y, mo)
        cnt[key] += 1
        mass[key] += kg / 1000.0

    keys = sorted(cnt)
    if args.by == 'year':
        xs = list(range(keys[0], keys[-1] + 1))
        pos = xs
        xlabel = 'launch year'
    else:
        (y0, m0), (y1, m1) = keys[0], keys[-1]
        n = (y1 - y0) * 12 + (m1 - m0) + 1
        xs = [(y0 + (m0 - 1 + i) // 12, (m0 - 1 + i) % 12 + 1) for i in range(n)]
        pos = list(range(n))
        xlabel = 'launch month'

    c = [cnt.get(k, 0) for k in xs]
    avg = [(mass[k] * 1000.0 / cnt[k]) if cnt.get(k) else float('nan') for k in xs]

    fig, ax = plt.subplots(figsize=(15, 6.0))
    ax.bar(pos, c, width=0.8 if args.by == 'year' else 1.0,
           color=BAR_COLOR, linewidth=0, zorder=2, label='payloads launched')
    ax.set_ylabel('payloads launched  [count]', fontsize=11, color='#3C5E86')
    ax.tick_params(axis='y', colors='#3C5E86')
    ax.set_ylim(0, max(c) * 1.25)

    ax2 = ax.twinx()
    ax2.plot(pos, avg, color=LINE_COLOR, lw=1.6, marker='o', ms=3.2,
             zorder=4, label='mean mass per payload')
    ax2.set_ylabel('mean mass per payload  [kg]', fontsize=11, color=LINE_COLOR)
    ax2.tick_params(axis='y', colors=LINE_COLOR)
    ax2.set_ylim(0, max(v for v in avg if v == v) * 1.15)

    if args.by == 'year':
        ax.set_xticks(range(((xs[0] // 10) + 1) * 10, xs[-1] + 1, 10))
        ax.set_xlim(xs[0] - 1, xs[-1] + 1)
    else:
        ticks = [i for i, (yy, mm) in enumerate(xs) if mm == 1 and yy % 10 == 0]
        ax.set_xticks(ticks)
        ax.set_xticklabels([str(xs[i][0]) for i in ticks])
        ax.set_xlim(-2, len(xs) + 1)

    note = ['%.0f kg 이하 동반발사분 통합' % MERGE_MAX_KG]
    ax.set_xlabel(xlabel, fontsize=11)
    ax.set_title('GEO payloads: count and mean mass per %s, %d–%d%s'
                 % (args.by, (xs[0] if args.by == 'year' else xs[0][0]),
                    (xs[-1] if args.by == 'year' else xs[-1][0]),
                    '   (CN/RU/SU excluded)' if args.exclude_cnru else ''),
                 fontsize=14, pad=12)
    ax.grid(axis='y', alpha=0.22, zorder=1)
    ax.set_axisbelow(True)

    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc='upper left', fontsize=10, frameon=False)

    sfx = ('_nocnru' if args.exclude_cnru else '')
    dst = os.path.join(HERE, 'geo_count_mass_%s%s.png' % (args.by, sfx))
    for out in (dst, dst.replace('.png', '.pdf')):
        fig.savefig(out, dpi=300, bbox_inches='tight', facecolor='white')
        print('saved:', out)

    print()
    print('GEO 위성 %d기 -> 통합 후 %d건 (소형 %d기가 %d건으로)'
          % (n_sat, len(us), m_sat, m_unit))
    print()
    if args.by == 'year':
        print('%-6s %6s %10s %11s' % ('year', 'count', 'mass_t', 'mean_kg'))
        for k in xs:
            if cnt.get(k):
                print('%-6d %6d %10.1f %11.0f' % (k, cnt[k], mass[k], mass[k] * 1000 / cnt[k]))


if __name__ == '__main__':
    main()
