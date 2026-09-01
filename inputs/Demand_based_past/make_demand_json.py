# -*- coding: utf-8 -*-
"""
geo_payload_demand_2019_2025.csv  ->  모델 입력 JSON (30일 격자).

[실제 달력 -> 360일/년 격자 매핑]
  실적은 2019-01-01 ~ 2025-12-31 의 실제 달력(윤년 포함 2557일)이고,
  모델은 1년 = 360일, 1스텝 = 30일 = 12스텝/년 이다.
  연도 안에서의 상대위치를 보존하는 방식으로 매핑한다.

      year        = 발사연도 - 2018                       (1..7)
      frac        = (doy - 1) / (그 해 일수)               (0 <= frac < 1)
      step_in_year= round(frac * 12), 0..11 로 클램프       <- 30일 격자 스냅
      demand_day  = (year - 1) * 360 + step_in_year * 30

  이렇게 하면 data.py 가 요구하는 두 조건이 항상 만족된다.
      demand_day // 30  = demand_step        (0..83, T=85 이므로 유효)
      demand_day // 360 = year - 1           (model.py 의 연도 경계와 일치)

[질량] geo_payload_demand_2019_2025.csv 의 payload_mass_kg
       = 위성 건조질량 + 궤도유지 추진제. 궤도상승 추진제는 OTV 가 담당하므로 제외됨.
"""
import calendar
import collections
import csv
import datetime as dt
import io
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'geo_payload_demand_2019_2025.csv')
DST = os.path.join(os.path.dirname(HERE), 'demand_hist_7yr_2019_2025.json')

YEAR0 = 2018                 # year = 발사연도 - YEAR0
MISSION_YEARS = 7
DAYS_PER_YEAR = 360
DAYS_PER_STEP = 30
PAYLOAD_LEAD_DAYS = 30
STEPS_PER_YEAR = DAYS_PER_YEAR // DAYS_PER_STEP      # 12


def safe_id(s):
    s = re.sub(r'[^0-9A-Za-z]+', '-', s).strip('-')
    return s[:28] if s else 'SAT'


def main():
    rows = list(csv.DictReader(io.open(SRC, encoding='utf-8-sig')))

    events = []
    used = set()
    for r in rows:
        d = dt.date(*[int(x) for x in r['launch_date'].split('-')])
        year = d.year - YEAR0
        if not (1 <= year <= MISSION_YEARS):
            raise ValueError('%s: year %d out of range' % (r['name'], year))

        doy = d.timetuple().tm_yday
        days_in_year = 366 if calendar.isleap(d.year) else 365
        frac = (doy - 1) / float(days_in_year)
        step_in_year = min(STEPS_PER_YEAR - 1, max(0, int(round(frac * STEPS_PER_YEAR))))
        demand_day = (year - 1) * DAYS_PER_YEAR + step_in_year * DAYS_PER_STEP

        mass = int(round(float(r['payload_mass_kg'])))
        if mass <= 0:
            raise ValueError('%s: nonpositive mass' % r['name'])

        eid = '%s-%s' % (r['jcat'], safe_id(r['name']))
        if (year, eid) in used:
            raise ValueError('duplicate event id %s' % eid)
        used.add((year, eid))

        events.append({
            'year': year,
            'event_id': eid,
            'demand_day': demand_day,
            'mass_kg': mass,
            '_name': r['name'],
            '_launch_date': r['launch_date'],
            '_class': r['launch_orbit_class'],
        })

    events.sort(key=lambda e: (e['demand_day'], e['event_id']))

    # ---- data.py / model.py 정합성 자체검증 ----
    mission_days = MISSION_YEARS * DAYS_PER_YEAR
    for e in events:
        assert 0 <= e['demand_day'] <= mission_days, e
        assert e['demand_day'] % DAYS_PER_STEP == 0, e
        assert e['demand_day'] // DAYS_PER_YEAR == e['year'] - 1, e
        assert 1 <= e['year'] <= MISSION_YEARS, e

    profile = {
        'name': 'hist_7yr_2019_2025_geo',
        'comment': ('GCAT satcat.tsv + O.tsv 기반 2019-2025 실적. '
                    '중국/러시아 및 최종목적지 비-GEO 제외. '
                    'mass_kg = 건조질량 + 궤도유지추진제 (궤도상승분은 OTV 담당이라 제외).'),
        'source': 'inputs/Demand_based_past/geo_payload_demand_2019_2025.csv',
        'mission_years': MISSION_YEARS,
        'days_per_year': DAYS_PER_YEAR,
        'days_per_step': DAYS_PER_STEP,
        'payload_lead_days': PAYLOAD_LEAD_DAYS,
        'events': [{k: v for k, v in e.items() if not k.startswith('_')} for e in events],
    }

    with io.open(DST, 'w', encoding='utf-8') as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)
        f.write('\n')

    # ---------------- 리포트 ----------------
    tot = sum(e['mass_kg'] for e in events)
    print('%s' % DST)
    print('  events %d   total %.1f t' % (len(events), tot / 1000.0))
    print()
    print('%-6s %5s %10s %10s' % ('year', 'n', 'mass_t', '실제_t'))
    for y in range(1, MISSION_YEARS + 1):
        sub = [e for e in events if e['year'] == y]
        real = sum(float(r['payload_mass_kg']) for r in rows
                   if int(r['year']) == y + YEAR0)
        print('%-6d %5d %10.1f %10.1f   (%d)' % (
            y, len(sub), sum(e['mass_kg'] for e in sub) / 1000.0, real / 1000.0, y + YEAR0))
    print()
    print('--- 스텝별 분포 (30일 격자, 총 %d 스텝) ---' % (MISSION_YEARS * STEPS_PER_YEAR))
    by_step = collections.defaultdict(list)
    for e in events:
        by_step[e['demand_day'] // DAYS_PER_STEP].append(e)
    occupied = sorted(by_step)
    print('  이벤트가 있는 스텝 : %d / %d' % (len(occupied), MISSION_YEARS * STEPS_PER_YEAR))
    print('  스텝당 이벤트 수   : 최대 %d, 평균 %.1f' % (
        max(len(v) for v in by_step.values()),
        len(events) / float(len(occupied))))
    hist = collections.Counter(len(v) for v in by_step.values())
    print('  분포 :', dict(sorted(hist.items())))
    print()
    print('--- 스텝별 상세 ---')
    for s in occupied:
        v = by_step[s]
        m = sum(e['mass_kg'] for e in v) / 1000.0
        y = s // STEPS_PER_YEAR + 1
        print('  step %2d (day %4d, Y%d) %5.2f t  n=%d  %s' % (
            s, s * DAYS_PER_STEP, y, m, len(v),
            ', '.join('%s(%s)' % (e['_name'][:18], e['_launch_date'][5:]) for e in v[:4])
            + (' ...' if len(v) > 4 else '')))


if __name__ == '__main__':
    main()
