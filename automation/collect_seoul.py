# -*- coding: utf-8 -*-
"""서울 25개구 실거래 수집 (요일글·경매·비교용 — 위젯용 collect_kangseo와 별도)
출력: data/molit_trade_live.csv  (daily_content.py가 읽는 파일명)
캐시: cache/ 를 repo에 커밋해 유지 → 매일 최근 2개월만 재호출 (~300콜)
"""
import sys, os
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from config import MOLIT_API_KEY, DEAL_TYPES
from collectors.molit_api import fetch_multi, normalize_to_legacy

def main():
    regions = pd.read_csv(Path(__file__).parent / "regions.csv", dtype=str)
    lawd_list = regions["lawd_cd"].tolist()
    region_map = dict(zip(regions["lawd_cd"], regions["region_name"]))
    months = int(os.environ.get("MONTHS_BACK", "6"))

    print(f"서울 {len(lawd_list)}개구 × {len(DEAL_TYPES)}유형 × {months}개월 (캐시 사용)")
    df = fetch_multi(
        api_key=MOLIT_API_KEY,
        lawd_cd_list=lawd_list,
        months_back=months,
        deal_types=DEAL_TYPES,
        use_cache=True,   # ★ 캐시 필수 — cache/를 repo에 커밋해 러너 간 유지
        verbose=True,
    )
    if df is None or len(df) == 0:
        sys.exit("수집 데이터 없음")

    normalized = normalize_to_legacy(df, region_map)
    out = Path("data/molit_trade_live.csv")
    out.parent.mkdir(exist_ok=True)
    normalized.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"저장: {out} ({len(normalized):,}건)")
    print(normalized.groupby("region_name").size().sort_values(ascending=False).head(8) if "region_name" in normalized.columns else "")

if __name__ == "__main__":
    main()
