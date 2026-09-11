# -*- coding: utf-8 -*-
"""수요 JSON 의 시간축 분포를 그린다.  사용법: py -3 plot_demand.py <demand_*.json>"""
import io
import json
import os
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.transforms import blended_transform_factory
from matplotlib.patches import Patch

HERE = os.path.dirname(os.path.abspath(__file__))
_arg = sys.argv[1] if len(sys.argv) > 1 else 'demand_hist_10yr_2016_2025.json'
SRC = os.path.join(os.path.dirname(HERE), os.path.basename(_arg))
DST = os.path.join(HERE, os.path.basename(SRC).replace('.json', '.png'))

with io.open(SRC, encoding='utf-8') as f:
    prof = json.load(f)

MY = prof['mission_years']
DPY = prof['days_per_year']
DPS = prof['days_per_step']
SPY = DPY // DPS
NSTEP = MY * SPY
Y0 = int(os.path.basename(SRC).split('_')[-2])

mass = [0.0] * NSTEP      # t
count = [0] * NSTEP
for e in prof['events']:
    s = min(e['demand_day'] // DPS, NSTEP - 1)
    mass[s] += e['mass_kg'] / 1000.0
    count[s] += 1

days = [s * DPS for s in range(NSTEP)]
occupied = [s for s in range(NSTEP) if count[s] > 0]
empty = [s for s in range(NSTEP) if count[s] == 0]

# 연속 공백 구간
gaps, run = [], []
for s in range(NSTEP):
    if count[s] == 0:
        run.append(s)
    else:
        if len(run) >= 2:
            gaps.append((run[0], run[-1]))
        run = []
if len(run) >= 2:
    gaps.append((run[0], run[-1]))

fig, ax = plt.subplots(figsize=(15, 6.2))

# 막대는 단색. 높이가 이미 질량을 나타내므로 색으로 별도 정보를 싣지 않는다.
BAR_COLOR = '#4C72B0'

ax.bar(days, mass, width=DPS * 0.82, color=BAR_COLOR, zorder=3)

# ---- 연도 구분선 + 축 바깥에 연도/연간총량 ----
YTOP = max(mass) * 1.08
for y in range(1, MY):
    ax.axvline(y * DPY, color='#999999', lw=0.8, ls='--', alpha=0.6, zorder=2)

tr = blended_transform_factory(ax.transData, ax.transAxes)
for y in range(MY):
    ytot = sum(mass[y * SPY:(y + 1) * SPY])
    ax.text(y * DPY + DPY / 2.0, 1.065, '%d' % (Y0 + y), transform=tr,
            ha='center', va='bottom', fontsize=10.5, color='#222222',
            clip_on=False)
    ax.text(y * DPY + DPY / 2.0, 1.018, '%.1f t' % ytot, transform=tr,
            ha='center', va='bottom', fontsize=9.5, color='#222222',
            clip_on=False)

top = sorted(range(NSTEP), key=lambda s: -mass[s])[:4]
for s in top:
    ax.annotate('%.1f t' % mass[s], (s * DPS, mass[s]),
                textcoords='offset points', xytext=(0, 5), ha='center',
                fontsize=8.5, color='#222222', zorder=8)

ax.set_ylabel('step demand mass  [t]', fontsize=11)
ax.set_xlabel('mission day   (1 step = 30 days)', fontsize=11)
ax.set_ylim(0, YTOP)
ax.set_yticks(range(0, int(max(mass)) + 1, 2))
ax.set_xlim(-DPS, NSTEP * DPS)
ax.set_xticks(range(0, NSTEP * DPS + 1, DPY))
ax.set_title('GEO payload demand from %d to %d' % (Y0, Y0 + MY - 1),
             fontsize=14, pad=44)
ax.grid(axis='y', alpha=0.22, zorder=1)
ax.set_axisbelow(True)

for out in (DST, DST.replace('.png', '.pdf')):
    fig.savefig(out, dpi=300, bbox_inches='tight', facecolor='white')
    print('saved:', out)

print()
print('스텝 %d개 중 수요 있음 %d / 비어있음 %d' % (NSTEP, len(occupied), len(empty)))
print('스텝 수요질량  최대 %.2f t (day %d) / 평균(있는스텝) %.2f t'
      % (max(mass), mass.index(max(mass)) * DPS, sum(mass) / len(occupied)))
print()
print('--- 연도별 ---')
for y in range(MY):
    sl = slice(y * SPY, (y + 1) * SPY)
    print('   %d : %6.1f t   수요스텝 %2d/12   최대스텝 %5.2f t'
          % (Y0 + y, sum(mass[sl]), sum(1 for c in count[sl] if c), max(mass[sl])))
