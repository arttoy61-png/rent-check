"""
강서구 전체 실거래 수집 (별도 파일 - 기존 화곡동 시스템 안 건드림)
출력: data/molit_kangseo.csv
"""
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from config import MOLIT_API_KEY, DEAL_TYPES, MONTHS_BACK, USE_CACHE
from collectors.molit_api import fetch_multi, normalize_to_legacy

KANGSEO_LAWD = "11500"
REGION_MAP = {KANGSEO_LAWD: "강서구"}

def main():
    print("=" * 50)
    print("  강서구 전체 실거래 수집")
    print("=" * 50)
    print(f"거래유형: {DEAL_TYPES}")
    print(f"기간: 최근 {MONTHS_BACK}개월")
    print()

    df = fetch_multi(
        api_key=MOLIT_API_KEY,
        lawd_cd_list=[KANGSEO_LAWD],
        months_back=MONTHS_BACK,
        deal_types=DEAL_TYPES,
        use_cache=USE_CACHE,
        verbose=True,
    )

    if df is None or len(df) == 0:
        print("\n수집된 데이터 없음")
        sys.exit(1)

    print(f"\n강서구 전체 수집: {len(df):,}건")

    # 필터 없이 전체 정규화 (동별 umd_name 유지)
    normalized = normalize_to_legacy(df, REGION_MAP)

    out_path = Path("data/molit_kangseo.csv")
    out_path.parent.mkdir(exist_ok=True)
    normalized.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n저장 완료: {out_path} ({len(normalized):,}건)")

    print("\n=== 법정동별 분포 ===")
    for umd, cnt in normalized["umd_name"].value_counts().items():
        print(f"  {umd}: {cnt:,}건")

    print("\n=== 거래유형별 ===")
    for dt, cnt in normalized["deal_type"].value_counts().items():
        print(f"  {dt}: {cnt:,}건")

if __name__ == "__main__":
    main()
