"""
강서구 전체 실거래 수집 (별도 파일 - 기존 화곡동 시스템 안 건드림)
출력: data/molit_kangseo.csv

★ 2026.6.1 변경: 회전율·장기 분석용 24개월로 오버라이드
  - 매일 화곡동 수집(collect_live_data.py)은 config.py의 6개월 그대로 유지
  - 이 파일(강서구 전체)만 24개월 수집

★ 2026.8.15 변경: 캐시 활성화 후 기본 수집 범위를 24개월로 통일
  - 환경변수 MONTHS_BACK 수동 지정은 유지 (기본 24)
  - 부분 수집이어도 기존 CSV와 병합해 저장 (과거 데이터 유실 방지)

★ 2026.8.19 변경: 신규 신고 누락 방지
  - 전체 수집 성공 시 24개월 API 스냅샷으로 통째 교체
  - 부분 수집 시 현재월·전월 중 6개 거래유형이 모두 성공한 월만 통째 교체
  - 날짜/건물/면적/층만으로 중복 제거하지 않음
    (서로 다른 실제 거래를 같은 거래로 오인해 삭제하던 문제 수정)
"""
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from config import MOLIT_API_KEY, DEAL_TYPES, USE_CACHE
from collectors.molit_api import fetch_multi, normalize_to_legacy

KANGSEO_LAWD = "11500"
REGION_MAP = {KANGSEO_LAWD: "강서구"}
MONTHS_BACK = int(os.environ.get("MONTHS_BACK", "24"))


def _recent_months(count=2):
    now = datetime.today()
    y, m = now.year, now.month
    result = []
    for _ in range(count):
        result.append(f"{y:04d}{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return result


def main():
    print("=" * 50)
    print(f"  강서구 전체 실거래 수집 (최근 {MONTHS_BACK}개월)")
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

    # fetch_multi가 남긴 수집 상태를 정규화 전에 보존
    collection_complete = bool(df.attrs.get("complete", False))
    successful_keys = {
        tuple(x) for x in df.attrs.get("successful_keys", [])
    }
    requested_months = set(df.attrs.get("requested_months", []))

    out_path = Path("data/molit_kangseo.csv")
    out_path.parent.mkdir(exist_ok=True)

    if df is None or len(df) == 0:
        print("\n수집된 데이터 없음 — 기존 CSV를 그대로 둡니다")
        if out_path.exists():
            print(f"  기존 파일 유지: {out_path}")
            sys.exit(0)
        sys.exit(1)

    print(f"\n강서구 전체 수집: {len(df):,}건")

    normalized = normalize_to_legacy(df, REGION_MAP)

    if out_path.exists():
        try:
            old = pd.read_csv(out_path, dtype=str)
            before = len(old)

            old["deal_ym"] = old["deal_ym"].astype(str)
            normalized["deal_ym"] = normalized["deal_ym"].astype(str)

            if collection_complete:
                # 전체 호출 성공 시 API 결과가 최신 스냅샷이므로 그대로 교체.
                # 과거처럼 날짜/건물/면적/층 기준 중복제거를 하지 않는다.
                fresh = normalized

                # 비정상적으로 데이터가 크게 줄어드는 경우만 안전장치.
                if before > 0 and len(fresh) < before * 0.75:
                    raise RuntimeError(
                        f"전체 수집 성공이지만 행수가 비정상 감소: "
                        f"기존 {before:,}건 → 최신 {len(fresh):,}건"
                    )

                fresh_months = set(
                    fresh["deal_ym"].dropna().astype(str).unique()
                )
                if requested_months and not requested_months.issubset(fresh_months):
                    missing = sorted(requested_months - fresh_months)
                    raise RuntimeError(
                        f"요청한 월 데이터 일부가 최신 수집 결과에 없습니다: {missing}"
                    )

                normalized = fresh
                print(
                    f"\n전체 수집 성공 — 기존 {before:,}건을 "
                    f"최신 API 스냅샷 {len(normalized):,}건으로 교체"
                )

            else:
                # 부분 수집일 때는 최근 2개월 중 6개 거래유형이 모두 성공한 월만 교체.
                # 나머지 월은 기존 CSV를 그대로 보존한다.
                replace_months = []
                for ym in _recent_months(2):
                    if all(
                        (KANGSEO_LAWD, ym, dt) in successful_keys
                        for dt in DEAL_TYPES
                    ):
                        old_n = int((old["deal_ym"] == ym).sum())
                        fresh_n = int((normalized["deal_ym"] == ym).sum())

                        # 기존에는 거래가 있었는데 최신 결과가 0이면 안전상 교체하지 않는다.
                        if old_n > 0 and fresh_n == 0:
                            print(
                                f"⚠ {ym} 최신 결과 0건 — 기존 {old_n:,}건 유지"
                            )
                            continue
                        replace_months.append(ym)

                if replace_months:
                    old_keep = old[~old["deal_ym"].isin(replace_months)]
                    fresh_recent = normalized[
                        normalized["deal_ym"].isin(replace_months)
                    ]
                    normalized = pd.concat(
                        [old_keep, fresh_recent],
                        ignore_index=True,
                        sort=False,
                    )
                    print(
                        f"\n부분 수집 — 최근월 {replace_months}만 최신값으로 교체: "
                        f"기존 {before:,}건 → {len(normalized):,}건"
                    )
                else:
                    print(
                        "\n부분 수집이며 안전하게 교체 가능한 최근월이 없습니다 "
                        "— 기존 CSV 유지"
                    )
                    normalized = old

        except Exception as e:
            print(f"\n🚨 병합 안전장치 작동: {e}")
            print(f"  기존 CSV 유지: {out_path}")
            sys.exit(1)

    normalized.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n저장 완료: {out_path} ({len(normalized):,}건)")

    print("\n=== 법정동별 분포 ===")
    for umd, cnt in normalized["umd_name"].value_counts().items():
        print(f"  {umd}: {cnt:,}건")

    print("\n=== 거래유형별 ===")
    for dt, cnt in normalized["deal_type"].value_counts().items():
        print(f"  {dt}: {cnt:,}건")

    print(f"\n=== 월별 분포 (최근 {MONTHS_BACK}개월) ===")
    if "deal_ym" in normalized.columns:
        ym_counts = normalized["deal_ym"].value_counts().sort_index()
        for ym, cnt in ym_counts.items():
            print(f"  {ym}: {cnt:,}건")


if __name__ == "__main__":
    main()
