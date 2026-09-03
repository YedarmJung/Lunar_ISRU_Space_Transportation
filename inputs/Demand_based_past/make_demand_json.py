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
import argparse
import calendar
import collections
import csv
import datetime as dt
import io
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = DST = None             # main() 에서 연도 범위로부터 결정
YEAR_MIN, YEAR_MAX = 2019, 2025
YEAR0 = YEAR_MIN - 1         # year = 발사연도 - YEAR0
MISSION_YEARS = YEAR_MAX - YEAR_MIN + 1
DAYS_PER_YEAR = 360
DAYS_PER_STEP = 30
PAYLOAD_LEAD_DAYS = 30
STEPS_PER_YEAR = DAYS_PER_YEAR // DAYS_PER_STEP      # 12

# 전체 수요를 뒤로 미는 오프셋. 첫 스텝에 수요가 몰리지 않게 한 스텝 늦춘다.
# 연말(step_in_year=11) 이벤트는 다음 해로 넘어가므로 year 를 재계산한다.
SHIFT_DAYS = 30

# 같은 발사에 실려 올라간 소형 위성은 하나의 이벤트로 묶는다.
# (개별 질량이 MERGE_MASS_MAX_KG 이하이고, 같은 발사에 2기 이상인 경우)
# 예: 2022-144 Falcon Heavy USSF-44/LDPE-2 의 ESPA 부속위성 16기,
#     2024-252 Astranis 4기(각 400 kg).
MERGE_MASS_MAX_KG = 500.0
MERGE_MIN_COUNT = 2


def safe_id(s):
    s = re.sub(r'[^0-9A-Za-z]+', '-', s).strip('-')
    return s[:28] if s else 'SAT'


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start-year', type=int, default=2019)
    ap.add_argument('--end-year', type=int, default=2025)
    return ap.parse_args()


def main(start_year=None, end_year=None):
    global SRC, DST, YEAR_MIN, YEAR_MAX, YEAR0, MISSION_YEARS
    if start_year is not None:
        YEAR_MIN = start_year
    if end_year is not None:
        YEAR_MAX = end_year
    YEAR0 = YEAR_MIN - 1
    MISSION_YEARS = YEAR_MAX - YEAR_MIN + 1
    SRC = os.path.join(HERE, 'geo_payload_demand_%d_%d.csv' % (YEAR_MIN, YEAR_MAX))
    DST = os.path.join(os.path.dirname(HERE),
                       'demand_hist_%dyr_%d_%d.json' % (MISSION_YEARS, YEAR_MIN, YEAR_MAX))

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
            '_tag': r['launch_tag'],
        })

    # ---- 소형 위성 통합 (같은 발사 + 개별 500 kg 이하) ----
    groups = collections.OrderedDict()
    for e in events:
        if e['mass_kg'] <= MERGE_MASS_MAX_KG:
            groups.setdefault((e['_tag'], e['demand_day']), []).append(e)
    merged_n = 0
    for (tag, day), grp in groups.items():
        if len(grp) < MERGE_MIN_COUNT:
            continue
        for e in grp:
            events.remove(e)
        tot = sum(e['mass_kg'] for e in grp)
        events.append({
            'year': grp[0]['year'],
            'event_id': '%s-smallsat-x%d' % (safe_id(tag), len(grp)),
            'demand_day': day,
            'mass_kg': tot,
            '_name': '%s 소형위성 %d기 통합' % (tag, len(grp)),
            '_launch_date': grp[0]['_launch_date'],
            '_class': grp[0]['_class'],
            '_tag': tag,
        })
        merged_n += len(grp)
        print('[merge] %-24s %2d기 -> %5d kg  (%s)' % (
            tag, len(grp), tot,
            ', '.join('%s %g' % (e['_name'][:16], e['mass_kg']) for e in grp)))
    if merged_n:
        print('[merge] 총 %d기가 %d개 이벤트로 통합됨'
              % (merged_n, sum(1 for g in groups.values() if len(g) >= MERGE_MIN_COUNT)))

    # ---- 전체 시프트 + year 재계산 ----
    mission_days = MISSION_YEARS * DAYS_PER_YEAR
    max_day = mission_days - DAYS_PER_STEP
    crossed = 0
    for e in events:
        new_day = min(e['demand_day'] + SHIFT_DAYS, max_day)
        new_year = new_day // DAYS_PER_YEAR + 1
        if new_year != e['year']:
            crossed += 1
        e['demand_day'], e['year'] = new_day, new_year
    if SHIFT_DAYS:
        print('[shift] 전체 +%d일, 연도 경계를 넘은 이벤트 %d건 (year 재계산)'
              % (SHIFT_DAYS, crossed))

    events.sort(key=lambda e: (e['demand_day'], e['event_id']))

    # ---- data.py / model.py 정합성 자체검증 ----
    mission_days = MISSION_YEARS * DAYS_PER_YEAR
    for e in events:
        assert 0 <= e['demand_day'] <= mission_days, e
        assert e['demand_day'] % DAYS_PER_STEP == 0, e
        assert e['demand_day'] // DAYS_PER_YEAR == e['year'] - 1, e
        assert 1 <= e['year'] <= MISSION_YEARS, e

    profile = {
        'name': 'hist_%dyr_%d_%d_geo' % (MISSION_YEARS, YEAR_MIN, YEAR_MAX),
        'comment': ('GCAT satcat.tsv + O.tsv 기반 %d-%d 실적. ' % (YEAR_MIN, YEAR_MAX) +
                    '중국/러시아 및 최종목적지 비-GEO 제외. '
                    'mass_kg = 건조질량 + 궤도유지추진제 (궤도상승분은 OTV 담당이라 제외).'),
        'source': 'inputs/Demand_based_past/%s' % os.path.basename(SRC),
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
    print('  %-4s %5d %10.1f %10.1f' % ('TOT', len(events),
                                        sum(e['mass_kg'] for e in events) / 1000.0,
                                        sum(float(r['payload_mass_kg']) for r in rows) / 1000.0))
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
    return DST


if __name__ == '__main__':
    _a = parse_args()
    main(_a.start_year, _a.end_year)
