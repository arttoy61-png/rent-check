"""
일일 블로그 글 자동생성 (요일별 7종 + 보강 v2.0)

[보강 내역 - 2026.05.16]
  - gen_weekly_summary       : 매매·전월세 통합 결산
  - gen_rent_check           : 화요일 — 첫주 월세·둘주 전세·셋주 매매·마지막주 월간종합(월간 시세 리포트)
  - gen_building_spotlight   : TOP 30 단지 권역 균등 로테이션 + 매매 통합 + 권역 비교
  - gen_jeonse_vs_monthly    : 일반인용 용어 설명 + 실제 사례 + 판정 컬럼
  - gen_value_picks          : "평균 이하 실거래 분석" + 절약 이유 추론 + 분석 인사이트
  - gen_neighborhood         : 시장 트렌드 인사이트 + 평형·연식·평균월세 추가
  - gen_tenant_guide         : 10주 회전 가이드 풀

Claude API 없이도 작동.
- 입력: data/aggregated_rent.json + data/molit_trade_live.csv
- 출력: outputs/blog/YYYYMMDD/<category>_(메타.txt|미리보기.html|본문.html)

요일별 카테고리:
  월: weekly_summary    [월] 주간 거래 결산 (매매·전월세)
  화: rent_check        [화] 화곡동 월세 시세
  수: building_spotlight [수] 단지별 시세 분석
  목: jeonse_vs_monthly [목] 전세 vs 월세 분석
  금: value_picks       [금] 평균 이하 실거래 분석
  토: neighborhood      [토] 화곡동 거래 활발 단지
  일: tenant_guide      [일] 임차인 가이드
"""
import csv
import json
import re
import random
from pathlib import Path
from datetime import datetime, timedelta
from collections import Counter, defaultdict
from statistics import mean, median

# widget_renderer 모듈에서 표 생성 함수 재활용
import sys
sys.path.insert(0, str(Path(__file__).parent))
from widget_renderer import (
    render_matrix_table,
    render_jeonse_table,
    render_checker_guide,
    render_full_widget,
    NAVY, BLUE, GOLD, GRAY_TXT,
)
from link_box import render_link_box, render_compact_link

WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]
WEEKDAY_CATEGORY = {
    0: "weekly_summary",     # 월
    1: "rent_check",         # 화
    2: "building_spotlight", # 수
    3: "jeonse_vs_monthly",  # 목
    4: "value_picks",        # 금
    5: "neighborhood",       # 토
    6: "tenant_guide",       # 일
}

# 네이버 새 카테고리 체계 (2026.7.4 확정 — 요일 카테고리 폐지, 주제 6대분류)
NAVER_CATEGORY = {
    "weekly_summary":     "실거래·시세 > 주간 거래결산",
    "rent_check":         "실거래·시세 > 화곡동 월세(첫째주)/전세(둘째)/매매(셋째) · 마지막 화요일=월간 시세 리포트",
    "building_spotlight": "실거래·시세 > 단지별 시세",
    "jeonse_vs_monthly":  "실거래·시세 > 화곡동 월세",
    "value_picks":        "실거래·시세 > 단지별 실거래 추적",
    "neighborhood":       "실거래·시세 > 거래활발 단지",
    "tenant_guide":       "임차인 가이드 > 체크리스트(주제별로 계약/전세/월세/계약갱신 중 선택)",
}

def get_week_label(deal_ym: str, deal_day: str) -> str:
    """거래일을 '5월 2주차 거래' 형식으로 변환"""
    if not deal_ym or not deal_day:
        return "최근 거래"
    try:
        month = int(deal_ym[4:6])
        day = int(deal_day)
        week = (day - 1) // 7 + 1
        return f"{month}월 {week}주차 거래"
    except:
        return "최근 거래"


def summary_box(items):
    lis = "".join(f'<div style="margin:3px 0">· {x}</div>' for x in items if x)
    return ('<div style="border:2px solid #1565c0;border-radius:12px;padding:16px 20px;margin:18px 0;font-family:\'Malgun Gothic\',sans-serif">'
            '<div style="font-size:16px;font-weight:800;color:#0d1f3c;margin-bottom:8px">📋 핵심 요약</div>'
            f'<div style="font-size:15px;line-height:1.85;color:#1c2733">{lis}</div></div>')

def oneline_box(text):
    return ('<div style="border:2px solid #d4a73a;border-radius:12px;padding:14px 20px;margin:22px 0;font-family:\'Malgun Gothic\',sans-serif">'
            '<div style="font-size:15px;font-weight:800;color:#b8860b;margin-bottom:6px">✅ 렌트체크 한 줄</div>'
            f'<div style="font-size:15px;line-height:1.85;color:#1c2733">{text}</div></div>')

def header_html(emoji: str, category: str, title: str, region: str, date_str: str, subtitle: str = "") -> str:
    subtitle_html = f'<div style="font-size:11px;opacity:.7;margin-top:6px;line-height:1.5">{subtitle}</div>' if subtitle else ""
    return f"""
<div style="background:linear-gradient(135deg,{NAVY},{BLUE});color:#fff;padding:24px 26px;border-radius:12px;margin-bottom:20px;font-family:'Malgun Gothic',sans-serif">
  <div style="font-size:12px;opacity:.85;letter-spacing:2px;margin-bottom:6px">{category.upper()}</div>
  <div style="font-size:22px;font-weight:700;line-height:1.4">{emoji} {title}</div>
  <div style="font-size:12px;opacity:.8;margin-top:8px">{region} · {date_str}</div>
  {subtitle_html}
</div>
"""


def section(title: str, body_html: str) -> str:
    return f"""
<div style="margin:24px 0;font-family:'Malgun Gothic',sans-serif">
  <h2 style="font-size:20px;color:{NAVY};border-left:4px solid {BLUE};padding-left:10px;margin:0 0 12px 0">{title}</h2>
  <div style="font-size:17px;line-height:1.85;color:#333">{body_html}</div>
</div>
"""


# ── 태그 30개 보강 (네이버 SEO: 시의성 + 화곡동 롱테일) ──
TIMELY_TAGS = []  # 시의성 태그: 시기마다 수동 갱신 (선거 종료로 비움 / ★6.3지방선거 태그 금지)

# 최근 상승 이유 블록 (시의성 자료 - 월 1회 수동 갱신, 출처 필수 / 추측 금지)
MARKET_TREND_NOTE = """
<div style="margin:24px 0;font-family:'Malgun Gothic',sans-serif">
  <h2 style="font-size:20px;color:#0d1f3c;border-left:4px solid #1565c0;padding-left:10px;margin:0 0 12px 0">📰 시장 배경 — 서울 전월세 흐름</h2>
  <p style="font-size:16px;line-height:1.85;color:#333;margin:0 0 12px 0">단지 숫자만 보면 반쪽입니다. 이 단지가 올라탄 서울 전체 흐름을 옆에 놓고 봐야 방향이 읽혀요.</p>
  <div style="background:#f7f9fc;border-left:4px solid #1565c0;border-radius:8px;padding:14px 18px;font-size:15px;line-height:1.95;color:#333">
    · 서울 아파트 <strong>전세</strong> 올해 5월 둘째 주까지 누적 <strong>+2.89%</strong>(작년 동기의 약 6배), 월세도 4월까지 +2.39% — 한국부동산원(2026.5)<br>
    · 서울 아파트 <strong>전세 매물 27.3%↓ · 월세 매물 27.9%↓</strong>(올초 대비, 5/13) — 아실<br>
    · 2026년 서울 아파트 <strong>입주물량 약 1.6만 세대로 반토막</strong>(공급 절벽) — 직방(2025.12)<br>
    · 서울 <strong>25개 자치구 전부 상승</strong>(강서 포함) — 2026.5 보도
  </div>
  <p style="font-size:13px;color:#999;line-height:1.7;margin-top:10px">※ 공급(입주·매물) 급감 + 전세의 월세화가 배경. 자료 시점(2026.5) 이후는 달라질 수 있어 월 1회 갱신합니다. 개별 단지는 이 흐름과 반대로 움직이기도 합니다.</p>
</div>
"""

# 관련 글 내부링크 (발행 후 네이버 에디터에서 href 연결)
INTERNAL_LINKS = """
<div style="margin:24px 0;padding:16px 20px;background:#f7f9fc;border-radius:10px;font-family:'Malgun Gothic',sans-serif">
  <div style="font-size:15px;font-weight:700;color:#0d1f3c;margin-bottom:10px">\U0001F517 \ud568\uaed8 \uc77d\uc73c\uba74 \uc88b\uc740 \uae00</div>
  <div style="font-size:15px;line-height:2;color:#1565c0">
    · <a href="#" style="color:#1565c0;text-decoration:none">[\ud2b8\ub80c\ub4dc] \uc804\uc138 \uc18c\uba78 \uc2dc\ub300 \u2014 \ud654\uacf5\ub3d9 \uc6d4\uc138 \ube44\uc911 70% \ub3cc\ud30c\uc758 \uc758\ubbf8</a><br>
    · <a href="#" style="color:#1565c0;text-decoration:none">[\ubd84\uc11d] \uc6d4\uc138 300\ub9cc \uc6d0 \uc2dc\ub300, \uac15\uc11c\uad6c \uc544\ud30c\ud2b8 \ud658\uc0b0\uc6d4\uc138 \ucd1d\uc815\ub9ac</a>
  </div>
  <div style="font-size:12px;color:#aaa;margin-top:8px">※ \ubc1c\ud589 \ud6c4 \uac01 \uae00 URL\ub85c \uc5f0\uacb0</div>
</div>
"""
_EVERGREEN_POOL = [
    "화곡동", "화곡동실거래", "화곡동실거래가", "화곡동시세", "화곡동부동산",
    "화곡동월세", "화곡동전세", "화곡동매매", "화곡동전월세", "화곡동월세시세",
    "화곡동전세시세", "화곡동아파트", "화곡동빌라", "화곡동오피스텔", "화곡동아파트매매",
    "화곡동빌라전세", "화곡동소형아파트", "화곡동원룸", "화곡동투룸", "화곡동역세권",
    "강서구화곡동", "강서구실거래", "강서구시세", "강서구부동산", "강서구월세",
    "화곡역실거래", "까치산역실거래", "우장산역실거래", "주간결산", "렌트체크강서",
]

def build_tags(existing=None, n=30):
    """태그를 항상 n개로: 시의성 → 카테고리 태그 → 화곡동 롱테일 순, 중복 제거(4글자+ 권장)."""
    out, seen = [], set()
    for t in list(TIMELY_TAGS) + list(existing or []) + _EVERGREEN_POOL:
        t = (t or "").strip().lstrip("#")
        if t and t not in seen:
            seen.add(t); out.append(t)
        if len(out) >= n:
            break
    return out[:n]


# ── 준전세/이상치 필터 (value_picks와 동일 기준) ──
def is_anomaly_rent(deposit, monthly_rent):
    """월세 거래 중 준전세성 이상치 제외: 월세<30만 또는 보증금>월세*240. 전세(월세0)는 대상 아님."""
    if monthly_rent is None or monthly_rent <= 0:
        return False
    return monthly_rent < 30 or deposit > monthly_rent * 240


# ── (월) 주간 브리핑: 주목 이슈 — 월요일은 '주간 거래' 데이터 중심.
#    이슈는 그 주 진짜 현안만 1~2개, 없으면 빈 리스트로 두면 섹션 자동 생략. 지어내기 금지·날짜 표기. ──
WEEKLY_ISSUES = [
    # 그 주 진짜 현안만 1~2개 수동 입력(없으면 빈 채로 두면 섹션 자동 생략). 지어내기 금지·날짜 표기.
    # ★6·3 지방선거 항목은 선거 종료로 제거(2026.6).
]


def footer_html(tags: list) -> str:
    tags = build_tags(tags)
    tag_html = " ".join(f'<span style="display:inline-block;background:#e8f1fb;color:{BLUE};padding:4px 10px;border-radius:12px;font-size:12px;margin:2px 4px 2px 0">#{t}</span>' for t in tags)
    cta_html = f"""
<div style="margin:24px 0;padding:18px 20px;background:linear-gradient(135deg,#fafbff 0%,#f0f6ff 100%);border:2px solid {BLUE};border-radius:12px;font-family:'Malgun Gothic',sans-serif">
  <div style="font-size:16px;font-weight:700;color:{NAVY};margin-bottom:10px">💬 내 계약, 시세랑 맞나 궁금하다면</div>
  <div style="font-size:15px;color:#444;line-height:1.8;margin-bottom:12px">
    <strong>단지명·평형·보증금(월세)</strong>을 댓글로 남겨주세요. 공개가 부담되면 댓글창의 자물쇠🔒를 잠그고 비밀댓글로 남기셔도 됩니다. 실거래 기준으로 적정한지 확인해 답글 드릴게요.<br>
    분석받고 싶은 단지가 있다면 단지명만 남겨주셔도 됩니다 — 수요일 단지별 시세 글에 우선 반영합니다.
  </div>
</div>
"""
    return f"""
{cta_html}
<div style="margin-top:32px;padding-top:18px;border-top:1px solid #eee;font-family:'Malgun Gothic',sans-serif">
  <div style="font-size:11px;color:#999;margin-bottom:10px">본 글은 국토교통부 실거래가 공개 자료를 기반으로 작성되었으며, 단순 정보 제공 목적입니다. 개별 매물의 가격은 시점·조건에 따라 다를 수 있습니다.</div>
  <div>{tag_html}</div>
</div>
"""


# ═══════════════════════════════════════════════════════════
# 요일별 글 생성 함수 (보강 v2.0)
# ═══════════════════════════════════════════════════════════

def gen_weekly_summary(report: dict, csv_rows: list, date_str: str) -> dict:
    """월요일: 화곡동 주간 브리핑 (최근 실거래 + 그 주 이슈 + 세입자 팁)"""
    region = report["region"]
    target_date = datetime.strptime(date_str, "%Y-%m-%d")

    def to_int(v):
        try: return int(str(v).replace(",", "")) if v else 0
        except: return 0
    def to_float(v):
        try: return float(v) if v else 0.0
        except: return 0.0
    def fmt_won(amt):
        if not amt: return "-"
        if amt >= 10000:
            eok = amt // 10000; rest = amt % 10000
            return f"{eok}억 {rest:,}만원" if rest else f"{eok}억"
        return f"{amt:,}만원"
    def deal_date_str(r):
        ym = r.get("deal_ym", ""); day = r.get("deal_day", "")
        if not ym or not day: return ""
        return f'{ym[:4]}.{ym[4:6]}.{str(day).zfill(2)}'
    def deal_date_obj(r):
        ym = r.get("deal_ym", ""); day = r.get("deal_day", "")
        if not ym or not day: return None
        try: return datetime(int(ym[:4]), int(ym[4:6]), int(day))
        except (ValueError, TypeError): return None

    # 1. 롤링 윈도우 (데이터 최신일 기준 7일, 신고 지연 대응)
    WINDOW_DAYS = 7
    _dates = [d for d in (deal_date_obj(r) for r in csv_rows) if d and d <= target_date]
    data_latest = max(_dates) if _dates else target_date
    week_end = data_latest
    week_start = week_end - timedelta(days=WINDOW_DAYS - 1)
    def in_recent_week(r):
        d = deal_date_obj(r)
        return bool(d) and week_start <= d <= week_end

    def get_building_type(deal_type):
        if "아파트" in deal_type: return "아파트"
        if "오피스텔" in deal_type: return "오피스텔"
        if "연립" in deal_type or "다세대" in deal_type: return "빌라·다세대"
        if "단독" in deal_type: return "단독주택"
        return "기타"

    grouped = {
        ("아파트", "매매"): [], ("아파트", "전월세"): [],
        ("오피스텔", "매매"): [], ("오피스텔", "전월세"): [],
        ("빌라·다세대", "매매"): [], ("빌라·다세대", "전월세"): [],
    }
    anomaly_dropped = 0
    for r in csv_rows:
        if not in_recent_week(r): continue
        dt = str(r.get("deal_type", ""))
        bt = get_building_type(dt)
        kind = "매매" if "매매" in dt else ("전월세" if "전월세" in dt else None)
        if not kind: continue
        if kind == "전월세":
            dep = to_int(r.get("deposit")); rent = to_int(r.get("monthly_rent"))
            if is_anomaly_rent(dep, rent):
                anomaly_dropped += 1
                continue
        key = (bt, kind)
        if key in grouped: grouped[key].append(r)

    sales_count = sum(len(v) for k, v in grouped.items() if k[1] == "매매")
    rent_count = sum(len(v) for k, v in grouped.items() if k[1] == "전월세")
    total_count = sales_count + rent_count

    week_start_str = week_start.strftime("%m월 %d일")
    week_end_str = week_end.strftime("%m월 %d일")

    # ── 카드 렌더 (최신 6건 캡) ──
    def render_sales_table(rows):
        rows = sorted(rows, key=lambda x: (x.get("deal_ym", ""), str(x.get("deal_day", "")).zfill(2)), reverse=True)[:6]
        html = ""
        for r in rows:
            area = to_float(r.get("area_m2")); pyung = round(area / 3.3058, 1) if area else "-"
            area_disp = f'전용 {r.get("area_m2","")}㎡({pyung}평)' if area else "-"
            amt = to_int(r.get("deal_amount")); name = r.get("building_name", "") or "-"
            html += (f'<div style="background:#fff;border:1px solid #e0e0e0;border-left:4px solid {BLUE};border-radius:10px;padding:12px 14px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">'
                     f'<div style="flex:1;min-width:0"><div style="font-size:15px;font-weight:700;color:{NAVY};word-break:break-all">{name}</div>'
                     f'<div style="font-size:12px;color:#666;margin-top:3px">{get_building_type(str(r.get("deal_type","")))} · {area_disp} · {deal_date_str(r)}</div></div>'
                     f'<div style="font-size:15px;font-weight:700;color:{BLUE};white-space:nowrap">{fmt_won(amt)}</div></div>')
        return html

    def render_rent_table(rows):
        rows = sorted(rows, key=lambda x: (x.get("deal_ym", ""), str(x.get("deal_day", "")).zfill(2)), reverse=True)[:6]
        html = ""
        for r in rows:
            area = to_float(r.get("area_m2")); pyung = round(area / 3.3058, 1) if area else "-"
            area_disp = f'전용 {r.get("area_m2","")}㎡({pyung}평)' if area else "-"
            dep = to_int(r.get("deposit")); rent = to_int(r.get("monthly_rent")); name = r.get("building_name", "") or "-"
            if rent > 0:
                label, color = "월세", "#ef476f"
                price = f'보증 <strong style="color:{NAVY}">{dep:,}만</strong> / 월 <strong style="color:#ef476f">{rent}만</strong>'
            else:
                label, color = "전세", "#06d6a0"
                price = f'<strong style="color:#06d6a0">{fmt_won(dep)}</strong>'
            html += (f'<div style="background:#fff;border:1px solid #e0e0e0;border-left:4px solid {color};border-radius:10px;padding:12px 14px;margin-bottom:8px">'
                     f'<div style="display:flex;justify-content:space-between;gap:10px;margin-bottom:4px"><div style="font-size:15px;font-weight:700;color:{NAVY};word-break:break-all;flex:1;min-width:0">{name}</div>'
                     f'<span style="background:{color};color:#fff;padding:2px 9px;border-radius:12px;font-size:11px;white-space:nowrap">{label}</span></div>'
                     f'<div style="font-size:12px;color:#666;margin-bottom:4px">{area_disp} · {deal_date_str(r)}</div>'
                     f'<div style="font-size:15px">{price}</div></div>')
        return html

    type_emoji = {"아파트": "🏢", "오피스텔": "🏬", "빌라·다세대": "🏘️"}
    SPACER = '<div style="height:14px"></div>'

    # ── 본문 조립 (브리핑) ──
    body = header_html("🗞️", "WEEKLY BRIEFING", f"{region} 주간 브리핑", region, date_str,
                       subtitle="📌 매주 월요일 · 최근 실거래 + 주목 이슈")

    # (A) 데이터 카드
    data_intro = (f'최근 신고 기준 <strong>{week_start_str}~{week_end_str}</strong> {region} 실거래 '
                  f'<strong style="color:{BLUE}">총 {total_count}건</strong> (매매 {sales_count} / 전·월세 {rent_count}).<br>'
                  f'<span style="font-size:13px;color:#888">※ 국토부 실거래는 계약 후 30일 내 신고 → 최신 주는 비어, 신고가 충분히 쌓인 최근 7일로 집계. '
                  f'준전세성 이상치 {anomaly_dropped}건은 시세 왜곡 방지로 제외.</span>')
    data_body = data_intro
    if total_count == 0:
        data_body += '<div style="margin-top:10px;color:#666">현재 신고가 반영된 거래가 없습니다. 다음 갱신 때 늘어납니다.</div>'
    else:
        def _amt(r): return to_int(r.get("deal_amount"))
        all_sales = [r for k, v in grouped.items() if k[1] == "매매" for r in v]
        top_sales = sorted(all_sales, key=_amt, reverse=True)[:5]
        if top_sales:
            data_body += f'<div style="margin-top:14px;font-weight:700;color:{NAVY}">💎 매매 — 이번 주 TOP {len(top_sales)}</div>'
            data_body += render_sales_table(top_sales)
            rest_s = len(all_sales) - len(top_sales)
            if rest_s > 0:
                lo = min(_amt(r) for r in all_sales); hi = max(_amt(r) for r in all_sales)
                data_body += (f'<div style="font-size:13px;color:#666;margin:4px 0 2px">이 밖 {rest_s}건은 '
                              f'{fmt_won(lo)}~{fmt_won(hi)} 사이입니다 — 대부분 빌라·소형이에요.</div>')
        jeonse_w = [r for k, v in grouped.items() if k[1] == "전월세" for r in v if to_int(r.get("monthly_rent")) == 0]
        wolse_w  = [r for k, v in grouped.items() if k[1] == "전월세" for r in v if to_int(r.get("monthly_rent")) > 0]
        top_j = sorted(jeonse_w, key=lambda r: to_int(r.get("deposit")), reverse=True)[:3]
        if top_j:
            data_body += f'<div style="margin-top:16px;font-weight:700;color:{NAVY}">🏠 전세 — 상단 3건 (이번 주 {len(jeonse_w)}건)</div>'
            data_body += render_rent_table(top_j)
        if wolse_w:
            from collections import Counter as _C
            _tb = _C((r.get("building_name") or "").strip() or "이름없음" for r in wolse_w).most_common(1)[0]
            data_body += (f'<div style="font-size:14px;color:#444;margin-top:10px">월세는 <strong>{len(wolse_w)}건</strong> 나왔고, '
                          f'가장 많이 나온 곳은 <strong>{_tb[0]}</strong>({_tb[1]}건)입니다. '
                          f'평형·보증금별 월세표는 화요일 글 몫이라 여기선 숫자만 봅니다.</div>')
        # 🔎 해석 한 줄: 같은 단지·같은 평형 주간 2건+ & 가격차 큰 것
        from collections import defaultdict as _dd
        _pairs = _dd(list)
        for r in jeonse_w + all_sales:
            try: _pairs[((r.get("building_name") or "").strip(), round(to_float(r.get("area_m2"))))].append(r)
            except Exception: pass
        _obs = []
        for (nm, ar), rs in _pairs.items():
            if len(rs) < 2 or not nm: continue
            vals = [(to_int(r.get("deal_amount")) or to_int(r.get("deposit")), r) for r in rs]
            vals.sort(key=lambda x: x[0])
            gap = vals[-1][0] - vals[0][0]
            if gap >= 1500:
                k = "매매" if "매매" in str(vals[0][1].get("deal_type","")) else "전세"
                _obs.append(f'<strong>{nm}</strong> {ar}㎡ {k}가 같은 주에 {fmt_won(vals[0][0])}→{fmt_won(vals[-1][0])}, '
                            f'<strong>{fmt_won(gap)} 차이</strong>가 났습니다. 층수 차이인지 옵션 차이인지 확인이 필요한 대목입니다.')
        if _obs:
            data_body += ('<div style="margin-top:12px;padding:10px 14px;border-left:4px solid #c4661f;font-size:14px;color:#444;line-height:1.85">'
                          '🔎 ' + "<br>🔎 ".join(_obs[:2]) + '</div>')
        # 직전 4주 주평균과 비교한 관찰 문장
    _p_start = week_start - timedelta(days=28)
    _prev = [r for r in csv_rows if (lambda d: d and _p_start <= d < week_start)(deal_date_obj(r))]
    _pm = len([r for r in _prev if "매매" in str(r.get("deal_type",""))]) / 4.0
    _pr = len([r for r in _prev if "전월세" in str(r.get("deal_type",""))]) / 4.0
    if _pm and sales_count >= _pm * 1.3: _lead = "이번 주는 생각보다 매매가 많았습니다 — 평소 주보다 확실히 손이 바뀌었어요."
    elif _pm and sales_count <= _pm * 0.6: _lead = "매매는 평소보다도 조용했습니다. 관망이 짙은 주간이에요."
    elif sales_count >= 10: _lead = "매매가 두 자릿수면 화곡 기준으로 분명히 움직임이 있는 주간이에요."
    else: _lead = "매매는 조용했고, 임차 쪽이 끌고 간 한 주였습니다."
    if _pr and rent_count >= _pr * 1.25: _lead += " 그리고 예상보다 전월세 신고가 많네요."
    body += section("🗞️ 이번 주, 먼저 한 줄",
        f"안녕하세요, 렌트체크강서입니다. 월요일은 지난 한 주 화곡동 신고분부터 훑고 시작하는데요 — "
        f"이번 주는 매매 <strong>{sales_count}건</strong>, 전·월세 <strong>{rent_count}건</strong>이 새로 잡혔습니다. {_lead} "
        f"아래에서 굵직한 것만 골라 보시죠."
        f'<div style="margin-top:10px;font-size:13.5px;color:#6b5a32">렌트체크강서는 국토부 실거래 신고분을 매주 직접 수집해, '
        f'화곡동만 <strong>{max(1,((week_end - min(_dates)).days // 7) + 1) if _dates else 1}주째</strong> 추적하고 있습니다.</div>')
    body += summary_box([
        f"이번 주 신고: 매매 <strong>{sales_count}건</strong> · 전월세 <strong>{rent_count}건</strong>",
        _lead,
        "굵직한 거래만 아래 표로 — 3분이면 훑습니다",
    ])
    body += section("📊 이번 주, 뭐가 얼마에 팔렸나", data_body)
    try:
        _sigs = scan_signals(csv_rows, date_str)
        if _sigs:
            import re as _re
            _rent_only, _bulk, _etc = [], [], []
            for s in _sigs[:5]:
                m = _re.match(r"\[신축월세전용\] \S+ (.+?)\((.+?), (\d{4})준공\) — 월세만 (\d+)건", s)
                if m: _rent_only.append((m.group(1), m.group(4))); continue
                m = _re.match(r"\[일괄후보\] \S+ (.+?)\((.+?)\) — (\S+) 하루 (\d+)건", s)
                if m: _bulk.append((m.group(1), m.group(3), m.group(4))); continue
                _etc.append(_re.sub(r"^\[[^\]]+\]\s*", "", s))
            _sent = []
            if len(_rent_only) >= 2:
                _names = "·".join(f"{n}({c}건)" for n, c in _rent_only[:3])
                _sent.append(f"<strong>{_names}</strong> — 셋 다" if len(_rent_only) >= 3 else f"<strong>{_names}</strong> — 둘 다")
                _sent[-1] += " 준공 2년 안 된 신축인데 거래가 월세뿐입니다. 일반 임대 흐름이라기보다 공공매입·청년임대 공급 영향일 가능성이 커 보여요."
            elif _rent_only:
                n, c = _rent_only[0]
                _sent.append(f"<strong>{n}</strong>는 이번에도 월세만 {c}건입니다. 공공매입·청년임대 공급 영향일 가능성이 커 보여요.")
            for n, d, c in _bulk[:2]:
                _sent.append(f"<strong>{n}</strong>에서 {d} 하루에 매매 {c}건이 한꺼번에 신고됐습니다. 통매각·일괄매입 냄새가 나는 자리라 등기·플레이스로 확인해볼 대목입니다.")
            _sent += _etc[:1]
            body += section("📌 이번 주 눈에 띈 것",
                "<br><br>".join(_sent[:3]) +
                '<br><span style="font-size:13px;color:#888">확인 전에는 단정하지 않습니다.</span>'
                + '<div style="margin-top:10px;font-size:15px;color:#1c2733">이 중 제일 궁금한 건 <strong>이번 주에 따로 파서</strong> 올리겠습니다 — 어떤 게 궁금하신지 댓글로 찍어주셔도 됩니다.</div>')
    except Exception:
        pass
    body += section("💬 이번 주 코멘트",
        '<div style="border-left:4px solid #d4a73a;padding-left:12px;color:#555;font-size:15px;line-height:1.95">'
        '[[코멘트 — 발행 전 클로드가 5줄로 채웁니다. 이 문구가 남아 있으면 발행하지 마세요.]]</div>')

    # (B) 주목 이슈 (그 주 현안만, 없으면 생략)
    if WEEKLY_ISSUES:
        issue_html = ""
        for it in WEEKLY_ISSUES[:3]:
            src = f'<div style="font-size:12px;color:#999;margin-top:6px">출처: {it["source"]}</div>' if it.get("source") else ""
            issue_html += (f'<div style="background:#f7f9fc;border:1px solid #d7e3f4;border-radius:10px;padding:14px 16px;margin-bottom:10px">'
                           f'<div style="font-size:16px;font-weight:700;color:{NAVY};margin-bottom:6px">{it.get("emoji","")} {it["title"]}</div>'
                           f'<div style="font-size:15px;color:#444;line-height:1.7">{it["body"]}</div>{src}</div>')
        body += section("🗞️ 화곡동·강서 주목 이슈", issue_html)

    # (C) 세입자 팁
    body += section("💬 이번 주 세입자 팁",
        "위 목록에서 낯익은 단지가 보이나요? 집주인이 시세보다 2천 높게 부르면 — <strong>\"지난주 실거래는 이 가격이었습니다\"</strong>라고 말할 수 있는 근거가 이 표입니다. "
        "재개발·모아타운 구역 물건이라면 가격보다 먼저 볼 게 있습니다 — <strong>정비사업 단계(조합·관리처분)</strong>와 <strong>실거주·전매 조건</strong>. "
        "이걸 놓치면 시세가 맞아도 계약이 틀려요. 단지 하나를 깊게 파는 건 매주 <strong>수요일 단지별 시세 글</strong> 몫입니다.")

    body += oneline_box("결산은 흐름 확인용입니다 — 계약·매수 판단은 단지·평형 단위 실거래로 하세요. 이 흐름이 이어지는지, 다음 주 월요일에 다시 봅니다.")
    body += footer_html([region, f"{region}주간브리핑", f"{region}실거래가", f"{region}시세", "화곡동고도제한", "화곡동모아타운", "화곡동재개발", "강서구화곡동", "주간결산", "화곡동부동산"])

    return {
        "title": f"{region} 주간 브리핑 | {week_start_str}~{week_end_str} 실거래 + 주목 이슈",
        "category": "weekly_summary",
        "html": body,
        "tags": [region, f"{region}주간브리핑", f"{region}실거래가", f"{region}시세", "화곡동고도제한", "화곡동모아타운", "화곡동재개발", "강서구화곡동", "주간결산", "화곡동부동산"],
    }


