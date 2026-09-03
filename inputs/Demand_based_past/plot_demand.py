# -*- coding: utf-8 -*-
"""수요 JSON 의 시간축 분포를 그린다.  사용법: py -3 plot_demand.py <demand_*.json>"""
import io
import json
import os
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

HERE = os.path.dirname(os.path.abspath(__file__))
_arg = sys.argv[1] if len(sys.argv) > 1 else 'demand_hist_7yr_2019_2025.json'
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
    s = e['demand_day'] // DPS
    if s >= NSTEP:
        s = NSTEP - 1
    mass[s] += e['mass_kg'] / 1000.0
    count[s] += 1

days = [s * DPS for s in range(NSTEP)]
cum, acc = [], 0.0
for v in mass:
    acc += v
    cum.append(acc)

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

fig, (ax, ax2) = plt.subplots(
    2, 1, figsize=(15, 8.5), sharex=True,
    gridspec_kw={'height_ratios': [3, 1.15], 'hspace': 0.13})

# ---------- 상단: 스텝별 수요 질량 ----------
for g0, g1 in gaps:
    ax.axvspan(g0 * DPS - DPS / 2.0, g1 * DPS + DPS / 2.0,
               color='#e8433a', alpha=0.07, zorder=0)

cmax = max(count) if count else 1
cmap = plt.get_cmap('viridis')
colors = [cmap(0.15 + 0.75 * (c - 1) / max(cmax - 1, 1)) if c else '#dddddd'
          for c in count]
ax.bar(days, mass, width=DPS * 0.82, color=colors, zorder=3)

YTOP = max(20.0, max(mass) * 1.32)
for y in range(1, MY):
    ax.axvline(y * DPY, color='#555555', lw=1.0, ls='--', alpha=0.65, zorder=2)
ax.axhspan(YTOP * 0.88, YTOP, color='white', zorder=5)
ax.axhline(YTOP * 0.88, color='#cccccc', lw=0.8, zorder=6)
for y in range(MY):
    ax.text(y * DPY + DPY / 2.0, YTOP * 0.94, 'Y%d\n%d' % (y + 1, Y0 + y),
            ha='center', va='center', fontsize=10, color='#333333',
            linespacing=1.25, zorder=7)

mean = sum(mass) / float(NSTEP)
ax.axhline(mean, color='#e8433a', lw=1.2, ls=':', zorder=4)
ax.text(NSTEP * DPS * 0.33, mean + 0.35, 'mean %.2f t/step (all steps)' % mean,
        ha='center', va='bottom', fontsize=9, color='#e8433a', zorder=8,
        bbox=dict(boxstyle='round,pad=0.2', fc='white', ec='none', alpha=0.85))

top = sorted(range(NSTEP), key=lambda s: -mass[s])[:5]
for s in top:
    ax.annotate('%.1f t (n=%d)' % (mass[s], count[s]),
                (s * DPS, mass[s]), textcoords='offset points',
                xytext=(0, 6), ha='center', fontsize=8.5, color='#222222',
                zorder=8)

ax.set_ylabel('step demand mass  [t]', fontsize=11)
ax.set_ylim(0, YTOP)
ax.set_yticks(range(0, int(YTOP * 0.88), 2))
ax.set_title('GEO payload demand on the 30-day grid  —  %s\n'
             '%d events, %.1f t total, %d/%d steps occupied'
             % (prof['name'], len(prof['events']), sum(mass), len(occupied), NSTEP),
             fontsize=12.5, pad=12)
ax.grid(axis='y', alpha=0.25, zorder=1)
ax.set_axisbelow(True)
handles = [Patch(fc=cmap(0.15 + 0.75 * (c - 1) / max(cmax - 1, 1)),
                 label='%d sat%s' % (c, '' if c == 1 else 's'))
           for c in (1, 2, 3, 4, 5, cmax)]
handles.append(Patch(fc='#e8433a', alpha=0.18,
                     label='gap (2+ consecutive empty steps)'))
fig.legend(handles=handles, loc='lower center', ncol=7, fontsize=9,
           frameon=False, bbox_to_anchor=(0.5, -0.035),
           title='satellites per step', title_fontsize=9)

# ---------- 하단: 누적 ----------
ax2.step(days, cum, where='mid', color='#1f6fb4', lw=1.8)
ax2.fill_between(days, cum, step='mid', color='#1f6fb4', alpha=0.13)
ideal = [(s + 1) / float(NSTEP) * sum(mass) for s in range(NSTEP)]
ax2.plot(days, ideal, color='#e8433a', lw=1.2, ls=':', label='uniform rate')
for y in range(1, MY):
    ax2.axvline(y * DPY, color='#555555', lw=1.0, ls='--', alpha=0.65)
ax2.set_ylabel('cumulative  [t]', fontsize=11)
ax2.set_xlabel('mission day   (1 step = 30 days,  1 year = 360 days)', fontsize=11)
ax2.grid(alpha=0.25)
ax2.set_axisbelow(True)
ax2.legend(loc='upper left', fontsize=9)

ax2.set_xlim(-DPS, NSTEP * DPS)
ax2.set_xticks(range(0, NSTEP * DPS + 1, DPY))

fig.savefig(DST, dpi=150, bbox_inches='tight', facecolor='white')
print('saved:', DST)

# ---------- 콘솔 요약 ----------
print()
print('스텝 %d개 중 수요 있음 %d / 비어있음 %d' % (NSTEP, len(occupied), len(empty)))
print('스텝 수요질량  최대 %.2f t (day %d) / 평균(전체) %.2f t / 평균(있는스텝) %.2f t'
      % (max(mass), mass.index(max(mass)) * DPS, mean, sum(mass) / len(occupied)))
print()
print('--- 연속 공백구간 (2스텝 이상) ---')
for g0, g1 in gaps:
    print('   day %4d ~ %4d  (%d스텝 = %d일)  Y%d'
          % (g0 * DPS, g1 * DPS, g1 - g0 + 1, (g1 - g0 + 1) * DPS, g0 // SPY + 1))
print()
print('--- 연도별 ---')
for y in range(MY):
    sl = slice(y * SPY, (y + 1) * SPY)
    print('   Y%d (%d) : %6.1f t   수요스텝 %2d/12   최대스텝 %5.2f t'
          % (y + 1, Y0 + y, sum(mass[sl]), sum(1 for c in count[sl] if c),
             max(mass[sl])))
