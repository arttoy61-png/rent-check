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
  · 취소거래(cdeal_type=O) 제외
"""
import csv, json, os, sys, time, urllib.parse, urllib.request

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


def num(x):
    try:
        return float(str(x).replace(',', '').strip())
    except Exception:
        return 0.0


def load_cache():
    if os.path.exists(CACHE):
        try:
            return json.load(open(CACHE, encoding='utf-8'))
        except Exception:
            pass
    return {}


def save_cache(c):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(c, open(CACHE, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))


def kakao_geocode(query):
    url = 'https://dapi.kakao.com/v2/local/search/address.json?' + \
          urllib.parse.urlencode({'query': query, 'size': 1})
    req = urllib.request.Request(url, headers={'Authorization': 'KakaoAK ' + KAKAO_KEY})
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            d = json.loads(r.read().decode('utf-8'))
        docs = d.get('documents') or []
        if not docs:
            return None
        return (round(float(docs[0]['y']), 6), round(float(docs[0]['x']), 6))
    except Exception as e:
        print(f'  [지오코딩 실패] {query} — {e}')
        return None


def build_geo(keys):
    cache = load_cache()
    todo = sorted(k for k in keys if k not in cache)
    if not KAKAO_KEY:
        print(f'! KAKAO_REST_KEY 없음 — 신규 지번 {len(todo)}건은 좌표 없이 진행합니다')
    else:
        if todo:
            print(f'지오코딩 신규 {len(todo)}건 (캐시 {len(cache)}건)')
        for i, k in enumerate(todo, 1):
            xy = kakao_geocode(SIGUNGU + ' ' + k)
            cache[k] = list(xy) if xy else None
            if i % 50 == 0:
                print(f'  {i}/{len(todo)}')
                save_cache(cache)
            time.sleep(0.05)
        if todo:
            save_cache(cache)
    return {k: v for k, v in cache.items() if v}


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
json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))

sz = os.path.getsize(OUT) / 1024
all_rows = sale + jeonse + wolse
with_j = [x for x in all_rows if x.get('j')]
hit = sum(1 for x in with_j if (x['u'] + ' ' + x['j']) in geo)
print(f'✓ {OUT} 생성 — 매매 {len(sale)} / 전세 {len(jeonse)} / 월세 {len(wolse)}건, {sz:.0f}KB, 기준 {latest_ym}')
print(f'  좌표: 지번 {len(geo_keys)}종 중 {len(geo)}종 확보 · 전체 거래 {hit}/{len(with_j)}건 매칭')
