# -*- coding: utf-8 -*-
"""Write a compact QA report for nearby rent comparison data."""
import json
import math
import sys
from pathlib import Path

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else 'auction_data.json')
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else 'nearby_rent_status.json')

LAT0, LNG0 = 37.5414336177, 126.8404964502  # 화곡역, 화곡로 지하 168
TARGET_AREA = 30.0
AREA_TOL = 8.0


def dist(lat1, lng1, lat2, lng2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def sample_row(distance, row):
    return {
        'distance_m': round(distance),
        'dong': row.get('u'),
        'jibun': row.get('j'),
        'type': row.get('t'),
        'area_m2': row.get('a'),
        'deposit_manwon': row.get('dp'),
        'rent_manwon': row.get('m'),
        'ym': row.get('ym'),
        'day': row.get('d'),
        'building': row.get('b'),
    }


data = json.loads(SRC.read_text(encoding='utf-8'))
geo = data.get('geo') or {}
wolse = list(data.get('wolse') or [])
jeonse = list(data.get('jeonse') or [])
rentals = jeonse + wolse

with_j = [r for r in rentals if r.get('u') and r.get('j')]
with_geo = [r for r in with_j if f"{r['u']} {r['j']}" in geo]

candidates = []
for row in wolse:
    try:
        area = float(row.get('a') or 0)
    except Exception:
        continue
    if abs(area - TARGET_AREA) > AREA_TOL:
        continue
    if not row.get('u') or not row.get('j'):
        continue
    xy = geo.get(f"{row['u']} {row['j']}")
    if not xy:
        continue
    try:
        d = dist(LAT0, LNG0, float(xy[0]), float(xy[1]))
    except Exception:
        continue
    if d <= 800:
        candidates.append((d, row))

candidates.sort(key=lambda x: (x[0], abs(float(x[1].get('a') or 0) - TARGET_AREA), str(x[1].get('ym') or ''), str(x[1].get('d') or '')))
within_500 = [(d, r) for d, r in candidates if d <= 500]
within_800 = candidates

report = {
    'updated': data.get('updated'),
    'geo_keys': len(geo),
    'rentals': {
        'total': len(rentals),
        'with_jibun': len(with_j),
        'with_coordinates': len(with_geo),
        'coordinate_coverage': round(len(with_geo) / len(with_j), 4) if with_j else 0,
    },
    'wolse': {'total': len(wolse)},
    'target_test': {
        'address': '서울 강서구 화곡로 지하 168',
        'area_m2': TARGET_AREA,
        'area_tolerance_m2': AREA_TOL,
        'within_500m': len(within_500),
        'within_800m': len(within_800),
        'samples_500m': [sample_row(d, r) for d, r in within_500[:5]],
        'samples_800m': [sample_row(d, r) for d, r in within_800[:5]],
    },
}

OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))

assert len(with_j) > 1000, f'rental jibun missing/too low: {len(with_j)}'
assert len(with_geo) > 1000, f'rental coordinates too low: {len(with_geo)}'
assert report['target_test']['within_800m'] > 0, 'target nearby monthly-rent sample is zero within 800m'
