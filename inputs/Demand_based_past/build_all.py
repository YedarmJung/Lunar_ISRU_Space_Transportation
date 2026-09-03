# -*- coding: utf-8 -*-
"""7/8/9/10년 수요 JSON 을 한 번에 생성한다 (끝은 2025 고정, 시작연도만 당김)."""
import sys

import make_geo_csv
import make_payload_mass
import make_demand_json

END = 2025
STARTS = [2019, 2018, 2017, 2016]


def run(start):
    n = END - start + 1
    bar = '=' * 72
    print('\n%s\n  %d년치  %d ~ %d\n%s' % (bar, n, start, END, bar))
    for mod in (make_geo_csv, make_payload_mass, make_demand_json):
        print('\n--- %s ---' % mod.__name__)
        mod.main(start, END)


if __name__ == '__main__':
    for s in (STARTS if len(sys.argv) < 2 else [int(a) for a in sys.argv[1:]]):
        run(s)
