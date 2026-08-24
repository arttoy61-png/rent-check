"""서울 25개 구 실거래 테스트 수집기.

기존 강서구 CSV/홈/도구는 수정하지 않고 repo/seoul_data 아래에만 저장한다.
첫 테스트 기본 범위는 최근 3개월이며, 구별 수집이 완전히 성공한 경우에만
해당 구 CSV를 새로 저장한다.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from config import MOLIT_API_KEY, DEAL_TYPES, USE_CACHE
from collectors.molit_api import fetch_multi, normalize_to_legacy

MONTHS_BACK = int(os.environ.get("SEOUL_MONTHS_BACK", "3"))
OUT_ROOT = ROOT / "seoul_data"
RAW_DIR = OUT_ROOT / "raw"
SUMMARY_PATH = OUT_ROOT / "seoul_summary.json"
STATUS_PATH = OUT_ROOT / "collection_status.json"

SEOUL_GU = {
    "11110": ("종로구", "jongno"), "11140": ("중구", "jung"),
    "11170": ("용산구", "yongsan"), "11200": ("성동구", "seongdong"),
    "11215": ("광진구", "gwangjin"), "11230": ("동대문구", "dongdaemun"),
    "11260": ("중랑구", "jungnang"), "11290": ("성북구", "seongbuk"),
    "11305": ("강북구", "gangbuk"), "11320": ("도봉구", "dobong"),
    "11350": ("노원구", "nowon"), "11380": ("은평구", "eunpyeong"),
    "11410": ("서대문구", "seodaemun"), "11440": ("마포구", "mapo"),
    "11470": ("양천구", "yangcheon"), "11500": ("강서구", "gangseo"),
    "11530": ("구로구", "guro"), "11545": ("금천구", "geumcheon"),
    "11560": ("영등포구", "yeongdeungpo"), "11590": ("동작구", "dongjak"),
    "11620": ("관악구", "gwanak"), "11650": ("서초구", "seocho"),
    "11680": ("강남구", "gangnam"), "11710": ("송파구", "songpa"),
    "11740": ("강동구", "gangdong"),
}


def to_num(series):
    return pd.to_numeric(series.astype(str).str.replace(",", "", regex=False), errors="coerce")


def build_summary(files):
    result = []
    for path in files:
        try:
            df = pd.read_csv(path, dtype=str)
        except Exception as exc:
            print(f"요약 제외 {path.name}: {exc}")
            continue
        if df.empty:
            continue
        for (gu, lawd, ym, deal_type), part in df.groupby(
            ["gu_name", "lawd_cd", "deal_ym", "deal_type"], dropna=False
        ):
            item = {
                "gu": str(gu), "lawd_cd": str(lawd), "deal_ym": str(ym),
                "deal_type": str(deal_type), "count": int(len(part)),
            }
            if "전월세" in str(deal_type):
                rent = to_num(part["monthly_rent"]).fillna(0)
                dep = to_num(part["deposit"])
                item["jeonse_count"] = int((rent <= 0).sum())
                item["wolse_count"] = int((rent > 0).sum())
                item["median_deposit_manwon"] = int(dep.median()) if dep.notna().any() else None
            else:
                amount = to_num(part["deal_amount"])
                item["median_deal_amount_manwon"] = int(amount.median()) if amount.notna().any() else None
            result.append(item)
    return sorted(result, key=lambda x: (x["deal_ym"], x["gu"], x["deal_type"]))


def main():
    if not MOLIT_API_KEY:
        raise RuntimeError("MOLIT_API_KEY가 없습니다")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    statuses = []
    print(f"서울 25개 구 별도 수집 · 최근 {MONTHS_BACK}개월")
    print("기존 강서구/홈 데이터는 수정하지 않습니다.")

    for i, (lawd, (gu_name, slug)) in enumerate(SEOUL_GU.items(), 1):
        target = RAW_DIR / f"{lawd}_{slug}.csv"
        print(f"\n[{i}/25] {gu_name} ({lawd})")
        try:
            df = fetch_multi(
                api_key=MOLIT_API_KEY,
                lawd_cd_list=[lawd],
                months_back=MONTHS_BACK,
                deal_types=DEAL_TYPES,
                use_cache=USE_CACHE,
                verbose=False,
                budget_sec=600,
                max_consecutive_fail=6,
            )
            complete = bool(df.attrs.get("complete", False)) if df is not None else False
            if df is None or df.empty or not complete:
                previous = target.exists()
                statuses.append({"gu": gu_name, "lawd_cd": lawd,
                                 "status": "kept_previous" if previous else "failed",
                                 "rows": None})
                print("  불완전 수집 — 기존 파일 유지" if previous else "  불완전 수집 — 저장 안 함")
                continue

            normalized = normalize_to_legacy(df, {lawd: gu_name})
            normalized["gu_name"] = gu_name
            normalized["lawd_cd"] = lawd
            front = ["gu_name", "lawd_cd"]
            normalized = normalized[front + [c for c in normalized.columns if c not in front]]
            normalized.to_csv(target, index=False, encoding="utf-8-sig")
            statuses.append({"gu": gu_name, "lawd_cd": lawd,
                             "status": "updated", "rows": int(len(normalized))})
            print(f"  저장 {len(normalized):,}건")
        except Exception as exc:
            previous = target.exists()
            statuses.append({"gu": gu_name, "lawd_cd": lawd,
                             "status": "kept_previous" if previous else "failed",
                             "rows": None, "error": str(exc)[:300]})
            print(f"  오류: {exc}")

    files = sorted(RAW_DIR.glob("*.csv"))
    now = datetime.now(ZoneInfo("Asia/Seoul")).isoformat()
    summary_rows = build_summary(files)
    SUMMARY_PATH.write_text(json.dumps({
        "scope": "Seoul 25 districts",
        "months_back": MONTHS_BACK,
        "generated_at_kst": now,
        "district_files": len(files),
        "deal_types": DEAL_TYPES,
        "rows": summary_rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    STATUS_PATH.write_text(json.dumps({
        "generated_at_kst": now,
        "months_back": MONTHS_BACK,
        "updated": sum(x["status"] == "updated" for x in statuses),
        "failed": sum(x["status"] == "failed" for x in statuses),
        "kept_previous": sum(x["status"] == "kept_previous" for x in statuses),
        "district_files": len(files),
        "districts": statuses,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n완료: 구별 CSV {len(files)}개 · 요약 {len(summary_rows):,}행")
    if not files:
        raise RuntimeError("서울 데이터 파일이 하나도 생성되지 않았습니다")


if __name__ == "__main__":
    main()
