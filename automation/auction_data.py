# -*- coding: utf-8 -*-
"""
auction_data.py — 경매 + 주변 전월세 비교용 JSON 가공
사용: python auction_data.py [csv경로]
입력: data/molit_kangseo.csv (또는 molit_trade_live.csv)
출력: auction_data.json

2026.08.19
  · 매매/전세/월세 모두 지번(j), 계약일(d), 건물명(b), 층(f) 포함
  · 모든 거래의 지번을 카카오 로컬 API로 지오코딩
  · data/geocode_cache.json 캐시 재사용
  · 신규 지번은 병렬 지오코딩 + 재시도, 일시 오류는 실패값으로 고정 캐시하지 않음
  · 취소거래(cdeal_type=O) 제외
"""
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))

if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
    SRC = sys.argv[1]
else:
    _CANDIDATES = [
        os.path.join(HERE, '..', 'data', 'molit_trade_live.csv'),
        os.path.join(HERE, 'data', 'molit_trade_live.csv'),
        os.path.join(HERE, 'molit_trade_live.csv'),
        os.path.join('data', 'molit_trade_live.csv'),
        'molit_trade_live.csv',
        os.path.join('data', 'molit_kangseo.csv'),
    ]
    SRC = next((p for p in _CANDIDATES if os.path.exists(p)), None)

if SRC is None:
    print('[오류] CSV를 찾지 못했습니다. 수집을 먼저 실행하세요.')
    sys.exit(1)

print(f'CSV: {os.path.abspath(SRC)}')
OUT = os.path.join(HERE, 'auction_data.json')
CACHE = os.path.join(HERE, 'data', 'geocode_cache.json')

TYPE_MAP = {'연립다세대': 'v', '아파트': 'a', '오피스텔': 'o'}
SIGUNGU = '서울 강서구'
KAKAO_KEY = os.environ.get('KAKAO_REST_KEY', '').strip()
GEO_WORKERS = max(1, min(12, int(os.environ.get('GEO_WORKERS', '8') or 8)))
GEO_TIMEOUT = max(3.0, float(os.environ.get('GEO_TIMEOUT', '8') or 8))
GEO_RETRIES = max(1, min(5, int(os.environ.get('GEO_RETRIES', '3') or 3)))


def num(x):
    try:
        return float(str(x).replace(',', '').strip())
    except Exception:
        return 0.0


def load_cache():
    if os.path.exists(CACHE):
        try:
            data = json.load(open(CACHE, encoding='utf-8'))
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print(f'! 지오 캐시 읽기 실패 — 새 캐시로 진행: {e}')
    return {}


def save_cache(c):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    tmp = CACHE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(c, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, CACHE)


def kakao_geocode(query):
    """return ('ok'|'not_found'|'transient', (lat,lng)|None)."""
    url = 'https://dapi.kakao.com/v2/local/search/address.json?' + \
          urllib.parse.urlencode({'query': query, 'size': 1})
    req = urllib.request.Request(
        url,
        headers={
            'Authorization': 'KakaoAK ' + KAKAO_KEY,
            'User-Agent': 'RentCheck-GeoBuilder/1.0',
        },
    )

    for attempt in range(1, GEO_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=GEO_TIMEOUT) as r:
                d = json.loads(r.read().decode('utf-8'))
            docs = d.get('documents') or []
            if not docs:
                return 'not_found', None
            return 'ok', (
                round(float(docs[0]['y']), 6),
                round(float(docs[0]['x']), 6),
            )
        except urllib.error.HTTPError as e:
            retryable = e.code in (408, 429, 500, 502, 503, 504)
            if not retryable:
                print(f'  [지오코딩 HTTP {e.code}] {query}')
                return 'not_found', None
            if attempt == GEO_RETRIES:
                print(f'  [지오코딩 일시 실패 HTTP {e.code}] {query}')
                return 'transient', None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as e:
            if attempt == GEO_RETRIES:
                print(f'  [지오코딩 일시 실패] {query} — {e}')
                return 'transient', None
        except Exception as e:
            if attempt == GEO_RETRIES:
                print(f'  [지오코딩 예외] {query} — {e}')
                return 'transient', None

        time.sleep(min(2.5, 0.35 * (2 ** (attempt - 1))))

    return 'transient', None


