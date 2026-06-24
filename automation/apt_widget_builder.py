"""
화곡 대표단지 시세 위젯 데이터 빌더
hwagok_map_widget.html 이 fetch 하는 hwagok_apt_data.json 생성.

국토부 실거래 CSV(아파트 매매·전월세)에서 12개 대표단지를 '지번'으로 매칭해,
평형별로 매매·전세·월세 최신 거래와 매매중위·전용평당가를 집계한다.

⚠️ rent-check 의 기존 widget_data_builder.py (임차 검증 전용, 매매 제외)와는 별개.
   이쪽은 '아파트 매매 포함' 단지 시세 위젯 전용 빌더.
"""
import csv, json
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from statistics import median

PYEONG = 3.3058
FEW = 7   # 매매+전세+월세 합이 이 미만이면 '표본 적음'

# 12개 대표단지. jibun = CSV 매칭키. units/lat/lng/year 는 CSV에 없어 확정값 고정.
COMPLEXES = [
    {"name":"강서힐스테이트",          "jibun":"1165",   "units":2603, "year":2015, "lat":37.5470, "lng":126.8396},
    {"name":"우장산아이파크이편한세상", "jibun":"1159",   "units":2517, "year":2008, "lat":37.5494, "lng":126.8388},
    {"name":"화곡푸르지오",            "jibun":"1091",   "units":2176, "year":2002, "lat":37.5430, "lng":126.8318},
    {"name":"우장산롯데캐슬",          "jibun":"1145",   "units":1164, "year":2003, "lat":37.5556, "lng":126.8485},
    {"name":"초록",                  "jibun":"1139",   "units":625,  "year":1998, "lat":37.5461, "lng":126.8339},
    {"name":"우장산숲아이파크",        "jibun":"1173",   "units":576,  "year":2022, "lat":37.5451, "lng":126.8390},
    {"name":"중앙화곡하이츠",          "jibun":"351-89", "units":473,  "year":1988, "lat":37.5357, "lng":126.8437},
    {"name":"대림e편한세상",          "jibun":"361-1",  "units":416,  "year":1992, "lat":37.5337, "lng":126.8372},
    {"name":"우장산롯데",             "jibun":"1148",   "units":206,  "year":2004, "lat":37.5535, "lng":126.8470},
    {"name":"우장산SK뷰",            "jibun":"1158",   "units":203,  "year":2006, "lat":37.5481, "lng":126.8430},
    {"name":"희훈리치파크",           "jibun":"1011-6", "units":None, "year":2002, "lat":37.5458, "lng":126.8345},
    {"name":"강서금호어울림퍼스티어",  "jibun":"980-19", "units":523,  "year":2023, "lat":37.5507, "lng":126.8511,
     "area_fallback":"69~83㎡", "note":"2023년 입주·전매제한 기간이라 아직 실거래가 없습니다."},
]

def fmt(ym, day):
    ym = int(ym); y, m = ym//100, ym%100
    try: d = int(day)
    except (ValueError, TypeError): return f"{y}.{m:02d}"
    return f"{y}.{m:02d}.{d:02d}"

def eok(man):  # 만원 -> 억(소수 2자리)
    return round(float(man)/10000, 2)

