"""
국토부 실거래가 API 자동수집 모듈 (실제 호출 버전)

지원:
- 오피스텔 매매/전월세
- 아파트 매매/전월세
- 연립다세대/단독다가구 매매

기능:
- 다년/다지역 자동 반복 수집
- 자동 캐싱 (data/cache/) — 같은 월 데이터는 재호출 안 함
- 한글 컬럼 정규화
- 재시도 + 에러 처리
- [2026.07 추가] 전월세 갱신 계약 필드 수집 (contract_type / use_rr_right / pre_deposit 등)
- [2026.07 추가2] 매매 거래유형(dealing_gbn: 중개거래/직거래) · 해제여부(cdeal_type) 수집
- [2026.08 안정화] GitHub Actions 30분 타임아웃 대응
    · timeout 30초 → 8초 / 재시도 3회 → 2회 / 백오프 단축
    · 연속 실패 감지 시 조기 중단(circuit breaker) — 수집분은 보존
    · 전체 시간 예산(budget) 초과 시 중단하고 수집분 반환
- [2026.08.19 수정] 월 1,000건 초과 데이터 페이지네이션 수집
    · 최근 2개월은 캐시 무시
    · 수집 성공/실패 메타데이터를 DataFrame.attrs에 기록
"""
import os
import time
import requests
import pandas as pd
from pathlib import Path
from xml.etree import ElementTree as ET
from datetime import datetime
from typing import Optional

ENDPOINTS = {
    "오피스텔_매매": "https://apis.data.go.kr/1613000/RTMSDataSvcOffiTrade/getRTMSDataSvcOffiTrade",
    "오피스텔_전월세": "https://apis.data.go.kr/1613000/RTMSDataSvcOffiRent/getRTMSDataSvcOffiRent",
    "아파트_매매": "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade",
    "아파트_전월세": "https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent",
    "연립다세대_매매": "https://apis.data.go.kr/1613000/RTMSDataSvcRHTrade/getRTMSDataSvcRHTrade",
    "연립다세대_전월세": "https://apis.data.go.kr/1613000/RTMSDataSvcRHRent/getRTMSDataSvcRHRent",
    "단독다가구_매매": "https://apis.data.go.kr/1613000/RTMSDataSvcSHTrade/getRTMSDataSvcSHTrade",
}

