"""
강서구 전체 실거래 수집 (별도 파일 - 기존 화곡동 시스템 안 건드림)
출력: data/molit_kangseo.csv

★ 2026.6.1 변경: 회전율·장기 분석용 24개월로 오버라이드
  - 매일 화곡동 수집(collect_live_data.py)은 config.py의 6개월 그대로 유지
  - 이 파일(강서구 전체)만 24개월 수집

★ 2026.8.12 변경: GitHub Actions 30분 타임아웃 대응
  - 환경변수 MONTHS_BACK 으로 수집 개월 조절 (기본 24)
    · 평일 자동실행: MONTHS_BACK=4  (최근 4개월만 → 호출 6분의 1)
    · 주 1회 전체:   MONTHS_BACK=24 (과거분 재검증)
  - 부분 수집이어도 기존 CSV와 병합해 저장 (과거 데이터 유실 방지)
"""
import os
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
# config의 MONTHS_BACK은 무시. 강서구 전체 분석용은 24개월.
from config import MOLIT_API_KEY, DEAL_TYPES, USE_CACHE
from collectors.molit_api import fetch_multi, normalize_to_legacy

KANGSEO_LAWD = "11500"
REGION_MAP = {KANGSEO_LAWD: "강서구"}
MONTHS_BACK = int(os.environ.get("MONTHS_BACK", "24"))  # 환경변수로 조절

def main():
    print("=" * 50)
    print("  강서구 전체 실거래 수집 (24개월)")
    print("=" * 50)
    print(f"거래유형: {DEAL_TYPES}")
    print(f"기간: 최근 {MONTHS_BACK}개월 (장기 분석용)")
    print()

    df = fetch_multi(
        api_key=MOLIT_API_KEY,
        lawd_cd_list=[KANGSEO_LAWD],
        months_back=MONTHS_BACK,
        deal_types=DEAL_TYPES,
        use_cache=USE_CACHE,
        verbose=True,
    )

    out_path = Path("data/molit_kangseo.csv")
    out_path.parent.mkdir(exist_ok=True)

    if df is None or len(df) == 0:
        print("\n수집된 데이터 없음 — 기존 CSV를 그대로 둡니다")
        if out_path.exists():
            print(f"  기존 파일 유지: {out_path}")
            sys.exit(0)      # 기존 파일이 있으면 실패 처리하지 않음
        sys.exit(1)

    print(f"\n강서구 전체 수집: {len(df):,}건")

    # 필터 없이 전체 정규화 (동별 umd_name 유지)
    normalized = normalize_to_legacy(df, REGION_MAP)

    # ── 기존 CSV와 병합 (부분 수집이어도 과거분 유실 방지) ──
    if out_path.exists():
        try:
            old = pd.read_csv(out_path, dtype=str)
            before = len(old)
            merged = pd.concat([old, normalized.astype(str)], ignore_index=True)
            key = ["deal_type", "deal_ym", "deal_day", "umd_name", "jibun",
                   "building_name", "area_m2", "floor"]
            key = [c for c in key if c in merged.columns]
            merged = merged.drop_duplicates(subset=key, keep="last")
            print(f"\n기존 {before:,}건 + 신규 {len(normalized):,}건 → 병합 {len(merged):,}건")
            normalized = merged
        except Exception as e:
            print(f"\n병합 실패({e}) — 신규 수집분으로 덮어씁니다")

    normalized.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n저장 완료: {out_path} ({len(normalized):,}건)")

    print("\n=== 법정동별 분포 ===")
    for umd, cnt in normalized["umd_name"].value_counts().items():
        print(f"  {umd}: {cnt:,}건")

    print("\n=== 거래유형별 ===")
    for dt, cnt in normalized["deal_type"].value_counts().items():
        print(f"  {dt}: {cnt:,}건")

    print("\n=== 월별 분포 (최근 24개월) ===")
    if "deal_ym" in normalized.columns:
        ym_counts = normalized["deal_ym"].value_counts().sort_index()
        for ym, cnt in ym_counts.items():
            print(f"  {ym}: {cnt:,}건")

if __name__ == "__main__":
    main()