def load(csv_path, region="화곡동"):
    rows=[]
    with open(csv_path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("region_name")!=region: continue
            if not r.get("deal_type","").startswith("아파트_"): continue
            try:
                r["area_m2"]=float(r["area_m2"])
                r["deal_amount"]=float(r["deal_amount"]) if r.get("deal_amount") else 0
                r["deposit"]=float(r["deposit"]) if r.get("deposit") else 0
                r["monthly_rent"]=float(r["monthly_rent"]) if r.get("monthly_rent") else 0
                r["floor"]=int(r["floor"]) if r.get("floor") else 0
            except (ValueError, TypeError): continue
            rows.append(r)
    return rows

def recent(lst, n=6):
    return sorted(lst, key=lambda x:x["_sort"], reverse=True)[:n]
def strip(arr):
    return [{k:v for k,v in d.items() if k!="_sort"} for d in recent(arr)]

def build(csv_path, region="화곡동"):
    rows = load(csv_path, region)
    by_jibun = defaultdict(list)
    for r in rows:
        by_jibun[str(r.get("jibun",""))].append(r)

    complexes=[]; detail={}
    for c in COMPLEXES:
        recs = by_jibun.get(c["jibun"], [])
        groups = defaultdict(lambda:{"sale":[],"je":[],"wo":[],"area":[]})
        for r in recs:
            dt=r["deal_type"]; m2=r["area_m2"]; g=groups[int(m2)]
            g["area"].append(m2)
            sortkey=(int(r["deal_ym"]), int(r.get("deal_day") or 0))
            base={"date":fmt(r["deal_ym"], r.get("deal_day")), "m2":round(m2,2),
                  "fl":r["floor"], "_sort":sortkey}
            if dt.endswith("_매매"):
                g["sale"].append({**base, "amt":eok(r["deal_amount"])})
            elif r["monthly_rent"]==0:
                g["je"].append({**base, "dep":eok(r["deposit"])})
            else:
                g["wo"].append({**base, "dep":eok(r["deposit"]), "rent":int(r["monthly_rent"])})

        areas=[]; tS=tJ=tW=0
        for key in sorted(groups):
            g=groups[key]; ns,nj,nw=len(g["sale"]),len(g["je"]),len(g["wo"])
            tS+=ns; tJ+=nj; tW+=nw
            sale_amts=[s["amt"] for s in g["sale"]]
            mid=round(median(sale_amts),2) if sale_amts else None
            rep=median(g["area"]) if g["area"] else key
            ppy=round(mid/(rep/PYEONG),2) if mid else None
            areas.append({"m2":key,"ppy":ppy,"mid":mid,"nSale":ns,"nJe":nj,"nWo":nw,
                          "sale":strip(g["sale"]),"jeonse":strip(g["je"]),"wolse":strip(g["wo"])})

        comp={"name":c["name"],"year":c["year"],"units":c["units"],
              "lat":c["lat"],"lng":c["lng"],
              "nSaleAll":tS,"nJeAll":tJ,"nWoAll":tW,"deals":tS}
        sale_areas=[a for a in areas if a["nSale"]>0]
        if sale_areas:
            main=max(sale_areas, key=lambda a:a["nSale"])
            comp["area"]=f'{main["m2"]}㎡'; comp["price"]=main["mid"]; comp["pyeong"]=main["ppy"]
        else:
            comp["area"]=c.get("area_fallback","-"); comp["price"]=None; comp["pyeong"]=None
        comp["few"]=(tS+tJ+tW)<FEW
        if c.get("note"): comp["note"]=c["note"]

        d={"areas":areas}
        if c.get("note"): d["note"]=c["note"]
        complexes.append(comp); detail[c["name"]]=d

    return {"generated_at":datetime.now().isoformat(),"region":region,
            "complexes":complexes,"detail":detail}

def save(data, out="outputs/widget/hwagok_apt_data.json"):
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out,"w",encoding="utf-8") as f:
        json.dump(data,f,ensure_ascii=False,separators=(",",":"))
    print(f"저장: {out}  ·  단지 {len(data['complexes'])}개  ·  {data['generated_at'][:19]}")
    for c in data["complexes"]:
        p=f'{c["price"]}억' if c["price"] is not None else '실거래전'
        print(f'  {c["name"]:17s} {str(c.get("area","")):8s} 매매중위 {p:9s} 평당 {str(c["pyeong"]):5s} 거래 {c["deals"]}')

if __name__=="__main__":
    import sys
    # 인자로 CSV 경로를 주면 그걸 사용 (Actions: data/molit_kangseo.csv 화곡동)
    if len(sys.argv)>1 and not sys.argv[1].startswith("-"):
        csv_path=sys.argv[1]
    elif Path("data/molit_kangseo.csv").exists():
        csv_path="data/molit_kangseo.csv"; print("✓ 자동수집 CSV 사용: data/molit_kangseo.csv")
    elif Path("data/molit_trade_live.csv").exists():
        csv_path="data/molit_trade_live.csv"; print("✓ 실데이터 사용: data/molit_trade_live.csv")
    else:
        csv_path="molit_trade_live.csv"; print(f"CSV: {csv_path}")
    region = sys.argv[2] if len(sys.argv)>2 else "화곡동"
    save(build(csv_path, region))