CACHE_DIR = Path("data/cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# v4: v3 캐시는 1,000건 초과 월이 잘렸을 수 있어 전부 무효화
CACHE_VERSION = "v4"


def _safe_text(item, tag: str) -> str:
    el = item.find(tag)
    if el is not None and el.text:
        return el.text.strip()
    return ""


def _parse_amount(s: str) -> Optional[int]:
    if not s:
        return None
    try:
        return int(s.replace(",", "").strip())
    except ValueError:
        return None


def _request_page(
    url: str,
    params: dict,
    deal_type: str,
    lawd_cd: str,
    deal_ym: str,
    max_retries: int,
    timeout: int,
):
    """국토부 API 한 페이지 호출 + XML/에러코드 검증."""
    last_err = None
    for _ in range(max_retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()

            try:
                root = ET.fromstring(r.content)
            except ET.ParseError as e:
                raise RuntimeError(f"XML 파싱 실패: {e}\n응답: {r.text[:500]}")

            result_code_el = root.find(".//resultCode")
            if (
                result_code_el is not None
                and result_code_el.text
                and result_code_el.text not in ("00", "000")
            ):
                result_msg_el = root.find(".//resultMsg")
                msg = result_msg_el.text if result_msg_el is not None else "알 수 없는 에러"
                raise RuntimeError(f"API 에러 [{result_code_el.text}] {msg}")

            return root
        except Exception as e:
            last_err = e
            time.sleep(1)

    raise RuntimeError(
        f"API 호출 실패 ({last_err}) — {deal_type} {lawd_cd} {deal_ym} "
        f"page={params.get('pageNo')}"
    )


def fetch_one_month(
    api_key: str,
    lawd_cd: str,
    deal_ym: str,
    deal_type: str = "오피스텔_매매",
    use_cache: bool = True,
    max_retries: int = 2,
    timeout: int = 8,
) -> pd.DataFrame:
    """한 달치 실거래 데이터 전체 페이지 수집."""
    if deal_type not in ENDPOINTS:
        raise ValueError(f"지원하지 않는 거래유형: {deal_type}")

    # 신고지연 대응: 현재월 포함 최근 2개월은 캐시 무시하고 항상 API 재수집
    _now = datetime.today()
    _recent_yms = set()
    _y, _m = _now.year, _now.month
    for _ in range(2):
        _recent_yms.add(f"{_y:04d}{_m:02d}")
        _m -= 1
        if _m == 0:
            _m = 12
            _y -= 1
    _is_recent = deal_ym in _recent_yms

    cache_file = CACHE_DIR / f"{CACHE_VERSION}_{deal_type}_{lawd_cd}_{deal_ym}.csv"
    if use_cache and not _is_recent and cache_file.exists():
        return pd.read_csv(cache_file, dtype=str)

    url = ENDPOINTS[deal_type]
    page_size = 1000
    page_no = 1
    rows = []
    total_count = None
    is_rent = "전월세" in deal_type

    while True:
        params = {
            "serviceKey": api_key,
            "LAWD_CD": lawd_cd,
            "DEAL_YMD": deal_ym,
            "pageNo": page_no,
            "numOfRows": page_size,
        }
        root = _request_page(
            url, params, deal_type, lawd_cd, deal_ym, max_retries, timeout
        )

        if total_count is None:
            total_el = root.find(".//totalCount")
            if total_el is not None and total_el.text:
                try:
                    total_count = int(total_el.text.strip())
                except ValueError:
                    total_count = None

        items = root.findall(".//item")
        if not items:
            break

        for item in items:
            name = (
                _safe_text(item, "offiNm")
                or _safe_text(item, "aptNm")
                or _safe_text(item, "mhouseNm")
                or _safe_text(item, "houseType")
            )
            umd = _safe_text(item, "umdNm")
            jibun = _safe_text(item, "jibun")
            area = _safe_text(item, "excluUseAr")
            floor = _safe_text(item, "floor")
            year = _safe_text(item, "dealYear")
            month = _safe_text(item, "dealMonth")
            day = _safe_text(item, "dealDay")
            build_year = _safe_text(item, "buildYear")

            row = {
                "deal_type": deal_type,
                "deal_ym": deal_ym,
                "lawd_cd": lawd_cd,
                "umd_name": umd,
                "jibun": jibun,
                "building_name": name,
                "area_m2": area,
                "floor": floor,
                "build_year": build_year,
                "deal_year": year,
                "deal_month": month,
                "deal_day": day,
            }

            if is_rent:
                row["deal_amount"] = None
                row["deposit"] = _parse_amount(_safe_text(item, "deposit"))
                row["monthly_rent"] = _parse_amount(_safe_text(item, "monthlyRent"))
                row["contract_type"] = _safe_text(item, "contractType")
                row["contract_term"] = _safe_text(item, "contractTerm")
                row["pre_deposit"] = _parse_amount(_safe_text(item, "preDeposit"))
                row["pre_monthly_rent"] = _parse_amount(
                    _safe_text(item, "preMonthlyRent")
                )
                row["use_rr_right"] = _safe_text(item, "useRRRight")
                row["dealing_gbn"] = ""
                row["cdeal_type"] = ""
            else:
                row["deal_amount"] = _parse_amount(_safe_text(item, "dealAmount"))
                row["deposit"] = None
                row["monthly_rent"] = None
                row["contract_type"] = ""
                row["contract_term"] = ""
                row["pre_deposit"] = None
                row["pre_monthly_rent"] = None
                row["use_rr_right"] = ""
                row["dealing_gbn"] = _safe_text(item, "dealingGbn")
                row["cdeal_type"] = _safe_text(item, "cdealType")

            rows.append(row)

        # totalCount가 있으면 그 수를 모두 받을 때 종료.
        # totalCount가 없으면 마지막 페이지가 page_size보다 작을 때 종료.
        if total_count is not None and len(rows) >= total_count:
            break
        if len(items) < page_size:
            break

        page_no += 1
        time.sleep(0.1)

    df = pd.DataFrame(rows)

    # 최근 달도 캐시는 최신 스냅샷으로 덮어두되, 읽을 때는 최근 2개월 캐시를 무시한다.
    if len(df) > 0:
        df.to_csv(cache_file, index=False, encoding="utf-8-sig")

    return df


def fetch_multi(
    api_key: str,
    lawd_cd_list: list,
    months_back: int = 6,
    deal_types: Optional[list] = None,
    use_cache: bool = True,
    verbose: bool = True,
    budget_sec: int = 1200,
    max_consecutive_fail: int = 12,
) -> pd.DataFrame:
    """여러 지역 × 여러 월 × 여러 거래유형 일괄 수집.

    Actions 30분 한도 안에서 끝나도록 안전장치를 둔다.
    성공/실패 항목은 반환 DataFrame.attrs에 기록한다.
    """
    if deal_types is None:
        deal_types = ["오피스텔_매매", "오피스텔_전월세"]

    today = datetime.today()
    months = []
    y, m = today.year, today.month
    for _ in range(months_back):
        months.append(f"{y:04d}{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1

    all_dfs = []
    total = len(lawd_cd_list) * len(months) * len(deal_types)
    cnt = 0
    ok_cnt = 0
    fail_cnt = 0
    consecutive_fail = 0
    aborted = False
    started = time.time()
    successful_keys = []
    failed_keys = []

    for lawd in lawd_cd_list:
        for ym in months:
            for dt in deal_types:
                if aborted:
                    break

                elapsed = time.time() - started
                if elapsed > budget_sec:
                    print(
                        f"\n⏱  시간 예산 {budget_sec}초 초과 "
                        f"({elapsed:.0f}초) — 남은 항목 건너뜀"
                    )
                    aborted = True
                    break

                cnt += 1
                if verbose:
                    print(f"[{cnt}/{total}] {dt} | LAWD={lawd} | {ym}")
                try:
                    one = fetch_one_month(
                        api_key, lawd, ym, dt, use_cache=use_cache
                    )
                    ok_cnt += 1
                    successful_keys.append((lawd, ym, dt))
                    consecutive_fail = 0
                    if len(one) > 0:
                        all_dfs.append(one)
                        if verbose:
                            print(f"  ✓ {len(one)}건")
                    elif verbose:
                        print("  (데이터 없음)")
                except Exception as e:
                    fail_cnt += 1
                    failed_keys.append((lawd, ym, dt))
                    consecutive_fail += 1
                    print(f"  ✗ 실패({consecutive_fail}연속): {e}")
                    if consecutive_fail >= max_consecutive_fail:
                        print(
                            f"\n🚨 연속 {consecutive_fail}회 실패 — "
                            "API 장애로 판단, 수집 중단"
                        )
                        print("   (수집된 분량은 그대로 저장합니다)")
                        aborted = True
                        break
                time.sleep(0.3)
            if aborted:
                break
        if aborted:
            break

    elapsed = time.time() - started
    complete = (not aborted) and fail_cnt == 0 and cnt == total

    print("\n=== 수집 요약 ===")
    print(
        f"  시도 {cnt}/{total} · 성공 {ok_cnt} · 실패 {fail_cnt} "
        f"· 소요 {elapsed:.0f}초"
    )
    if aborted:
        print("  ⚠ 중단됨 — 부분 데이터입니다")

    result = (
        pd.concat(all_dfs, ignore_index=True)
        if all_dfs
        else pd.DataFrame()
    )
    result.attrs["complete"] = complete
    result.attrs["successful_keys"] = successful_keys
    result.attrs["failed_keys"] = failed_keys
    result.attrs["requested_months"] = months
    result.attrs["requested_total"] = total
    return result


def normalize_to_legacy(df: pd.DataFrame, region_map: dict) -> pd.DataFrame:
    """기존 main.py의 region_name 컬럼 구조에 맞춰 변환."""
    if len(df) == 0:
        return df

    df = df.copy()
    df["region_name"] = df["umd_name"].where(
        df["umd_name"] != "",
        df["lawd_cd"].map(region_map).fillna("")
    )

    legacy_cols = [
        "region_name", "deal_ym", "deal_type", "building_name",
        "umd_name", "jibun", "area_m2", "deal_amount",
        "deposit", "monthly_rent", "floor", "deal_day", "build_year",
        "contract_type", "contract_term", "pre_deposit", "pre_monthly_rent",
        "use_rr_right", "dealing_gbn", "cdeal_type",
    ]
    return df[[c for c in legacy_cols if c in df.columns]]


def load_sample_molit(path="data/molit_trade_sample.csv") -> pd.DataFrame:
    """샘플 CSV 로드 (기존 호환)"""
    return pd.read_csv(path)


def load_live_or_sample(
    live_path="data/molit_trade_live.csv",
    sample_path="data/molit_trade_sample.csv",
) -> pd.DataFrame:
    """실데이터 있으면 실데이터, 없으면 샘플 자동 로드"""
    if Path(live_path).exists():
        return pd.read_csv(live_path)
    return pd.read_csv(sample_path)


if __name__ == "__main__":
    import sys
    try:
        from config import MOLIT_API_KEY
    except ImportError:
        print("ERROR: config.py가 없습니다. config_example.py를 복사해 만드세요.")
        sys.exit(1)

    if not MOLIT_API_KEY or MOLIT_API_KEY == "YOUR_MOLIT_API_KEY":
        print("ERROR: config.py의 MOLIT_API_KEY를 발급받은 키로 변경하세요.")
        sys.exit(1)

    regions = pd.read_csv("regions.csv")
    lawd_list = regions["lawd_cd"].astype(str).str.zfill(5).tolist()
    region_map = dict(
        zip(
            regions["lawd_cd"].astype(str).str.zfill(5),
            regions["region_name"],
        )
    )

    print("=== 국토부 실거래 자동수집 시작 ===")
    print(f"지역: {len(lawd_list)}개 · {list(region_map.values())}")
    print("최근 6개월 · 오피스텔 매매+전월세\n")

    df = fetch_multi(
        api_key=MOLIT_API_KEY,
        lawd_cd_list=lawd_list,
        months_back=6,
        deal_types=["오피스텔_매매", "오피스텔_전월세"],
    )

    if len(df) == 0:
        print("수집된 데이터가 없습니다.")
        sys.exit(0)

    norm = normalize_to_legacy(df, region_map)
    Path("data").mkdir(exist_ok=True)
    out_path = Path("data/molit_trade_live.csv")
    norm.to_csv(out_path, index=False, encoding="utf-8-sig")

    print("\n=== 완료 ===")
    print(f"총 {len(norm):,}건 수집")
    print(f"저장: {out_path}")
    print("\n지역별 건수:")
    print(norm.groupby("region_name").size().to_string())
