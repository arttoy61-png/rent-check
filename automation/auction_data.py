# -*- coding: utf-8 -*-
"""
auction_data.py — 경매 실거래 체크 도구용 JSON 가공
사용: python auction_data.py
입력: data/molit_trade_live.csv (collect_kangseo.bat 산출물)
출력: auction_data.json → GitHub rent-check 레포에 업로드
파이프라인: collect_kangseo.bat 실행 후 이 스크립트 → JSON을 CSV와 함께 매주 갱신
"""
import csv, json, os, sys

# ── CSV 자동 탐색: 스크립트를 어디 두든 알아서 찾음 ──
HERE = os.path.dirname(os.path.abspath(__file__))
# 인자로 CSV 경로 지정 가능 (GitHub Actions: python auction_data.py data/molit_kangseo.csv)
if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
    SRC = sys.argv[1]
    _CANDIDATES = []
else:
    _CANDIDATES = [
    os.path.join(HERE, '..', 'data', 'molit_trade_live.csv'),  # rent_blog\auction\ 에 둘 때
    os.path.join(HERE, 'data', 'molit_trade_live.csv'),
    os.path.join(HERE, 'molit_trade_live.csv'),
    os.path.join('data', 'molit_trade_live.csv'),              # rent_blog 루트에서 실행할 때
    'molit_trade_live.csv',
    os.path.join('data', 'molit_kangseo.csv'),
]
if not (len(sys.argv) > 1 and os.path.exists(sys.argv[1])):
    SRC = next((p for p in _CANDIDATES if os.path.exists(p)), None)
if SRC is None:
    print('[오류] molit_trade_live.csv를 찾지 못했습니다. collect_kangseo.bat을 먼저 실행하세요.')
    sys.exit(1)
print(f'CSV: {os.path.abspath(SRC)}')
OUT = os.path.join(HERE, 'auction_data.json')  # 출력은 스크립트 옆에

TYPE_MAP = {'연립다세대': 'v', '아파트': 'a', '오피스텔': 'o'}

def num(x):
    try:
        return float(str(x).replace(',', '').strip())
    except:
        return 0.0

rows = list(csv.DictReader(open(SRC, encoding='utf-8-sig')))

sale, jeonse, wolse = [], [], []
latest_ym = max(r['deal_ym'] for r in rows if r.get('deal_ym'))

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
    if trade == '매매':
        p = num(r.get('deal_amount'))
        if p > 0 and a > 0:
            sale.append({'u': umd, 't': t, 'a': a, 'p': int(p), 'ym': ym, 'd': day, 'y': y,
                         'b': r.get('building_name', '')[:20], 'f': r.get('floor', '')})
    elif trade == '전월세':
        dep = num(r.get('deposit'))
        mon = num(r.get('monthly_rent'))
        if mon == 0 and dep > 0 and a > 0:
            jeonse.append({'u': umd, 't': t, 'a': a, 'p': int(dep), 'ym': ym})
        elif mon > 0 and a > 0:
            wolse.append({'u': umd, 't': t, 'a': a, 'dp': int(dep), 'm': int(mon), 'ym': ym})

out = {
    'updated': latest_ym,
    'regions': sorted(set(x['u'] for x in sale)),
    'sale': sale,
    'jeonse': jeonse,
    'wolse': wolse,
}
json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
sz = os.path.getsize(OUT) / 1024
print(f'✓ {OUT} 생성 — 매매 {len(sale)} / 전세 {len(jeonse)} / 월세 {len(wolse)}건, {sz:.0f}KB, 기준 {latest_ym}')
print('→ GitHub rent-check 레포에 auction_data.json 업로드 (매주 CSV 갱신 때 함께)')
