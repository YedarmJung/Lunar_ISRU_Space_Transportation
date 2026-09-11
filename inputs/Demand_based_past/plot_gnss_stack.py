# -*- coding: utf-8 -*-
"""GPS / Galileo / O3b 만 뽑아 연별 누적막대로 그린다.

  대상 : plot_satcat_monthly.is_meo_payload 를 통과한 MEO 인공위성 중
         아래 세 성좌. (유인 우주선/상단 부착물/OpOrbit 미갱신 GEO 위성은 이미 제외됨)
  질량 : satcat TotMass [kg]

  satcat 이름 규칙
    GPS     : 초기 'Navstar 1~..' -> 이후 'GPS IIR-..', 'GPS III SV..'
    Galileo : 시험기 'Giove A/B'  -> 'GalileoSat-1~34'
    O3b     : 'O3b PFM/FM..'      -> 'O3b mPOWER F1~F10'
"""
import collections
import io
import os

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

import make_geo_csv as G
import plot_satcat_monthly as P

HERE = os.path.dirname(os.path.abspath(__file__))
DST = os.path.join(HERE, 'gnss_o3b_yearly_mass.png')

# 아래에서 위로 쌓이는 순서
GROUPS = [
    ('GPS',     ('GPS', 'Navstar'),          '#4C72B0'),   # 파랑
    ('Galileo', ('GalileoSat', 'Giove'),     '#C44E52'),   # 빨강
    ('O3b',     ('O3b',),                    '#CCB974'),   # 노랑
]


def group_of(name):
    for label, prefixes, _ in GROUPS:
        if name.startswith(prefixes):
            return label
    return None


def main():
    sat = G.read_tsv(P.SRC)
    tags = P.geo_bound_tags()
    meo = [s for s in sat if s['Type'].startswith('P') and P.is_meo_payload(s, tags)]

    agg = collections.defaultdict(float)     # (year, group) -> t
    cnt = collections.Counter()
    for s in meo:
        g = group_of(s['Name'])
        if g is None:
            continue
        y = s['LDate'][:4]
        if not y.isdigit():
            continue
        agg[(int(y), g)] += (G.num(s['TotMass']) or 0.0) / 1000.0
        cnt[(int(y), g)] += 1

    years = sorted(set(y for y, _ in agg))
    y0, y1 = years[0], years[-1]
    xs = list(range(y0, y1 + 1))

    last_y, last_m = max((P.ym(s['LDate']) for s in meo if group_of(s['Name'])))
    partial = (last_y == y1 and last_m < 12)

    fig, ax = plt.subplots(figsize=(15, 6.0))
    bottom = [0.0] * len(xs)
    for label, _, color in GROUPS:
        vals = [agg.get((y, label), 0.0) for y in xs]
        n = sum(cnt.get((y, label), 0) for y in xs)
        ax.bar(xs, vals, width=0.8, bottom=bottom, color=color, linewidth=0,
               zorder=3, label='%s  (%d sats, %.0f t)' % (label, n, sum(vals)))
        bottom = [b + v for b, v in zip(bottom, vals)]

    if partial:
        ax.bar([xs[-1]], [bottom[-1]], width=0.8, color='none',
               edgecolor='#666666', lw=0.9, ls=':', zorder=4)
        ax.set_xlabel('launch year        (%d partial: through %s)'
                      % (last_y, ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul',
                                  'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][last_m - 1]),
                      fontsize=11)
    else:
        ax.set_xlabel('launch year', fontsize=11)

    ax.set_ylabel('payload mass launched  [t / year]', fontsize=11)
    ax.set_title('GPS, Galileo and O3b: payload mass launched per year, %d–%d'
                 % (y0, y1), fontsize=14, pad=12)
    ax.set_xlim(y0 - 1, y1 + 1)
    ax.set_ylim(0, max(bottom) * 1.12)
    ax.set_xticks(range(((y0 // 5) + 1) * 5, y1 + 1, 5))
    ax.grid(axis='y', alpha=0.25, zorder=1)
    ax.set_axisbelow(True)
    ax.legend(loc='upper left', fontsize=10, frameon=False)

    for out in (DST, DST.replace('.png', '.pdf')):
        fig.savefig(out, dpi=300, bbox_inches='tight', facecolor='white')
        print('saved:', out)

    print()
    print('%-6s %8s %8s %8s %9s' % ('year', 'GPS', 'Galileo', 'O3b', 'total'))
    for y in xs:
        row = [agg.get((y, lb), 0.0) for lb, _, _ in GROUPS]
        if sum(row) == 0:
            continue
        print('%-6d %8.1f %8.1f %8.1f %9.1f' % (y, row[0], row[1], row[2], sum(row)))
    print()
    for lb, _, _ in GROUPS:
        v = sum(agg.get((y, lb), 0.0) for y in xs)
        n = sum(cnt.get((y, lb), 0) for y in xs)
        print('%-8s %3d기  %6.1f t   기당 평균 %.0f kg' % (lb, n, v, v * 1000 / n))


if __name__ == '__main__':
    main()