def gen_rent_check(report: dict, csv_rows: list, date_str: str) -> dict:
    """화요일: 화곡동 시세 (격주 롤링: 월세→전세→매매→종합)"""
    region = report["region"]
    target_date = datetime.strptime(date_str, "%Y-%m-%d")
    week_of_month = (target_date.day - 1) // 7 + 1
    
    def to_int(v):
        try: return int(str(v).replace(",", "")) if v else 0
        except: return 0
    
    def to_float(v):
        try: return float(v) if v else 0.0
        except: return 0.0
    
    def fmt_won(amt):
        if not amt: return "-"
        if amt >= 10000:
            eok = amt // 10000
            rest = amt % 10000
            return f"{eok}억 {rest:,}만원" if rest else f"{eok}억"
        return f"{amt:,}만원"
    
    # ────────────────────────────────────────
    # 분기: 그달 '마지막 화요일'=월간종합(월간 시세 리포트), 나머지=월세→전세→매매→월세 롤링
    # (7일 뒤가 다음 달이면 이번 화요일이 그달 마지막 화요일)
    # ────────────────────────────────────────
    is_last_tuesday = (target_date + timedelta(days=7)).month != target_date.month
    if is_last_tuesday:
        # 월간 = 유형별 3편(아파트/빌라/오피스텔) 한 번에 생성 → 화·수·목 분산 발행
        posts = []
        for bt, lbl, slug in [("아파트", "아파트", "monthly_apt"),
                              ("연립다세대", "빌라", "monthly_villa"),
                              ("오피스텔", "오피스텔", "monthly_officetel")]:
            p = _gen_monthly_by_type(report, csv_rows, date_str, target_date, region,
                                     to_int, to_float, fmt_won, bt, lbl, slug)
            p["sub_category"] = "월간 시세 리포트"
            posts.append(p)
        return posts   # ★리스트 반환 (run_for_today에서 분기 처리)
    elif week_of_month == 1:
        post = _gen_monthly_rent(report, csv_rows, date_str, target_date, region, to_int, to_float)
        post["sub_category"] = "월세"
    elif week_of_month == 2:
        post = _gen_jeonse(report, csv_rows, date_str, target_date, region, to_int, to_float, fmt_won)
        post["sub_category"] = "전세"
    elif week_of_month == 3:
        post = _gen_sales(report, csv_rows, date_str, target_date, region, to_int, to_float, fmt_won)
        post["sub_category"] = "매매"
    else:  # 4번째 화요일 (화요일이 5번 있는 달 / 마지막주는 위에서 이미 처리)
        post = _gen_monthly_rent(report, csv_rows, date_str, target_date, region, to_int, to_float)
        post["sub_category"] = "월세"
    return post


def _gen_monthly_rent(report, csv_rows, date_str, target_date, region, to_int, to_float):
    """1주차: 월세 시세표"""
    total_m = report.get("total_monthly", 0)
    
    # 인사이트
    insights = []
    for py, cells in report["matrix"].items():
        valid = [(d, c) for d, c in cells.items() if c]
        if valid:
            min_rent = min(c["avg"] for _, c in valid)
            max_rent = max(c["avg"] for _, c in valid)
            mid_rent = round((min_rent + max_rent) / 2)
            insights.append(f"<strong>{py}</strong>: 월세 <strong>{min_rent}~{max_rent}만원</strong>")
    
    insight_html = "<ul style='margin:0 0 0 18px;line-height:1.9'>" + \
                   "".join(f"<li>{i}</li>" for i in insights[:6]) + "</ul>"
    
    # 평형별 인기 보증금 구간
    popular_html = ""
    popular_items = []
    for py, cells in report["matrix"].items():
        valid_cells = [(d, c) for d, c in cells.items() if c]
        if not valid_cells: continue
        max_cell = max(valid_cells, key=lambda x: x[1].get("count", 0))
        d_label, cell = max_cell
        popular_items.append({
            "pyung": py, "dep": d_label, "rent": cell["avg"], "count": cell["count"],
        })
    
    if popular_items:
        popular_html = "<ul style='margin:0 0 0 18px;line-height:1.9'>"
        for item in popular_items[:5]:
            popular_html += f"<li><strong>{item['pyung']}</strong>: 보증금 {item['dep']} → 월세 <strong style='color:#1565c0'>{item['rent']}만원</strong> ({item['count']}건)</li>"
        popular_html += "</ul>"
    
    intro = f"""
안녕하세요, 렌트체크강서입니다.<br>
"이 월세, 남들도 이 정도 내나?" — 계약 앞두면 제일 궁금한 게 이거잖아요.
그래서 최근 6개월 {region} 월세 신고분 <strong>{total_m}건</strong>을
<strong>평형 × 보증금</strong> 표 하나에 눌러 담았습니다.<br><br>
내 평형 행에서 내 보증금 칸, 딱 하나만 찾으면 됩니다.
📱 표가 옆으로 기니 모바일에선 <strong>옆으로 스크롤</strong>해서 보세요.
"""
    
    body = (
        header_html("💰", "MONTHLY RENT", f"{region} 월세 시세표 ({date_str[:7]})", region, date_str,
                   subtitle="📌 매월 1주차 화요일 · 평형×보증금 매트릭스")
        + section("📋 내 월세, 남들은 얼마 내나", intro + summary_box([
            f"최근 6개월 {region} 월세 신고 <strong>{total_m}건</strong>을 평형×보증금 한 표로",
            "찾는 법: <strong>내 평형 행 × 내 보증금 칸</strong> 하나면 끝",
            "표 값은 대표가·범위 기준 — 평균 안 씁니다",
        ]))
        + render_matrix_table(report)
        + section("📊 평형별로, 얼마부터 얼마까지 냈나", insight_html)
    )
    
    if popular_html:
        body += section("🔥 사람들이 실제로 제일 많이 고른 조건은?", popular_html)
    
    body += section("💬 내 월세 적정한지 확인하는 법",
        "1. 월세 시세표(화요일 글)에서 <strong>본인 평형 행</strong>을 찾으세요<br>"
        "2. <strong>본인 보증금에 가까운 열</strong>을 찾으세요<br>"
        "3. 그 칸의 시세와 <strong>본인 월세 비교</strong><br>"
        "4. 시세보다 10% 이상 비싸면 협상 여지 있음")
    body += render_checker_guide(report)
    body += oneline_box("내 칸 하나 찾으셨으면 끝났습니다 — 그 숫자보다 비싸게 부르면 협상 카드, 근처면 정상가입니다.")
    body += footer_html([region, f"{region}월세", f"{region}월세시세", f"{region}실거래가", "월세시세표", "월세실거래가", "강서구월세", "화곡동부동산", "실거래"])
    
    return {
        "title": f"{region} 월세 실거래가 - {date_str[:4]}년 {date_str[5:7]}월 평형별 보증금 시세표",
        "category": "rent_check",
        "html": body,
        "tags": [region, f"{region}월세", f"{region}월세시세", f"{region}실거래가", "월세시세표", "월세실거래가", "강서구월세", "화곡동부동산", "실거래"],
    }


def _gen_jeonse(report, csv_rows, date_str, target_date, region, to_int, to_float, fmt_won):
    """2주차: 전세 시세표"""
    # CSV에서 전세 거래만 추출
    jeonse_rows = [r for r in csv_rows
                   if "전월세" in str(r.get("deal_type", ""))
                   and to_int(r.get("monthly_rent")) == 0
                   and to_int(r.get("deposit")) > 0]
    
    # 평형별 전세 통계
    by_size = defaultdict(list)
    for r in jeonse_rows:
        area = to_float(r.get("area_m2"))
        if area <= 0: continue
        pyung = round(area / 3.3058)
        if not (6 <= pyung <= 60): continue
        dep = to_int(r.get("deposit"))
        if dep < 1000: continue  # 비정상 제외
        by_size[pyung].append(dep)
    
    # 평형 그룹화 (5평 단위)
    grouped = defaultdict(list)
    for py, deps in by_size.items():
        if py <= 10: key = "10평 이하"
        elif py <= 15: key = "11~15평"
        elif py <= 20: key = "16~20평"
        elif py <= 25: key = "21~25평"
        elif py <= 30: key = "26~30평"
        else: key = "30평 이상"
        grouped[key].extend(deps)
    
    # 시세표 생성
    size_order = ["10평 이하", "11~15평", "16~20평", "21~25평", "26~30평", "30평 이상"]
    rows_html = ""
    for size in size_order:
        if size not in grouped: continue
        deps = grouped[size]
        if len(deps) < 3: continue
        avg = round(mean(deps))
        med = round(median(deps))
        mn = min(deps)
        mx = max(deps)
        rows_html += (
            f'<div style="background:#fff;border:1px solid #e0e0e0;border-left:4px solid {BLUE};'
            f'border-radius:10px;padding:14px 16px;margin-bottom:8px">'
            f'  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
            f'    <div style="font-size:16px;font-weight:700;color:{NAVY}">{size}</div>'
            f'    <div style="font-size:11px;color:#888">{len(deps)}건</div>'
            f'  </div>'
            f'  <div style="font-size:15px"><span style="color:#888">범위</span> <strong style="color:{BLUE}">{fmt_won(mn)} ~ {fmt_won(mx)}</strong></div>'
            f'  <div style="font-size:11px;color:#888;margin-top:4px">중위 {fmt_won(med)}</div>'
            f'</div>'
        )
    
    table = rows_html
    
    total_jeonse = len(jeonse_rows)
    intro = f"""
이번 주는 {region}의 <strong>전세 시세표</strong>를 정리합니다.<br>
최근 6개월간 신고된 전세 실거래 <strong>{total_jeonse}건</strong>을
평형별로 묶어 중위값·범위로 보여드립니다.<br><br>
<strong>중위값(median)</strong>은 이상치(특이 거래)의 영향을 받지 않아
가장 안정적인 시세 기준이 됩니다.
"""
    
    body = (
        header_html("🏠", "JEONSE", f"{region} 전세 시세표 ({date_str[:7]})", region, date_str,
                   subtitle="📌 매월 2주차 화요일 · 평형별 전세금 범위")
        + section("📋 이번 주: 전세 시세표", intro)
        + section("💎 평형별 전세 시세", table)
        + section("💬 전세 시세 어떻게 활용?",
            "1. 본인 평형의 <strong>중위값</strong>을 적정 협상 기준으로 활용<br>"
            "2. 범위(최저~최고)가 넓으면 → 층·향·동에 따라 가격 편차가 크다는 신호<br>"
            "3. <strong>최저~최고 범위가 좁으면</strong> 시세가 명확한 안정 평형<br>"
            "4. 전세보증보험 가입 가능 한도와 비교 (시세 90%까지 보장)")
    )
    body += render_checker_guide(report)
    body += footer_html([region, f"{region}전세", f"{region}전세시세", f"{region}전세가", f"{region}실거래가", "전세시세표", "전세실거래가", "강서구전세", "화곡동부동산"])
    
    return {
        "title": f"{region} 전세 실거래가 - {date_str[:4]}년 {date_str[5:7]}월 평형별 전세금 시세표",
        "category": "rent_check",
        "html": body,
        "tags": [region, f"{region}전세", f"{region}전세시세", f"{region}전세가", f"{region}실거래가", "전세시세표", "전세실거래가", "강서구전세", "화곡동부동산"],
    }


def _gen_sales(report, csv_rows, date_str, target_date, region, to_int, to_float, fmt_won):
    """3주차: 매매 시세표"""
    sales_rows = [r for r in csv_rows
                  if "매매" in str(r.get("deal_type", ""))
                  and to_int(r.get("deal_amount")) > 0]
    
    if len(sales_rows) < 10:
        # 매매 데이터 부족시 fallback
        return _gen_monthly_rent(report, csv_rows, date_str, target_date, region, to_int, to_float)
    
    # 건물 유형별 분리
    by_type = defaultdict(list)
    for r in sales_rows:
        dt = str(r.get("deal_type", ""))
        if "아파트" in dt:
            key = "아파트"
        elif "오피스텔" in dt:
            key = "오피스텔"
        elif "연립" in dt or "다세대" in dt:
            key = "빌라·다세대"
        else:
            key = "기타"
        by_type[key].append(r)
    
    # 각 유형별 평형 시세표 생성
    tables_html = ""
    for type_name in ["아파트", "오피스텔", "빌라·다세대"]:
        if type_name not in by_type: continue
        type_rows = by_type[type_name]
        
        # 평형별 그룹화
        by_size = defaultdict(list)
        for r in type_rows:
            area = to_float(r.get("area_m2"))
            if area <= 0: continue
            pyung = round(area / 3.3058)
            if not (6 <= pyung <= 60): continue
            amt = to_int(r.get("deal_amount"))
            if amt < 1000: continue
            
            if pyung <= 10: key = "10평 이하"
            elif pyung <= 15: key = "11~15평"
            elif pyung <= 20: key = "16~20평"
            elif pyung <= 25: key = "21~25평"
            elif pyung <= 30: key = "26~30평"
            else: key = "30평 이상"
            by_size[key].append(amt)
        
        size_order = ["10평 이하", "11~15평", "16~20평", "21~25평", "26~30평", "30평 이상"]
        rows_html = ""
        for size in size_order:
            if size not in by_size: continue
            amts = by_size[size]
            if len(amts) < 2: continue
            avg = round(mean(amts))
            med = round(median(amts))
            mn = min(amts)
            mx = max(amts)
            rows_html += (
                f'<div style="background:#fff;border:1px solid #e0e0e0;border-left:4px solid {BLUE};'
                f'border-radius:10px;padding:14px 16px;margin-bottom:8px">'
                f'  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
                f'    <div style="font-size:16px;font-weight:700;color:{NAVY}">{size}</div>'
                f'    <div style="font-size:11px;color:#888">{len(amts)}건</div>'
                f'  </div>'
                f'  <div style="font-size:15px"><span style="color:#888">범위</span> <strong style="color:{BLUE}">{fmt_won(mn)} ~ {fmt_won(mx)}</strong></div>'
                f'  <div style="font-size:11px;color:#888;margin-top:4px">중위 {fmt_won(med)}</div>'
                f'</div>'
            )
        
        if rows_html:
            tables_html += f'<h3 style="margin:24px 0 10px;color:{NAVY};font-size:16px">🏢 {type_name} 매매 시세</h3>{rows_html}'
    
    total_sales = len(sales_rows)
    intro = f"""
이번 주는 {region}의 <strong>매매 시세표</strong>를 정리합니다.<br>
최근 6개월간 신고된 매매 실거래 <strong>{total_sales}건</strong>을
<strong>건물 유형(아파트·오피스텔·빌라) × 평형</strong>으로 분류했습니다.<br><br>
호가가 아닌 <strong>실제 거래된 가격</strong> 기준이라 가장 정확한 시세 자료입니다.
"""
    
    body = (
        header_html("💎", "SALES", f"{region} 매매 시세표 ({date_str[:7]})", region, date_str,
                   subtitle="📌 매월 3주차 화요일 · 건물유형×평형 매매가")
        + section("📋 이번 주: 매매 시세표", intro)
        + section("📊 건물 유형별 매매 시세", tables_html)
        + section("💬 매매 시세 어떻게 활용?",
            "1. 매수 검토 중이라면 본인 관심 단지의 <strong>건물유형·평형</strong>을 확인<br>"
            "2. 표의 중위값·범위와 호가를 비교 → 호가가 중위(시세)보다 10% 이상 높으면 협상 여지<br>"
            "3. <strong>최저가</strong>는 저층·구축·옵션 부재 등 특수 조건일 가능성<br>"
            "4. 거래수가 많은 평형이 시세가 안정적")
    )
    body += render_checker_guide(report)
    body += footer_html([region, f"{region}매매", f"{region}매매시세", f"{region}매매가", f"{region}실거래가", "매매시세표", "아파트매매가", "오피스텔매매", "강서구부동산", "화곡동아파트"])
    
    return {
        "title": f"{region} 매매 실거래가 - {date_str[:4]}년 {date_str[5:7]}월 아파트·오피스텔 매매가",
        "category": "rent_check",
        "html": body,
        "tags": [region, f"{region}매매", f"{region}매매시세", f"{region}매매가", f"{region}실거래가", "매매시세표", "아파트매매가", "오피스텔매매", "강서구부동산", "화곡동아파트"],
    }


