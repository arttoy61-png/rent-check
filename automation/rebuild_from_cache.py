#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
molit_kangseo.csv 원장 복구 — API 재호출 없이 캐시에서 재조립.

배경:
  2026.08.12 평일 4개월 수집 전환 당시, collect_kangseo.py 의 병합 로직이
  automation/data/molit_kangseo.csv 를 읽는데 러너에 그 파일이 없어서
  (커밋되는 원장은 레포 루트에만 존재) 신규 4개월분이 그대로 덮어써졌다.
  "Seed previous CSV" 스텝이 08.13 에 추가되어 추가 유실은 없지만
  이미 사라진 과거분은 돌아오지 않는다.

  다행히 automation/data/cache/ 에 월별 API 응답 캐시가 남아 있다.
  이 스크립트는 그 캐시만 읽어 원장을 되살린다. API 는 호출하지 않는다.

실행:
  cd automation && python rebuild_from_cache.py
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from collectors.molit_api import normalize_to_legacy  # noqa: E402

CACHE_DIR = Path("data/cache")
CSV_PATH = Path("data/molit_kangseo.csv")
REGION_MAP = {"11500": "강서구"}

# collect_kangseo.py 와 동일해야 한다. 바꾸지 말 것.
DEDUP_KEY = [
    "deal_type", "deal_ym", "deal_day", "umd_name",
    "jibun", "building_name", "area_m2", "floor",
]


def die(msg: str) -> None:
    print(f"\n중단: {msg}")
    print("원장은 건드리지 않았습니다.")
    sys.exit(1)


def summarize(df: pd.DataFrame, title: str) -> None:
    print(f"\n=== {title} ===")
    print(f"  총 {len(df):,}행 · 범위 {df['deal_ym'].min()} ~ {df['deal_ym'].max()}")
    print("\n  [월별]")
    for ym, n in df["deal_ym"].value_counts().sort_index().items():
        print(f"    {ym}  {n:>6,}")
    print("\n  [거래유형별]")
    for dt, n in df["deal_type"].value_counts().items():
        print(f"    {dt:<16} {n:>6,}")


def main() -> None:
    print("=" * 56)
    print("  molit_kangseo.csv 원장 복구 (캐시 재조립 · API 호출 없음)")
    print("=" * 56)

    if not CACHE_DIR.exists():
        die(f"캐시 폴더가 없습니다: {CACHE_DIR.resolve()}")

    # ── 1. 기존 원장 로드 + 백업 ────────────────────────────────
    if not CSV_PATH.exists():
        die(f"기존 원장이 없습니다: {CSV_PATH.resolve()}\n"
            "  (루트 molit_kangseo.csv 를 data/ 로 복사한 뒤 실행하세요)")

    old = pd.read_csv(CSV_PATH, dtype=str)
    old_rows = len(old)
    old_min = old["deal_ym"].min()
    print(f"\n기존 원장: {old_rows:,}행 · 범위 {old_min} ~ {old['deal_ym'].max()}")

    backup = CSV_PATH.parent / f"molit_kangseo_backup_{datetime.now():%Y%m%d}.csv"
    shutil.copy2(CSV_PATH, backup)
    print(f"백업 생성: {backup.name}")

    # ── 2. 캐시 읽기 ──────────────────────────────────────────
    # 캐시 CSV 는 deal_type · deal_ym · lawd_cd 를 이미 컬럼으로 갖고 있어
    # 파일명 파싱이 필요 없다. 원시 응답 스키마 그대로 읽어 concat 한다.
    files = sorted(CACHE_DIR.glob("v3_*.csv"))
    if not files:
        die(f"캐시 파일이 없습니다: {CACHE_DIR.resolve()}")

    print(f"\n캐시 파일 {len(files)}개 읽는 중...")
    frames, skipped = [], []
    for f in files:
        try:
            df = pd.read_csv(f, dtype=str)
            if len(df) == 0:
                skipped.append(f"{f.name} (빈 파일)")
                continue
            frames.append(df)
        except Exception as e:
            skipped.append(f"{f.name} ({e})")

    if not frames:
        die("읽을 수 있는 캐시가 없습니다")
    for s in skipped:
        print(f"  건너뜀: {s}")

    raw = pd.concat(frames, ignore_index=True)
    print(f"  캐시 원본 합계: {len(raw):,}행")

    missing = [c for c in ("deal_type", "deal_ym", "umd_name") if c not in raw.columns]
    if missing:
        die(f"캐시 스키마에 필수 컬럼이 없습니다: {missing}")

    # ── 3. 정규화 (기존 함수 재사용 — 직접 매핑 금지) ──────────
    if "lawd_cd" not in raw.columns:
        raw["lawd_cd"] = "11500"
    raw["lawd_cd"] = raw["lawd_cd"].astype(str).str.zfill(5)

    norm = normalize_to_legacy(raw, REGION_MAP)
    print(f"  정규화 완료: {len(norm):,}행 · {len(norm.columns)}컬럼")

    # ── 4. 병합 ───────────────────────────────────────────────
    merged = pd.concat([old, norm.astype(str)], ignore_index=True)
    key = [c for c in DEDUP_KEY if c in merged.columns]
    merged = merged.drop_duplicates(subset=key, keep="last")
    print(f"\n병합: 기존 {old_rows:,} + 캐시 {len(norm):,} → 중복 제거 후 {len(merged):,}행")

    # ── 5. 안전장치 ───────────────────────────────────────────
    if len(merged) <= old_rows:
        die(f"병합 결과가 기존보다 늘지 않았습니다 ({old_rows:,} → {len(merged):,})")

    new_min = merged["deal_ym"].min()
    if new_min > old_min:
        die(f"과거분이 오히려 줄었습니다 (최소 {old_min} → {new_min})")

    # ── 6. 저장 ───────────────────────────────────────────────
    merged = merged.sort_values(["deal_ym", "deal_type"], kind="stable")
    merged.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
    summarize(merged, "복구 완료")
    print(f"\n저장: {CSV_PATH}")
    print(f"백업: {backup}")
    print(f"증가: {old_rows:,} → {len(merged):,}행 (+{len(merged) - old_rows:,})")


if __name__ == "__main__":
    main()
