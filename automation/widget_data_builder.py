"""
위젯용 데이터 빌더 v2.0 (보강)

[보강 내역 - 2026.05.16]
  - 익명화 제거 → 실명 표시 (anon_name 제거, building_name 사용)
  - 거래시기 정확 표기 ("2025년 하반기" → "2026.04.10")
  - 단지명 자동완성용 인덱스 추가 (building_names 배열, 가나다순)

aggregated_rent.json 의 raw 거래 데이터로 widget_data.json 생성.
화곡동 임차 검증 도구(rent-check) 전용 데이터.

⚠️ 본 위젯은 임차 검증 전용 (전세·월세 한정)
   매매 데이터는 포함하지 않음.
"""
import csv
import json
import re
from pathlib import Path
from collections import defaultdict
from datetime import datetime

# 유형 정규화 (임차 거래만 처리)
TYPE_MAP = {
    "오피스텔_전월세": "오피스텔",
    "아파트_전월세": "아파트",
    "연립다세대_전월세": "빌라",
}


def format_deal_period(deal_ym: str, deal_day: str = "") -> str:
    """202604 + 10 → '2026.04.10' (정확한 거래 시점)"""
    if not deal_ym or len(str(deal_ym)) < 6:
        return "-"
    ym = str(deal_ym)
    year = ym[:4]
    month = ym[4:6]
    if deal_day and str(deal_day).strip():
        day = str(deal_day).strip().zfill(2)
        return f"{year}.{month}.{day}"
    return f"{year}.{month}"


def normalize_building_name(name: str) -> str:
    """단지명 정규화 (동 번호·괄호 등 제거)"""
    if not name:
        return "(미상)"
    s = str(name).strip()
    # 동 번호 제거 (예: "우장산아이파크 101동" → "우장산아이파크")
    s = re.sub(r'\s*\d{3}동\s*$', '', s)
    # 괄호 안 정보 제거 (예: "블루힐(846-14)" → "블루힐")
    s = re.sub(r'\s*\(\d{3,4}-?\d*\)\s*', '', s)
    return s.strip() or "(미상)"


def build_widget_data(csv_path: str, region: str = "화곡동") -> dict:
    """위젯이 읽을 실명화된 임차 데이터 JSON 생성"""
    rows = []
    with open(csv_path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("region_name") != region:
                continue
            # 임차 거래만 처리 (매매 제외)
            if r.get("deal_type") not in TYPE_MAP:
                continue
            try:
                r["area_m2"] = float(r["area_m2"]) if r.get("area_m2") else 0
                r["deposit"] = int(r["deposit"]) if r.get("deposit") else 0
                r["monthly_rent"] = int(r["monthly_rent"]) if r.get("monthly_rent") else 0
            except (ValueError, TypeError):
                continue
            if r["area_m2"] <= 0:
                continue
            rows.append(r)

    # 거래 데이터 정리
    deals = []
    building_set = set()  # 단지명 중복 제거용
    building_type_map = {}  # 단지명 → 건물유형 매핑 (자동완성용)
    
    for r in rows:
        raw_type = r.get("deal_type", "")
        type_kr = TYPE_MAP.get(raw_type, "기타")
        building = normalize_building_name(r.get("building_name", "(미상)"))

        pyung = round(r["area_m2"] / 3.3058, 1)
        deal_period = format_deal_period(r.get("deal_ym", ""), r.get("deal_day", ""))

        deals.append({
            "building_name": building,        # ← 실명 (익명화 제거)
            "type": type_kr,
            "area_m2": r["area_m2"],
            "pyung": pyung,
            "deposit": r["deposit"],
            "monthly_rent": r["monthly_rent"],
            "deal_period": deal_period,        # ← 정확한 날짜
            "floor": r.get("floor", ""),
            "build_year": r.get("build_year", ""),
            "is_jeonse": r["monthly_rent"] == 0 and r["deposit"] > 0,
        })
        
        # 자동완성용 단지명 인덱스
        if building != "(미상)":
            building_set.add(building)
            if building not in building_type_map:
                building_type_map[building] = type_kr

    # 평형 구간별 인덱싱 (위젯 빠른 검색용)
    by_pyung_range = defaultdict(list)
    for d in deals:
        p = d["pyung"]
        if p < 10:
            key = "10평 이하"
        elif p < 15:
            key = "10~15평"
        elif p < 20:
            key = "15~20평"
        elif p < 25:
            key = "20~25평"
        else:
            key = "25평 이상"
        by_pyung_range[key].append(d)

    # 단지명 자동완성 인덱스 (가나다순 + 건물유형 포함)
    building_index = sorted([
        {"name": name, "type": building_type_map[name]}
        for name in building_set
    ], key=lambda x: x["name"])

    return {
        "region": region,
        "generated_at": datetime.now().isoformat(),
        "total_deals": len(deals),
        "deals": deals,
        "by_pyung_range": dict(by_pyung_range),
        "building_index": building_index,
        "data_note": "본 데이터는 국토교통부 실거래가 공개자료 기반으로, 임차(전세·월세) 거래만 포함합니다. 매매 데이터는 포함되지 않습니다.",
        # 법정 전월세전환율 = min(기준금리+2%, 10%). 기준금리 변경 시 bok_base_rate만 수정.
        "conversion_basis": {
            "bok_base_rate": 0.025,   # 한국은행 기준금리 (2026.5 현재 2.5%)
            "legal_add_rate": 0.02,   # 법정 가산 2%p
            "legal_max_rate": 0.10,   # 법정 상한 10%
            "applied_rate": round(min(0.025 + 0.02, 0.10), 4),  # = 0.045
        },
    }


def save(data: dict, out_path: str = "outputs/widget/widget_data.json"):
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"저장: {out_path}")
    print(f"  - 총 거래: {data['total_deals']}건 (임차 전용)")
    print(f"  - 단지 수: {len(data['building_index'])}개 (자동완성 인덱스 포함)")
    
    # 데이터 확인 (앞 10건)
    print(f"\n=== 거래 데이터 예시 (앞 10건) ===")
    for d in data["deals"][:10]:
        rent_str = (f"보증 {d['deposit']:,}/월 {d['monthly_rent']}만" 
                   if not d["is_jeonse"] else f"전세 {d['deposit']:,}만")
        bldg = d['building_name'][:20]
        print(f"  [{d['type']:5s}] {bldg:22s} {d['pyung']:>5.1f}평  {rent_str:30s}  ({d['deal_period']})")
    
    # 자동완성 인덱스 예시
    print(f"\n=== 단지 인덱스 예시 (앞 10개, 가나다순) ===")
    for b in data["building_index"][:10]:
        print(f"  [{b['type']:5s}] {b['name']}")


if __name__ == "__main__":
    import sys
    # 실데이터 우선, 없으면 샘플 사용
    if len(sys.argv) > 1:
        csv_path = sys.argv[1]
    elif Path("data/molit_trade_live.csv").exists():
        csv_path = "data/molit_trade_live.csv"
        print("✓ 실데이터 사용: data/molit_trade_live.csv")
    else:
        csv_path = "data/sample_rent_hwagok.csv"
        print("⚠ 샘플 데이터 사용 (실데이터 없음): data/sample_rent_hwagok.csv")
    region = sys.argv[2] if len(sys.argv) > 2 else "화곡동"
    data = build_widget_data(csv_path, region)
    save(data)