def _gen_market_summary(report, csv_rows, date_str, target_date, region, to_int, to_float, fmt_won):
    """4주차: 시세 종합 리포트"""
    sales_rows = [r for r in csv_rows if "매매" in str(r.get("deal_type", ""))
                  and to_int(r.get("deal_amount")) > 0]
    jeonse_rows = [r for r in csv_rows if "전월세" in str(r.get("deal_type", ""))
                   and to_int(r.get("monthly_rent")) == 0
                   and to_int(r.get("deposit")) > 0]
    monthly_rows = [r for r in csv_rows if "전월세" in str(r.get("deal_type", ""))
                    and to_int(r.get("monthly_rent")) > 0]
    
    # 통계
    sales_amts = [to_int(r.get("deal_amount")) for r in sales_rows]
    jeonse_amts = [to_int(r.get("deposit")) for r in jeonse_rows]
    monthly_rents = [to_int(r.get("monthly_rent")) for r in monthly_rows]
    monthly_hwan = [to_int(r.get("monthly_rent")) + to_int(r.get("deposit")) * 0.045 / 12 for r in monthly_rows]
    def _won_s(a):
        if not a: return "0"
        return (f"{a//10000}억" + (f" {a%10000:,}만" if a % 10000 else "")) if a >= 10000 else f"{a:,}만"
    monthly_rep = None
    if monthly_hwan:
        _mi = sorted(range(len(monthly_hwan)), key=lambda i: monthly_hwan[i])[len(monthly_hwan) // 2]
        monthly_rep = (to_int(monthly_rows[_mi].get("deposit")), to_int(monthly_rows[_mi].get("monthly_rent")))
    _rs = sorted(monthly_rents)
    if len(_rs) >= 4:
        _lo = _rs[len(_rs) // 4]; _hi = _rs[len(_rs) * 3 // 4]
        wol_val_html = f'<span style="color:#888">월</span> <strong style="color:#ef476f">{_lo}~{_hi}만</strong> <span style="color:#888;font-size:12px">(보증금·크기에 따라)</span>'
    elif _rs:
        wol_val_html = f'<span style="color:#888">월</span> <strong style="color:#ef476f">{round(median(_rs))}만</strong>'
    else:
        wol_val_html = '<strong style="color:#ef476f">-</strong>'
    
    summary_html = (
        # 매매
        f'<div style="background:#fff;border:1px solid #e0e0e0;border-left:4px solid {BLUE};'
        f'border-radius:10px;padding:14px 16px;margin-bottom:8px">'
        f'  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
        f'    <div style="font-size:16px;font-weight:700;color:{NAVY}">💎 매매</div>'
        f'    <div style="font-size:11px;color:#888">{len(sales_rows):,}건</div>'
        f'  </div>'
        f'  <div style="font-size:15px"><span style="color:#888">중위</span> <strong style="color:{BLUE}">{fmt_won(round(median(sales_amts))) if sales_amts else "-"}</strong></div>'
        f'</div>'
        # 전세
        f'<div style="background:#fff;border:1px solid #e0e0e0;border-left:4px solid #06d6a0;'
        f'border-radius:10px;padding:14px 16px;margin-bottom:8px">'
        f'  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
        f'    <div style="font-size:16px;font-weight:700;color:{NAVY}">🏠 전세</div>'
        f'    <div style="font-size:11px;color:#888">{len(jeonse_rows):,}건</div>'
        f'  </div>'
        f'  <div style="font-size:15px"><span style="color:#888">중위</span> <strong style="color:#06d6a0">{fmt_won(round(median(jeonse_amts))) if jeonse_amts else "-"}</strong></div>'
        f'</div>'
        # 월세
        f'<div style="background:#fff;border:1px solid #e0e0e0;border-left:4px solid #ef476f;'
        f'border-radius:10px;padding:14px 16px;margin-bottom:8px">'
        f'  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
        f'    <div style="font-size:16px;font-weight:700;color:{NAVY}">💰 월세</div>'
        f'    <div style="font-size:11px;color:#888">{len(monthly_rows):,}건</div>'
        f'  </div>'
        f'  <div style="font-size:15px">{wol_val_html}</div>'
        f'</div>'
    )
    
    # 인사이트
    insights = []
    total = len(sales_rows) + len(jeonse_rows) + len(monthly_rows)
    if total > 0:
        s_pct = round(len(sales_rows) / total * 100, 1)
        j_pct = round(len(jeonse_rows) / total * 100, 1)
        m_pct = round(len(monthly_rows) / total * 100, 1)
        insights.append(f"거래 비중: 매매 <strong>{s_pct}%</strong> · 전세 <strong>{j_pct}%</strong> · 월세 <strong>{m_pct}%</strong>")
        
        if m_pct > 50:
            insights.append(f"<strong>월세 거래가 절반 넘음</strong> — 1인가구·단기 임차 수요 강세")
        elif s_pct > 30:
            insights.append(f"<strong>매매 비중 30% 이상</strong> — 투자·실수요 활발한 시장")
        else:
            insights.append(f"<strong>전월세 중심 시장</strong> — 거주 수요 안정")
    
    if jeonse_amts and monthly_rents:
        # 전세↔월세 환산 비교
        avg_j = mean(jeonse_amts)
        avg_m = mean(monthly_rents)
        converted = avg_j * 0.045 / 12
        if converted > avg_m * 1.1:
            insights.append(f"전세 → 월세 환산 시 <strong>월세가 약 {round(converted - avg_m)}만원 저렴</strong> → 월세 유리 시장")
        elif converted < avg_m * 0.9:
            insights.append(f"전세 → 월세 환산 시 <strong>전세가 약 {round(avg_m - converted)}만원 저렴</strong> → 전세 유리 시장")
        else:
            insights.append(f"전세와 월세 가격 균형 — 본인 자금 사정으로 선택")
    
    insight_html = "<ul style='margin:0 0 0 18px;line-height:1.9'>" + \
                   "".join(f"<li>{i}</li>" for i in insights) + "</ul>"
    
    intro = f"""
이번 달은 {region}의 <strong>매매·전세·월세 시세 종합 리포트</strong>입니다.<br>
한 달 간 발행된 시세표를 종합해, {region}의 현재 시장 상황을 한눈에 보여드립니다.<br><br>
어떤 유형 거래가 활발한지, 어느 쪽이 가격적으로 유리한지 큰 그림으로 정리합니다.
"""
    
    body = (
        header_html("📊", "MARKET SUMMARY", f"{region} 시세 종합 리포트 ({date_str[:7]})", region, date_str,
                   subtitle="📌 매월 마지막 화요일 · 매매·전세·월세 월간 종합")
        + section("📋 이번 달: 시세 종합", intro)
        + section("💎 거래 유형별 종합 시세", summary_html)
        + section("🔥 이번 달 시장 인사이트", insight_html)
        + section("💬 종합 리포트 활용법",
            "1. <strong>본인 자금 상황과 거래 비중</strong>을 비교 → 시장이 본인 의도와 맞는지 확인<br>"
            "2. <strong>매매↔전세↔월세 가격 균형</strong>을 보고 어느 쪽이 유리한지 판단<br>"
            "3. 매주 화요일 시세표를 한 달간 모으면 본인 조건에 맞는 정보 누적<br>"
            "4. 월간 종합 리포트는 <strong>월간 시장 흐름</strong> 파악용")
    )
    body += render_checker_guide(report)
    body += footer_html([region, f"{region}시세", f"{region}실거래가", f"{region}매매가", f"{region}전세", f"{region}월세", "시세종합", "월간리포트", "화곡동부동산", "강서구화곡동"])
    
    return {
        "title": f"{region} 실거래가 종합 - {date_str[:4]}년 {date_str[5:7]}월 매매·전세·월세 시세",
        "category": "rent_check",
        "html": body,
        "tags": [region, f"{region}시세", f"{region}실거래가", f"{region}매매가", f"{region}전세", f"{region}월세", "시세종합", "월간리포트", "화곡동부동산", "강서구화곡동"],
    }


def _gen_monthly_by_type(report, csv_rows, date_str, target_date, region,
                          to_int, to_float, fmt_won, building_type, type_label, slug):
    """월간 유형별 리포트 (아파트/빌라/오피스텔). 마지막 화요일에 3편 중 1편 생성."""
    from statistics import median as _median

    # ── 유형 필터 ──
    trows = [r for r in csv_rows if building_type in str(r.get("deal_type", ""))]
    sale = [r for r in trows if "매매" in str(r.get("deal_type", "")) and to_int(r.get("deal_amount")) > 0]
    jeon = [r for r in trows if "전월세" in str(r.get("deal_type", "")) and to_int(r.get("monthly_rent")) == 0 and to_int(r.get("deposit")) > 0]
    wol  = [r for r in trows if "전월세" in str(r.get("deal_type", "")) and to_int(r.get("monthly_rent")) > 0]

    # ── 면적 구간 (유형별로 다름) ──
    if building_type == "아파트":
        bands = [("60㎡ 이하", 0, 60), ("60~85㎡", 60, 85), ("85㎡ 이상", 85, 99999)]
    elif building_type == "오피스텔":
        bands = [("20㎡ 이하", 0, 20), ("20~33㎡", 20, 33), ("33㎡ 이상", 33, 99999)]
    else:  # 연립다세대(빌라)
        bands = [("33㎡ 이하", 0, 33), ("33~50㎡", 33, 50), ("50㎡ 이상", 50, 99999)]

    def in_band(m2, lo, hi):
        return lo <= m2 < hi

    def med(lst):
        s = sorted(lst)
        return s[len(s) // 2] if s else 0

    def mid50(lst):
        s = sorted(lst)
        if len(s) < 4:
            return (s[0], s[-1]) if s else (0, 0)
        return (s[len(s) // 4], s[len(s) * 3 // 4])

    # 색
    NAVY = "#0d1f3c"; BLUE = "#1565c0"

    def _th(t):
        return f'<th style="padding:10px 8px;background:#e8edf5;color:#0d1f3c;font-weight:800;border:1px solid #9aa7bd;border-bottom:2px solid #0d1f3c;text-align:left">{t}</th>'

    def _td(t, bold=False, color="#1c2733", size=14):
        w = "700" if bold else "400"
        return f'<td style="padding:9px 8px;border:1px solid #9aa7bd;text-align:center;font-weight:{w};color:{color};font-size:{size}px">{t}</td>'

    # ── 매매·전세 공통 면적대 표 (단일 금액) ──
    def amount_band_table(rows, amt_key, value_color):
        body = ""
        for name, lo, hi in bands:
            amts = [to_int(r.get(amt_key)) for r in rows
                    if in_band(to_float(r.get("area_m2")), lo, hi) and to_int(r.get(amt_key)) > 0]
            if not amts:
                body += f'<tr>{_td(name, bold=True, color=NAVY)}<td colspan="3" style="padding:9px 8px;border:1px solid #9aa7bd;text-align:center;color:#999;font-size:13px">거래 없음</td></tr>'
                continue
            m = med(amts); lo50, hi50 = mid50(amts)
            tag = "" if len(amts) >= 7 else ' <span style="font-size:11px;color:#c4661f">(표본 적음)</span>'
            body += (f'<tr>{_td(name, bold=True, color=NAVY)}{_td(f"{len(amts)}건{tag}")}'
                     f'{_td(fmt_won(m), bold=True, color=value_color)}'
                     f'{_td(f"{fmt_won(lo50)}~{fmt_won(hi50)}", color="#666", size=13)}</tr>')
        return (f'<table style="width:100%;border-collapse:collapse;font-size:14px;margin:6px 0">'
                f'<tr>{_th("면적대")}{_th("건수")}{_th("중위값")}{_th("중간 50% 구간")}</tr>'
                f'{body}</table>')

    # ── 월세 면적대 표 (보증금+월세 둘) ──
    def wolse_band_table():
        body = ""
        for name, lo, hi in bands:
            brows = [r for r in wol if in_band(to_float(r.get("area_m2")), lo, hi) and to_int(r.get("monthly_rent")) > 0]
            if not brows:
                body += f'<tr>{_td(name, bold=True, color=NAVY)}<td colspan="3" style="padding:9px 8px;border:1px solid #9aa7bd;text-align:center;color:#999;font-size:13px">거래 없음</td></tr>'
                continue
            deps = [to_int(r.get("deposit")) for r in brows]
            rents = [to_int(r.get("monthly_rent")) for r in brows]
            md = med(deps); mr = med(rents)
            tag = "" if len(brows) >= 7 else ' <span style="font-size:11px;color:#c4661f">(표본 적음)</span>'
            body += (f'<tr>{_td(name, bold=True, color=NAVY)}{_td(f"{len(brows)}건{tag}")}'
                     f'{_td(fmt_won(md))}'
                     f'{_td(f"월 {mr}만", bold=True, color="#c4661f")}</tr>')
        return (f'<table style="width:100%;border-collapse:collapse;font-size:14px;margin:6px 0">'
                f'<tr>{_th("면적대")}{_th("건수")}{_th("중위 보증금")}{_th("중위 월세")}</tr>'
                f'{body}</table>')

    # ── (아파트 전용) 주력 단지 84㎡급 표 ──
    # CSV 등록명 → 블로그 표시명 교정 (데이터는 그대로, 표시만 정리)
    NAME_DISPLAY = {
        "우장산아이파크,이편한세상": "우장산아이파크이편한세상",
        "초록": "화곡초록아파트", "화곡초록": "화곡초록아파트", "초록아파트": "화곡초록아파트",
        "화곡대림": "화곡대림아파트", "대림아파트": "화곡대림아파트",
        "화곡중앙하이츠": "중앙하이츠빌",
        "화곡푸르지오": "화곡프루지오",
    }
    def disp_name(nm):
        s = re.sub(r'\s*\d{3}동\s*$', '', str(nm).strip())
        s = re.sub(r'\s*\(\d{3,4}-?\d*\)\s*', '', s).strip()
        return NAME_DISPLAY.get(s, s)

    def apt_complex_table():
        groups = {}
        for r in sale:
            m2 = to_float(r.get("area_m2"))
            if not (80 <= m2 < 90):  # 84㎡급
                continue
            nm = disp_name(r.get("building_name", ""))
            if not nm:
                continue
            groups.setdefault(nm, []).append(to_int(r.get("deal_amount")))
        # 건수 많은 순 상위 6
        ranked = sorted([(nm, v) for nm, v in groups.items() if len(v) >= 2],
                        key=lambda x: -len(x[1]))[:6]
        if not ranked:
            return ""
        body = ""
        for nm, amts in ranked:
            m = med([a for a in amts if a > 0])
            body += (f'<tr>{_td(nm, bold=True, color=NAVY, size=13)}{_td(f"{len(amts)}건")}'
                     f'{_td(fmt_won(m), bold=True, color=BLUE)}</tr>')
        return (f'<table style="width:100%;border-collapse:collapse;font-size:14px;margin:6px 0">'
                f'<tr>{_th("단지")}{_th("84㎡급 건수")}{_th("매매 중위")}</tr>'
                f'{body}</table>')

    # ── 거래 요약 카드 ──
    sale_amts = [to_int(r.get("deal_amount")) for r in sale if to_int(r.get("deal_amount")) > 0]
    jeon_amts = [to_int(r.get("deposit")) for r in jeon if to_int(r.get("deposit")) > 0]
    wol_rents = [to_int(r.get("monthly_rent")) for r in wol if to_int(r.get("monthly_rent")) > 0]
    wol_deps  = [to_int(r.get("deposit")) for r in wol]

    sale_med = fmt_won(med(sale_amts)) if sale_amts else "-"
    jeon_med = fmt_won(med(jeon_amts)) if jeon_amts else "-"
    wol_rent_med = f"월 {med(wol_rents)}만" if wol_rents else "-"
    wol_dep_med = fmt_won(med(wol_deps)) if wol_deps else "-"

    summary_html = (
        f'<div style="background:#fff;border:2px solid {BLUE};border-radius:12px;padding:16px 18px;margin:6px 0">'
        f'<div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #e5e5e5">'
        f'<span style="font-weight:700;color:{NAVY}">💎 매매</span>'
        f'<span><span style="color:#888">{len(sale)}건 · 중위 </span><strong style="color:{BLUE}">{sale_med}</strong></span></div>'
        f'<div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #e5e5e5">'
        f'<span style="font-weight:700;color:{NAVY}">🏠 전세</span>'
        f'<span><span style="color:#888">{len(jeon)}건 · 중위 </span><strong style="color:#2e7d32">{jeon_med}</strong></span></div>'
        f'<div style="display:flex;justify-content:space-between;padding:6px 0">'
        f'<span style="font-weight:700;color:{NAVY}">💰 월세</span>'
        f'<span><span style="color:#888">{len(wol)}건 · 보증 {wol_dep_med} / </span><strong style="color:#c4661f">{wol_rent_med}</strong></span></div>'
        f'</div>'
    )

    # ── 손맛 도입 (유형별 톤) ──
    intros = {
        "아파트": (f"화곡동에서 아파트는 단지마다 가격대가 크게 갈립니다. 우장산 자락 대단지는 84㎡급이 14억~15억을 넘기도 하고, "
                 f"구축 소형은 같은 동네에서도 한참 아래입니다. 그래서 \"화곡동 아파트 평균\" 하나로 보면 판단이 흐려집니다. "
                 f"이번 달 실거래를 면적대와 주력 단지로 나눠 정리했습니다."),
        "빌라":  (f"화곡동은 서울에서도 손꼽히는 빌라 밀집 지역입니다. 이번 달만 봐도 빌라 거래가 가장 많았습니다. "
                 f"빌라는 같은 평형이라도 신축인지 구축인지, 어느 골목인지에 따라 값이 크게 달라집니다. "
                 f"단지 이름보다 면적대와 보증금 구조로 보는 게 현실에 맞습니다."),
        "오피스텔": (f"화곡동 오피스텔은 매매보다 임대(월세) 위주로 돕니다. 1인가구 원룸 수요가 많아 거래도 소형에 몰립니다. "
                  f"그래서 매매 시세보다는 보증금·월세 조건을 면적대별로 보는 게 실제 판단에 더 도움이 됩니다."),
    }
    intro_html = f'<div style="font-size:16px;line-height:1.85">{intros.get(type_label, "")}</div>'

    # ── 인사이트 ──
    insights = []
    tot = len(sale) + len(jeon) + len(wol)
    if tot:
        insights.append(f"이번 달 {type_label} 거래: 매매 <strong>{len(sale)}건</strong> · 전세 <strong>{len(jeon)}건</strong> · 월세 <strong>{len(wol)}건</strong>")
    # 전세가율은 아파트만 (오피·빌라는 매매 표본이 얇거나 전세>매매라 왜곡)
    if building_type == "아파트" and sale_amts and jeon_amts:
        ratio = round(med(jeon_amts) / med(sale_amts) * 100)
        insights.append(f"단순 전세가율(전세 중위 ÷ 매매 중위): 약 <strong>{ratio}%</strong>")
    if building_type == "오피스텔" and len(wol) > len(sale) * 3:
        insights.append("매매보다 <strong>월세 거래가 압도적</strong> — 임대(원룸) 수요 중심 시장")
    if building_type == "오피스텔" and sale_amts and jeon_amts and med(jeon_amts) > med(sale_amts):
        insights.append("전세 중위가 매매 중위보다 높게 보이는 건 <strong>매매가 소형(원룸)에 몰린</strong> 반면 전세엔 큰 평형도 섞였기 때문입니다")
    insight_html = "<ul style='margin:0 0 0 18px;line-height:1.9'>" + "".join(f"<li>{i}</li>" for i in insights) + "</ul>"

    # ── 본문 조립 ──
    ym = f"{date_str[:4]}년 {date_str[5:7]}월"
    body = header_html("📊", "MONTHLY REPORT", f"{region} {type_label} 월간 시세 ({date_str[:7]})", region, date_str,
                       subtitle=f"📌 매월 마지막 화요일 · {type_label} 매매·전세·월세")
    body += section(f"📋 이번 달 {region} {type_label} 시장", intro_html)
    body += section("💎 거래 요약", summary_html)
    body += section("📊 면적대별 매매 시세", amount_band_table(sale, "deal_amount", BLUE))
    if building_type == "아파트":
        ct = apt_complex_table()
        if ct:
            body += section("🏢 주력 단지 84㎡급 매매", ct + '<div style="font-size:12px;color:#888;margin-top:4px">※ 84㎡급(전용 80~90㎡) 기준, 거래 2건 이상 단지</div>')
    body += section("🏠 면적대별 전세 시세", amount_band_table(jeon, "deposit", "#2e7d32"))
    body += section("💰 면적대별 월세 시세", wolse_band_table()
                    + '<div style="font-size:12px;color:#888;margin-top:4px">※ 월세는 보증금에 따라 크게 달라집니다. 같은 면적이라도 보증금이 높으면 월세가 낮아집니다.</div>')
    body += section("🔥 이번 달 인사이트", insight_html)

    # 면책 + 출처
    body += ('<div style="border:2px solid #d4a73a;border-radius:12px;padding:14px 16px;margin:14px 0;font-size:13px;color:#6b5a32;line-height:1.7">'
             '📌 <strong>데이터 출처</strong>: 국토교통부 실거래가 공개시스템<br>'
             '📌 <strong>정리 방식</strong>: 동일 계약일·단지·면적·층·금액이 같은 중복은 1건으로 정리, 중위값 기준<br>'
             '📌 가장 최근 월은 신고 지연으로 일부 거래가 추가 반영될 수 있습니다. 개별 매물은 층·향·수리 상태에 따라 다릅니다.</div>')

    # CTA (시세류 → rent-check)
    body += ('<div style="margin:18px 0;padding:16px 18px;border:2px solid #1565c0;border-radius:12px">'
             '<div style="font-weight:700;color:#0d1f3c;margin-bottom:8px">🔍 내 계약, 적정한가요?</div>'
             '<div style="font-size:15px;color:#1c2733;line-height:1.75">단지명·평형·보증금(월세)을 넣으면 실거래 중위와의 격차가 바로 나옵니다. (국토부 실거래 기반·무료)<br>'
             '<a href="https://arttoy61-png.github.io/rent-check/" style="color:#1565c0;font-weight:800;font-size:16px;text-decoration:none">▶ 내 시세 확인하러 가기 →</a></div></div>')

    tags = [region, f"{region}{type_label}", f"{region}{type_label}시세", f"{region}실거래가",
            f"{region}{type_label}매매", f"{region}{type_label}전세", f"{region}{type_label}월세",
            "월간시세리포트", f"{region}시세", "강서구화곡동", f"강서구{type_label}"]

    return {
        "title": f"{region} {type_label} 월간 시세 리포트 {ym}｜매매·전세·월세 한눈에",
        "category": slug,
        "html": body,
        "tags": tags,
    }



def gen_building_spotlight(report: dict, csv_rows: list, date_str: str) -> dict:
    """수요일: 단지별 시세 분석 (300세대 이상 아파트 9개 가나다순 로테이션)"""
    region = report["region"]
    target_date = datetime.strptime(date_str, "%Y-%m-%d")
    
    # ────────────────────────────────────────
    # 1. 화곡동 300세대 이상 아파트 9개 풀 (가나다순)
    # ────────────────────────────────────────
    # 각 단지: (정식 표시명, CSV 데이터에 들어있을 수 있는 이름들)
    APT_300_POOL = [
        {
            "display": "강서금호어울림퍼스티어",
            "aliases": ["강서금호어울림퍼스티어"],
            "zone": "kkachisan",
            "households": 487,
        },
        {
            "display": "강서힐스테이트",
            "aliases": ["강서힐스테이트"],
            "zone": "ujangsan",
            "households": 2603,
        },
        {
            "display": "우장산롯데캐슬",
            "aliases": ["우장산롯데캐슬"],
            "zone": "ujangsan",
            "households": 1164,
        },
        {
            "display": "우장산숲아이파크",
            "aliases": ["우장산숲아이파크"],
            "zone": "ujangsan",
            "households": 576,
        },
        {
            "display": "우장산아이파크이편한세상",
            "aliases": ["우장산아이파크,이편한세상", "우장산아이파크이편한세상"],
            "zone": "ujangsan",
            "households": 2517,
        },
        {
            "display": "중앙하이츠빌",
            "aliases": ["중앙하이츠빌", "화곡중앙하이츠"],
            "zone": "hwagok",
            "households": 473,
        },
        {
            "display": "화곡대림아파트",
            "aliases": ["화곡대림", "대림아파트"],
            "zone": "hwagok",
            "households": 416,
        },
        {
            "display": "화곡초록아파트",
            "aliases": ["화곡초록", "초록아파트"],
            "zone": "hwagok",
            "households": 625,
        },
        {
            "display": "화곡프루지오",
            "aliases": ["화곡프루지오", "화곡푸르지오"],
            "zone": "ujangsan",  # 발산-우장산 사이, 우장산 권역으로 분류
            "households": 2176,
        },
    ]
    
    ZONE_NAME = {
        "ujangsan": "우장산",
        "kkachisan": "까치산",
        "hwagok": "화곡",
        "balsan": "발산",
        "non_station": "화곡본동",
    }
    
    def normalize_bldg(name):
        if not name: return None
        s = str(name).strip()
        s = re.sub(r'\s*\d{3}동\s*$', '', s)
        s = re.sub(r'\s*\(\d{3,4}-?\d*\)\s*', '', s)
        return s.strip()
    
    # ────────────────────────────────────────
    # 2. 각 단지별 거래 데이터 그룹화 (alias 매칭)
    # ────────────────────────────────────────
    apt_data = {}  # display_name → rows
    for apt in APT_300_POOL:
        matched_rows = []
        for r in csv_rows:
            bldg = str(r.get("building_name", "") or "").strip()
            if not bldg: continue
            norm = normalize_bldg(bldg)
            # alias 중 하나라도 매칭되면 포함
            for alias in apt["aliases"]:
                if alias in bldg or alias in norm:
                    matched_rows.append(r)
                    break
        apt_data[apt["display"]] = matched_rows
    
    # ────────────────────────────────────────
    # 3. 데이터 충분한 단지만 후보 (3건 이상)
    # ────────────────────────────────────────
    valid_pool = [apt for apt in APT_300_POOL if len(apt_data[apt["display"]]) >= 3]
    
    if not valid_pool:
        return {
            "title": f"{region} 단지 분석 ({date_str})",
            "category": "building_spotlight",
            "html": "<p>이번 주는 매핑된 단지의 실거래 데이터가 부족해 글을 생성할 수 없습니다.</p>",
            "tags": [region],
            "sub_category": "단지별 시세 분석",
            "zone": "",
            "building_name": "",
        }
    
    # ────────────────────────────────────────
    # 4. 오늘의 단지 선택 (날짜 기반 로테이션, 가나다순)
    # ────────────────────────────────────────
    # 수요일은 주(week)당 1번 발행이므로 week 기반 로테이션 사용
    # (day_of_year를 쓰면 매주 같은 단지만 나옴 - 7일 간격 = 같은 mod 결과)
    week_of_year = target_date.isocalendar()[1]
    today_apt = valid_pool[week_of_year % len(valid_pool)]
    target_name = today_apt["display"]
    target_zone = today_apt["zone"]
    target_rows = apt_data[target_name]
    target_households = today_apt["households"]
    
    # 비교용 권역 내 단지 (같은 권역의 다른 풀 단지들)
    zone_buildings_in_pool = [apt for apt in valid_pool 
                              if apt["zone"] == target_zone]
    
    # ────────────────────────────────────────
    # 3. 평형별 통계 (매매/전세/월세)
    # ────────────────────────────────────────
    def to_int(v):
        if v is None or v == "": return 0
        try: return int(str(v).replace(",", ""))
        except: return 0
    
    def to_float(v):
        if v is None or v == "": return 0.0
        try: return float(v)
        except: return 0.0
    
    def fmt_won(amt):
        if not amt: return "-"
        if amt >= 10000:
            eok = amt // 10000
            rest = amt % 10000
            return f"{eok}억 {rest:,}만원" if rest else f"{eok}억"
        return f"{amt:,}만원"
    
    by_size = defaultdict(lambda: {"sales": [], "jeonse": [], 
                                     "monthly_dep": [], "monthly_rent": [], "monthly_hwan": [], "areas": []})
    by_type_count = defaultdict(int)
    build_year = None
    
    for r in target_rows:
        dt = str(r.get("deal_type", ""))
        by_type_count[dt] += 1
        
        area = to_float(r.get("area_m2"))
        if area <= 0: continue
        pyung = round(area / 3.3058)
        if not (6 <= pyung <= 80): continue
        py_key = f"{pyung}평"
        by_size[py_key]["areas"].append(area)
        
        if "매매" in dt:
            amt = to_int(r.get("deal_amount"))
            if amt > 0:
                by_size[py_key]["sales"].append(amt)
        elif "전월세" in dt:
            dep = to_int(r.get("deposit"))
            rent = to_int(r.get("monthly_rent"))
            if rent > 0:
                by_size[py_key]["monthly_dep"].append(dep)
                by_size[py_key]["monthly_rent"].append(rent)
                by_size[py_key]["monthly_hwan"].append(rent + dep * 0.045 / 12)
            elif dep > 0:
                by_size[py_key]["jeonse"].append(dep)
        
        if not build_year:
            by = to_int(r.get("build_year"))
            if 1980 < by < 2030:
                build_year = by
    
    # 통계 정리
    size_stats = {}
    for py, data in by_size.items():
        stat = {}
        for k, vals in data.items():
            if vals:
                stat[k] = {
                    "count": len(vals),
                    "avg": round(mean(vals)),
                    "min": min(vals),
                    "max": max(vals),
                    "median": round(median(vals)),
                }
        if stat:
            size_stats[py] = stat
            _d = data["monthly_dep"]; _rt = data["monthly_rent"]
            if _rt:
                _bands = [round(_d[i] / 5000) * 5000 for i in range(len(_rt))]
                _bc = {}
                for _b in _bands:
                    _bc[_b] = _bc.get(_b, 0) + 1
                _common = max(_bc, key=_bc.get)
                _rin = [_rt[i] for i in range(len(_rt)) if _bands[i] == _common]
                size_stats[py]["monthly_rep"] = {"dep": _common, "rent": round(median(_rin)), "n": len(_rt)}

    size_m2 = {}
    for _py, _data in by_size.items():
        _ars = [round(a, 2) for a in _data.get("areas", []) if a > 0]
        if _ars:
            size_m2[_py] = max(set(_ars), key=_ars.count)
    def size_label(sz):
        m = size_m2.get(sz)
        return f"전용 {m:g}㎡({sz})" if m else sz
    
    # ────────────────────────────────────────
    # 4. 권역 내 단지 비교 (매매가 기준) - 9개 풀 중 같은 권역
    # ────────────────────────────────────────
    comparisons = []
    for apt in zone_buildings_in_pool:
        apt_rows = apt_data[apt["display"]]
        sales = [to_int(r.get("deal_amount")) for r in apt_rows
                if "매매" in str(r.get("deal_type", "")) and to_int(r.get("deal_amount")) > 0]
        if not sales: continue
        comparisons.append({
            "name": apt["display"],
            "avg": round(mean(sales)),
            "count": len(sales),
            "is_self": apt["display"] == target_name,
        })
    
    if not any(c["is_self"] for c in comparisons):
        sales = [to_int(r.get("deal_amount")) for r in target_rows
                if "매매" in str(r.get("deal_type", "")) and to_int(r.get("deal_amount")) > 0]
        if sales:
            comparisons.append({"name": target_name, "avg": round(mean(sales)),
                              "count": len(sales), "is_self": True})
    
    comparisons.sort(key=lambda x: x["avg"])
    self_avg = next((c["avg"] for c in comparisons if c["is_self"]), 0)
    
    # 기준 대비 % 계산
    for c in comparisons:
        if self_avg > 0 and not c["is_self"]:
            pct = (c["avg"] - self_avg) / self_avg * 100
            if abs(pct) < 1:
                c["diff"], c["diff_color"] = "기준선", "#666"
            elif pct > 0:
                c["diff"], c["diff_color"] = f"+{pct:.1f}%", "#ef476f"
            else:
                c["diff"], c["diff_color"] = f"{pct:.1f}%", "#06d6a0"
        else:
            c["diff"], c["diff_color"] = "기준", "#d4af37"
    
    # ────────────────────────────────────────
    # 5. HTML 본문 조립
    # ────────────────────────────────────────
    sales_count = sum(v for k, v in by_type_count.items() if "매매" in k)
    rent_count = sum(v for k, v in by_type_count.items() if "전월세" in k)
    zone_full = ZONE_NAME.get(target_zone, "화곡동")
    total_count = len(target_rows)
    
    # 도입부 (SEO 키워드 자연 삽입: 실거래가·매매가·전세·월세)
    intro = f"""
안녕하세요, 렌트체크강서입니다.<br>
이번 주 수요일 단지는 <strong>「{target_name}」</strong>입니다.
{zone_full}의 <strong>{target_households:,}세대</strong> 대단지인데,
최근 6개월 국토부 신고만 <strong style="color:{BLUE}">{total_count}건</strong>(매매 {sales_count}·전월세 {rent_count})이 쌓였어요.<br><br>
호가는 어디까지나 부르는 값이고 — 아래는 <strong>실제로 돈이 오간 가격</strong>만 모았습니다.
{target_name} 매매·전세·월세, 평형별로 보시죠.
"""
    intro += summary_box([
        f"<strong>{target_name}</strong> 최근 6개월 신고 <strong>{total_count}건</strong> (매매 {sales_count} · 전월세 {rent_count})",
        "순서: 매매 → 전세 → 월세, 평형별 표",
        "표시는 중위·범위 — 호가·평균 아님",
    ])
    
    # 평형별 매매 시세표
    sales_table = ""
    if any("sales" in s for s in size_stats.values()):
        rows_html = ""
        for size in sorted(size_stats.keys(), key=lambda x: int(x.replace("평", ""))):
            s = size_stats[size]
            if "sales" not in s: continue
            sd = s["sales"]
            rows_html += (
                f'<div style="background:#fff;border:1px solid #e0e0e0;border-left:4px solid {BLUE};'
                f'border-radius:10px;padding:14px 16px;margin-bottom:8px">'
                f'  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
                f'    <div style="font-size:16px;font-weight:700;color:{NAVY}">{size_label(size)}</div>'
                f'    <div style="font-size:11px;color:#888">{sd["count"]}건</div>'
                f'  </div>'
                f'  <div style="font-size:15px"><span style="color:#888">범위</span> <strong style="color:{BLUE}">{fmt_won(sd["min"])} ~ {fmt_won(sd["max"])}</strong></div>'
                f'  <div style="font-size:11px;color:#888;margin-top:4px">중위 {fmt_won(sd["median"])}</div>'
                f'</div>'
            )
        sales_table = rows_html
    
    # 평형별 전세·월세 시세표
    jw_table = ""
    has_jw = any(("jeonse" in s or "monthly_rent" in s) for s in size_stats.values())
    if has_jw:
        rows_html = ""
        for size in sorted(size_stats.keys(), key=lambda x: int(x.replace("평", ""))):
            s = size_stats[size]
            j_html = ""
            w_html = ""
            if "jeonse" in s:
                j = s["jeonse"]
                j_html = f'<div style="font-size:15px;margin-top:6px"><span style="color:#888">🏠 전세</span> <strong style="color:#06d6a0">{fmt_won(j["median"])}</strong> <span style="color:#888;font-size:11px">({j["count"]}건)</span></div>'
            if "monthly_rent" in s:
                mr = s["monthly_rent"]
                rep = s.get("monthly_rep")
                if rep:
                    lead = "보통" if rep["n"] >= 3 else "예)"
                    dep_txt = f'보증 <strong>{fmt_won(rep["dep"])}</strong> / ' if rep["dep"] >= 1000 else ''
                    rep_txt = f'{lead} {dep_txt}월 <strong style="color:#ef476f">{rep["rent"]}만</strong>'
                else:
                    rep_txt = f'월 <strong style="color:#ef476f">{mr["median"]}만</strong>'
                w_html = f'<div style="font-size:15px;margin-top:6px"><span style="color:#888">💰 월세</span> {rep_txt} <span style="color:#888;font-size:11px">({mr["count"]}건)</span></div>'
            
            if not j_html and not w_html: continue
            
            rows_html += (
                f'<div style="background:#fff;border:1px solid #e0e0e0;border-left:4px solid #06d6a0;'
                f'border-radius:10px;padding:14px 16px;margin-bottom:8px">'
                f'  <div style="font-size:16px;font-weight:700;color:{NAVY}">{size_label(size)}</div>'
                f'  {j_html}{w_html}'
                f'</div>'
            )
        jw_table = rows_html + '<p style="font-size:13px;color:#888;margin-top:8px">※ 월세는 보증금에 따라 달라집니다(보증금 많이 걸면 월세↓, 적게 걸면 월세↑). 정확한 호가는 아래 최근 실거래 내역을 참고하세요.</p>'
    
    # 권역 내 비교표
    comp_table = ""
    if len(comparisons) >= 2:
        rows_html = ""
        for c in comparisons:
            border_color = GOLD if c["is_self"] else "#bbb"
            bg = "background:#fff8e0" if c["is_self"] else "background:#fff"
            marker = ' ★' if c["is_self"] else ''
            rows_html += (
                f'<div style="{bg};border:1px solid #e0e0e0;border-left:4px solid {border_color};'
                f'border-radius:10px;padding:14px 16px;margin-bottom:8px;'
                f'display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">'
                f'  <div style="flex:1;min-width:0">'
                f'    <div style="font-size:16px;font-weight:700;color:{NAVY};word-break:break-all">{c["name"]}{marker}</div>'
                f'    <div style="font-size:11px;color:#888;margin-top:3px">{c["count"]}건</div>'
                f'  </div>'
                f'  <div style="text-align:right">'
                f'    <div style="font-size:16px;font-weight:700;color:{BLUE};white-space:nowrap">{fmt_won(c["avg"])}</div>'
                f'    <div style="font-size:11px;color:{c["diff_color"]};font-weight:600;margin-top:3px">{c["diff"]}</div>'
                f'  </div>'
                f'</div>'
            )
        comp_table = rows_html
    
    # 포지셔닝 메시지
    positioning = ""
    if comparisons and self_avg > 0:
        others = [c for c in comparisons if not c["is_self"]]
        if others:
            avg_others = mean([c["avg"] for c in others])
            if self_avg < avg_others * 0.9:
                msg = f"<strong>「{target_name}」</strong>은 {zone_full} 주요 단지 평균보다 매매가가 낮습니다. 입지·연식·세대수 대비 <strong>가성비 진입 후보</strong>가 될 수 있습니다."
            elif self_avg > avg_others * 1.1:
                msg = f"<strong>「{target_name}」</strong>은 {zone_full} 주요 단지 평균보다 매매가가 높습니다. <strong>프리미엄 단지 포지션</strong>이며 평형·층·향 조건에 따라 변동 폭이 있습니다."
            else:
                msg = f"<strong>「{target_name}」</strong>은 {zone_full} 주요 단지들과 비슷한 가격대를 형성하고 있습니다. 평형·층·향에 따라 변동 폭이 있으니 개별 매물 확인이 필요합니다."
            positioning = f'<div style="background:#fff8e0;padding:16px 20px;border-left:4px solid {GOLD};border-radius:8px;margin:14px 0;font-size:15px;line-height:1.7">{msg}</div>'
    
    # 최근 실거래 8건
    recent_rows = sorted(target_rows,
                        key=lambda x: (x.get("deal_ym", ""), x.get("deal_day", "")),
                        reverse=True)[:8]
    recent_html = ""
    for r in recent_rows:
        ym = r.get("deal_ym", "")
        _d = str(r.get("deal_day", "")).strip()
        _d = _d.zfill(2) if _d.isdigit() else _d
        ym_t = (f"{ym[:4]}.{ym[4:]}.{_d}" if (len(ym) == 6 and _d) else (f"{ym[:4]}.{ym[4:]}" if len(ym) == 6 else ym))
        area = to_float(r.get("area_m2"))
        pyung = round(area/3.3058, 1) if area else "-"
        floor = r.get("floor", "")
        dt = str(r.get("deal_type", ""))
        
        if "매매" in dt:
            amt = to_int(r.get("deal_amount"))
            price = f'<strong style="color:{BLUE}">{fmt_won(amt)}</strong>'
            label, color = "매매", BLUE
        else:
            dep = to_int(r.get("deposit"))
            rent = to_int(r.get("monthly_rent"))
            if rent > 0:
                price = f'보증 {dep:,}만 / 월세 <strong style="color:#ef476f">{rent}만</strong>'
                label, color = "월세", "#ef476f"
            else:
                price = f'<strong style="color:#06d6a0">{fmt_won(dep)}</strong>'
                label, color = "전세", "#06d6a0"
        
        recent_html += (
            f'<div style="background:#fff;border:1px solid #e0e0e0;border-left:4px solid {color};'
            f'border-radius:10px;padding:12px 16px;margin-bottom:6px">'
            f'  <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:4px">'
            f'    <div style="font-size:15px;color:{NAVY}">'
            f'      <strong>전용 {r.get("area_m2","")}㎡({pyung}평)</strong> · {floor}층 · {ym_t}'
            f'    </div>'
            f'    <span style="background:{color};color:#fff;padding:2px 10px;border-radius:12px;font-size:11px;font-weight:600;white-space:nowrap">{label}</span>'
            f'  </div>'
            f'  <div style="font-size:15px;text-align:right">{price}</div>'
            f'</div>'
        )
    
    recent_table = recent_html
    
    # ── 단지 월별 추세 (대표 평형: 전세·환산월세 중위) — 자동 생성 ──
    trend_html = ""
    if size_stats:
        def _group(_pyung):
            _mo = {}
            for _r in target_rows:
                _a = to_float(_r.get("area_m2"))
                if _a <= 0 or round(_a / 3.3058) != _pyung:
                    continue
                if "전월세" not in str(_r.get("deal_type", "")):
                    continue
                _ym = str(_r.get("deal_ym", ""))
                if len(_ym) != 6:
                    continue
                _dep = to_int(_r.get("deposit")); _rent = to_int(_r.get("monthly_rent"))
                _m = _mo.setdefault(_ym, {"jeonse": [], "hwan": []})
                if _rent > 0:
                    _m["hwan"].append(_rent + _dep * 0.045 / 12)
                elif _dep > 0:
                    _m["jeonse"].append(_dep)
            return _mo
        def _cj(_mo):  # 전세 2건 이상인 달 수 (추세 신뢰도)
            return sum(1 for _v in _mo.values() if len(_v["jeonse"]) >= 2)
        def _ch(_mo):  # 환산월세 2건 이상인 달 수
            return sum(1 for _v in _mo.values() if len(_v["hwan"]) >= 2)
        def _tot(_mo):
            return sum(len(_v["jeonse"]) + len(_v["hwan"]) for _v in _mo.values())
        # 대표 평형 = 전세가 깨끗한 달(2건 이상)이 가장 많은 평형 (추세가 의미있도록)
        _cands = []
        for _py in size_stats:
            try:
                _pyung = int(_py.replace("평", ""))
            except ValueError:
                continue
            _mo = _group(_pyung)
            _cands.append((_cj(_mo), _ch(_mo), _tot(_mo), _py, _mo))
        _cands.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
        if _cands and (_cands[0][0] >= 3 or _cands[0][1] >= 3):
            dom_py = _cands[0][3]
            _monthly = _cands[0][4]
            _months = sorted(_ym for _ym in _monthly
                             if len(_monthly[_ym]["jeonse"]) >= 2 or len(_monthly[_ym]["hwan"]) >= 2)[-6:]
            _rows = []
            for _ym in _months:
                _jv = _monthly[_ym]["jeonse"]; _hv = _monthly[_ym]["hwan"]
                _jok = len(_jv) >= 2; _hok = len(_hv) >= 2
                _rows.append((f"{_ym[2:4]}.{_ym[4:6]}",
                              fmt_won(round(median(_jv))) if _jok else "-",
                              f"{round(median(_hv))}만" if _hok else "-"))
            _js = [median(_monthly[_ym]["jeonse"]) for _ym in _months if len(_monthly[_ym]["jeonse"]) >= 2]
            _hs = [median(_monthly[_ym]["hwan"]) for _ym in _months if len(_monthly[_ym]["hwan"]) >= 2]
            if len(_rows) >= 3 and (len(_js) >= 3 or len(_hs) >= 3):
                _line = ""
                _ser = None; _unit = ""
                if len(_js) >= 3 and _js[0] > 0:
                    _ser, _unit = _js, "전세 중위"
                elif len(_hs) >= 3 and _hs[0] > 0:
                    _ser, _unit = _hs, "월세 시세"
                if _ser:
                    _chg = (_ser[-1] - _ser[0]) / _ser[0] * 100
                    _dir = "상승" if _chg > 1 else ("하락" if _chg < -1 else "보합")
                    _line = f"최근 {len(_rows)}개월간 {_unit}가 <strong>약 {abs(_chg):.0f}% {_dir}</strong>했습니다. 그래서 최근 실거래가 6개월 중위값보다 {'높게' if _chg > 0 else '낮게'} 뜨는 거예요."
                    if _chg > 1:
                        _line += " 봄 성수기 영향도 섞여 있으니, 세입자라면 고점 부근으로 보고 최근 조정된 실거래를 근거로 협상하세요."
                _tr = ""
                for _i, (_mm, _jj, _hh) in enumerate(_rows):
                    _bg = "background:#f7f9fc" if _i % 2 else ""
                    _tr += f'<tr style="{_bg}"><td style="padding:9px;text-align:center;border:1px solid #9aa7bd">{_mm}</td><td style="padding:9px;text-align:center;border:1px solid #9aa7bd">{_jj}</td></tr>'
                _note = f'<p style="font-size:15px;line-height:1.85;color:#333;margin-top:12px">{_line}</p>' if _line else ""
                trend_html = f"""<div style="margin:24px 0;font-family:'Malgun Gothic',sans-serif">
  <h2 style="font-size:20px;color:{NAVY};border-left:4px solid {BLUE};padding-left:10px;margin:0 0 12px 0">📊 {target_name} {size_label(dom_py)} 월별 추세</h2>
  <div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:15px">
  <tr><th style="padding:10px 8px;background:#e8edf5;color:#0d1f3c;font-weight:800;border:1px solid #9aa7bd;border-bottom:2px solid #0d1f3c;text-align:left">월</th><th style="padding:10px 8px;background:#e8edf5;color:#0d1f3c;font-weight:800;border:1px solid #9aa7bd;border-bottom:2px solid #0d1f3c;text-align:left">전세 중위</th></tr>
  {_tr}</table></div>
  {_note}
  <p style="font-size:13px;color:#888;margin-top:6px">※ 대표 평형({size_label(dom_py)}) 전세 중위 · 국토부 실거래 기준 (월세 시세는 위 시세표 참고)</p>
</div>"""

    # 본문 조립
    subtitle_text = f"📌 매주 수요일 발행 · {zone_full}"
    if build_year:
        subtitle_text += f" · 준공 {build_year}년"
    
    body = (
        header_html("🏢", "BUILDING SPOTLIGHT", f"{target_name} 실거래가·시세", region, date_str,
                   subtitle=subtitle_text)
        + section(f"📌 오늘의 단지: {target_name}", intro)
    )
    
    if sales_table:
        body += section(f"💰 {target_name} 평형별 매매 실거래가", sales_table)
    # (우장산 권역 비교 섹션 제거 — 매매·전세·월세 + 월별추세만)
    if jw_table:
        body += section(f"🏠 {target_name} 전세·월세 시세표", jw_table)
    
    body += section(f"📋 {target_name} 최근 실거래 내역", recent_table)
    body += trend_html
    body += MARKET_TREND_NOTE
    body += INTERNAL_LINKS
    body += oneline_box("기준은 호가가 아니라 위 신고가입니다 — 계약서 쓰기 전, 이 표 한 번만 다시 여세요.")
    body += footer_html([
        region, target_name,
        f"{target_name} 시세",
        f"{target_name} 실거래가",
        f"{target_name} 매매가",
        "실거래", "단지분석", zone_full,
        f"{region} {target_name}",
        f"{zone_full} 아파트 시세",
    ])
    
    # 하위 카테고리: 단지명 그대로 (네이버 블로그 2단계 구조)
    sub_category = target_name
    
    return {
        "title": f"{target_name} 실거래가 - {region} 평형별 매매·전세·월세 시세 ({date_str[:7]})",
        "category": "building_spotlight",
        "sub_category": sub_category,
        "zone": ZONE_NAME.get(target_zone, ""),
        "building_name": target_name,
        "html": body,
        "tags": [
            region, target_name,
            f"{target_name} 시세",
            f"{target_name} 실거래가",
            f"{target_name} 매매가",
            f"{target_name} 전세",
            f"{target_name} 월세",
            "실거래", "단지분석", zone_full,
        ],
    }


def gen_jeonse_vs_monthly(report, csv_rows, date_str):
    region = report["region"]
    j = report.get("jeonse_matrix", {})
    m = report.get("matrix", {})
    
    def fmt_won(amt):
        if not amt: return "-"
        if amt >= 10000:
            eok = amt // 10000
            rest = amt % 10000
            return f"{eok}억 {rest:,}만원" if rest else f"{eok}억"
        return f"{amt:,}만원"
    
    # ────────────────────────────────────────
    # 0. csv_rows에서 [건물유형 × 평형구간]별 전세/월세 raw 값 수집
    #    ⭐ 아파트·빌라·오피스텔은 같은 평형도 가격대가 완전히 달라
    #       반드시 건물 유형을 분리해서 비교해야 함
    #    ⭐ 면적은 전용면적(area_m2) 기준
    # ────────────────────────────────────────
    def _building_type(deal_type):
        dt = str(deal_type)
        if "아파트" in dt: return "아파트"
        if "오피스텔" in dt: return "오피스텔"
        if "연립" in dt or "다세대" in dt: return "빌라·다세대·도시형생활주택 포함"
        return None

    def _pyung_band(area_m2):
        """전용면적(㎡) → ㎡ 구간 라벨"""
        if not area_m2:
            return None
        if area_m2 < 30:   return "30㎡ 미만"
        if area_m2 < 50:   return "30~50㎡"
        if area_m2 < 66:   return "50~66㎡"
        if area_m2 < 85:   return "66~85㎡"
        if area_m2 < 100:  return "85~100㎡"
        return "100㎡ 이상"

    def _to_int(v):
        try:
            return int(float(str(v).replace(",", "").strip()))
        except Exception:
            return 0

    # (건물유형, 평형구간)별 전세금·월세 raw 리스트
    bt_band_jeonse = {}   # {(building, band): [전세금, ...]}
    bt_band_rent   = {}   # {(building, band): [환산월세, ...]}
    # 실거래 사례용 상세 (단지명·면적·금액·날짜)
    bt_band_jeonse_detail = {}  # {(building, band): [{name, area, dep, ym}, ...]}
    bt_band_rent_detail   = {}
    for r in csv_rows:
        dt = str(r.get("deal_type", ""))
        if "전월세" not in dt:
            continue
        building = _building_type(dt)
        if not building:
            continue
        try:
            area = float(str(r.get("area_m2", "")).replace(",", "").strip())  # 전용면적
        except Exception:
            area = None
        # ⭐ 아파트는 전용 45㎡ 이상만 인정
        #    (45㎡ 미만 '아파트'는 도생·주거형 소형이라 진짜 아파트와 가격대가 달라 제외)
        if building == "아파트" and (area is None or area < 45):
            continue
        band = _pyung_band(area)
        if not band:
            continue
        dep = _to_int(r.get("deposit"))
        rent = _to_int(r.get("monthly_rent"))
        name = str(r.get("building_name", "") or "-").strip()
        ym = str(r.get("deal_ym", "") or "")
        ym_fmt = f"{ym[2:4]}.{ym[4:6]}" if len(ym) >= 6 else ym
        key = (building, band)
        if rent == 0 and dep > 0:
            bt_band_jeonse.setdefault(key, []).append(dep)
            bt_band_jeonse_detail.setdefault(key, []).append(
                {"name": name, "area": area, "dep": dep, "ym": ym_fmt})
        elif rent > 0:
            converted_rent = rent + round(max(dep - 1000, 0) * 0.04 / 12)
            bt_band_rent.setdefault(key, []).append(converted_rent)
            bt_band_rent_detail.setdefault(key, []).append(
                {"name": name, "area": area, "dep": dep, "rent": rent, "ym": ym_fmt})

    # ────────────────────────────────────────
    # 1. [건물유형 × 평형]별 전세↔월세 비교 데이터
    # ────────────────────────────────────────
    BUILDING_ORDER = ["아파트", "오피스텔", "빌라·다세대·도시형생활주택 포함"]
    BUILDING_EMOJI = {"아파트": "🏢", "오피스텔": "🏬", "빌라·다세대·도시형생활주택 포함": "🏘️"}

    compare_data = []
    # 모든 (건물유형, 평형) 조합 순회
    all_keys = set(list(bt_band_jeonse.keys()) + list(bt_band_rent.keys()))
    # 정렬: 건물유형 순 → 면적 순
    band_order = ["30㎡ 미만", "30~50㎡", "50~66㎡", "66~85㎡", "85~100㎡", "100㎡ 이상"]
    def _sort_key(k):
        b, band = k
        return (BUILDING_ORDER.index(b) if b in BUILDING_ORDER else 9,
                band_order.index(band) if band in band_order else 9)

    for key in sorted(all_keys, key=_sort_key):
        building, py = key
        jeonse_vals = sorted(bt_band_jeonse.get(key, []))
        rent_vals   = sorted(bt_band_rent.get(key, []))
        j_count = len(jeonse_vals)
        r_count = len(rent_vals)

        # 양쪽 다 데이터 있어야 비교 의미
        if j_count == 0 and r_count == 0:
            continue

        # 전세 통계
        if jeonse_vals:
            j_min = jeonse_vals[0]
            j_max = jeonse_vals[-1]
            j_median = round(median(jeonse_vals))
        else:
            j_min = j_max = j_median = 0

        # 월세 통계 (환산값)
        if rent_vals:
            r_min = rent_vals[0]
            r_max = rent_vals[-1]
            r_median = round(median(rent_vals))
        else:
            r_min = r_max = r_median = 0

        avg_rent = r_median  # 비교 기준 = 중앙값

        # 전세 환산월세 (중앙값 기준)
        if j_median > 1000:
            converted = round((j_median - 1000) * 0.04 / 12)
        else:
            converted = 0

        # ── 신뢰성 판정 ──
        low_sample = (j_count < 3 or r_count < 3)
        is_wide = False
        if jeonse_vals and j_min > 0:
            spread = (j_max - j_min) / j_min
            if spread >= 1.5:
                is_wide = True

        # 판정 (양쪽 데이터 있고 신뢰 가능할 때만)
        if j_count == 0 or r_count == 0:
            verdict = "데이터 부족"
            verdict_color = "#bbb"
            verdict_icon = "—"
        elif low_sample or is_wide:
            verdict = "판단 보류"
            verdict_color = "#888"
            verdict_icon = "⚠️"
        elif converted > avg_rent * 1.05:
            verdict = "월세 유리"
            verdict_color = "#06d6a0"
            verdict_icon = "💚"
        elif converted < avg_rent * 0.95:
            verdict = "전세 유리"
            verdict_color = "#1565c0"
            verdict_icon = "💙"
        else:
            verdict = "비슷"
            verdict_color = "#666"
            verdict_icon = "⚖️"

        compare_data.append({
            "building": building,
            "building_emoji": BUILDING_EMOJI.get(building, "📋"),
            "pyung": py,
            "pyung_label": f"전용 {py}",   # 전용면적 명시
            "jeonse_avg": j_median,
            "jeonse_min": j_min,
            "jeonse_max": j_max,
            "jeonse_count": j_count,
            "rent_avg": avg_rent,
            "rent_min": r_min,
            "rent_max": r_max,
            "rent_count": r_count,
            "converted": converted,
            "verdict": verdict,
            "verdict_color": verdict_color,
            "verdict_icon": verdict_icon,
            "diff": converted - avg_rent,
            "low_sample": low_sample,
            "is_wide": is_wide,
            "jeonse_detail": sorted(bt_band_jeonse_detail.get(key, []), key=lambda x: x["ym"], reverse=True),
            "rent_detail": sorted(bt_band_rent_detail.get(key, []), key=lambda x: x["ym"], reverse=True),
        })
    
    # ────────────────────────────────────────
    # 2. 비교 카드 HTML
    # ────────────────────────────────────────
    # ────────────────────────────────────────
    # 2. 카드 생성 함수 (재사용)
    # ────────────────────────────────────────
    def render_card(d):
        # 전세 표기
        if d["jeonse_count"] > 0 and d["jeonse_min"] != d["jeonse_max"]:
            jeonse_line = (
                f'🏠 <span style="color:#888">전세 범위</span>: '
                f'<strong style="color:#1565c0">{fmt_won(d["jeonse_min"])} ~ {fmt_won(d["jeonse_max"])}</strong> '
                f'<span style="color:#888;font-size:11px">(중간값 {fmt_won(d["jeonse_avg"])} · {d["jeonse_count"]}건)</span>'
            )
        elif d["jeonse_count"] > 0:
            jeonse_line = (
                f'🏠 <span style="color:#888">전세</span>: '
                f'<strong style="color:#1565c0">{fmt_won(d["jeonse_avg"])}</strong> '
                f'<span style="color:#888;font-size:11px">({d["jeonse_count"]}건)</span>'
            )
        else:
            jeonse_line = '🏠 <span style="color:#bbb">전세 거래 없음</span>'

        # 월세 표기
        if d["rent_count"] > 0 and d["rent_min"] != d["rent_max"]:
            rent_line = (
                f'💰 <span style="color:#888">월세 범위</span>(환산): '
                f'<strong style="color:#ef476f">{d["rent_min"]}~{d["rent_max"]}만원</strong> '
                f'<span style="color:#888;font-size:11px">(중간값 {d["rent_avg"]}만원 · {d["rent_count"]}건)</span>'
            )
        elif d["rent_count"] > 0:
            rent_line = (
                f'💰 <span style="color:#888">실제 월세</span>(환산): '
                f'<strong style="color:#ef476f">{d["rent_avg"]}만원</strong> '
                f'<span style="color:#888;font-size:11px">({d["rent_count"]}건)</span>'
            )
        else:
            rent_line = '💰 <span style="color:#bbb">월세 거래 없음</span>'

        # 전세 환산월세
        if d["jeonse_count"] > 0 and d["converted"] > 0:
            conv_line = f'🔄 <span style="color:#888">전세 환산월세</span>: <strong>{d["converted"]}만원</strong> <span style="color:#888;font-size:11px">(중간값 기준)</span>'
        else:
            conv_line = ""

        # 편차/표본 경고
        warn_line = ""
        if d["is_wide"]:
            warn_line = (
                '<div style="font-size:12px;color:#b8860b;margin-top:6px;'
                'background:#fff8e8;padding:6px 10px;border-radius:6px">'
                '⚠️ 같은 유형·평형이라도 동·층·연식에 따라 편차가 큽니다. '
                '<strong>범위</strong>로 참고하세요.</div>'
            )
        elif d["low_sample"]:
            warn_line = (
                '<div style="font-size:12px;color:#888;margin-top:6px;'
                'background:#f5f5f5;padding:6px 10px;border-radius:6px">'
                '⚠️ 거래 건수가 적어 참고용으로만 보세요.</div>'
            )

        # ── 유리 판정 시: 실거래 사례 + 강화된 행동 가이드 ──
        case_line = ""
        cases, side_color, side_bg, side_label = [], "", "", ""
        if d["verdict"] == "전세 유리" and d["jeonse_detail"]:
            cases = d["jeonse_detail"][:3]
            side_color, side_bg, side_label = "#1565c0", "#eff6ff", "전세"
        elif d["verdict"] == "월세 유리" and d["rent_detail"]:
            cases = d["rent_detail"][:3]
            side_color, side_bg, side_label = "#ef476f", "#f0fdf4", "월세"

        if cases:
            items = ""
            for c in cases:
                area_str = f'{c["area"]:.0f}㎡' if c["area"] else "-"
                if side_label == "전세":
                    price = f'전세 <strong style="color:{side_color}">{fmt_won(c["dep"])}</strong>'
                else:
                    price = f'보증 {c["dep"]:,}만 / 월세 <strong style="color:{side_color}">{c["rent"]}만</strong>'
                items += (
                    f'<div style="font-size:12px;color:#333;padding:3px 0">'
                    f'• <strong>{c["name"]}</strong> {area_str} / {price} '
                    f'<span style="color:#999">({c["ym"]})</span></div>'
                )
            case_line = (
                f'<div style="margin-top:8px;background:{side_bg};border-radius:8px;padding:12px 14px">'
                f'<div style="font-size:12px;font-weight:700;color:{side_color};margin-bottom:6px">'
                f'📋 실제 이런 {side_label} 거래가 있었어요 (최신순)</div>'
                f'{items}'
                # ── 강화된 행동 가이드 ──
                f'<div style="margin-top:10px;padding-top:8px;border-top:1px dashed #d0d0d0;font-size:12px;color:#555">🔔 이 조건이 보이면 — 단지 앞 부동산에 알림을 걸어두고, 계약 전 Rent Check로 적정가만 확인하세요.</div>'
                f'</div>'
            )

        return (
            f'<div style="background:#fff;border:1px solid #e0e0e0;border-left:4px solid {d["verdict_color"]};'
            f'border-radius:10px;padding:14px 16px;margin-bottom:8px">'
            f'  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
            f'    <div style="font-size:16px;font-weight:700;color:{NAVY}">{d["building_emoji"]} {d["building"].split("·")[0]} · {d["pyung_label"]}</div>'
            f'    <span style="background:{d["verdict_color"]};color:#fff;padding:3px 12px;border-radius:14px;font-size:12px;font-weight:600">{d["verdict_icon"]} {d["verdict"]}</span>'
            f'  </div>'
            f'  <div style="font-size:15px;line-height:1.9;color:#333">'
            f'    <div>{jeonse_line}</div>'
            f'    <div>{rent_line}</div>'
            + (f'    <div>{conv_line}</div>' if conv_line else '')
            + f'  </div>'
            f'  {warn_line}'
            f'  {case_line}'
            f'</div>'
        )

    # ────────────────────────────────────────
    # 3. 유리(메인) vs 보류(접기) 분리
    # ────────────────────────────────────────
    favorable = [d for d in compare_data if d["verdict"] in ("전세 유리", "월세 유리")]
    others    = [d for d in compare_data if d["verdict"] not in ("전세 유리", "월세 유리")]

    # 메인: 유리 판정 카드
    if favorable:
        main_cards = "".join(render_card(d) for d in favorable)
        favorable_html = (
            '<div style="background:linear-gradient(135deg,#f0fdf4,#eff6ff);border-radius:12px;padding:14px 16px;margin-bottom:14px">'
            '<div style="font-size:16px;font-weight:800;color:#0d1f3c;margin-bottom:10px">'
            '✅ 지금 명확하게 유리한 조건 (실거래 기준)</div>'
            f'{main_cards}'
            '</div>'
        )
    else:
        favorable_html = (
            '<div style="background:#fff8e8;border-left:4px solid #d4a73a;border-radius:0 10px 10px 0;'
            'padding:16px 18px;margin-bottom:14px">'
            '<strong style="color:#b8860b">이번 집계 기간엔 "명확히 유리"한 조건이 적어요</strong><br>'
            '<span style="font-size:15px;color:#555">화곡동은 같은 평형도 단지·연식별 편차가 커서, '
            '아래 범위를 참고하시고 본인 조건은 시세 검증 도구로 직접 확인해보세요.</span>'
            '</div>'
        )

    # 접기: 나머지(보류·비슷·데이터부족)
    others_html = ""
    if others:
        others_cards = "".join(render_card(d) for d in others)
        others_html = (
            '<details style="margin-top:6px">'
            '<summary style="cursor:pointer;font-size:15px;font-weight:700;color:#666;'
            'padding:10px 14px;background:#f5f5f5;border-radius:8px;list-style:none">'
            f'▼ 나머지 평형·유형 전체 보기 ({len(others)}개 · 편차 크거나 거래 적음)</summary>'
            f'<div style="margin-top:10px">{others_cards}</div>'
            '</details>'
        )

    table = favorable_html + others_html
    
    # ────────────────────────────────────────
    # 3. 용어 설명 박스
    # ────────────────────────────────────────
    glossary = """
<div style="background:#f8fbff;padding:18px 20px;border-radius:10px;border-left:4px solid #1565c0">
<strong style="color:#1565c0">📚 잠깐, 용어 설명부터!</strong><br><br>

<strong>전월세 전환율이란?</strong><br>
전세를 월세로 바꿀 때 적용하는 이율입니다. 현재 화곡동 기준 약 <strong>연 4%</strong>를 적용합니다.<br>
(예: 전세 2억을 월세로 바꾸면 → 2억 × 4% ÷ 12개월 = 약 67만원/월)<br><br>

<strong>왜 "보증금 1,000만원 기준"인가요?</strong><br>
월세 계약은 보증금이 있고 그 위에 월차임이 얹어집니다. 보증금이 클수록 월세는 줄어듭니다.<br>
공정한 비교를 위해 <strong>모든 평형의 환산값을 "보증금 1,000만원" 기준</strong>으로 통일했어요.<br><br>

<strong>판정 기준은?</strong><br>
• 💚 <strong>월세 유리</strong>: 전세 환산값 &gt; 실제 월세 → 실제 월세가 더 저렴<br>
• 💙 <strong>전세 유리</strong>: 전세 환산값 &lt; 실제 월세 → 전세금 이자가 월세보다 쌈<br>
• ⚖️ <strong>비슷</strong>: 차이 5% 이내 → 본인 자금·생활 패턴으로 결정<br>
• ⚠️ <strong>판단 보류</strong>: 매물 편차가 크거나 거래가 적어 단정 어려움<br><br>

<strong>왜 '평균'이 아니라 '범위·중위'인가요?</strong><br>
평균은 극단값 한두 건에 휘둘려 "실제로 존재하지 않는 가격"을 만들거든요.
그래서 <strong>최소~최대 범위</strong>와 <strong>중위(가운데값)</strong>로만 보여드립니다.
</div>
"""
    
    # ────────────────────────────────────────
    # 4. 실제 사례 (판정 신뢰 가능한 평형 중 차이 큰 1개)
    #    편차 크거나(is_wide) 표본 적은(low_sample) 평형은 제외
    # ────────────────────────────────────────
    example = ""
    reliable = [d for d in compare_data
                if not d["is_wide"] and not d["low_sample"]
                and d["verdict"] in ("월세 유리", "전세 유리")]
    if reliable:
        max_diff = max(reliable, key=lambda x: abs(x['diff']))

        # 전세 표기: 범위 우선
        if max_diff["jeonse_min"] != max_diff["jeonse_max"]:
            jeonse_disp = f'{fmt_won(max_diff["jeonse_min"])} ~ {fmt_won(max_diff["jeonse_max"])} (중간값 {fmt_won(max_diff["jeonse_avg"])})'
        else:
            jeonse_disp = fmt_won(max_diff["jeonse_avg"])

        if max_diff['verdict'] == "월세 유리":
            example = f"""
<div style="background:#f0fdf4;padding:18px 20px;border-radius:10px;border-left:4px solid #06d6a0;margin:12px 0">
<strong style="color:#06d6a0">💡 실제 사례로 보면</strong><br><br>

<strong>{max_diff['building_emoji']} {max_diff['building']} 전용 {max_diff['pyung']}</strong>의 경우:<br>
• 전세 시세: <strong>{jeonse_disp}</strong><br>
• 전세를 월세로 환산(중간값 기준): 보증 1,000만 + 월세 <strong>{max_diff['converted']}만원</strong> 수준<br>
• 실제 월세 중간값: 월 <strong>{max_diff['rent_avg']}만원</strong> ← 더 저렴!<br><br>

→ 같은 유형·평형이라면 <strong>월세가 약 {abs(max_diff['diff'])}만원/월 저렴</strong>한 편입니다.<br>
연간 약 {abs(max_diff['diff']) * 12}만원 차이가 나니, <strong>전세 자금 여유가 없다면 월세도 합리적 선택</strong>입니다.<br>
<span style="font-size:12px;color:#888">※ 중간값(median) 기준 · 건물 유형(아파트/빌라/오피스텔)을 분리해 비교했습니다.</span>
</div>
"""
        elif max_diff['verdict'] == "전세 유리":
            example = f"""
<div style="background:#eff6ff;padding:18px 20px;border-radius:10px;border-left:4px solid #1565c0;margin:12px 0">
<strong style="color:#1565c0">💡 실제 사례로 보면</strong><br><br>

<strong>{max_diff['building_emoji']} {max_diff['building']} 전용 {max_diff['pyung']}</strong>의 경우:<br>
• 전세 시세: <strong>{jeonse_disp}</strong><br>
• 전세를 월세로 환산(중간값 기준): 보증 1,000만 + 월세 <strong>{max_diff['converted']}만원</strong><br>
• 실제 월세 중간값: 월 <strong>{max_diff['rent_avg']}만원</strong> ← 더 비쌈<br><br>

→ 전세금을 마련할 수 있다면 <strong>전세가 약 {abs(max_diff['diff'])}만원/월 저렴</strong>한 편입니다.<br>
대출 이자가 4% 미만이라면 전세가 유리하다는 신호입니다.<br>
<span style="font-size:12px;color:#888">※ 중간값(median) 기준 · 건물 유형(아파트/빌라/오피스텔)을 분리해 비교했습니다.</span>
</div>
"""
    else:
        # 신뢰 가능한 평형이 없으면 사례 대신 안내
        example = """
<div style="background:#fff8e8;padding:18px 20px;border-radius:10px;border-left:4px solid #d4a73a;margin:12px 0">
<strong style="color:#b8860b">💡 이번 달은 단정적 비교를 보류합니다</strong><br><br>
이번 집계 기간에는 평형별 매물 편차가 크거나 거래 건수가 적어, 
"전세 vs 월세 어느 쪽이 유리하다"고 단정하기 어렵습니다.<br>
위 표의 <strong>범위(최소~최대)</strong>를 참고하시고, 본인 조건은 아래 시세 검증 도구로 직접 확인해 보세요.
</div>
"""
    
    # ────────────────────────────────────────
    # 5. 자가 진단 가이드
    # ────────────────────────────────────────
    self_check = """
<ul style="margin:0 0 0 18px;line-height:1.9">
  <li><strong>① 본인 평형 행</strong>을 위 표에서 찾으세요</li>
  <li><strong>② "전세 환산 월세"와 "실제 월세 중위"를 비교</strong>하세요</li>
  <li><strong>③ 판정 컬럼</strong>을 보고 본인 상황과 맞는지 확인하세요
    <ul style="margin:4px 0 0 18px">
      <li>전세 자금 여유 + 대출 이자 4% 미만 → <strong>전세</strong></li>
      <li>초기 자금 부족 + 단기 거주 예정 → <strong>월세</strong></li>
      <li>차이가 비슷하면 → <strong>본인 자금 운용·이사 계획</strong>으로 결정</li>
    </ul>
  </li>
</ul>
"""
    
    # ────────────────────────────────────────
    # 6. 본문 조립
    # ────────────────────────────────────────
    # 당월(집계 진행 중) 안내
    now_ym = datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y%m")
    collecting_note = (
        f'<div style="background:#fff8e8;border-left:4px solid #d4a73a;border-radius:0 8px 8px 0;'
        f'padding:10px 14px;margin:10px 0;font-size:15px;color:#8a6e1a">'
        f'📅 <strong>{int(now_ym[4:6])}월은 신고 집계 진행 중</strong>입니다. '
        f'부동산 거래는 계약 후 30일 내 신고되므로, 당월 데이터는 일부만 반영되어 있어요. '
        f'(최근 약 6개월 누적 기준 분석)</div>'
    )

    body = (
        header_html("⚖️", "JEONSE vs MONTHLY",
                   f"{region} 전세 vs 월세 어느 쪽이 유리할까?", region, date_str,
                   subtitle="📌 매주 목요일 발행 · 실거래 기반 비교 분석")
        + section("🤔 같은 평형, 전세냐 월세냐?",
                 f"안녕하세요, 렌트체크강서입니다. 목돈 묶어 전세로 갈까 다달이 월세로 낼까 — 감으로 하면 꼭 후회하는 계산이죠. 같은 평형에서 어느 쪽이 유리한지 실거래로 직접 따져봤습니다.<br>본인 조건을 옆에 두고 비교하면서 보세요.")
        + summary_box([
            "같은 평형의 전세·월세를 <strong>환산월세</strong> 한 잣대로 비교",
            "표본 = 최근 6개월 실거래, 값은 중위 기준",
            "판정: 환산값이 실제 월세보다 낮으면 <strong>전세 우위</strong>",
        ])
        + glossary
        + collecting_note
        + section("📊 같은 평형, 전세와 월세 어느 쪽이 유리했나", table)
    )
    
    if example:
        body += example
    
    body += section("🔍 본인 상황으로 자가 진단하기", self_check)
    body += section("💬 마무리 조언",
        "<strong>전세도 월세도 정답은 없습니다.</strong> 본인의 <strong>자금 상황·거주 기간·금리</strong>에 따라 유리한 쪽이 달라집니다.<br>"
        "위 비교표는 <strong>중간값·범위</strong>를 보여드린 것으로, 개별 매물은 같은 단지·평형이라도 동·층·향·연식·옵션에 따라 차이가 크니 직접 비교가 필요합니다.")
    body += oneline_box("전세냐 월세냐는 취향이 아니라 계산입니다 — 환산월세 하나만 뽑아보면 답이 나옵니다.")
    body += footer_html([region, f"{region}전세", f"{region}월세", f"{region}전세시세", f"{region}월세시세", "전세월세비교", "전월세환산", "환산월세", "전세금월세", "화곡동부동산"])
    
    return {
        "title": f"{region} 전세 vs 월세 비교 - 환산월세 기준 적정 시세 ({date_str[:7]})",
        "category": "jeonse_vs_monthly",
        "html": body,
        "tags": [region, f"{region}전세", f"{region}월세", f"{region}전세시세", f"{region}월세시세", "전세월세비교", "전월세환산", "환산월세", "전세금월세", "화곡동부동산"],
    }


def gen_value_picks(report: dict, csv_rows: list, date_str: str) -> dict:
    def fmt_won(amt):
        if not amt: return "-"
        if amt >= 10000:
            eok = amt // 10000; rest = amt % 10000
            return f"{eok}억 {rest:,}만원" if rest else f"{eok}억"
        return f"{amt:,}만원"
    """금요일: 대장 단지 실거래 추적 (소유자 시세 모니터링용)"""
    from statistics import median
    region = report["region"]
    target_date = datetime.strptime(date_str, "%Y-%m-%d")

    def to_int(v):
        try: return int(str(v).replace(",", "")) if v else 0
        except: return 0
    def to_float(v):
        try: return float(v) if v else 0.0
        except: return 0.0

    # ────────────────────────────────────────
    # 1. 아파트 매매만 (소유자 자산 = 1순위)
    # ────────────────────────────────────────
    apt = [r for r in csv_rows
           if r.get("deal_type") == "아파트_매매" and to_int(r.get("deal_amount")) > 0]

    # 단지별 그룹 (거래 5건 이상 = 대장 후보)
    danji = defaultdict(list)
    for r in apt:
        danji[r.get("building_name", "-")].append(r)

    danji_main = {b: items for b, items in danji.items() if len(items) >= 5}

    # 평당가 기준 정렬 (대장 = 평당 높은 순)
    def avg_pp(items):
        pps = [to_int(r["deal_amount"]) / (to_float(r["area_m2"]) / 3.3058)
               for r in items if to_float(r.get("area_m2")) > 0]
        return mean(pps) if pps else 0

    ranked = sorted(danji_main.items(), key=lambda x: -avg_pp(x[1]))

    # ────────────────────────────────────────
    # 2. 단지별 카드 생성 (주력 평형 + 월별 추이)
    # ────────────────────────────────────────
    cards_html = ""
    rank_no = 0
    for bld, items in ranked:
        rank_no += 1
        if rank_no > 9: break  # 화곡동 대장 9개까지

        pp = round(avg_pp(items))
        yrs = [to_int(r.get("build_year")) for r in items if to_int(r.get("build_year")) > 0]
        build_y = min(yrs) if yrs else None
        age = (target_date.year - build_y) if build_y else None

        # 주력 평형 (거래 최다 1개)
        py_groups = defaultdict(list)
        for r in items:
            a = to_float(r.get("area_m2"))
            if a <= 0: continue
            py = round(a / 3.3058)
            py_groups[py].append(r)
        if not py_groups: continue
        main_py = max(py_groups.keys(), key=lambda p: len(py_groups[p]))
        py_items = py_groups[main_py]
        # 대표 면적(㎡) - 실거래 값 그대로 (소수점 유지)
        from statistics import median as _median
        main_m2 = _median([to_float(r.get("area_m2")) for r in py_items])
        main_m2 = round(main_m2, 2)
        main_m2 = int(main_m2) if main_m2 == int(main_m2) else main_m2

        # 월별 중앙값
        by_month = defaultdict(list)
        for r in py_items:
            by_month[r.get("deal_ym", "")].append(to_int(r["deal_amount"]))
        months = sorted([m for m in by_month if m])

        # 최근달 vs 직전달 변동
        trend_html = ""
        if len(months) >= 2:
            recent = round(median(by_month[months[-1]]))
            prev = round(median(by_month[months[-2]]))
            diff = recent - prev
            if diff > 0:
                arrow, color = "▲", "#ef476f"
                diff_txt = f"+{diff:,}만"
            elif diff < 0:
                arrow, color = "▼", "#1565c0"
                diff_txt = f"{diff:,}만"
            else:
                arrow, color = "—", "#888"
                diff_txt = "보합"
            trend_html = f'<span style="color:{color};font-weight:700">{arrow} {diff_txt}</span> <span style="color:#999;font-size:15px">(전월 대비)</span>'
            recent_price = recent
        else:
            recent_price = round(median(by_month[months[-1]])) if months else 0
            trend_html = '<span style="color:#999;font-size:15px">거래 1개월</span>'

        # 가격 범위
        all_prices = [to_int(r["deal_amount"]) for r in py_items]
        lo, hi = min(all_prices), max(all_prices)

        # 연식 라벨
        if age is not None:
            age_label = f"{build_y}년 ({age}년차)"
        else:
            age_label = "-"

        # 월별 미니 추이 텍스트
        mini = " · ".join(f"{m[4:]}월 {fmt_won(round(median(by_month[m])))}" for m in months[-4:])

        rank_badge = "👑" if rank_no == 1 else f"{rank_no}."

        cards_html += f'''
<div style="background:#fff;border:1px solid #e0e0e0;border-left:4px solid {GOLD};border-radius:10px;padding:16px 18px;margin:12px 0">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
    <div style="font-size:17px;font-weight:700;color:{NAVY}">{rank_badge} {bld} <span style="font-size:16px;color:#888;font-weight:400">· 전용 {main_m2}㎡({main_py}평) · {age_label}</span></div>
    <span style="background:{NAVY};color:#fff;padding:3px 10px;border-radius:8px;font-size:15px;white-space:nowrap">평당 {pp:,}만</span>
  </div>
  <div style="font-size:20px;font-weight:700;color:{NAVY};margin-bottom:6px">
    {fmt_won(recent_price)} &nbsp;{trend_html}
  </div>
  <div style="font-size:16px;color:#666;line-height:1.6">
    📊 최근 거래가 범위: {fmt_won(lo)} ~ {fmt_won(hi)}<br>
    📅 월별: {mini}
  </div>
</div>'''

    # ────────────────────────────────────────
    # 3. 본문 조립
    # ────────────────────────────────────────
    intro = f"""
안녕하세요, 렌트체크강서입니다.<br>
금요일은 화곡동 대장 단지들의 <strong>실거래 추적</strong> 시간입니다 — 내 단지가 이번 달 얼마에 팔렸는지, 흐름이 이어지는지 꺾였는지만 빠르게 보죠.<br><br>
정렬은 <strong>평당가 순</strong>, 기준은 각 단지 <strong>주력 평형</strong>. 국토부 신고 실거래만 쓰고, 호가는 없습니다.
"""
    intro += summary_box([
        f"{region} 대장 단지들을 <strong>평당가 순</strong>으로 추적",
        "단지 카드 = 최신 거래가 · 범위 · 월별 흐름",
        "급변 단지는 ⚠️ 특수거래(직거래·증여) 여부부터",
    ])
    notice = """
<div style="background:#fff8f0;padding:14px 18px;border-radius:8px;border-left:4px solid #ef476f;font-size:16px;line-height:1.7">
<strong>⚠️ 참고:</strong> 실거래가는 <strong>신고 시점·층·향·동·옵션</strong>에 따라 같은 평형도 차이가 큽니다.
표시 금액은 <strong>거래된 동일 평형대의 중앙값</strong>이며, 개별 거래는 범위 내에서 다를 수 있습니다.
거래량이 적은 달은 한두 건에 의해 변동폭이 과장될 수 있습니다.
</div>
"""
    subtitle = f"📌 매주 금요일 발행 · 🏢 {region} 아파트 단지별 실거래 추적"

    body = (
        header_html("🏢", "WEEKLY APARTMENT TRACKER",
                   f"{region} 아파트 단지별 실거래 시세", region, date_str,
                   subtitle=subtitle)
        + section(f"🏢 {region} 대장 단지 실거래 추적", intro)
        + section("📈 단지별 시세 (평당가 순)", cards_html)
    )
    body += notice
    # 글에 나온 단지명을 태그에 추가 (검색 유입용) - 정리
    import re as _re
    danji_tags = []
    for bld, _ in ranked[:9]:
        clean = _re.sub(r"\(.*?\)", "", bld)  # 괄호 지번 제거
        clean = clean.replace(",", "").replace(" ", "").strip()  # 쉼표·공백 제거
        if len(clean) >= 4:  # 너무 짧은 이름 제외
            danji_tags.append(clean)
    base_tags = [region, "아파트실거래", f"{region}아파트", f"{region}실거래가", f"{region}시세", "아파트시세", "단지별실거래", "화곡동부동산", "강서구화곡동"]
    all_tags = base_tags + danji_tags

    body += oneline_box("대장 단지 가격은 화곡 전체의 천장입니다 — 내 단지 협상 전에 이 천장부터 확인하세요.")
    body += footer_html(all_tags)

    return {
        "title": f"{region} 아파트 단지별 실거래가 - 평당가 순위 TOP 9 ({date_str[:7]})",
        "category": "value_picks",
        "html": body,
        "tags": all_tags,
    }

def gen_neighborhood(report, csv_rows, date_str):
    """토요일: 거래 활발 단지 (롤링 + 매매·전세·월세 분리 건수)"""
    region = report["region"]
    target_date = datetime.strptime(date_str, "%Y-%m-%d")
    week_of_month = (target_date.day - 1) // 7 + 1
    
    def to_int(v):
        try: return int(str(v).replace(",", "")) if v else 0
        except: return 0
    
    def to_float(v):
        try: return float(v) if v else 0.0
        except: return 0.0
    
    # ────────────────────────────────────────
    # 주차별 필터링 (롤링)
    # ────────────────────────────────────────
    def filter_by_type(rows, type_key):
        """건물유형으로 필터링"""
        return [r for r in rows if type_key in str(r.get("deal_type", ""))]
    
    def filter_new(rows, current_year, max_age=5):
        """신축 필터링"""
        return [r for r in rows
                if r.get("build_year") and str(r["build_year"]).isdigit()
                and (current_year - int(r["build_year"])) <= max_age]
    
    if week_of_month == 1:
        filtered = filter_by_type(csv_rows, "아파트")
        type_label = "아파트"
        emoji = "🏢"
        subtitle = "🏢 이번 주는 아파트 단지"
        tag_extra = "아파트"
        fallback_used = False
    elif week_of_month == 2:
        filtered = filter_by_type(csv_rows, "오피스텔")
        type_label = "오피스텔"
        emoji = "🏬"
        subtitle = "🏬 이번 주는 오피스텔"
        tag_extra = "오피스텔"
        fallback_used = False
    elif week_of_month == 3:
        filtered = [r for r in csv_rows 
                    if "연립" in str(r.get("deal_type", "")) or "다세대" in str(r.get("deal_type", ""))]
        type_label = "빌라·다세대"
        emoji = "🏘️"
        subtitle = "🏘️ 이번 주는 빌라·연립다세대"
        tag_extra = "빌라"
        fallback_used = False
    else:  # 4주차 + 5주차: 신축
        filtered = filter_new(csv_rows, target_date.year, max_age=5)
        type_label = "신축(5년 이내)"
        emoji = "✨"
        subtitle = "✨ 이번 주는 신축 단지 (준공 5년 이내)"
        tag_extra = "신축"
        fallback_used = False
        
        # ⚠️ Fallback: 신축 데이터 부족시 (단지 5개 미만)
        unique_buildings = len(set(r.get("building_name", "") for r in filtered if r.get("building_name")))
        if unique_buildings < 5:
            filtered = csv_rows
            type_label = "전체"
            emoji = "📊"
            subtitle = "📊 이번 주는 전체 단지 종합 (신축 데이터 부족으로 전환)"
            tag_extra = "전체단지"
            fallback_used = True
    
    # ────────────────────────────────────────
    # 단지별 거래 데이터 그룹화 (매매/전세/월세 분리)
    # ────────────────────────────────────────
    by_building = defaultdict(lambda: {
        "rows": [],
        "sales": 0,
        "jeonse": 0,
        "monthly": 0,
        "areas": [],
        "build_year": None,
        "recent_date": "",
    })
    
    # ⚠️ 토요일 글은 매매·전세·월세 다 표시해야 하므로 전체 csv_rows에서 단지 정보 가져오기
    # 필터링된 rows의 단지 이름만 기준으로 추리기
    filtered_names = set(r.get("building_name", "") for r in filtered if r.get("building_name"))
    
    for r in csv_rows:
        name = r.get("building_name", "")
        if not name or name == "nan": continue
        if name not in filtered_names: continue
        
        b = by_building[name]
        b["rows"].append(r)
        
        dt = str(r.get("deal_type", ""))
        if "매매" in dt:
            b["sales"] += 1
        elif "전월세" in dt:
            rent = to_int(r.get("monthly_rent"))
            if rent > 0:
                b["monthly"] += 1
            else:
                b["jeonse"] += 1
        
        area = to_float(r.get("area_m2"))
        if area > 0:
            pyung = round(area / 3.3058)
            if 6 <= pyung <= 60:
                b["areas"].append(pyung)
        
        if not b["build_year"]:
            by = to_int(r.get("build_year"))
            if 1980 < by < 2030:
                b["build_year"] = by
        
        ym = r.get("deal_ym", "")
        day = r.get("deal_day", "")
        if ym and day:
            deal_date = f"{ym}{day.zfill(2)}"
            if deal_date > b["recent_date"]:
                b["recent_date"] = deal_date
    
    # ────────────────────────────────────────
    # TOP 10 추출
    # ────────────────────────────────────────
    building_list = []
    for name, b in by_building.items():
        total = b["sales"] + b["jeonse"] + b["monthly"]
        if total < 3: continue
        
        if b["areas"]:
            min_py = min(b["areas"])
            max_py = max(b["areas"])
            area_range = f"{min_py}평" if min_py == max_py else f"{min_py}~{max_py}평"
        else:
            area_range = "-"
        
        age = target_date.year - b["build_year"] if b["build_year"] else None
        age_text = f"{age}년차" if age else "-"
        
        rd = b["recent_date"]
        recent_label = f"{rd[2:4]}.{rd[4:6]}.{rd[6:8]}" if len(rd) == 8 else "-"
        
        building_list.append({
            "name": name,
            "sales": b["sales"],
            "jeonse": b["jeonse"],
            "monthly": b["monthly"],
            "total": total,
            "area_range": area_range,
            "age": age,
            "age_text": age_text,
            "recent_date": recent_label,
        })
    
    # 빌라 주차는 전세+월세만 합산해서 정렬, 다른 주차는 매매 포함 총계
    if tag_extra == "빌라":
        building_list.sort(key=lambda x: -(x["jeonse"] + x["monthly"]))
    else:
        building_list.sort(key=lambda x: -x["total"])
    top10 = building_list[:10]
    
    # ────────────────────────────────────────
    # 시장 트렌드 인사이트 (빌라 주차는 매매 인사이트 제외)
    # ────────────────────────────────────────
    insights = []
    if top10:
        # 매매 인사이트는 빌라 주차에서 제외
        if tag_extra != "빌라":
            sales_sorted = sorted(top10, key=lambda x: -x["sales"])
            if sales_sorted[0]["sales"] > 0:
                insights.append(f"<strong>매매 활발 TOP</strong>: {sales_sorted[0]['name']} ({sales_sorted[0]['sales']}건)")
        
        jeonse_sorted = sorted(top10, key=lambda x: -x["jeonse"])
        if jeonse_sorted[0]["jeonse"] > 0:
            insights.append(f"<strong>전세 활발 TOP</strong>: {jeonse_sorted[0]['name']} ({jeonse_sorted[0]['jeonse']}건)")
        
        monthly_sorted = sorted(top10, key=lambda x: -x["monthly"])
        if monthly_sorted[0]["monthly"] > 0:
            insights.append(f"<strong>월세 활발 TOP</strong>: {monthly_sorted[0]['name']} ({monthly_sorted[0]['monthly']}건)")
        
        new_count = sum(1 for d in top10 if d["age"] and d["age"] <= 10)
        old_count = sum(1 for d in top10 if d["age"] and d["age"] >= 20)
        if new_count >= 5:
            insights.append(f"<strong>신축(10년 이내) {new_count}개</strong> 거래 주도")
        elif old_count >= 5:
            insights.append(f"<strong>구축(20년 이상) {old_count}개</strong> 거래 주도")
    
    insight_html = "<ul style='margin:0 0 0 18px;line-height:1.9'>" + \
                   "".join(f"<li>{i}</li>" for i in insights) + "</ul>" if insights else ""
    
    # ────────────────────────────────────────
    # TOP 10 카드 (빌라 주차는 매매 항목 제외)
    # ────────────────────────────────────────
    is_villa_week = (tag_extra == "빌라")  # 빌라 주차는 매매 항목 제외
    
    rows_html = ""
    for i, d in enumerate(top10):
        # 거래 유형 표시 (있는 것만)
        deal_pills = []
        if not is_villa_week and d["sales"] > 0:
            deal_pills.append(f'<span style="background:{BLUE};color:#fff;padding:3px 10px;border-radius:12px;font-size:11px;font-weight:600;display:inline-block;margin:2px 4px 2px 0">💎 매매 {d["sales"]}건</span>')
        if d["jeonse"] > 0:
            deal_pills.append(f'<span style="background:#06d6a0;color:#fff;padding:3px 10px;border-radius:12px;font-size:11px;font-weight:600;display:inline-block;margin:2px 4px 2px 0">🏠 전세 {d["jeonse"]}건</span>')
        if d["monthly"] > 0:
            deal_pills.append(f'<span style="background:#ef476f;color:#fff;padding:3px 10px;border-radius:12px;font-size:11px;font-weight:600;display:inline-block;margin:2px 4px 2px 0">💰 월세 {d["monthly"]}건</span>')
        
        total_count = (d["jeonse"] + d["monthly"]) if is_villa_week else d["total"]
        
        rows_html += (
            f'<div style="background:#fff;border:1px solid #e0e0e0;border-left:4px solid {GOLD};'
            f'border-radius:10px;padding:14px 16px;margin-bottom:8px">'
            f'  <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:6px">'
            f'    <div style="display:flex;align-items:center;gap:10px;flex:1;min-width:0">'
            f'      <span style="font-size:16px;font-weight:700;color:{GOLD};white-space:nowrap">#{i+1}</span>'
            f'      <span style="font-size:16px;font-weight:700;color:{NAVY};word-break:break-all">{d["name"]}</span>'
            f'    </div>'
            f'    <span style="font-size:12px;color:#888;white-space:nowrap;font-weight:600">총 {total_count}건</span>'
            f'  </div>'
            f'  <div style="font-size:12px;color:#666;margin-bottom:8px">📐 {d["area_range"]} · {d["age_text"]} · 최근 {d["recent_date"]}</div>'
            f'  <div style="margin-top:6px">{"".join(deal_pills)}</div>'
            f'</div>'
        )
    
    if is_villa_week:
        table_note = '<div style="font-size:11px;color:#888;margin-top:6px">📅 최근 6개월 국토부 신고 실거래 기준 (3건 이상 거래) · 빌라는 임차(전세·월세) 중심 시장이라 매매 항목은 생략</div>'
    else:
        table_note = '<div style="font-size:11px;color:#888;margin-top:6px">📅 최근 6개월 국토부 신고 실거래 기준 (3건 이상 거래된 단지만 집계)</div>'
    
    table = rows_html + table_note
    
    # 본문 조립
    fallback_note = ""
    if fallback_used:
        fallback_note = '<div style="background:#fff8f0;padding:12px 16px;border-radius:8px;border-left:4px solid #ef476f;font-size:12px;line-height:1.7;margin:14px 0">⚠️ 이번 주차는 신축(5년 이내) 단지 데이터가 부족해, 전체 단지 기준으로 보여드립니다.</div>'
    
    intro = f"""
안녕하세요, 렌트체크강서입니다.<br>
토요일은 "요즘 화곡에서 어디가 제일 붐비나"를 보는 날이에요. 최근 6개월 <strong>{type_label}</strong> 신고분을 단지별로 전부 세서 <strong>거래량 TOP 10</strong>을 뽑았습니다.<br><br>
{subtitle}<br><br>
{"이번 글은 <strong>전세·월세를 분리</strong>해 보여드립니다. (빌라는 임차 수요 중심 시장이라 매매 컬럼은 생략)<br>한 단지에서 전세와 월세 분포를 보면, 그 단지가 <strong>장기 거주 선호</strong>인지 <strong>단기·유동 임차</strong> 중심인지 한눈에 보입니다." if tag_extra == "빌라" else "이번 글은 <strong>매매·전세·월세를 분리</strong>해 보여드립니다.<br>한 단지에서 거래 유형이 어떻게 분포하는지 보면, 그 단지가 <strong>투자 수요</strong>가 강한지 <strong>임차 수요</strong>가 강한지 한눈에 보입니다."}
"""
    
    intro += summary_box([
        f"최근 6개월 <strong>{type_label}</strong> 거래량 TOP 10",
        "거래 많음 = 시세 검증·환금성 신호",
        "임대 전용 단지는 하단 각주 확인",
    ])
    # 빌라 주차는 매매 항목 제외한 가이드
    if tag_extra == "빌라":
        how_to_read = """
<ul style="margin:0 0 0 18px;line-height:1.9">
  <li><strong>전세 多</strong> → 안정적 거주 선호 단지 (장기 거주자 많음)</li>
  <li><strong>월세 多</strong> → 단기·1인가구 수요 강세 (유동 인구)</li>
  <li><strong>전세·월세 비슷</strong> → 다양한 임차층이 찾는 단지</li>
</ul>
"""
        table_section_title = "🏆 어디가 제일 붐볐나 — TOP 10 — 전세·월세 분리"
    else:
        how_to_read = """
<ul style="margin:0 0 0 18px;line-height:1.9">
  <li><strong>매매 多</strong> → 투자·실수요 모두 활발</li>
  <li><strong>전세 多</strong> → 안정적 거주 선호 단지</li>
  <li><strong>월세 多</strong> → 단기·1인가구 수요 강세</li>
  <li><strong>3종 골고루</strong> → 모든 수요층이 찾는 단지</li>
</ul>
"""
        table_section_title = "🏆 어디가 제일 붐볐나 — TOP 10 — 매매·전세·월세 분리"
    
    body = (
        header_html(emoji, "NEIGHBORHOOD",
                   f"{region} 거래 활발 {type_label} TOP 10", region, date_str,
                   subtitle="📌 매주 토요일 발행 · 주차별 건물유형 롤링")
        + section(f"📊 이번 주의 {type_label} 분석", intro)
    )
    
    if fallback_note:
        body += fallback_note
    
    if insight_html:
        body += section("🔥 이번 주 시장 트렌드", insight_html)
    
    body += section(table_section_title, table)
    body += section("💬 거래 유형별 어떻게 읽나?", how_to_read)
    # TOP 10 단지명을 태그에 추가 (검색 유입)
    import re as _re
    _danji_tags = []
    for _b in top10:
        _c = _re.sub(r"\(.*?\)", "", _b.get("name","")).replace(",", "").replace(" ", "").strip()
        if len(_c) >= 4:
            _danji_tags.append(_c)
    _nb_tags = [region, f"{region}실거래", f"{region}아파트", f"{region}부동산", "동네분석", "단지분석", "거래활발", "인기단지", "강서구화곡동", tag_extra] + _danji_tags
    if "해링턴타워" in body:
        body += '<p style="font-size:13px;color:#888;margin:-4px 0 18px 2px">※ 우장산역해링턴타워는 청년안심주택(공공 임대 전용)입니다. 월세 건수가 많은 건 그래서이고, 일반 시세의 기준으로는 참고만 하세요.</p>'
    body += oneline_box("거래 많은 단지 = 시세가 검증된 단지입니다. 낯선 단지 계약 전, 이 목록에 있는지부터 보세요.")
    body += footer_html(_nb_tags)
    
    # 제목: 빌라 주차는 전세·월세만, 나머지는 매매·전세·월세
    if tag_extra == "빌라":
        title_suffix = "전세·월세"
    else:
        title_suffix = "매매·전세·월세"
    
    return {
        "title": f"{region} {type_label} 실거래 TOP 10 - 거래 활발 단지 ({date_str[:7]})",
        "category": "neighborhood",
        "html": body,
        "tags": _nb_tags,
    }


def gen_tenant_guide(report, csv_rows, date_str):
    """일요일: 임차인 가이드 (주제 회전)"""
    region = report["region"]
    target_date = datetime.strptime(date_str, "%Y-%m-%d")
    
    # 연중 주차 기반 주제 로테이션 (10개 풀)
    week_of_year = target_date.isocalendar()[1]
    topic_idx = week_of_year % 10
    
    # ────────────────────────────────────────
    # 10개 가이드 주제 풀
    # ────────────────────────────────────────
    topics = [
        # 0: 계약 체크리스트
        {
            "title": "계약 전 필수 체크리스트",
            "emoji": "📝",
            "tag_extra": "계약체크",
            "intro": f"{region}에서 월세·전세 계약을 준비 중이라면, 계약서 도장 찍기 전 반드시 확인해야 할 항목들입니다.",
            "sections": [
                ("📋 등기부등본 5분 확인법", """
<ul style="margin:0 0 0 18px;line-height:1.9">
  <li><strong>1단계 - 표제부</strong>: 주소·면적이 매물과 일치하는지</li>
  <li><strong>2단계 - 갑구</strong>: 압류·가압류·가처분 표시 있으면 위험 (계약 보류)</li>
  <li><strong>3단계 - 을구</strong>: 근저당권 채권최고액 + 본인 전세금이 시세의 80% 초과시 위험</li>
  <li><strong>4단계 - 소유자 일치</strong>: 계약서 임대인 = 등기부 소유자</li>
  <li><strong>5단계 - 최신 발급</strong>: 발급일이 7일 이내</li>
</ul>"""),
                ("🏠 건축물대장 확인 포인트", """
<ul style="margin:0 0 0 18px;line-height:1.9">
  <li><strong>위반건축물 표시</strong> 여부 (있으면 보증보험 가입 불가)</li>
  <li><strong>실제 용도</strong>: 주거용/상업용 확인</li>
  <li><strong>준공 연도</strong>: 노후도 파악</li>
  <li>화곡동은 다세대·연립이 많아 <strong>"세대구분형"</strong> 표시 확인 중요</li>
</ul>"""),
                ("📅 계약 당일 체크", """
<ul style="margin:0 0 0 18px;line-height:1.9">
  <li><strong>임대인 본인 확인</strong>: 신분증 vs 등기부 일치</li>
  <li><strong>대리계약시</strong>: 위임장 + 인감증명서 필수</li>
  <li><strong>계약금</strong>: 임대인 본인 계좌로만 송금</li>
  <li><strong>특약사항</strong>: 입주청소·도배·장판 교체 시점 명시</li>
</ul>"""),
            ],
        },
        # 1: 보증금 보호
        {
            "title": "보증금 안전하게 지키는 5단계",
            "emoji": "🛡",
            "tag_extra": "보증금보호",
            "intro": "월세든 전세든 보증금을 떼이지 않으려면 다음 5단계를 반드시 따라야 합니다.",
            "sections": [
                ("🔒 5단계 보증금 보호 흐름", """
<ol style="margin:0 0 0 18px;line-height:1.9">
  <li><strong>등기부 확인</strong> (근저당·압류 체크)</li>
  <li><strong>전세보증보험 가입</strong> (HUG·SGI·HF 비교)</li>
  <li><strong>전입신고 + 확정일자</strong> 즉시 처리 (입주 당일)</li>
  <li><strong>주민센터 방문</strong> 또는 정부24 온라인 신청</li>
  <li><strong>계약서 분실 대비</strong> 사본 보관 (스마트폰 + 클라우드)</li>
</ol>"""),
                ("⚠️ 보증금 떼인 사례 패턴", """
<ul style="margin:0 0 0 18px;line-height:1.9">
  <li><strong>전입신고 지연</strong> → 후순위로 밀려 보증금 못 받음</li>
  <li><strong>근저당 무시</strong> → 경매시 우선순위 밀림</li>
  <li><strong>대리인 사기</strong> → 임대인 본인 확인 안 함</li>
  <li><strong>다가구 함정</strong> → 한 건물에 임차인 많아 후순위 위험</li>
</ul>"""),
            ],
        },
        # 2: 전세보증보험
        {
            "title": "전세보증보험 완전 정복 (HUG·SGI·HF 비교)",
            "emoji": "🛡",
            "tag_extra": "전세보증보험",
            "intro": "전세 사기 위험을 막는 가장 확실한 방법은 보증보험 가입입니다. 3개 보험사를 비교해드립니다.",
            "sections": [
                ("📊 3대 보증보험 비교", """
<div style="background:#fff;border:1px solid #e0e0e0;border-left:4px solid #1565c0;border-radius:10px;padding:14px 16px;margin-bottom:8px">
  <div style="font-size:16px;font-weight:700;color:#0d1f3c;margin-bottom:8px">🏛️ HUG (주택도시보증공사)</div>
  <div style="font-size:15px;line-height:1.9">
    <div>📊 <span style="color:#888">한도</span>: <strong>수도권 7억</strong></div>
    <div>💵 <span style="color:#888">요율</span>: <strong>0.115~0.154%</strong></div>
    <div>📋 <span style="color:#888">조건</span>: 전세금+선순위 ≤ 90%</div>
  </div>
</div>
<div style="background:#fff;border:1px solid #e0e0e0;border-left:4px solid #06d6a0;border-radius:10px;padding:14px 16px;margin-bottom:8px">
  <div style="font-size:16px;font-weight:700;color:#0d1f3c;margin-bottom:8px">🏦 SGI (서울보증보험)</div>
  <div style="font-size:15px;line-height:1.9">
    <div>📊 <span style="color:#888">한도</span>: <strong>10억</strong></div>
    <div>💵 <span style="color:#888">요율</span>: <strong>0.183%</strong></div>
    <div>📋 <span style="color:#888">조건</span>: 전세금 ≤ 시세 80%</div>
  </div>
</div>
<div style="background:#fff;border:1px solid #e0e0e0;border-left:4px solid #ef476f;border-radius:10px;padding:14px 16px;margin-bottom:8px">
  <div style="font-size:16px;font-weight:700;color:#0d1f3c;margin-bottom:8px">🏠 HF (주택금융공사)</div>
  <div style="font-size:15px;line-height:1.9">
    <div>📊 <span style="color:#888">한도</span>: <strong>7억</strong></div>
    <div>💵 <span style="color:#888">요율</span>: <strong>0.04~0.05%</strong></div>
    <div>📋 <span style="color:#888">조건</span>: 청년·신혼 전용</div>
  </div>
</div>
"""),
                ("⏰ 가입 시기", """
<ul style="margin:0 0 0 18px;line-height:1.9">
  <li>가장 안전: <strong>잔금일 이전</strong> 가입 (이상적)</li>
  <li>가능 시점: 계약 만료 <strong>6개월 전까지</strong></li>
  <li>너무 늦으면: 보장 기간 줄어들고 일부 보험사는 거절</li>
</ul>"""),
            ],
        },
        # 3: 월세 협상 팁
        {
            "title": "월세 협상 실전 가이드",
            "emoji": "💰",
            "tag_extra": "월세협상",
            "intro": f"{region}에서 월세 계약 시 협상 가능한 범위와 협상 노하우를 정리했습니다.",
            "sections": [
                ("💬 3단계 협상 프로세스", """
<ol style="margin:0 0 0 18px;line-height:1.9">
  <li><strong>1단계 - 시세 조사</strong>: 월세 시세표(화요일 글)에서 본인 조건의 중위(시세) 확인</li>
  <li><strong>2단계 - 증거 수집</strong>: 같은 단지·인근 단지 실거래 3건 캡처</li>
  <li><strong>3단계 - 제안</strong>: "같은 평형·비슷한 보증금에 월세 ○○만원이 시세라, 시세선에 맞춰주시면 바로 계약하겠다"</li>
</ol>"""),
                ("📊 협상 여지 큰 조건", """
<ul style="margin:0 0 0 18px;line-height:1.9">
  <li><strong>장기 공실 매물</strong> (포털 등록일 1달 이상)</li>
  <li><strong>비수기</strong> (1~2월, 7~8월)</li>
  <li><strong>장기 계약 약속</strong> (2년 갱신 의지 명확화)</li>
  <li><strong>현금 일시불 보증금</strong> (대출 조건 매물 협상 어려움)</li>
</ul>"""),
                ("⚠️ 협상 금기 사항", """
<ul style="margin:0 0 0 18px;line-height:1.9">
  <li>계약서 작성 직전 가격 깎기 시도 (감정 상해 위험)</li>
  <li>임대인 인격적 평가나 비교</li>
  <li>"○○ 부동산 사장님이 이 가격에 해준다더라" (확인 불가)</li>
</ul>"""),
            ],
        },
        # 4: 중개수수료·세금
        {
            "title": "중개수수료·세금 한 번에 정리",
            "emoji": "💵",
            "tag_extra": "중개수수료",
            "intro": "임차 계약시 부담하는 비용을 미리 계산해서 협상 근거로 활용하세요.",
            "sections": [
                ("📊 중개수수료 요율표 (서울 기준)", """
<div style="background:#fff;border:1px solid #e0e0e0;border-left:4px solid #ef476f;border-radius:10px;padding:14px 16px;margin-bottom:8px">
  <div style="font-size:16px;font-weight:700;color:#0d1f3c;margin-bottom:8px">💰 월세</div>
  <div style="font-size:15px;line-height:1.9">
    <div><span style="color:#888">5천만원 미만</span>: <strong>0.5%</strong> (한도 20만원)</div>
    <div><span style="color:#888">5천만원~1억</span>: <strong>0.4%</strong> (한도 30만원)</div>
  </div>
</div>
<div style="background:#fff;border:1px solid #e0e0e0;border-left:4px solid #06d6a0;border-radius:10px;padding:14px 16px;margin-bottom:8px">
  <div style="font-size:16px;font-weight:700;color:#0d1f3c;margin-bottom:8px">🏠 전세</div>
  <div style="font-size:15px;line-height:1.9">
    <div><span style="color:#888">5천만원 미만</span>: <strong>0.5%</strong> (한도 20만원)</div>
    <div><span style="color:#888">5천만원~1억</span>: <strong>0.4%</strong> (한도 30만원)</div>
    <div><span style="color:#888">1억~6억</span>: <strong>0.3%</strong> (한도 없음)</div>
  </div>
</div>
<div style="font-size:11px;color:#888;margin-top:6px">💡 월세 거래대금 = 보증금 + (월세 × 100)</div>"""),
                ("💰 추가 비용 체크", """
<ul style="margin:0 0 0 18px;line-height:1.9">
  <li><strong>전세보증보험료</strong>: 전세금의 0.04~0.18%</li>
  <li><strong>이사비</strong>: 일반 100~150만원, 포장이사 200~300만원</li>
  <li><strong>입주청소</strong>: 평당 1~2만원 (10평 → 15만원선)</li>
  <li><strong>도배·장판</strong>: 임대인 부담이 원칙 (협상 사항)</li>
</ul>"""),
            ],
        },
        # 5: 이사철 가이드
        {
            "title": "이사 잘하는 법 (체크리스트)",
            "emoji": "📦",
            "tag_extra": "이사가이드",
            "intro": "이사 한 번에 하루가 망가지는 일이 없도록, 30일 전부터 준비해야 할 항목을 정리했습니다.",
            "sections": [
                ("📅 D-30: 이사 30일 전", """
<ul style="margin:0 0 0 18px;line-height:1.9">
  <li>이사 업체 견적 3곳 비교</li>
  <li>입주청소 예약 (이사일 ±1일)</li>
  <li>도배·장판 협상·진행</li>
  <li>인터넷·TV 이전 또는 신규 신청</li>
</ul>"""),
                ("📅 D-7: 이사 1주일 전", """
<ul style="margin:0 0 0 18px;line-height:1.9">
  <li>전입신고 준비 (신분증·임대차계약서)</li>
  <li>택배 주소 변경 (쿠팡·로켓·이커머스)</li>
  <li>가스·전기·수도 정산 예약</li>
  <li>버릴 물건 정리 (대형폐기물 신고)</li>
</ul>"""),
                ("📅 D-Day: 이사 당일", """
<ul style="margin:0 0 0 18px;line-height:1.9">
  <li>이전 집 가스 점검 + 폐쇄</li>
  <li>새 집 도착 → 가스 점검 + 개방</li>
  <li>전입신고 + 확정일자 (당일 처리!)</li>
  <li>이사 비용 결제 (송금·카드)</li>
</ul>"""),
            ],
        },
        # 6: 부동산 용어
        {
            "title": "부동산 용어 사전 (임차인 필수 30개)",
            "emoji": "📖",
            "tag_extra": "부동산용어",
            "intro": "계약서·매물 정보를 봤을 때 막히지 않도록, 자주 나오는 용어 30개를 정리했습니다.",
            "sections": [
                ("📚 계약 관련 용어", """
<ul style="margin:0 0 0 18px;line-height:1.9">
  <li><strong>임차인</strong>: 빌리는 사람 (=나)</li>
  <li><strong>임대인</strong>: 빌려주는 사람 (=집주인)</li>
  <li><strong>가계약금</strong>: 본계약 전 마음 잡는 돈 (보통 10만원)</li>
  <li><strong>계약금</strong>: 보증금의 10% (해약 시 위약금)</li>
  <li><strong>중도금·잔금</strong>: 입주 전 분할 납부</li>
  <li><strong>특약</strong>: 계약서 추가 약속 사항</li>
</ul>"""),
                ("📚 권리·세금 용어", """
<ul style="margin:0 0 0 18px;line-height:1.9">
  <li><strong>근저당권</strong>: 임대인이 빌린 돈 (담보)</li>
  <li><strong>채권최고액</strong>: 근저당 최대 한도 (실제 빚의 120%)</li>
  <li><strong>대항력</strong>: 새 집주인에게도 권리 주장 가능 (전입+확정일자)</li>
  <li><strong>우선변제권</strong>: 경매시 보증금 먼저 받을 권리</li>
  <li><strong>최우선변제</strong>: 소액 임차인 우선 보호 (서울 5천만원 이하)</li>
</ul>"""),
                ("📚 매물 표시 용어", """
<ul style="margin:0 0 0 18px;line-height:1.9">
  <li><strong>전용면적 vs 공급면적</strong>: 전용 = 실제 거주, 공급 = 공용 포함</li>
  <li><strong>실평수 vs 분양평수</strong>: 실평수 = 전용면적, 분양평수 = 공급면적</li>
  <li><strong>융자</strong>: 임대인이 받은 대출 (= 근저당)</li>
  <li><strong>풀옵션</strong>: 세탁기·냉장고·에어컨·가구 포함</li>
  <li><strong>옥탑·반지하·다락</strong>: 시세 할인 요인</li>
</ul>"""),
            ],
        },
        # 7: 화곡동 지역 정보
        {
            "title": "화곡동 임차 전 꼭 알아야 할 지역 정보",
            "emoji": "🗺",
            "tag_extra": "화곡동정보",
            "intro": "화곡동에 처음 이사 오시는 분이라면 알아두면 좋을 지역 특성과 생활 정보입니다.",
            "sections": [
                ("🚇 교통 인프라", """
<ul style="margin:0 0 0 18px;line-height:1.9">
  <li><strong>5호선 화곡역</strong>: 광화문·여의도 30분대</li>
  <li><strong>2·5호선 까치산역</strong>: 환승 가능, 합정·홍대 빠름</li>
  <li><strong>5호선 우장산역</strong>: 마곡지구 5분, 김포공항 10분</li>
  <li><strong>9호선 발산역</strong>: 강남 직통 (급행)</li>
</ul>"""),
                ("🏫 학군·교육", """
<ul style="margin:0 0 0 18px;line-height:1.9">
  <li><strong>주요 초등학교</strong>: 화곡·강신·발산·우장초</li>
  <li><strong>중학교</strong>: 화곡중·세현중·등명중</li>
  <li>강서구청·구민회관 도서관 (학습 공간)</li>
</ul>"""),
                ("🛒 생활 인프라", """
<ul style="margin:0 0 0 18px;line-height:1.9">
  <li><strong>대형마트</strong>: 까치산 이마트·홈플러스</li>
  <li><strong>전통시장</strong>: 화곡본동 시장, 까치산 시장</li>
  <li><strong>병원</strong>: 강서연세병원·이대서울병원 (방화동)</li>
  <li><strong>공원</strong>: 우장산 근린공원, 봉제산</li>
</ul>"""),
            ],
        },
        # 8: 전세 사기 예방
        {
            "title": "전세 사기 5대 유형과 예방법",
            "emoji": "🚨",
            "tag_extra": "전세사기예방",
            "intro": "최근 늘어난 전세 사기 유형을 미리 알아두면 피해를 막을 수 있습니다.",
            "sections": [
                ("🚨 5대 전세 사기 유형", """
<ol style="margin:0 0 0 18px;line-height:1.9">
  <li><strong>깡통 전세</strong>: 시세 대비 전세금이 너무 높은 매물 (시세 80% 초과)</li>
  <li><strong>이중 계약</strong>: 한 매물에 2명 이상에게 전세금 받음</li>
  <li><strong>위임장 사기</strong>: 가짜 위임장으로 대리계약</li>
  <li><strong>다가구 함정</strong>: 한 건물에 임차인 많아 후순위 보증금 떼임</li>
  <li><strong>매매 전환</strong>: 전세 들고 임대인이 집을 팔아 새 주인이 보증금 거부</li>
</ol>"""),
                ("🛡 5중 안전망", """
<ul style="margin:0 0 0 18px;line-height:1.9">
  <li><strong>등기부등본</strong>: 근저당·압류 확인</li>
  <li><strong>임대인 본인 확인</strong>: 신분증 vs 등기부 일치</li>
  <li><strong>시세 대비 80% 이하</strong>: 깡통 전세 회피</li>
  <li><strong>전세보증보험 가입</strong>: HUG·SGI·HF 중 선택</li>
  <li><strong>전입 + 확정일자</strong>: 입주 당일 즉시 처리</li>
</ul>"""),
            ],
        },
        # 9: 분기 점검 (특수)
        {
            "title": "내 임차 계약 분기 점검 가이드",
            "emoji": "🔍",
            "tag_extra": "계약점검",
            "intro": "이미 거주 중이라면 3개월에 한 번씩 본인 계약 상태를 점검해보세요.",
            "sections": [
                ("📋 분기 점검 체크리스트", """
<ul style="margin:0 0 0 18px;line-height:1.9">
  <li><strong>등기부 재발급</strong>: 임대인이 새 대출 받았는지 확인</li>
  <li><strong>시세 변동 확인</strong>: 본인 임차 조건이 여전히 적정한지</li>
  <li><strong>관리비 정산</strong>: 평균 대비 과도하지 않은지</li>
  <li><strong>계약서 사본 보관</strong>: 스마트폰 + 클라우드 백업</li>
  <li><strong>전세보증보험 만기</strong>: 갱신 시기 확인 (계약 6개월 전)</li>
</ul>"""),
                ("⚠️ 즉시 조치가 필요한 신호", """
<ul style="margin:0 0 0 18px;line-height:1.9">
  <li>등기부에 새 근저당 추가 → 임대인에게 즉시 확인</li>
  <li>임대인 연락 두절 → 우편 통지 보관 (증거)</li>
  <li>건물 매매 알림 → 새 임대인과 계약 승계 확인</li>
  <li>주변 시세 급락 → 갱신 시 인하 협상 가능</li>
</ul>"""),
            ],
        },
    ]
    
    topic = topics[topic_idx]
    
    # ────────────────────────────────────────
    # 본문 조립
    # ────────────────────────────────────────
    _htitle = topic['title'] if topic['title'].startswith(region) else f"{region} {topic['title']}"
    body = header_html(topic["emoji"], "TENANT GUIDE",
                       _htitle, region, date_str,
                       subtitle="📌 매주 일요일 발행 · 매주 다른 임차인 가이드")
    body += summary_box([
        f"오늘 주제: <strong>{topic['title']}</strong>",
        "핵심만 항목별로 — 3분 컷",
        "끝에서 바로 쓸 체크 행동 하나 가져가세요",
    ])
    body += section("📌 오늘, 이것만 알면 되는 주제",
                   f"안녕하세요, 렌트체크강서입니다.<br>{topic['intro']}<br><br><strong>「{topic['title']}」</strong> — 오늘 이거 하나만 챙기면 됩니다.")
    
    for sec_title, sec_content in topic["sections"]:
        body += section(sec_title, sec_content)
    
    body += render_checker_guide(report)
    body += oneline_box("오늘 것 하나만 실행해도 보증금 사고 확률이 달라집니다 — 저장해두고 계약 전날 다시 여세요.")
    body += footer_html([region, "임차인가이드", "전세사기예방", "보증금", "임대차계약", "부동산상식", "화곡동임차", "강서구부동산", topic["tag_extra"]])
    
    return {
        "title": (f"{topic['title']} ({date_str})" if topic['title'].startswith(region) else f"{region} {topic['title']} ({date_str})"),
        "category": "tenant_guide",
        "html": body,
        "tags": [region, "임차인가이드", "전세사기예방", "보증금", "임대차계약", "부동산상식", "화곡동임차", "강서구부동산", topic["tag_extra"]],
    }


def gen_simple(category_key: str, title_template: str, intro: str, emoji: str, category_label: str, extra_section: str = "") -> callable:
    """간단한 카테고리용 공장 함수 (호환성 유지)"""
    def _gen(report, csv_rows, date_str):
        region = report["region"]
        title = title_template.format(region=region, date=date_str)
        body = (
            header_html(emoji, category_label, title, region, date_str)
            + section("오늘의 주제", intro.format(region=region))
            + render_matrix_table(report)
            + extra_section
            + render_checker_guide(report)
            + footer_html([region, category_label.lower(), "실거래"])
        )
        return {
            "title": title,
            "category": category_key,
            "html": body,
            "tags": [region, category_label.lower()],
        }
    return _gen




# 카테고리 → 함수 매핑
CATEGORY_FN = {
    "weekly_summary": gen_weekly_summary,
    "rent_check": gen_rent_check,
    "building_spotlight": gen_building_spotlight,
    "jeonse_vs_monthly": gen_jeonse_vs_monthly,
    "value_picks": gen_value_picks,
    "neighborhood": gen_neighborhood,
    "tenant_guide": gen_tenant_guide,
}


def load_data(json_path: str, csv_path: str):
    # aggregated_rent.json이 없어도 죽지 않게 (Actions 환경 — CSV만으로 요일글 생성)
    try:
        with open(json_path, encoding="utf-8") as f:
            report = json.load(f)
    except FileNotFoundError:
        print(f"  · {json_path} 없음 → 기본 구조로 진행 (CSV 기반 생성)")
        report = {"region": "화곡동", "matrix": {}, "total_monthly": 0}
    csv_rows = []
    excluded = 0
    with open(csv_path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            # 해제(취소)된 거래 제외 — cdeal_type='O'이면 계약 해제 건이라 시세에서 뺌
            if str(r.get("cdeal_type", "")).strip().upper() == "O":
                excluded += 1
                continue
            csv_rows.append(r)
    if excluded:
        print(f"  · 해제 거래 {excluded}건 제외")
    return report, csv_rows


def save_post(post: dict, date_str: str, out_dir: str = "outputs/blog"):
    """블로그 글 저장 (단독 HTML + 네이버 붙여넣기 스니펫)"""
    # 월간 리포트(monthly_*)는 별도 폴더(monthly_YYYYMM)에 모음 — 화·수·목 분산 발행이라 한 폴더에서 꺼내기 편하게
    if str(post.get("category", "")).startswith("monthly"):
        folder = Path(out_dir) / f"monthly_{date_str.replace('-', '')[:6]}"
    else:
        folder = Path(out_dir) / date_str.replace("-", "")
    folder.mkdir(parents=True, exist_ok=True)

    # 본문 마지막에 위젯 링크 박스 자동 추가 (footer 직전)
    html = post["html"]
    if "RENT CHECKER" not in html:  # 중복 방지
        footer_marker = '<div style="margin-top:32px;padding-top:18px;border-top'
        if footer_marker in html:
            html = html.replace(footer_marker, render_link_box() + footer_marker, 1)
        else:
            html += render_link_box()
        post["html"] = html

    # 본문만 (네이버 붙여넣기용)
    snippet_path = folder / f"{post['category']}_본문.html"
    with open(snippet_path, "w", encoding="utf-8") as f:
        f.write(post["html"])

    # 단독 미리보기 (브라우저로 열어볼 때)
    full = f"""<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{post['title']}</title><style>body{{max-width:780px;margin:0 auto;padding:20px;background:#fafafa;font-family:'Malgun Gothic',sans-serif}}</style>
</head><body>{post['html']}</body></html>"""
    preview_path = folder / f"{post['category']}_미리보기.html"
    with open(preview_path, "w", encoding="utf-8") as f:
        f.write(full)

    # 메타 정보 (제목, 태그, 카테고리)
    # 카테고리를 네이버 블로그 한글 경로로 변환 (발행 시 어느 카테고리에 넣을지 안내)
    # 2026.7.4 카테고리 대개편: 요일 카테고리 폐지 → 주제 6대분류. 단지명은 카테고리 아닌 제목으로.
    CATEGORY_KO = {
        "weekly_summary": "실거래·시세 > 주간 거래결산",
        "building_spotlight": "실거래·시세 > 단지별 시세",
        "jeonse_vs_monthly": "실거래·시세 > 화곡동 월세",
        "value_picks": "실거래·시세 > 단지별 실거래 추적",
        "neighborhood": "실거래·시세 > 거래활발 단지",
        "monthly_report": "실거래·시세 > 월간 시세 리포트",
        "monthly_apt": "실거래·시세 > 월간 시세 리포트",
        "monthly_villa": "실거래·시세 > 월간 시세 리포트",
        "monthly_officetel": "실거래·시세 > 월간 시세 리포트",
    }
    cat = post["category"]
    sub = post.get("sub_category", "")
    if cat == "rent_check":
        # 화요일: 주차별 월세/전세/매매, 마지막 화요일=월간
        if sub == "월간 시세 리포트":
            cat_label = "실거래·시세 > 월간 시세 리포트"
        else:
            cat_label = f"실거래·시세 > 화곡동 {sub or '월세'}"
    elif cat == "building_spotlight":
        cat_label = "실거래·시세 > 단지별 시세" + (f"  (단지명 '{sub}'은 제목에)" if sub and sub != "단지별 시세 분석" else "")
    elif cat == "tenant_guide":
        cat_label = "임차인 가이드 > 주제에 맞게(계약/전세/월세/계약갱신/중개수수료/체크리스트)"
    else:
        cat_label = CATEGORY_KO.get(cat, cat)
    meta_path = folder / f"{post['category']}_메타.txt"
    with open(meta_path, "w", encoding="utf-8") as f:
        f.write(f"제목: {post['title']}\n")
        f.write(f"태그: {' '.join('#' + t for t in build_tags(post['tags']))}\n")
        f.write(f"카테고리: {cat_label}\n")

    # 대문 PNG 자동 생성 (2026.7.4 — thumb_maker v3 카테고리 스타일, 실패해도 글 생성엔 영향 없음)
    try:
        from thumb_maker import auto_thumb
        tp = auto_thumb(post, date_str, folder)
        print(f"  ✦ 대문 생성 → {tp}")
    except Exception as e:
        print(f"  (대문 생성 생략: {e})")
    return snippet_path


# ═══════════════ 글감 스캐너 (2026.7.4 추가) ═══════════════
# CSV 이상신호 자동 감지 → 컬럼 글감 후보. today 실행 시 자동 동작, 단독은 `python daily_content.py scan`
KNOWN_SIGNALS = {  # 이미 글로 다뤘거나 확인된 곳 (제외)
    ("화곡동","163-7"),("화곡동","155-22"),("화곡동","24-345"),("화곡동","1083-14"),
    ("화곡동","24-104"),("화곡동","24-539"),("화곡동","964-18"),("화곡동","807-6"),
}

def scan_signals(csv_rows, date_str, out_dir="outputs/blog"):
    """이상신호 3종: ①같은날 매매 4건+(일괄매입 후보) ②신축 월세전용(공공임대 후보) ③급등·급락"""
    from collections import defaultdict
    def g(r,k): return (r.get(k) or "").strip()
    cur_year = int(date_str[:4])
    sig=[]

    # ① 같은 날 매매 4건+ (일괄매입·통매각 후보)
    clu=defaultdict(list)
    for r in csv_rows:
        if "매매" in g(r,"deal_type"):
            clu[(g(r,"umd_name"), g(r,"building_name"), g(r,"jibun"), g(r,"deal_ym"), g(r,"deal_day"))].append(r)
    for (umd,nm,jb,ym,day),rs in clu.items():
        if len(rs)>=4 and (umd,jb) not in KNOWN_SIGNALS:
            amts=[int(g(r,"deal_amount") or 0) for r in rs]
            sig.append((len(rs)*10, f"[일괄후보] {umd} {nm or jb}({jb}) — {ym[:4]}.{ym[4:]}.{int(day):02d} 하루 {len(rs)}건, {min(amts)/10000:.2f}~{max(amts)/10000:.2f}억 → 플레이스 역추적/등기 확인"))

    # ② 신축(2년내 준공) 월세전용 5건+ & 매매·전세 0 (공공 매입임대 후보)
    prof=defaultdict(lambda:{"mm":0,"js":0,"ws":0,"nm":"","by":""})
    for r in csv_rows:
        by=g(r,"build_year")
        if by.isdigit() and int(by)>=cur_year-2:
            p=prof[(g(r,"umd_name"), g(r,"jibun"))]; p["nm"]=g(r,"building_name"); p["by"]=by
            if "매매" in g(r,"deal_type"): p["mm"]+=1
            elif int(g(r,"monthly_rent") or 0)==0: p["js"]+=1
            else: p["ws"]+=1
    for (umd,jb),p in prof.items():
        if p["mm"]==0 and p["js"]==0 and p["ws"]>=5 and (umd,jb) not in KNOWN_SIGNALS:
            sig.append((p["ws"]*5, f"[신축월세전용] {umd} {p['nm'] or jb}({jb}, {p['by']}준공) — 월세만 {p['ws']}건 → 청년임대/공공매입 여부 확인(오피는 평범할 수 있음)"))

    # ③ 급등·급락: 같은 단지·비슷한 면적(±3㎡) 최신 매매가 vs 직전 중위 ±25%
    grp=defaultdict(list)
    for r in csv_rows:
        if "매매" in g(r,"deal_type") and g(r,"deal_amount"):
            try: grp[(g(r,"umd_name"), g(r,"jibun"), round(float(g(r,"area_m2"))/3)*3)].append(r)
            except: pass
    for (umd,jb,ab),rs in grp.items():
        if len(rs)>=4 and (umd,jb) not in KNOWN_SIGNALS:
            rs.sort(key=lambda r:(g(r,"deal_ym"), int(g(r,"deal_day") or 0)))
            prev=[int(g(r,"deal_amount")) for r in rs[:-1]]; last=int(g(rs[-1],"deal_amount"))
            med=sorted(prev)[len(prev)//2]
            if med>0 and (last>=med*1.25 or last<=med*0.75):
                d="급등" if last>med else "급락"
                nm=g(rs[-1],"building_name") or jb
                sig.append((30, f"[{d}] {umd} {nm}({jb}, {ab}㎡대) — 직전 중위 {med/10000:.2f}억 → 최신 {last/10000:.2f}억 ({g(rs[-1],'deal_ym')[:4]}.{g(rs[-1],'deal_ym')[4:]}.{int(g(rs[-1],'deal_day') or 0):02d}) → 특수거래(직거래·증여) 여부 확인"))

    if not sig:
        return None
    sig.sort(reverse=True)
    folder = Path(out_dir) / date_str.replace("-","")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "글감스캔.txt"
    with open(path,"w",encoding="utf-8") as f:
        f.write(f"글감 스캐너 — {date_str} (신호 {len(sig)}건, 강한 순)\n")
        f.write("※ 후보일 뿐. 단정 금지 — 반드시 확인(플레이스 역추적/등기/공고) 후 글감으로.\n\n")
        for _,line in sig:
            f.write(line+"\n")
    return [line for _, line in sig]



def run_for_today(json_path: str, csv_path: str, target_date: datetime = None):
    """오늘 요일에 맞는 카테고리 1편 생성"""
    target_date = target_date or datetime.now()
    weekday = target_date.weekday()  # 0=월 ~ 6=일
    date_str = target_date.strftime("%Y-%m-%d")

    report, csv_rows = load_data(json_path, csv_path)
    category = WEEKDAY_CATEGORY[weekday]
    gen_fn = CATEGORY_FN[category]
    post = gen_fn(report, csv_rows, date_str)
    # 화요일 마지막주는 월간 3편(리스트)이 올 수 있음
    if isinstance(post, list):
        saved_list = []
        for p in post:
            s = save_post(p, date_str)
            print(f"  ✓ [{WEEKDAY_KO[weekday]}] {p['category']}: {s}")
            saved_list.append(s)
        return saved_list
    saved = save_post(post, date_str)
    print(f"  ✓ [{WEEKDAY_KO[weekday]}] {category}: {saved}")
    try:
        sp = scan_signals(csv_rows, date_str)
        if sp: print(f"  ✦ 글감 스캔 → {sp}")
        else: print("  ✦ 글감 스캔: 새 이상신호 없음")
    except Exception as e:
        print(f"  (글감 스캔 생략: {e})")
    return [saved]


def run_all_categories(json_path: str, csv_path: str, target_date: datetime = None):
    """7개 카테고리 전부 생성 (테스트/검수용)"""
    target_date = target_date or datetime.now()
    date_str = target_date.strftime("%Y-%m-%d")
    report, csv_rows = load_data(json_path, csv_path)

    results = []
    for cat, fn in CATEGORY_FN.items():
        post = fn(report, csv_rows, date_str)
        saved = save_post(post, date_str)
        print(f"  ✓ {cat}: {saved}")
        results.append(saved)
    return results


# ═══════════════════════════════════════════════════════════
# 티스토리 분석체 모듈 (분석체 어울리는 요일만)
# ═══════════════════════════════════════════════════════════

# 티스토리 버전 생성 요일 (0=월, 1=화, 2=수, 3=목)
TISTORY_WEEKDAYS = {0, 1, 2, 3}

# 위젯 임베드할 요일 (핵심 글만)
TISTORY_EMBED_WIDGET = {2, 3}  # 수(단지), 목(전세vs월세)

# 네이버 → 티스토리 문체 변환 사전
TISTORY_PHRASE_MAP = {
    # 감정 → 분석
    "꽤 높습니다": "상승 압력을 보이는 것으로 해석됩니다",
    "꽤 높아졌습니다": "상승 압력을 보이는 것으로 해석됩니다",
    "비싼 편입니다": "시세(중위) 대비 다소 높은 구간에 위치합니다",
    "저렴한 편입니다": "시세(중위) 대비 낮은 구간에 위치합니다",
    "꼭 확인하세요": "검토할 필요가 있습니다",
    "꼭 확인해야": "검토할 필요가",
    "확인하세요": "확인할 필요가 있습니다",
    "주목하세요": "주목할 필요가 있습니다",
    "참고하세요": "참고하시기 바랍니다",
    "활용하세요": "활용하시기 바랍니다",
    "가늠하는": "파악하는",
    "한눈에 보입니다": "확인할 수 있습니다",
    "한 눈에": "한 번에",
    "정말 좋습니다": "양호한 수준으로 평가됩니다",
    "엄청": "상당히",
    "완전": "전반적으로",
    "정말": "비교적",
    "대박": "주목할 만한",
    "1등": "최상위",
    "최고": "최상위",
    "최저": "최하위",
    "확실히": "분명히",
    "쉽게": "효율적으로",
    "빠르게": "신속하게",
    # 단정 → 추정
    "입니다.": "로 판단됩니다.",
    "보입니다.": "관찰됩니다.",
    "나옵니다.": "관찰됩니다.",
    "나타납니다.": "관찰됩니다.",
}

# 티스토리 글 하단 고정 문구
TISTORY_FOOTER_NOTE = """본 자료는 국토교통부 실거래 공개자료를 기반으로 정리한 참고용 분석입니다.
개별 계약 판단 시에는 건물 상태, 층수, 관리비, 보증금 구조, 권리관계 등을 함께 확인해야 합니다."""


def convert_to_tistory_tone(text: str) -> str:
    """네이버 톤 텍스트를 티스토리 분석체로 변환"""
    if not text: return text
    result = text
    for old, new in TISTORY_PHRASE_MAP.items():
        result = result.replace(old, new)
    return result


def tistory_html_template(title: str, subtitle: str, sections: list, tags: list,
                          embed_widget: bool = False, date_str: str = "") -> str:
    """티스토리용 HTML 템플릿 - 분석 보고서 톤"""
    
    # 섹션 HTML 조립
    sections_html = ""
    for i, sec in enumerate(sections, 1):
        sec_num = f"SECTION {i:02d}"
        sections_html += f'''
<div style="margin-bottom:32px">
  <div style="font-size:11px;font-weight:700;color:#c9a444;letter-spacing:2px;margin-bottom:6px">{sec_num}</div>
  <h2 style="font-size:19px;font-weight:700;color:#1a1a1a;margin-bottom:14px;padding-bottom:10px;border-bottom:2px solid #0d1f3c;letter-spacing:-0.3px">{sec.get("title", "")}</h2>
  <div style="font-size:16px;line-height:1.85;color:#2a2a2a">
    {sec.get("content", "")}
  </div>
</div>
'''
    
    # 위젯 임베드 (선택)
    widget_embed = ""
    if embed_widget:
        widget_embed = '''
<div style="background:#fff;border:2px solid #1a1a1a;border-radius:8px;margin:24px 0;overflow:hidden">
  <div style="background:linear-gradient(135deg,#1a1a1a 0%,#2a3a55 100%);color:#fff;padding:16px 22px;display:flex;justify-content:space-between;align-items:center;gap:14px">
    <div>
      <div style="font-size:11px;color:#c9a444;letter-spacing:2px;font-weight:700;margin-bottom:4px">RENT CHECKER</div>
      <div style="font-size:16px;font-weight:600;line-height:1.5">아래 도구로 본인 계약 조건의 적정성을 즉시 검증할 수 있습니다</div>
    </div>
    <div style="background:#c9a444;color:#1a1a1a;padding:5px 10px;border-radius:12px;font-size:11px;font-weight:700;white-space:nowrap;flex-shrink:0">실시간 분석</div>
  </div>
  <iframe src="https://arttoy61-png.github.io/rent-check/" style="width:100%;height:1000px;border:none;display:block;background:#f5f7fa" loading="lazy" title="화곡동 월세 시세 검증 도구"></iframe>
  <p style="background:#fafbfc;padding:12px 22px;font-size:12px;color:#555;line-height:1.7;border-top:1px solid #e5e5e5;margin:0">
    위 도구는 국토교통부 실거래가 공개자료를 기반으로 작동합니다. 본인의 계약 조건(평형·보증금·월세)을 입력하면 시세(중위)와의 격차·실거래 사례와의 비교 결과가 제시됩니다.
  </p>
</div>

<a href="https://arttoy61-png.github.io/rent-check/" style="display:flex;align-items:center;gap:14px;background:#fff;border:1px solid #1a1a1a;border-radius:6px;padding:14px 18px;text-decoration:none;color:#1a1a1a;margin:14px 0" target="_blank">
  <div style="flex:1">
    <div style="font-size:15px;font-weight:700;margin-bottom:3px">🔍 위젯을 새 창에서 크게 보기</div>
    <div style="font-size:11px;color:#6b6b6b">모바일에서 편하게 사용하려면 새 창 열기 권장</div>
  </div>
  <div style="font-size:18px;color:#c9a444">→</div>
</a>
'''
    
    # 태그
    tags_html = "".join(f'<span style="display:inline-block;padding:4px 10px;background:#f0f2f5;color:#555;border-radius:12px;font-size:12px;margin-right:5px;margin-bottom:5px">#{t}</span>' for t in tags)
    
    return f'''<div style="max-width:760px;margin:0 auto;background:#fff;padding:0 32px;font-family:'Noto Sans KR','Malgun Gothic',sans-serif;color:#1a1a1a;line-height:1.85;box-sizing:border-box">
<style>
@media (max-width: 600px) {{
  div[style*="max-width:760px"][style*="padding:0 32px"] {{
    padding: 0 18px !important;
  }}
}}
</style>

<!-- 헤더 -->
<div style="padding:32px 0 24px;border-bottom:3px solid #1a1a1a;margin-bottom:28px">
  <div style="display:inline-block;font-size:11px;letter-spacing:1.5px;color:#6b6b6b;margin-bottom:14px;padding:4px 10px;background:#f0f2f5;border-radius:3px">MARKET ANALYSIS · WEEKLY REPORT</div>
  <h1 style="font-size:24px;font-weight:700;line-height:1.4;color:#1a1a1a;margin-bottom:10px;letter-spacing:-0.5px">{title}</h1>
  <p style="font-size:16px;color:#555;line-height:1.7">{subtitle}</p>
  <div style="margin-top:14px;padding-top:12px;border-top:1px solid #e5e5e5;font-size:12px;color:#888">
    📊 분석 일자: {date_str} · 🏢 분석 대상: 서울 강서구 화곡동
  </div>
</div>

<!-- 섹션들 -->
{sections_html}

{widget_embed if embed_widget else ""}

<!-- 태그 -->
<div style="margin-top:24px;padding-top:18px;border-top:1px solid #e0e0e0;line-height:1.9">
{tags_html}
</div>

<!-- 푸터 -->
<div style="background:#f8f9fb;padding:20px;border-top:1px solid #e0e0e0;font-size:12px;color:#6b6b6b;line-height:1.8;margin-top:24px;border-radius:4px">
  <div style="font-size:15px;font-weight:700;color:#1a1a1a;margin-bottom:8px">📌 자료 출처 및 참고 사항</div>
  <p style="margin:0">{TISTORY_FOOTER_NOTE.replace(chr(10), "<br>")}</p>
</div>

</div>
'''


# ─── 티스토리 글 생성 함수들 ───

def gen_tistory_weekly_summary(report: dict, csv_rows: list, date_str: str) -> dict:
    """(월) 주간결산 - 티스토리 분석체 버전"""
    region = report["region"]
    target_date = datetime.strptime(date_str, "%Y-%m-%d")
    
    def to_int(v):
        try: return int(str(v).replace(",", "")) if v else 0
        except: return 0
    def to_float(v):
        try: return float(v) if v else 0.0
        except: return 0.0
    def deal_date_obj(r):
        ym = r.get("deal_ym", ""); day = r.get("deal_day", "")
        if not ym or not day: return None
        try: return datetime(int(ym[:4]), int(ym[4:6]), int(day))
        except: return None
    
    # 지난주(월~일)
    weekday = target_date.weekday()
    this_monday = target_date - timedelta(days=weekday)
    week_start = this_monday - timedelta(days=7)
    week_end = week_start + timedelta(days=6)
    
    recent_rows = [r for r in csv_rows if (d := deal_date_obj(r)) and week_start <= d <= week_end]
    
    sales = [r for r in recent_rows if "매매" in r.get("deal_type", "")]
    rents = [r for r in recent_rows if "전월세" in r.get("deal_type", "")]
    jeonse = [r for r in rents if to_int(r.get("monthly_rent")) == 0 and to_int(r.get("deposit")) > 0]
    monthly = [r for r in rents if to_int(r.get("monthly_rent")) > 0]
    
    total = len(recent_rows)
    week_range = f"{week_start.strftime('%m월 %d일')} ~ {week_end.strftime('%m월 %d일')}"
    
    # 일별 거래량 (SVG 차트용)
    daily_counts = {}
    for r in recent_rows:
        d = deal_date_obj(r)
        if d: daily_counts[d.strftime("%m/%d")] = daily_counts.get(d.strftime("%m/%d"), 0) + 1
    
    days_in_week = [(week_start + timedelta(days=i)) for i in range(7)]
    daily_data = [(d.strftime("%m/%d"), ["월","화","수","목","금","토","일"][d.weekday()], daily_counts.get(d.strftime("%m/%d"), 0)) for d in days_in_week]
    max_count = max([c for _,_,c in daily_data] + [1])
    
    # SVG 일별 차트
    chart_svg = f'''<svg viewBox="0 0 600 220" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block">
  <line x1="50" y1="20" x2="580" y2="20" stroke="#e5e5e5" stroke-dasharray="3,3"/>
  <line x1="50" y1="65" x2="580" y2="65" stroke="#e5e5e5" stroke-dasharray="3,3"/>
  <line x1="50" y1="110" x2="580" y2="110" stroke="#e5e5e5" stroke-dasharray="3,3"/>
  <line x1="50" y1="155" x2="580" y2="155" stroke="#1a1a1a" stroke-width="1"/>
  <text x="585" y="14" text-anchor="end" font-size="10" fill="#888">(단위: 건)</text>'''
    
    for i, (date, day, cnt) in enumerate(daily_data):
        x = 80 + i * 75
        h = (cnt / max_count) * 130 if max_count > 0 else 2
        y = 155 - h
        color = "#1a1a1a" if cnt > 0 else "#d0d0d0"
        h = max(h, 2)
        chart_svg += f'''
  <rect x="{x}" y="{y}" width="55" height="{h:.0f}" fill="{color}" rx="2"/>
  <text x="{x+27.5}" y="{y-5}" text-anchor="middle" font-size="12" font-weight="700" fill="#1a1a1a">{cnt}</text>
  <text x="{x+27.5}" y="175" text-anchor="middle" font-size="11" fill="#555">{date}</text>
  <text x="{x+27.5}" y="190" text-anchor="middle" font-size="10" fill="#888">{day}</text>'''
    chart_svg += '</svg>'
    
    # 통계 박스
    def stat_box(label, value, sub):
        return f'<div style="background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:14px 12px;text-align:center"><div style="font-size:11px;color:#888;margin-bottom:6px">{label}</div><div style="font-size:18px;font-weight:700;color:#1a1a1a">{value}</div><div style="font-size:11px;color:#888;margin-top:4px">{sub}</div></div>'
    
    stat_row = f'<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:16px 0">{stat_box("전체 거래", f"{total}건", "신고 기준")}{stat_box("전세", f"{len(jeonse)}건", f"전체의 {round(len(jeonse)/total*100) if total else 0}%")}{stat_box("월세", f"{len(monthly)}건", f"전체의 {round(len(monthly)/total*100) if total else 0}%")}</div>'
    
    # 섹션 정의
    sections = [
        {
            "title": "분석 개요",
            "content": f'''
<p>2026년 5월 둘째 주(<strong>{week_range}</strong>) 기간 동안 {region}에서 신고된 부동산 실거래는 <strong>총 {total}건</strong>으로 확인됩니다. 
해당 기간의 거래는 매매 {len(sales)}건, 임대차(전월세) {len(rents)}건의 분포로 관찰되며, 본 분석은 신고 거래만을 대상으로 하므로 실제 계약 시점과는 시차가 존재할 수 있습니다.</p>
{stat_row}
'''
        },
        {
            "title": "데이터 기준",
            "content": f'''
<p>본 보고서에서 다루는 모든 거래 데이터는 <strong>국토교통부 실거래가 공개시스템</strong>에 신고된 자료를 기준으로 합니다. 호가나 매물 광고가 아닌, 실제 계약이 체결되어 신고된 거래만을 분석 대상으로 삼습니다.</p>
<div style="background:#fafbfc;border:1px solid #e5e5e5;border-left:4px solid #1a1a1a;padding:14px 20px;margin:14px 0;border-radius:0 6px 6px 0">
  <div style="font-size:11px;color:#6b6b6b;letter-spacing:1px;margin-bottom:6px;font-weight:600">분석 데이터 출처</div>
  <div style="font-size:16px;color:#1a1a1a;font-weight:700">국토교통부 실거래가 공개시스템 (rt.molit.go.kr)</div>
</div>
<p>실거래 신고는 통상 계약 후 30일 이내에 이루어지므로, 본 보고서에서 다루는 거래는 주로 직전 1~2개월의 계약 건으로 해석할 필요가 있습니다.</p>
'''
        },
        {
            "title": "주요 거래 흐름",
            "content": f'''
<p>지난주 거래량을 일별로 살펴보면 아래와 같은 분포가 관찰됩니다.</p>
<div style="background:#fafbfc;border:1px solid #e5e5e5;border-radius:6px;padding:18px;margin:16px 0">
  <div style="font-size:15px;font-weight:600;color:#1a1a1a;margin-bottom:12px;text-align:center">일별 신고 거래량 ({week_range})</div>
  {chart_svg}
  <div style="font-size:11px;color:#888;text-align:center;margin-top:8px">신고일 기준 / 계약일은 신고일 이전 30일 이내 분포</div>
</div>
<p>매매 {len(sales)}건과 전월세 {len(rents)}건의 분포는 {region}의 임대차 시장이 매매 시장보다 활성도가 높은 구조임을 시사하는 흐름으로 해석됩니다.</p>
'''
        },
    ]
    
    # 거래 카드 섹션 (4번)
    if jeonse or monthly:
        rent_cards_html = ""
        for r in (sorted(jeonse + monthly, key=lambda x: (x.get("deal_ym", ""), str(x.get("deal_day", "")).zfill(2)), reverse=True))[:8]:
            ym, day = r.get("deal_ym", ""), r.get("deal_day", "")
            d_str = f'{ym[:4]}.{ym[4:6]}.{str(day).zfill(2)}' if ym and day else "-"
            area = to_float(r.get("area_m2"))
            py = round(area/3.3058, 1) if area else "-"
            dep = to_int(r.get("deposit"))
            rent = to_int(r.get("monthly_rent"))
            name = r.get("building_name", "") or "-"
            if rent > 0:
                price = f'보증 <strong>{dep:,}만</strong> / 월세 <strong style="color:#c9a444">{rent}만</strong>'
                tag = '<span style="background:#c9a444;color:#1a1a1a;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:700">월세</span>'
            else:
                eok = dep // 10000; rest = dep % 10000
                price = f'<strong style="color:#2a3a55;font-size:16px">{eok}억 {rest:,}만</strong>' if eok else f'<strong>{dep:,}만</strong>'
                tag = '<span style="background:#2a3a55;color:#fff;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:700">전세</span>'
            rent_cards_html += f'<div style="background:#fff;border:1px solid #e5e5e5;border-radius:6px;padding:14px 16px;margin-bottom:8px"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px"><div style="font-weight:700;color:#1a1a1a;flex:1;min-width:0;font-size:16px">{name}</div>{tag}</div><div style="font-size:11px;color:#888;margin-bottom:5px">{py}평 · {d_str}</div><div style="font-size:15px">{price}</div></div>'
        
        sections.append({
            "title": "주요 임대차 실거래 사례",
            "content": f'<p>지난주 {region}에서 신고된 주요 임대차 거래 사례를 요약하면 다음과 같이 관찰됩니다.</p>{rent_cards_html}'
        })
    
    # 결론
    sections.append({
        "title": "결론",
        "content": f'''
<p>{week_range} 기간 {region}의 부동산 시장은 <strong>임대차 거래 {len(rents)}건 / 매매 {len(sales)}건</strong>의 분포를 보이며, 신고 거래 수치만 기준으로 보면 임대차 중심의 거래 흐름이 지속되는 것으로 해석됩니다.</p>
<div style="background:linear-gradient(135deg,#1a1a1a 0%,#2a3a55 100%);color:#fff;padding:22px 26px;border-radius:8px;margin:18px 0">
  <div style="font-size:12px;letter-spacing:2px;color:#c9a444;margin-bottom:8px">CORE INSIGHT</div>
  <div style="font-size:16px;line-height:1.8">{region} 임대차 계약을 검토 중이라면, 단순 호가가 아닌 <strong style="color:#c9a444">최근 실거래 사례</strong>와의 비교를 통해 본인 계약 조건의 객관적 위치를 파악하는 것이 합리적 의사결정의 출발점이 될 것으로 판단됩니다.</div>
</div>
<p>다음 주의 거래 흐름을 통해 매매 거래 회복 여부와 월세·전세 거래 비율의 변화를 지속적으로 관찰할 필요가 있습니다.</p>
'''
    })
    
    title = f"{region} 임대차 시장 동향: {week_range} 분석"
    subtitle = f"국토교통부 실거래 공개자료를 기반으로 {week_range} 기간의 {region} 임대차·매매 거래 흐름을 정리합니다."
    
    tags = [region, f"{region}실거래가", f"{region}전세", f"{region}월세", f"{region}매매", "임대차시장분석", "부동산실거래", "주간시장보고서", "강서구화곡동", "국토부실거래가"]
    
    html = tistory_html_template(title, subtitle, sections, tags, embed_widget=False, date_str=date_str)
    
    return {
        "title": title,
        "category": "weekly_summary_tistory",
        "html": html,
        "tags": tags,
    }


def gen_tistory_rent_check(report: dict, csv_rows: list, date_str: str) -> dict:
    """(화) 시세표 - 티스토리 분석체 (SVG 차트 + 분석 단락)"""
    region = report["region"]
    target_date = datetime.strptime(date_str, "%Y-%m-%d")
    
    def to_int(v):
        try: return int(str(v).replace(",", "")) if v else 0
        except: return 0
    def to_float(v):
        try: return float(v) if v else 0.0
        except: return 0.0
    def fmt_won(amt):
        if not amt: return "-"
        if amt >= 10000:
            eok = amt // 10000; rest = amt % 10000
            return f"{eok}억 {rest:,}만" if rest else f"{eok}억"
        return f"{amt:,}만"
    
    # 화요일 4주 롤링 결정
    week_of_month = (target_date.day - 1) // 7 + 1
    sub_idx = (week_of_month - 1) % 4  # 0:월세, 1:전세, 2:매매, 3:종합
    sub_names = ["월세", "전세", "매매", "시세 종합"]
    sub_name = sub_names[sub_idx]
    
    # 거래 데이터 분리 (최근 6개월 누적)
    rents = [r for r in csv_rows if "전월세" in str(r.get("deal_type", ""))]
    jeonse_rows = [r for r in rents if to_int(r.get("monthly_rent")) == 0 and to_int(r.get("deposit")) > 0]
    monthly_rows = [r for r in rents if to_int(r.get("monthly_rent")) > 0]
    sales_rows = [r for r in csv_rows if "매매" in str(r.get("deal_type", ""))]
    
    # 평형별 통계 계산
    def pyung_bin(area):
        py = area / 3.3058
        if py < 10: return "10평 미만"
        elif py < 15: return "10~15평"
        elif py < 20: return "15~20평"
        elif py < 25: return "20~25평"
        elif py < 30: return "25~30평"
        else: return "30평+"
    
    pyung_order = ["10평 미만", "10~15평", "15~20평", "20~25평", "25~30평", "30평+"]
    
    # 주차별 데이터·차트 생성
    if sub_idx == 0:  # 월세
        # 평형별 월세 분포
        pyung_rents = {p: [] for p in pyung_order}
        for r in monthly_rows:
            area = to_float(r.get("area_m2"))
            rent = to_int(r.get("monthly_rent"))
            if area > 0 and rent > 0:
                pyung_rents[pyung_bin(area)].append(rent)
        
        # SVG 차트: 평형별 중위 월세
        chart_data = [(p, round(median(v)) if v else 0, len(v)) for p, v in pyung_rents.items() if len(v) >= 3]
        max_val = max([v for _,v,_ in chart_data] + [1])
        
        svg = '<svg viewBox="0 0 600 220" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block">'
        svg += '<line x1="50" y1="20" x2="580" y2="20" stroke="#e5e5e5" stroke-dasharray="3,3"/>'
        svg += '<line x1="50" y1="65" x2="580" y2="65" stroke="#e5e5e5" stroke-dasharray="3,3"/>'
        svg += '<line x1="50" y1="110" x2="580" y2="110" stroke="#e5e5e5" stroke-dasharray="3,3"/>'
        svg += '<line x1="50" y1="155" x2="580" y2="155" stroke="#1a1a1a" stroke-width="1"/>'
        svg += '<text x="585" y="14" text-anchor="end" font-size="10" fill="#888">(만원)</text>'
        bar_w = min(70, 500 / max(len(chart_data), 1))
        for i, (p, v, n) in enumerate(chart_data):
            x = 80 + i * 85
            h = (v / max_val) * 130 if max_val > 0 else 2
            y = 155 - h
            svg += f'<rect x="{x}" y="{y}" width="{bar_w}" height="{h:.0f}" fill="#1a1a1a" rx="2"/>'
            svg += f'<text x="{x+bar_w/2}" y="{y-5}" text-anchor="middle" font-size="12" font-weight="700" fill="#1a1a1a">{v}</text>'
            svg += f'<text x="{x+bar_w/2}" y="175" text-anchor="middle" font-size="11" fill="#555">{p}</text>'
            svg += f'<text x="{x+bar_w/2}" y="190" text-anchor="middle" font-size="10" fill="#888">{n}건</text>'
        svg += '</svg>'
        
        # 통계 박스
        all_rents = [to_int(r.get("monthly_rent")) for r in monthly_rows if to_int(r.get("monthly_rent")) > 0]
        avg_r = round(mean(all_rents)) if all_rents else 0
        med_r = round(median(all_rents)) if all_rents else 0
        
        stat_html = f'<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:16px 0">'
        stat_html += f'<div style="background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:14px 12px;text-align:center"><div style="font-size:11px;color:#888;margin-bottom:6px">중위 월세</div><div style="font-size:18px;font-weight:700;color:#1a1a1a">{med_r}<span style="font-size:11px;color:#888">만</span></div><div style="font-size:11px;color:#888;margin-top:4px">{len(all_rents)}건 기준</div></div>'
        stat_html += f'<div style="background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:14px 12px;text-align:center"><div style="font-size:11px;color:#888;margin-bottom:6px">월세 범위</div><div style="font-size:15px;font-weight:700;color:#1a1a1a">{min(all_rents)}~{max(all_rents)}<span style="font-size:11px;color:#888">만</span></div><div style="font-size:11px;color:#888;margin-top:4px">최저~최고</div></div>'
        stat_html += f'<div style="background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:14px 12px;text-align:center"><div style="font-size:11px;color:#888;margin-bottom:6px">거래 건수</div><div style="font-size:18px;font-weight:700;color:#1a1a1a">{len(all_rents)}<span style="font-size:11px;color:#888">건</span></div><div style="font-size:11px;color:#888;margin-top:4px">최근 6개월</div></div>'
        stat_html += '</div>'
        
        analysis = f'<p>{region}의 월세 시장은 평형 구간별로 뚜렷한 분포 차이를 보입니다. 전반적으로 소형(10평 미만) 구간의 거래량이 가장 많으며, 표본 중간값(중위)으로 봐야 소수 고가·저가 거래에 휘둘리지 않습니다.</p>'
        chart_title = f"{region} 평형별 중위 월세 분포"
        
    elif sub_idx == 1:  # 전세
        pyung_jeonse = {p: [] for p in pyung_order}
        for r in jeonse_rows:
            area = to_float(r.get("area_m2"))
            dep = to_int(r.get("deposit"))
            if area > 0 and dep > 0:
                pyung_jeonse[pyung_bin(area)].append(dep)
        
        chart_data = [(p, round(median(v)) if v else 0, len(v)) for p, v in pyung_jeonse.items() if len(v) >= 3]
        max_val = max([v for _,v,_ in chart_data] + [1])
        
        svg = '<svg viewBox="0 0 600 220" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block">'
        svg += '<line x1="50" y1="20" x2="580" y2="20" stroke="#e5e5e5" stroke-dasharray="3,3"/>'
        svg += '<line x1="50" y1="65" x2="580" y2="65" stroke="#e5e5e5" stroke-dasharray="3,3"/>'
        svg += '<line x1="50" y1="110" x2="580" y2="110" stroke="#e5e5e5" stroke-dasharray="3,3"/>'
        svg += '<line x1="50" y1="155" x2="580" y2="155" stroke="#1a1a1a" stroke-width="1"/>'
        svg += '<text x="585" y="14" text-anchor="end" font-size="10" fill="#888">(억원)</text>'
        bar_w = min(70, 500 / max(len(chart_data), 1))
        for i, (p, v, n) in enumerate(chart_data):
            x = 80 + i * 85
            h = (v / max_val) * 130 if max_val > 0 else 2
            y = 155 - h
            v_eok = v / 10000
            svg += f'<rect x="{x}" y="{y}" width="{bar_w}" height="{h:.0f}" fill="#2a3a55" rx="2"/>'
            svg += f'<text x="{x+bar_w/2}" y="{y-5}" text-anchor="middle" font-size="11" font-weight="700" fill="#1a1a1a">{v_eok:.1f}억</text>'
            svg += f'<text x="{x+bar_w/2}" y="175" text-anchor="middle" font-size="11" fill="#555">{p}</text>'
            svg += f'<text x="{x+bar_w/2}" y="190" text-anchor="middle" font-size="10" fill="#888">{n}건</text>'
        svg += '</svg>'
        
        all_jeonse = [to_int(r.get("deposit")) for r in jeonse_rows]
        avg_j = round(mean(all_jeonse)) if all_jeonse else 0
        med_j = round(median(all_jeonse)) if all_jeonse else 0
        med_j = round(median(all_jeonse)) if all_jeonse else 0
        
        stat_html = f'<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:16px 0">'
        stat_html += f'<div style="background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:14px 12px;text-align:center"><div style="font-size:11px;color:#888;margin-bottom:6px">중위 전세</div><div style="font-size:18px;font-weight:700;color:#1a1a1a">{fmt_won(med_j)}</div><div style="font-size:11px;color:#888;margin-top:4px">{len(all_jeonse)}건 기준</div></div>'
        stat_html += f'<div style="background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:14px 12px;text-align:center"><div style="font-size:11px;color:#888;margin-bottom:6px">중위 전세</div><div style="font-size:18px;font-weight:700;color:#1a1a1a">{fmt_won(med_j)}</div><div style="font-size:11px;color:#888;margin-top:4px">표본 중간값</div></div>'
        stat_html += f'<div style="background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:14px 12px;text-align:center"><div style="font-size:11px;color:#888;margin-bottom:6px">거래 건수</div><div style="font-size:18px;font-weight:700;color:#1a1a1a">{len(all_jeonse)}<span style="font-size:11px;color:#888">건</span></div><div style="font-size:11px;color:#888;margin-top:4px">최근 6개월</div></div>'
        stat_html += '</div>'
        
        analysis = f'<p>{region}의 전세 시장은 평형 구간 확대에 따라 가격이 비례적으로 상승하는 구조가 관찰됩니다. 고가 거래가 섞인 구간일수록 평균 대신 중위로 봐야 실제 체감가에 가깝습니다.</p>'
        chart_title = f"{region} 평형별 중위 전세금 분포"
        
    elif sub_idx == 2:  # 매매
        # 건물유형별 매매가
        type_sales = {"아파트": [], "오피스텔": [], "빌라·다세대": []}
        for r in sales_rows:
            dt = str(r.get("deal_type", ""))
            amt = to_int(r.get("deal_amount"))
            if amt <= 0: continue
            if "아파트" in dt: type_sales["아파트"].append(amt)
            elif "오피스텔" in dt: type_sales["오피스텔"].append(amt)
            elif "연립" in dt or "다세대" in dt: type_sales["빌라·다세대"].append(amt)
        
        chart_data = [(t, round(median(v)) if v else 0, len(v)) for t, v in type_sales.items() if v]
        max_val = max([v for _,v,_ in chart_data] + [1])
        
        svg = '<svg viewBox="0 0 600 220" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block">'
        svg += '<line x1="50" y1="20" x2="580" y2="20" stroke="#e5e5e5" stroke-dasharray="3,3"/>'
        svg += '<line x1="50" y1="65" x2="580" y2="65" stroke="#e5e5e5" stroke-dasharray="3,3"/>'
        svg += '<line x1="50" y1="110" x2="580" y2="110" stroke="#e5e5e5" stroke-dasharray="3,3"/>'
        svg += '<line x1="50" y1="155" x2="580" y2="155" stroke="#1a1a1a" stroke-width="1"/>'
        svg += '<text x="585" y="14" text-anchor="end" font-size="10" fill="#888">(억원)</text>'
        colors = ["#1a1a1a", "#2a3a55", "#c9a444"]
        bar_w = min(100, 400 / max(len(chart_data), 1))
        for i, (t, v, n) in enumerate(chart_data):
            x = 150 + i * 130
            h = (v / max_val) * 130 if max_val > 0 else 2
            y = 155 - h
            v_eok = v / 10000
            svg += f'<rect x="{x}" y="{y}" width="{bar_w}" height="{h:.0f}" fill="{colors[i % len(colors)]}" rx="2"/>'
            svg += f'<text x="{x+bar_w/2}" y="{y-5}" text-anchor="middle" font-size="13" font-weight="700" fill="#1a1a1a">{v_eok:.1f}억</text>'
            svg += f'<text x="{x+bar_w/2}" y="175" text-anchor="middle" font-size="12" fill="#555">{t}</text>'
            svg += f'<text x="{x+bar_w/2}" y="190" text-anchor="middle" font-size="10" fill="#888">{n}건</text>'
        svg += '</svg>'
        
        stat_html = '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:16px 0">'
        for t in ["아파트", "오피스텔", "빌라·다세대"]:
            vals = type_sales.get(t, [])
            if vals:
                mv = round(median(vals))
                stat_html += f'<div style="background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:14px 12px;text-align:center"><div style="font-size:11px;color:#888;margin-bottom:6px">{t}</div><div style="font-size:17px;font-weight:700;color:#1a1a1a">{fmt_won(mv)}</div><div style="font-size:11px;color:#888;margin-top:4px">{len(vals)}건 중위</div></div>'
            else:
                stat_html += f'<div style="background:#fafafa;border:1px solid #e0e0e0;border-radius:6px;padding:14px 12px;text-align:center;opacity:.5"><div style="font-size:11px;color:#888;margin-bottom:6px">{t}</div><div style="font-size:16px;color:#888">거래 없음</div></div>'
        stat_html += '</div>'
        
        analysis = f'<p>{region}의 매매 시장은 건물유형별로 가격대가 뚜렷이 구분됩니다. 아파트는 단지 규모와 입지에 따라, 오피스텔·빌라는 평형·연식에 따라 가격 변동성이 크게 나타나는 구조로 해석됩니다.</p>'
        chart_title = f"{region} 건물유형별 중위 매매가"
        
    else:  # 종합
        # 매매·전세·월세 거래 비중
        total = len(sales_rows) + len(jeonse_rows) + len(monthly_rows)
        sales_pct = round(len(sales_rows)/total*100, 1) if total else 0
        jeonse_pct = round(len(jeonse_rows)/total*100, 1) if total else 0
        monthly_pct = round(len(monthly_rows)/total*100, 1) if total else 0
        
        # 도넛 차트 (SVG)
        svg = '<svg viewBox="0 0 600 220" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block">'
        
        # 가로 막대형 (스택)
        bar_y = 80
        bar_h = 50
        total_w = 500
        x = 50
        if sales_pct > 0:
            w = total_w * sales_pct / 100
            svg += f'<rect x="{x}" y="{bar_y}" width="{w:.1f}" height="{bar_h}" fill="#1a1a1a"/>'
            svg += f'<text x="{x + w/2}" y="{bar_y + 30}" text-anchor="middle" font-size="13" font-weight="700" fill="#fff">매매 {sales_pct}%</text>'
            x += w
        if jeonse_pct > 0:
            w = total_w * jeonse_pct / 100
            svg += f'<rect x="{x}" y="{bar_y}" width="{w:.1f}" height="{bar_h}" fill="#2a3a55"/>'
            svg += f'<text x="{x + w/2}" y="{bar_y + 30}" text-anchor="middle" font-size="13" font-weight="700" fill="#fff">전세 {jeonse_pct}%</text>'
            x += w
        if monthly_pct > 0:
            w = total_w * monthly_pct / 100
            svg += f'<rect x="{x}" y="{bar_y}" width="{w:.1f}" height="{bar_h}" fill="#c9a444"/>'
            svg += f'<text x="{x + w/2}" y="{bar_y + 30}" text-anchor="middle" font-size="13" font-weight="700" fill="#1a1a1a">월세 {monthly_pct}%</text>'
        svg += f'<text x="300" y="50" text-anchor="middle" font-size="13" font-weight="700" fill="#1a1a1a">총 {total:,}건 거래 구조</text>'
        svg += f'<text x="300" y="170" text-anchor="middle" font-size="11" fill="#888">최근 6개월 누적 / 거래 비중 분포</text>'
        svg += '</svg>'
        
        stat_html = f'<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:16px 0">'
        stat_html += f'<div style="background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:14px 12px;text-align:center"><div style="font-size:11px;color:#888;margin-bottom:6px">매매</div><div style="font-size:18px;font-weight:700;color:#1a1a1a">{len(sales_rows):,}<span style="font-size:11px;color:#888">건</span></div><div style="font-size:11px;color:#888;margin-top:4px">{sales_pct}%</div></div>'
        stat_html += f'<div style="background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:14px 12px;text-align:center"><div style="font-size:11px;color:#888;margin-bottom:6px">전세</div><div style="font-size:18px;font-weight:700;color:#1a1a1a">{len(jeonse_rows):,}<span style="font-size:11px;color:#888">건</span></div><div style="font-size:11px;color:#888;margin-top:4px">{jeonse_pct}%</div></div>'
        stat_html += f'<div style="background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:14px 12px;text-align:center"><div style="font-size:11px;color:#888;margin-bottom:6px">월세</div><div style="font-size:18px;font-weight:700;color:#1a1a1a">{len(monthly_rows):,}<span style="font-size:11px;color:#888">건</span></div><div style="font-size:11px;color:#888;margin-top:4px">{monthly_pct}%</div></div>'
        stat_html += '</div>'
        
        analysis = f'<p>{region}은 최근 6개월간 총 {total:,}건의 부동산 거래가 신고되었으며, 거래 구조는 매매 {sales_pct}% · 전세 {jeonse_pct}% · 월세 {monthly_pct}%의 분포를 보입니다. 임대차 거래가 매매를 크게 상회하는 구조는 {region}의 시장이 임차 수요 중심임을 시사하는 흐름으로 해석됩니다.</p>'
        chart_title = f"{region} 거래 유형 분포"
    
    chart_block = f'<div style="background:#fafbfc;border:1px solid #e5e5e5;border-radius:6px;padding:18px;margin:16px 0"><div style="font-size:15px;font-weight:600;color:#1a1a1a;margin-bottom:12px;text-align:center">{chart_title}</div>{svg}</div>'
    
    sections = [
        {
            "title": "분석 개요",
            "content": f'<p>본 보고서는 {region}의 <strong>{sub_name}</strong> 시장 구조를 국토교통부 실거래 자료 기반으로 정리한 자료입니다. 분석 대상은 최근 6개월간 신고된 거래로 한정합니다.</p>{stat_html}'
        },
        {
            "title": "데이터 기준",
            "content": f'<p>모든 데이터는 <strong>국토교통부 실거래가 공개시스템</strong>의 신고 거래를 기반으로 합니다. 평형 구간별 중위값(median)을 산출하였으며, 표본 수가 3건 미만인 구간은 통계적 신뢰도 확보를 위해 분석에서 제외했습니다.</p>'
        },
        {
            "title": f"{sub_name} 시세 분포 분석",
            "content": chart_block + analysis
        },
        {
            "title": "결론",
            "content": f'''
<p>{region}의 {sub_name} 시장은 평형 구간·건물유형에 따라 명확한 가격 분포 차이를 보이는 구조로 관찰됩니다. 본인의 계약 조건이 분석 범위 내 어느 구간에 위치하는지를 우선 확인할 필요가 있습니다.</p>
<div style="background:linear-gradient(135deg,#1a1a1a 0%,#2a3a55 100%);color:#fff;padding:22px 26px;border-radius:8px;margin:18px 0">
  <div style="font-size:12px;letter-spacing:2px;color:#c9a444;margin-bottom:8px">CORE INSIGHT</div>
  <div style="font-size:16px;line-height:1.8">본인의 평형 구간을 본 보고서의 중위값·범위와 비교하여 적정 가격 범위를 파악할 필요가 있습니다. 동일 평형의 실거래가가 보고서 범위를 벗어날 경우 협상 여지가 있는 것으로 해석할 수 있습니다.</div>
</div>
'''
        },
    ]
    
    title = f"{region} {sub_name} 시세 분석 ({date_str[:7]})"
    subtitle = f"{region}의 {sub_name} 시장 구조를 국토교통부 실거래 자료 기반으로 정리합니다."
    tags = [region, f"{region}{sub_name}", f"{region}실거래가", f"{region}시세", "임대차시장분석", "강서구화곡동", "국토부실거래가"]
    
    html = tistory_html_template(title, subtitle, sections, tags, embed_widget=False, date_str=date_str)
    
    return {
        "title": title,
        "category": "rent_check_tistory",
        "html": html,
        "tags": tags,
    }


def gen_tistory_building_spotlight(report: dict, csv_rows: list, date_str: str) -> dict:
    """(수) 단지 분석 - 티스토리 분석체 (SVG 차트 + 위젯 임베드)"""
    region = report["region"]
    target_date = datetime.strptime(date_str, "%Y-%m-%d")
    
    def to_int(v):
        try: return int(str(v).replace(",", "")) if v else 0
        except: return 0
    def to_float(v):
        try: return float(v) if v else 0.0
        except: return 0.0
    def fmt_won(amt):
        if not amt: return "-"
        if amt >= 10000:
            eok = amt // 10000; rest = amt % 10000
            return f"{eok}억 {rest:,}만" if rest else f"{eok}억"
        return f"{amt:,}만"
    
    # 9개 단지 중 이번 주 단지 선정 (week_of_year % 7)
    valid_pool = [
        ("강서금호어울림퍼스티어", ["강서금호", "금호어울림"], 487),
        ("강서힐스테이트", ["강서힐스테이트"], 2603),
        ("우장산롯데캐슬", ["우장산롯데캐슬"], 1164),
        ("우장산숲아이파크", ["우장산숲"], 576),
        ("우장산아이파크,이편한세상", ["우장산아이파크", "이편한세상"], 2517),
        ("중앙하이츠빌", ["중앙하이츠"], 473),
        ("화곡프루지오", ["화곡프루지오"], 2176),
    ]
    week_of_year = target_date.isocalendar()[1]
    target_name, aliases, households = valid_pool[week_of_year % len(valid_pool)]
    
    # 단지 거래 추출
    def match_building(r):
        bn = str(r.get("building_name", ""))
        return any(a in bn for a in aliases) or target_name in bn
    
    building_rows = [r for r in csv_rows if match_building(r)]
    b_sales = [r for r in building_rows if "매매" in str(r.get("deal_type", ""))]
    b_rents = [r for r in building_rows if "전월세" in str(r.get("deal_type", ""))]
    b_jeonse = [r for r in b_rents if to_int(r.get("monthly_rent")) == 0]
    b_monthly = [r for r in b_rents if to_int(r.get("monthly_rent")) > 0]
    
    total_count = len(building_rows)
    
    # 평형별 매매가 분포
    pyung_sales = {}
    for r in b_sales:
        area = to_float(r.get("area_m2"))
        amt = to_int(r.get("deal_amount"))
        if area <= 0 or amt <= 0: continue
        py = round(area / 3.3058)
        pyung_sales.setdefault(py, []).append(amt)
    
    # SVG 차트: 평형별 중위 매매가
    sorted_py = sorted(pyung_sales.keys())
    chart_data = [(py, round(median(pyung_sales[py])), len(pyung_sales[py])) for py in sorted_py if len(pyung_sales[py]) >= 1]
    
    if chart_data:
        max_val = max([v for _,v,_ in chart_data] + [1])
        svg_sales = '<svg viewBox="0 0 600 220" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block">'
        svg_sales += '<line x1="50" y1="20" x2="580" y2="20" stroke="#e5e5e5" stroke-dasharray="3,3"/>'
        svg_sales += '<line x1="50" y1="65" x2="580" y2="65" stroke="#e5e5e5" stroke-dasharray="3,3"/>'
        svg_sales += '<line x1="50" y1="110" x2="580" y2="110" stroke="#e5e5e5" stroke-dasharray="3,3"/>'
        svg_sales += '<line x1="50" y1="155" x2="580" y2="155" stroke="#1a1a1a" stroke-width="1"/>'
        svg_sales += '<text x="585" y="14" text-anchor="end" font-size="10" fill="#888">(억원)</text>'
        bar_w = min(70, 500 / max(len(chart_data), 1))
        for i, (py, v, n) in enumerate(chart_data):
            x = 80 + i * 85
            h = (v / max_val) * 130 if max_val > 0 else 2
            y = 155 - h
            v_eok = v / 10000
            svg_sales += f'<rect x="{x}" y="{y}" width="{bar_w}" height="{h:.0f}" fill="#1a1a1a" rx="2"/>'
            svg_sales += f'<text x="{x+bar_w/2}" y="{y-5}" text-anchor="middle" font-size="11" font-weight="700" fill="#1a1a1a">{v_eok:.1f}억</text>'
            svg_sales += f'<text x="{x+bar_w/2}" y="175" text-anchor="middle" font-size="11" fill="#555">{py}평</text>'
            svg_sales += f'<text x="{x+bar_w/2}" y="190" text-anchor="middle" font-size="10" fill="#888">{n}건</text>'
        svg_sales += '</svg>'
        sales_chart = f'<div style="background:#fafbfc;border:1px solid #e5e5e5;border-radius:6px;padding:18px;margin:16px 0"><div style="font-size:15px;font-weight:600;color:#1a1a1a;margin-bottom:12px;text-align:center">{target_name} 평형별 중위 매매가</div>{svg_sales}</div>'
    else:
        sales_chart = '<p style="background:#fafafa;padding:14px;border-radius:6px;color:#888;text-align:center;font-size:15px">최근 6개월간 매매 신고 거래가 부족하여 분석을 제공하지 않습니다.</p>'
    
    # 거래 유형 비중 차트 (가로 막대)
    if total_count > 0:
        s_pct = round(len(b_sales)/total_count*100, 1)
        j_pct = round(len(b_jeonse)/total_count*100, 1)
        m_pct = round(len(b_monthly)/total_count*100, 1)
        
        svg_mix = '<svg viewBox="0 0 600 130" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block">'
        svg_mix += f'<text x="300" y="20" text-anchor="middle" font-size="13" font-weight="700" fill="#1a1a1a">총 {total_count}건 거래 구조</text>'
        bar_y = 45; bar_h = 50; total_w = 500; x = 50
        if s_pct > 0:
            w = total_w * s_pct / 100
            svg_mix += f'<rect x="{x}" y="{bar_y}" width="{w:.1f}" height="{bar_h}" fill="#1a1a1a"/>'
            if w > 50: svg_mix += f'<text x="{x + w/2}" y="{bar_y + 30}" text-anchor="middle" font-size="12" font-weight="700" fill="#fff">매매 {s_pct}%</text>'
            x += w
        if j_pct > 0:
            w = total_w * j_pct / 100
            svg_mix += f'<rect x="{x}" y="{bar_y}" width="{w:.1f}" height="{bar_h}" fill="#2a3a55"/>'
            if w > 50: svg_mix += f'<text x="{x + w/2}" y="{bar_y + 30}" text-anchor="middle" font-size="12" font-weight="700" fill="#fff">전세 {j_pct}%</text>'
            x += w
        if m_pct > 0:
            w = total_w * m_pct / 100
            svg_mix += f'<rect x="{x}" y="{bar_y}" width="{w:.1f}" height="{bar_h}" fill="#c9a444"/>'
            if w > 50: svg_mix += f'<text x="{x + w/2}" y="{bar_y + 30}" text-anchor="middle" font-size="12" font-weight="700" fill="#1a1a1a">월세 {m_pct}%</text>'
        svg_mix += '</svg>'
        mix_chart = f'<div style="background:#fafbfc;border:1px solid #e5e5e5;border-radius:6px;padding:18px;margin:16px 0">{svg_mix}</div>'
    else:
        mix_chart = ''
    
    # 통계 박스
    stat_html = f'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:16px 0">'
    stat_html += f'<div style="background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:12px 8px;text-align:center"><div style="font-size:10px;color:#888;margin-bottom:4px">전체</div><div style="font-size:16px;font-weight:700;color:#1a1a1a">{total_count}<span style="font-size:10px;color:#888">건</span></div></div>'
    stat_html += f'<div style="background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:12px 8px;text-align:center"><div style="font-size:10px;color:#888;margin-bottom:4px">매매</div><div style="font-size:16px;font-weight:700;color:#1a1a1a">{len(b_sales)}<span style="font-size:10px;color:#888">건</span></div></div>'
    stat_html += f'<div style="background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:12px 8px;text-align:center"><div style="font-size:10px;color:#888;margin-bottom:4px">전세</div><div style="font-size:16px;font-weight:700;color:#1a1a1a">{len(b_jeonse)}<span style="font-size:10px;color:#888">건</span></div></div>'
    stat_html += f'<div style="background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:12px 8px;text-align:center"><div style="font-size:10px;color:#888;margin-bottom:4px">월세</div><div style="font-size:16px;font-weight:700;color:#1a1a1a">{len(b_monthly)}<span style="font-size:10px;color:#888">건</span></div></div>'
    stat_html += '</div>'
    
    sections = [
        {
            "title": "분석 개요",
            "content": f'<p>본 보고서는 {region}의 <strong>「{target_name}」</strong> 단지를 대상으로 최근 6개월간 신고된 실거래를 종합 분석한 자료입니다. {target_name}은 <strong>{households:,}세대</strong> 규모의 단지로, 이번 보고 기간 동안 총 <strong>{total_count}건</strong>의 거래가 신고되었습니다.</p>{stat_html}'
        },
        {
            "title": "데이터 기준",
            "content": f'<p>분석 대상은 국토교통부 실거래가 공개시스템에 신고된 「{target_name}」 단지의 모든 거래입니다. 평형별·거래유형별로 중위값·범위를 산출하였으며, 단지명 표기 차이(동·층 등)를 고려한 별칭 매칭을 적용했습니다.</p>'
        },
        {
            "title": "거래 구조 분석",
            "content": f'<p>{target_name}의 최근 6개월 거래는 매매·전세·월세 세 유형으로 분리되며, 거래 구조는 다음과 같이 관찰됩니다.</p>{mix_chart}<p>거래 유형 분포는 단지의 수요 특성을 반영합니다. 매매 비중이 높을수록 투자·실거주 매수 수요가 강한 단지, 임대차(전세·월세) 비중이 높을수록 임차 수요가 강한 단지로 해석할 수 있습니다.</p>'
        },
        {
            "title": "평형별 매매가 분석",
            "content": f'<p>{target_name}의 평형 구간별 중위 매매가는 다음과 같이 분포합니다. 평형 확대에 따른 단가 변화 패턴을 통해 단지 내 가격 분포 특성을 확인할 수 있습니다.</p>{sales_chart}<p>매매가는 동·향·층수·리모델링 여부 등에 따라 동일 평형에서도 편차가 발생할 수 있으므로, 중위값으로 봐야 소수 특이거래에 휘둘리지 않습니다.</p>'
        },
        {
            "title": "임차인 관점 체크포인트",
            "content": f'''
<p>「{target_name}」 단지에서 계약을 검토 중인 임차인의 관점에서 다음을 점검할 필요가 있습니다.</p>
<div style="background:#f0f4f8;border:1px solid #d6dde5;border-radius:6px;padding:18px 22px;margin:14px 0">
  <ul style="list-style:none;padding:0;margin:0">
    <li style="position:relative;padding:5px 0 5px 22px;font-size:16px"><span style="position:absolute;left:0;color:#c9a444;font-weight:700">▸</span>본인 계약 조건이 단지 중위 시세와 어떻게 비교되는지 검토</li>
    <li style="position:relative;padding:5px 0 5px 22px;font-size:16px"><span style="position:absolute;left:0;color:#c9a444;font-weight:700">▸</span>같은 평형의 최근 거래 범위 내에 본인 계약 조건이 위치하는지 확인</li>
    <li style="position:relative;padding:5px 0 5px 22px;font-size:16px"><span style="position:absolute;left:0;color:#c9a444;font-weight:700">▸</span>층수·향·옵션 등 가격 외 조건이 시세 차이를 설명하는지 점검</li>
    <li style="position:relative;padding:5px 0 5px 22px;font-size:16px"><span style="position:absolute;left:0;color:#c9a444;font-weight:700">▸</span>매매·전세·월세 거래 비중이 단지 특성에 부합하는지 확인</li>
  </ul>
</div>
'''
        },
        {
            "title": "결론",
            "content": f'''
<div style="background:linear-gradient(135deg,#1a1a1a 0%,#2a3a55 100%);color:#fff;padding:22px 26px;border-radius:8px;margin:18px 0">
  <div style="font-size:12px;letter-spacing:2px;color:#c9a444;margin-bottom:8px">CORE INSIGHT</div>
  <div style="font-size:16px;line-height:1.8">{target_name}의 시세는 본 보고서에 정리된 평형별 분포를 기준으로 검토할 필요가 있습니다. <strong style="color:#c9a444">아래 위젯</strong>을 활용하면 본인 계약 조건의 적정성을 즉시 확인할 수 있습니다.</div>
</div>
'''
        },
    ]
    
    title = f"{target_name} 실거래가 분석 ({date_str[:7]})"
    subtitle = f"{target_name}의 평형별 매매·전세·월세 실거래 흐름과 가격 분포를 정리합니다."
    tags = [region, target_name, f"{target_name}실거래가", f"{target_name}매매가", f"{target_name}전세", f"{target_name}월세", f"{target_name}시세", "단지분석", "강서구화곡동"]
    
    html = tistory_html_template(title, subtitle, sections, tags, embed_widget=True, date_str=date_str)
    
    return {
        "title": title,
        "category": "building_spotlight_tistory",
        "html": html,
        "tags": tags,
        "building_name": target_name,
    }


def gen_tistory_jeonse_vs_monthly(report: dict, csv_rows: list, date_str: str) -> dict:
    """(목) 전세 vs 월세 - 티스토리 분석체 (SVG 차트 + 위젯 임베드)"""
    region = report["region"]
    
    def to_int(v):
        try: return int(str(v).replace(",", "")) if v else 0
        except: return 0
    def to_float(v):
        try: return float(v) if v else 0.0
        except: return 0.0
    def fmt_won(amt):
        if not amt: return "-"
        if amt >= 10000:
            eok = amt // 10000; rest = amt % 10000
            return f"{eok}억 {rest:,}만" if rest else f"{eok}억"
        return f"{amt:,}만"
    
    # 전세·월세 분리
    rents = [r for r in csv_rows if "전월세" in str(r.get("deal_type", ""))]
    jeonse_rows = [r for r in rents if to_int(r.get("monthly_rent")) == 0 and to_int(r.get("deposit")) > 0]
    monthly_rows = [r for r in rents if to_int(r.get("monthly_rent")) > 0]
    
    # 환산월세 계산 (법정 전환율 4.5%)
    CONVERSION_RATE = 0.045
    def to_converted(r):
        dep = to_int(r.get("deposit"))
        rent = to_int(r.get("monthly_rent"))
        if rent > 0:
            return rent + (dep * CONVERSION_RATE / 12)
        else:
            return (dep * CONVERSION_RATE / 12)
    
    # 평형별 환산월세 비교
    def pyung_bin(area):
        py = area / 3.3058
        if py < 10: return 10
        elif py < 15: return 15
        elif py < 20: return 20
        elif py < 25: return 25
        elif py < 30: return 30
        else: return 35
    
    py_converted_jeonse = {}  # 전세 환산월세
    py_converted_monthly = {}  # 월세 환산월세
    for r in jeonse_rows:
        area = to_float(r.get("area_m2"))
        if area <= 0: continue
        py = pyung_bin(area)
        py_converted_jeonse.setdefault(py, []).append(to_converted(r))
    for r in monthly_rows:
        area = to_float(r.get("area_m2"))
        if area <= 0: continue
        py = pyung_bin(area)
        py_converted_monthly.setdefault(py, []).append(to_converted(r))
    
    # 공통 평형 구간만
    common_pyung = sorted(set(py_converted_jeonse.keys()) & set(py_converted_monthly.keys()))
    
    # 비교 차트 (그룹화된 막대 차트)
    py_labels = {10: "10평", 15: "10~15", 20: "15~20", 25: "20~25", 30: "25~30", 35: "30+"}
    chart_data = []
    for py in common_pyung:
        j_avg = round(mean(py_converted_jeonse[py]))
        m_avg = round(mean(py_converted_monthly[py]))
        if len(py_converted_jeonse[py]) >= 2 and len(py_converted_monthly[py]) >= 2:
            chart_data.append((py_labels.get(py, f"{py}평"), j_avg, m_avg, len(py_converted_jeonse[py]), len(py_converted_monthly[py])))
    
    if chart_data:
        max_val = max([max(j, m) for _, j, m, _, _ in chart_data] + [1])
        svg = '<svg viewBox="0 0 600 240" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block">'
        svg += '<line x1="60" y1="30" x2="580" y2="30" stroke="#e5e5e5" stroke-dasharray="3,3"/>'
        svg += '<line x1="60" y1="75" x2="580" y2="75" stroke="#e5e5e5" stroke-dasharray="3,3"/>'
        svg += '<line x1="60" y1="120" x2="580" y2="120" stroke="#e5e5e5" stroke-dasharray="3,3"/>'
        svg += '<line x1="60" y1="165" x2="580" y2="165" stroke="#1a1a1a" stroke-width="1"/>'
        svg += '<text x="585" y="24" text-anchor="end" font-size="10" fill="#888">(환산월세, 만원)</text>'
        
        # 범례
        svg += '<rect x="60" y="210" width="14" height="14" fill="#2a3a55"/>'
        svg += '<text x="80" y="222" font-size="11" fill="#1a1a1a">전세 환산</text>'
        svg += '<rect x="170" y="210" width="14" height="14" fill="#c9a444"/>'
        svg += '<text x="190" y="222" font-size="11" fill="#1a1a1a">월세 환산</text>'
        
        group_w = min(80, 500 / max(len(chart_data), 1))
        bar_w = group_w / 2 - 3
        for i, (label, j, m, jn, mn) in enumerate(chart_data):
            x = 80 + i * (group_w + 10)
            h_j = (j / max_val) * 130 if max_val > 0 else 2
            h_m = (m / max_val) * 130 if max_val > 0 else 2
            y_j = 165 - h_j
            y_m = 165 - h_m
            svg += f'<rect x="{x}" y="{y_j}" width="{bar_w}" height="{h_j:.0f}" fill="#2a3a55" rx="2"/>'
            svg += f'<text x="{x+bar_w/2}" y="{y_j-3}" text-anchor="middle" font-size="10" font-weight="700" fill="#1a1a1a">{j}</text>'
            svg += f'<rect x="{x+bar_w+4}" y="{y_m}" width="{bar_w}" height="{h_m:.0f}" fill="#c9a444" rx="2"/>'
            svg += f'<text x="{x+bar_w+4+bar_w/2}" y="{y_m-3}" text-anchor="middle" font-size="10" font-weight="700" fill="#1a1a1a">{m}</text>'
            svg += f'<text x="{x+group_w/2}" y="183" text-anchor="middle" font-size="11" fill="#555">{label}</text>'
            svg += f'<text x="{x+group_w/2}" y="197" text-anchor="middle" font-size="9" fill="#888">전{jn}/월{mn}건</text>'
        svg += '</svg>'
        compare_chart = f'<div style="background:#fafbfc;border:1px solid #e5e5e5;border-radius:6px;padding:18px;margin:16px 0"><div style="font-size:15px;font-weight:600;color:#1a1a1a;margin-bottom:12px;text-align:center">평형별 전세·월세 환산 비교 (법정 전환율 4.5% 적용)</div>{svg}</div>'
    else:
        compare_chart = '<p style="background:#fafafa;padding:14px;border-radius:6px;color:#888;text-align:center;font-size:15px">동일 평형에서 전세·월세 거래가 모두 충분한 표본이 부족하여 비교 분석을 제공하지 않습니다.</p>'
    
    # 통계
    total_j = len(jeonse_rows); total_m = len(monthly_rows)
    avg_j_dep = round(median([to_int(r.get("deposit")) for r in jeonse_rows])) if jeonse_rows else 0
    avg_m_rent = round(median([to_int(r.get("monthly_rent")) for r in monthly_rows])) if monthly_rows else 0
    avg_m_dep = round(median([to_int(r.get("deposit")) for r in monthly_rows])) if monthly_rows else 0
    
    stat_html = f'<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin:16px 0">'
    stat_html += f'<div style="background:#fff;border:1px solid #e0e0e0;border-left:4px solid #2a3a55;border-radius:6px;padding:14px 16px"><div style="font-size:12px;color:#888;margin-bottom:6px">전세 거래</div><div style="font-size:18px;font-weight:700;color:#1a1a1a;margin-bottom:4px">{total_j}건</div><div style="font-size:12px;color:#555">중위 보증금: <strong>{fmt_won(avg_j_dep)}</strong></div></div>'
    stat_html += f'<div style="background:#fff;border:1px solid #e0e0e0;border-left:4px solid #c9a444;border-radius:6px;padding:14px 16px"><div style="font-size:12px;color:#888;margin-bottom:6px">월세 거래</div><div style="font-size:18px;font-weight:700;color:#1a1a1a;margin-bottom:4px">{total_m}건</div><div style="font-size:12px;color:#555">중위 월세: <strong>{avg_m_rent}만</strong> / 보증: {fmt_won(avg_m_dep)}</div></div>'
    stat_html += '</div>'
    
    sections = [
        {
            "title": "분석 개요",
            "content": f'<p>전세와 월세 중 어느 쪽이 임차인에게 더 합리적인 선택인지는 보증금 규모·전환율·기회비용에 따라 달라집니다. 본 보고서는 {region}의 최근 6개월 임대차 실거래를 기반으로 두 거래유형의 비교 분석을 정리합니다.</p>{stat_html}'
        },
        {
            "title": "법정 전월세 전환율 기준",
            "content": '<p>전세와 월세를 동일 기준으로 비교하기 위해 <strong>법정 전월세 전환율(한국은행 기준금리 + 2%, 최대 10%)</strong>을 적용한 환산월세 산정 방식을 사용합니다. 환산월세는 다음과 같이 계산됩니다.</p><div style="background:#fafbfc;border:1px solid #e5e5e5;border-left:4px solid #1a1a1a;padding:14px 20px;margin:14px 0;border-radius:0 6px 6px 0"><div style="font-size:11px;color:#6b6b6b;letter-spacing:1px;margin-bottom:6px;font-weight:600">환산월세 공식</div><div style="font-size:16px;color:#1a1a1a;font-weight:700;font-family:Consolas,monospace">환산월세 = 월세 + (보증금 × 4.5% ÷ 12)</div></div><p>이 기준으로 계산하면 보증금 1,000만원당 약 3.75만원의 월세에 해당하므로, 전세와 월세 거래를 동일 단위로 비교할 수 있습니다.</p>'
        },
        {
            "title": "평형별 환산월세 비교",
            "content": f'<p>{region}의 임대차 거래를 평형 구간별로 분리하여 전세 환산월세와 실제 월세의 환산값을 비교한 결과는 다음과 같이 관찰됩니다.</p>{compare_chart}<p>일반적으로 동일 평형에서 전세 환산월세가 월세 환산값보다 낮게 형성되면 전세가 임차인 입장에서 유리한 선택으로 해석됩니다. 반대의 경우 월세가 합리적인 선택지가 됩니다.</p>'
        },
        {
            "title": "선택 가이드",
            "content": f'''
<p>본인의 자금 규모와 거주 계획에 따라 합리적 선택은 달라집니다.</p>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:16px 0">
  <div style="background:#fff;border:1px solid #2a3a55;border-radius:8px;padding:16px 18px">
    <div style="font-size:15px;font-weight:700;color:#2a3a55;margin-bottom:8px">📌 전세가 유리한 경우</div>
    <ul style="margin:0;padding-left:18px;font-size:15px;line-height:1.8;color:#2a2a2a">
      <li>보증금 자금 여력이 충분함</li>
      <li>장기 거주 계획 (2년 이상)</li>
      <li>전세대출 금리가 낮음</li>
      <li>환산월세 < 월세 환산값</li>
    </ul>
  </div>
  <div style="background:#fff;border:1px solid #c9a444;border-radius:8px;padding:16px 18px">
    <div style="font-size:15px;font-weight:700;color:#8a6e1a;margin-bottom:8px">📌 월세가 유리한 경우</div>
    <ul style="margin:0;padding-left:18px;font-size:15px;line-height:1.8;color:#2a2a2a">
      <li>보증금 자금 부족</li>
      <li>단기 거주 (1년 이하)</li>
      <li>현금 흐름 유지가 중요</li>
      <li>환산월세 > 월세 환산값</li>
    </ul>
  </div>
</div>
'''
        },
        {
            "title": "결론",
            "content": f'''
<p>{region} 임대차 시장의 전세·월세 선택은 본인의 자금 구조와 거주 기간을 종합적으로 고려할 필요가 있습니다.</p>
<div style="background:linear-gradient(135deg,#1a1a1a 0%,#2a3a55 100%);color:#fff;padding:22px 26px;border-radius:8px;margin:18px 0">
  <div style="font-size:12px;letter-spacing:2px;color:#c9a444;margin-bottom:8px">CORE INSIGHT</div>
  <div style="font-size:16px;line-height:1.8">아래 위젯에 본인 조건을 입력하면 <strong style="color:#c9a444">환산월세 기준</strong>으로 전세·월세를 객관적으로 비교할 수 있습니다.</div>
</div>
'''
        },
    ]
    
    title = f"{region} 전세 vs 월세 비교 분석 ({date_str[:7]})"
    subtitle = f"법정 전월세 전환율을 적용한 {region} 임대차 거래유형 비교 분석"
    tags = [region, f"{region}전세", f"{region}월세", f"{region}전세시세", f"{region}월세시세", "전세월세비교", "전월세환산", "환산월세", "강서구화곡동"]
    
    html = tistory_html_template(title, subtitle, sections, tags, embed_widget=True, date_str=date_str)
    
    return {
        "title": title,
        "category": "jeonse_vs_monthly_tistory",
        "html": html,
        "tags": tags,
    }


# 티스토리 카테고리 매핑
TISTORY_CATEGORY_FN = {
    "weekly_summary_tistory": gen_tistory_weekly_summary,
    "rent_check_tistory": gen_tistory_rent_check,
    "building_spotlight_tistory": gen_tistory_building_spotlight,
    "jeonse_vs_monthly_tistory": gen_tistory_jeonse_vs_monthly,
}

# 요일 → 티스토리 카테고리
TISTORY_WEEKDAY_CAT = {
    0: "weekly_summary_tistory",
    1: "rent_check_tistory",
    2: "building_spotlight_tistory",
    3: "jeonse_vs_monthly_tistory",
}


def save_tistory_post(post: dict, date_str: str) -> Path:
    """티스토리용 글 저장 (별도 폴더)"""
    folder = Path("outputs") / "blog" / date_str.replace("-", "") / "tistory"
    folder.mkdir(parents=True, exist_ok=True)
    
    # 본문
    snippet_path = folder / f"{post['category']}_본문.html"
    with open(snippet_path, "w", encoding="utf-8") as f:
        f.write(post["html"])
    
    # 미리보기
    full = f'''<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{post['title']}</title>
<style>body{{margin:0;padding:30px 20px;background:#f5f6f8;font-family:'Noto Sans KR','Malgun Gothic',sans-serif}}</style>
</head><body>{post['html']}</body></html>'''
    preview_path = folder / f"{post['category']}_미리보기.html"
    with open(preview_path, "w", encoding="utf-8") as f:
        f.write(full)
    
    # 메타
    meta_path = folder / f"{post['category']}_메타.txt"
    with open(meta_path, "w", encoding="utf-8") as f:
        f.write(f"제목: {post['title']}\n")
        f.write(f"태그: {' '.join('#' + t for t in build_tags(post['tags']))}\n")
        f.write(f"카테고리: {post['category']}\n")
        f.write(f"네이버 카테고리: {NAVER_CATEGORY.get(post['category'], post['category'])}\n")
        f.write(f"플랫폼: 티스토리 (분석체)\n")
    
    return snippet_path


def run_tistory_for_today(json_path: str, csv_path: str, target_date: datetime = None):
    """오늘 요일 기준 티스토리 글 1편 생성 (월~목만)"""
    target_date = target_date or datetime.now()
    weekday = target_date.weekday()
    
    if weekday not in TISTORY_WEEKDAYS:
        print(f"  [-] 오늘({['월','화','수','목','금','토','일'][weekday]})은 티스토리 분석체 대상 요일이 아님 (월~목만)")
        return []
    
    date_str = target_date.strftime("%Y-%m-%d")
    report, csv_rows = load_data(json_path, csv_path)
    
    cat = TISTORY_WEEKDAY_CAT[weekday]
    fn = TISTORY_CATEGORY_FN[cat]
    post = fn(report, csv_rows, date_str)
    saved = save_tistory_post(post, date_str)
    print(f"  ✓ [티스토리·{['월','화','수','목'][weekday]}] {cat}: {saved}")
    return [saved]


def run_tistory_all(json_path: str, csv_path: str, target_date: datetime = None):
    """티스토리 4개 분석체 글 일괄 생성"""
    target_date = target_date or datetime.now()
    date_str = target_date.strftime("%Y-%m-%d")
    report, csv_rows = load_data(json_path, csv_path)
    
    results = []
    for cat, fn in TISTORY_CATEGORY_FN.items():
        try:
            post = fn(report, csv_rows, date_str)
            saved = save_tistory_post(post, date_str)
            print(f"  ✓ [티스토리] {cat}: {saved}")
            results.append(saved)
        except Exception as e:
            print(f"  ✗ [티스토리] {cat}: {e}")
    return results


# ═══════════════════════════════════════════════════════════
# 메인 실행 (모든 함수 정의 이후에 위치)
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    
    # 인자 파싱: mode [--date=YYYY-MM-DD]
    args = sys.argv[1:]
    mode = "all"
    custom_date = None
    
    for arg in args:
        if arg.startswith("--date="):
            try:
                custom_date = datetime.strptime(arg.replace("--date=", ""), "%Y-%m-%d")
                print(f"✓ 지정 날짜 사용: {custom_date.strftime('%Y-%m-%d (%a)')}")
            except ValueError:
                print(f"⚠ 잘못된 날짜 형식: {arg}. YYYY-MM-DD 형식으로 입력하세요.")
                sys.exit(1)
        elif not arg.startswith("--"):
            mode = arg
    
    json_path = "data/aggregated_rent.json"
    # 실데이터 우선
    if Path("data/molit_trade_live.csv").exists():
        csv_path = "data/molit_trade_live.csv"
        print("✓ 실데이터 사용: data/molit_trade_live.csv")
    else:
        csv_path = "data/sample_rent_hwagok.csv"
        print("⚠ 샘플 데이터 사용")

    target_date = custom_date or datetime.now()
    
    if mode == "scan":
        report, csv_rows = load_data(json_path, csv_path)
        d = (custom_date or datetime.now()).strftime("%Y-%m-%d")
        sp = scan_signals(csv_rows, d)
        print(f"글감 스캔 완료 → {sp}" if sp else "새 이상신호 없음")
        sys.exit(0)
    if mode == "today":
        print(f"=== {target_date.strftime('%Y-%m-%d (%a)')} 요일의 글 생성 ===")
        run_for_today(json_path, csv_path, target_date=target_date)
        # 분석체 어울리는 요일이면 티스토리 버전도 생성
        weekday = target_date.weekday()
        if weekday in TISTORY_WEEKDAYS:
            print("\n=== 티스토리 분석체 버전 생성 ===")
            run_tistory_for_today(json_path, csv_path, target_date=target_date)
    elif mode == "tistory":
        print("=== 티스토리 분석체 글 일괄 생성 ===")
        run_tistory_all(json_path, csv_path, target_date=target_date)
    elif mode == "all":
        print("=== 전체 7개 카테고리 생성 (검수용) ===")
        run_all_categories(json_path, csv_path, target_date=target_date)
        print("\n=== 티스토리 분석체 4개 추가 생성 ===")
        run_tistory_all(json_path, csv_path, target_date=target_date)
    else:
        print("=== 전체 7개 카테고리 생성 (검수용) ===")
        run_all_categories(json_path, csv_path, target_date=target_date)
    print("\n저장 위치: outputs/blog/<날짜>/")
