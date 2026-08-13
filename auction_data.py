# -*- coding: utf-8 -*-
"""
auction_data.py — 경매 실거래 체크 도구용 JSON 가공
사용: python auction_data.py [csv경로]
입력: data/molit_kangseo.csv (또는 molit_trade_live.csv)
출력: auction_data.json → GitHub rent-check 레포에 업로드

2026.08.13 추가 — 지번·좌표(지오코딩)
  · 매매 레코드에 지번(j) 추가
  · 지번 → 좌표를 카카오 로컬 API로 변환해 최상위 geo 테이블에 저장
  · data/geocode_cache.json에 캐시 — 새 지번만 호출한다
  · KAKAO_REST_KEY 환경변수가 없으면 좌표 없이 그대로 생성 (도구는 동 단위로 동작)
"""
import csv, json, os, sys, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

# ── CSV 자동 탐색 ──
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
SIGUNGU = '서울 강서구'          # 확장 시 CSV의 region_name을 쓰도록 바꿀 것
KAKAO_KEY = os.environ.get('KAKAO_REST_KEY', '').strip()


def num(x):
    try:
        return float(str(x).replace(',', '').strip())
    except Exception:
        return 0.0


# ══ 지오코딩 ══
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
    """주소 → (lat, lng). 실패하면 None."""
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
    """keys = {'화곡동 1073-11', ...} → {키: [lat, lng]}"""
    cache = load_cache()
    todo = [k for k in keys if k not in cache]
    if not KAKAO_KEY:
        print(f'! KAKAO_REST_KEY 없음 — 신규 지번 {len(todo)}건은 좌표 없이 진행합니다')
    else:
        if todo:
            print(f'지오코딩 신규 {len(todo)}건 (캐시 {len(cache)}건)')
        for i, k in enumerate(todo, 1):
            xy = kakao_geocode(SIGUNGU + ' ' + k)
            cache[k] = list(xy) if xy else None      # 실패도 기록해 재시도 폭주 방지
            if i % 50 == 0:
                print(f'  {i}/{len(todo)}')
                save_cache(cache)
            time.sleep(0.05)                          # 초당 20건 이하
        if todo:
            save_cache(cache)
    return {k: v for k, v in cache.items() if v}


# ══ 본 처리 ══
rows = list(csv.DictReader(open(SRC, encoding='utf-8-sig')))
sale, jeonse, wolse = [], [], []
latest_ym = max(r['deal_ym'] for r in rows if r.get('deal_ym'))
geo_keys = set()

for r in rows:
    dt = r.get('deal_type', '')
    if '_' not in dt:
        continue
    btype, trade = dt.rsplit('_', 1)
    t = TYPE_MAP.get(btype)
    if not t:
        continue
    umd = r.get('umd_name', '').strip()
    a = num(r.get('area_m2'))
    ym = r.get('deal_ym', '')
    day = r.get('deal_day', '')
    y = r.get('build_year', '')
    jibun = (r.get('jibun') or '').strip()

    if trade == '매매':
        p = num(r.get('deal_amount'))
        if p > 0 and a > 0:
            rec = {'u': umd, 't': t, 'a': a, 'p': int(p), 'ym': ym, 'd': day, 'y': y,
                   'b': r.get('building_name', '')[:20], 'f': r.get('floor', '')}
            # 직거래(중개사 없음) 표시 — 저가면 특수관계 거래일 수 있어 비교군에서 걸러낼 근거가 된다
            if (r.get('dealing_gbn') or '').strip() == '직거래':
                rec['dg'] = 1
            if jibun:
                rec['j'] = jibun
                geo_keys.add(umd + ' ' + jibun)
            sale.append(rec)
    elif trade == '전월세':
        dep = num(r.get('deposit'))
        mon = num(r.get('monthly_rent'))
        if mon == 0 and dep > 0 and a > 0:
            jeonse.append({'u': umd, 't': t, 'a': a, 'p': int(dep), 'ym': ym})
        elif mon > 0 and a > 0:
            wolse.append({'u': umd, 't': t, 'a': a, 'dp': int(dep), 'm': int(mon), 'ym': ym})

geo = build_geo(geo_keys)

out = {
    'updated': latest_ym,
    'regions': sorted(set(x['u'] for x in sale)),
    'geo': geo,                      # {"화곡동 1073-11": [37.540123, 126.845678]}
    'sale': sale,
    'jeonse': jeonse,
    'wolse': wolse,
}
json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
sz = os.path.getsize(OUT) / 1024
hit = sum(1 for x in sale if x.get('j') and (x['u'] + ' ' + x['j']) in geo)
print(f'✓ {OUT} 생성 — 매매 {len(sale)} / 전세 {len(jeonse)} / 월세 {len(wolse)}건, {sz:.0f}KB, 기준 {latest_ym}')
dg = sum(1 for x in sale if x.get('dg'))
print(f'  좌표: 지번 {len(geo_keys)}종 중 {len(geo)}종 확보 · 매매 레코드 {hit}/{len(sale)}건 매칭')
print(f'  직거래 표시: {dg}건')