def build_geo(keys):
    cache = load_cache()

    # 과거 실행에서 timeout/네트워크 오류가 None으로 저장된 값도 다시 시도한다.
    todo = sorted(k for k in keys if not cache.get(k))
    if not KAKAO_KEY:
        print(f'! KAKAO_REST_KEY 없음 — 신규/재시도 지번 {len(todo)}건은 좌표 없이 진행합니다')
        return {k: v for k, v in cache.items() if v}

    if not todo:
        print(f'지오코딩 신규 0건 (유효 캐시 {sum(1 for v in cache.values() if v)}건)')
        return {k: v for k, v in cache.items() if v}

    print(
        f'지오코딩 신규/재시도 {len(todo)}건 '
        f'(유효 캐시 {sum(1 for v in cache.values() if v)}건, workers={GEO_WORKERS}, retries={GEO_RETRIES})'
    )

    ok = not_found = transient = done = 0
    with ThreadPoolExecutor(max_workers=GEO_WORKERS) as pool:
        futures = {pool.submit(kakao_geocode, SIGUNGU + ' ' + k): k for k in todo}
        for fut in as_completed(futures):
            k = futures[fut]
            try:
                status, xy = fut.result()
            except Exception as e:
                print(f'  [지오코딩 worker 실패] {k} — {e}')
                status, xy = 'transient', None

            done += 1
            if status == 'ok' and xy:
                cache[k] = [xy[0], xy[1]]
                ok += 1
            elif status == 'not_found':
                # 실제 검색 결과 없음만 None으로 기록. 다음 전체 실행에서 다시 시도될 수 있다.
                cache[k] = None
                not_found += 1
            else:
                # timeout/429/5xx 등은 실패를 캐시에 고정하지 않는다.
                cache.pop(k, None)
                transient += 1

            if done % 100 == 0 or done == len(todo):
                print(f'  {done}/{len(todo)} · 성공 {ok} · 검색없음 {not_found} · 일시실패 {transient}')
                save_cache(cache)

    geo = {k: v for k, v in cache.items() if v}
    print(f'지오코딩 완료 · 유효 좌표 {len(geo)}건 · 이번 성공 {ok} · 일시실패 {transient}')
    return geo


rows = list(csv.DictReader(open(SRC, encoding='utf-8-sig')))
sale, jeonse, wolse = [], [], []
latest_ym = max(r['deal_ym'] for r in rows if r.get('deal_ym'))
geo_keys = set()

for r in rows:
    if str(r.get('cdeal_type') or '').strip() == 'O':
        continue

    dt = r.get('deal_type', '')
    if '_' not in dt:
        continue
    btype, trade = dt.rsplit('_', 1)
    t = TYPE_MAP.get(btype)
    if not t:
        continue

    umd = (r.get('umd_name') or '').strip()
    a = num(r.get('area_m2'))
    ym = (r.get('deal_ym') or '').strip()
    day = (r.get('deal_day') or '').strip()
    y = (r.get('build_year') or '').strip()
    jibun = (r.get('jibun') or '').strip()
    building = (r.get('building_name') or '').strip()[:40]
    floor = (r.get('floor') or '').strip()

    base = {'u': umd, 't': t, 'a': a, 'ym': ym, 'd': day, 'y': y, 'b': building, 'f': floor}
    if jibun:
        base['j'] = jibun
        geo_keys.add(umd + ' ' + jibun)

    if trade == '매매':
        p = num(r.get('deal_amount'))
        if p > 0 and a > 0:
            rec = dict(base)
            rec['p'] = int(p)
            sale.append(rec)
    elif trade == '전월세':
        dep = num(r.get('deposit'))
        mon = num(r.get('monthly_rent'))
        if mon == 0 and dep > 0 and a > 0:
            rec = dict(base)
            rec['p'] = int(dep)
            jeonse.append(rec)
        elif mon > 0 and a > 0:
            rec = dict(base)
            rec['dp'] = int(dep)
            rec['m'] = int(mon)
            wolse.append(rec)

geo = build_geo(geo_keys)

out = {
    'updated': latest_ym,
    'regions': sorted(set(x['u'] for x in sale + jeonse + wolse)),
    'geo': geo,
    'sale': sale,
    'jeonse': jeonse,
    'wolse': wolse,
}
with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, separators=(',', ':'))

sz = os.path.getsize(OUT) / 1024
all_rows = sale + jeonse + wolse
with_j = [x for x in all_rows if x.get('j')]
hit = sum(1 for x in with_j if (x['u'] + ' ' + x['j']) in geo)
rentals = jeonse + wolse
rental_j = [x for x in rentals if x.get('j')]
rental_hit = sum(1 for x in rental_j if (x['u'] + ' ' + x['j']) in geo)
print(f'✓ {OUT} 생성 — 매매 {len(sale)} / 전세 {len(jeonse)} / 월세 {len(wolse)}건, {sz:.0f}KB, 기준 {latest_ym}')
print(f'  좌표: 지번 {len(geo_keys)}종 중 {len(geo)}종 확보 · 전체 거래 {hit}/{len(with_j)}건 매칭')
print(f'  전월세 좌표 매칭: {rental_hit}/{len(rental_j)}건')
