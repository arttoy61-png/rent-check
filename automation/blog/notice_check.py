# -*- coding: utf-8 -*-
"""
공고 레이더 v2 (LH + SH) — Rent Check 강서

목표
- LH: 공공데이터포털 LH 분양임대공고 API로 새 공고 감지
- SH: SH 홈페이지/검색 페이지 HTML에서 새 공고 감지(공식 API가 확정되면 URL만 추가)
- 새 공고는 outputs/notice_alarm/공고알람_YYYYMMDD_HHMM.txt 로 저장
- 이미 본 공고는 data/notice_seen.json 에 기억해서 중복 알림 방지

실행
  python blog\공고감지_LH_SH.py

키 넣는 법
  1) 아래 LH_SERVICE_KEY에 공공데이터포털 Encoding 키 붙여넣기
  2) 또는 환경변수로 설정: set LH_SERVICE_KEY=인증키

주의
- LH는 API라 안정적.
- SH는 홈페이지 HTML 구조가 바뀔 수 있어 raw 저장 기능을 넣어둠.
  SH가 0건이면 outputs/notice_alarm/raw_SH_*.html 을 확인해서 SH_URLS만 보정하면 됨.
"""

import io
import json
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ═══════════════ 여기만 보통 고치면 됨 ═══════════════
LH_SERVICE_KEY = "db37b0e016c17916a73f8ac3e66b66c6fdd4a2af8f85992654c9712b93298202"
REGION_KEYWORDS = ["서울", "강서", "화곡", "염창", "가양"]
HOT_KEYWORDS = ["청년", "매입임대", "안심주택", "협동조합", "신혼", "행복주택", "기숙사", "위하우스", "한경스페이스", "레인트리", "그랑", "이음채",
    # 주거비 지원 (2026.7.20 추가)
    "월세지원", "이사비", "중개보수", "임차보증금", "보증료",
    # 금융 (2026.7.20 추가)
    "청년수당", "영테크", "희망두배", "학자금"]
LH_PAGES = 3

# SH 공식 홈페이지 공고/입주자모집 페이지 후보.
# 접속해서 0건이면 raw_SH_*.html 저장됨. 실제 페이지 주소 확인 후 여기에 추가하면 됨.
SH_URLS = [
    # ✅ SH 인터넷청약시스템 - 청약정보>공고및공지>주택임대 (실제 입주자 모집공고 게시판, 1600+건)
    "https://www.i-sh.co.kr/app/lay2/program/S48T1581C563/www/brd/m_247/list.do?multi_itm_seq=2",
    # SH소식>공고및공지>주택임대 (본사 홈페이지 버전)
    "https://www.i-sh.co.kr/main/lay2/program/S1T294C297/www/brd/m_247/list.do?multi_itm_seq=2",
]
# ═══════════════════════════════════════════════

# 서울주거포털 — 두레주택·기숙사형·도시형·매입임대·안심주택 등 서울시 계열 공고
SEOUL_URLS = [
    "https://housing.seoul.go.kr/site/main/sh/publicLease/list",
    "https://housing.seoul.go.kr/",
    # 청년몽땅 — 서울시 정책 공지 + 지원정보 (주거비·금융 정책 공고)
    "https://youth.seoul.go.kr/bbs/list.do?key=2303300002&sc_bbsCtgrySn=2304110001",
    "https://youth.seoul.go.kr/infoData/sprtInfo/list.do?key=2309130006",
]

SEEN_PATH = Path("data/notice_seen.json")
OUT_DIR = Path("outputs/notice_alarm")

# lhLeaseInfo1 = 임대주택단지 조회 (승인받음). 지역코드(CNP_CD)+공급유형(SPL_TP_CD)로 조회.
# 매입임대13·행복주택10·장기전세11을 서울(11)로 돌려 '현재 공급 단지' 목록을 얻고,
# seen과 비교해 새로 뜬 단지 = 새 공고로 감지.
LH_API_URL = "http://apis.data.go.kr/B552555/lhLeaseInfo1/lhLeaseInfo1"
LH_REGION_CD = "11"          # 서울
LH_SPL_TYPES = ["13", "10", "11"]  # 매입임대·행복주택·장기전세 (청년 관련만; 영구·공공·국민 제외)


@dataclass
class Notice:
    org: str
    id: str
    name: str
    type: str = ""
    region: str = ""
    start: str = ""
    close: str = ""
    url: str = ""
    hot: Tuple[str, ...] = ()
    source: str = ""


def _ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def send_telegram(text: str) -> bool:
    """새 공고를 텔레그램으로 전송. TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID 없으면 스킵."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False
    try:
        for i in range(0, len(text), 3800):
            chunk = text[i:i+3800]
            data = urllib.parse.urlencode({"chat_id": chat_id, "text": chunk}).encode()
            req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
            urllib.request.urlopen(req, timeout=15, context=_ctx()).read()
        print("텔레그램 전송 완료")
        return True
    except Exception as e:
        print(f"텔레그램 전송 실패: {e}")
        return False



def fetch_url(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7,en;q=0.6",
            "Referer": "https://www.google.com/",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as r:
        return r.read()


def decode_bytes(b: bytes) -> str:
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode("utf-8", errors="replace")


def gv(d: Dict[str, Any], *keys: str) -> str:
    for k in keys:
        v = d.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def hot_words(text: str) -> Tuple[str, ...]:
    return tuple(k for k in HOT_KEYWORDS if k in text)


def passes_region(text: str) -> bool:
    if not REGION_KEYWORDS:
        return True
    return any(k in text for k in REGION_KEYWORDS)


# ───────────────── LH ─────────────────
def lh_find_rows(obj: Any) -> List[Dict[str, Any]]:
    """단지조회 응답에서 단지명(SBD_LGO_NM 등)이 있는 dict 목록을 찾는다."""
    KEYS = ("SBD_LGO_NM","sbdLgoNm","AIS_TP_CD_NM","LGDONG_NM","CNP_CD_NM","BSNS_MBY_NM","SPL_TP_NM")
    found: List[Dict[str, Any]] = []
    def has_key(d): return isinstance(d, dict) and any(k in d for k in KEYS)
    def walk(o: Any):
        if isinstance(o, list):
            if o and any(has_key(d) for d in o):
                found.extend(d for d in o if isinstance(d, dict))
            else:
                for x in o: walk(x)
        elif isinstance(o, dict):
            for v in o.values(): walk(v)
    walk(obj)
    return found


def lh_fetch(spl_tp: str, page: int) -> Any:
    q = urllib.parse.urlencode({
        "serviceKey": LH_SERVICE_KEY, "PG_SZ": 100, "PAGE": page,
        "CNP_CD": LH_REGION_CD, "SPL_TP_CD": spl_tp,
    }, safe="%")
    return json.loads(decode_bytes(fetch_url(f"{LH_API_URL}?{q}")))


def load_lh_notices() -> Tuple[List[Notice], Optional[Any]]:
    if not LH_SERVICE_KEY:
        print("⚠ LH_SERVICE_KEY가 비었습니다 — LH API는 건너뜁니다. SH만 확인합니다.")
        return [], None

    rows: List[Dict[str, Any]] = []
    raw_last: Optional[Any] = None
    for tp in LH_SPL_TYPES:
        for p in range(1, LH_PAGES + 1):
            try:
                data = lh_fetch(tp, p)
                raw_last = data
                got = lh_find_rows(data)
                rows += got
                if len(got) < 100:
                    break
            except Exception as e:
                print(f"⚠ LH API 호출 실패(유형 {tp}, 페이지 {p}): {e}")
                break

    notices: List[Notice] = []
    for d in rows:
        name = gv(d, "SBD_LGO_NM", "sbdLgoNm", "BSNS_MBY_NM")   # 단지명
        typ  = gv(d, "AIS_TP_CD_NM", "SPL_TP_NM", "aisTpCdNm")  # 공급유형
        reg  = gv(d, "CNP_CD_NM", "LGDONG_NM", "cnpCdNm")        # 지역/법정동
        area = gv(d, "DDO_AR", "TNO_AR", "ddoAr")                # 전용면적(있으면)
        pid  = gv(d, "SBD_CD", "sbdCd", "HOUSE_SN") or name      # 단지코드 또는 단지명
        blob = f"{name} {typ} {reg}"
        if not passes_region(blob) and not hot_words(blob):
            continue
        notices.append(Notice(
            org="LH",
            id=f"LH:{pid}",  # 면적 제외 -> 단지 단위 중복 방지
            name=name,
            type=typ,
            region=reg,
            start="",
            close="",
            url="https://apply.lh.or.kr",
            hot=hot_words(blob),
            source="LH_단지조회",
        ))
    return notices, raw_last


# ───────────────── SH HTML ─────────────────
class LinkCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: List[Tuple[str, str]] = []
        self._href: Optional[str] = None
        self._buf: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]):
        if tag.lower() == "a":
            attrs_d = {k.lower(): v for k, v in attrs if k}
            self._href = attrs_d.get("href")
            self._buf = []

    def handle_data(self, data: str):
        if self._href is not None:
            self._buf.append(data)

    def handle_endtag(self, tag: str):
        if tag.lower() == "a" and self._href is not None:
            text = " ".join("".join(self._buf).split())
            if text:
                self.links.append((self._href, unescape(text)))
            self._href = None
            self._buf = []


def abs_url(base: str, href: str) -> str:
    if not href:
        return base
    return urllib.parse.urljoin(base, href)


def clean_title(s: str) -> str:
    s = unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def date_near_link(html: str, title: str) -> Tuple[str, str]:
    """링크 제목 주변에서 날짜 1~2개를 대충 찾아 게시/마감 후보로 쓴다."""
    idx = html.find(title)
    if idx < 0:
        return "", ""
    chunk = html[max(0, idx - 500): idx + 800]
    dates = re.findall(r"20\d{2}[.\-/]\s*\d{1,2}[.\-/]\s*\d{1,2}|\d{1,2}[.]\s*\d{1,2}[(][^)]+[)]|\d{1,2}[.]\s*\d{1,2}", chunk)
    dates = [re.sub(r"\s+", "", d) for d in dates]
    if not dates:
        return "", ""
    if len(dates) == 1:
        return dates[0], ""
    return dates[0], dates[-1]


def load_sh_notices() -> Tuple[List[Notice], List[Tuple[str, str]]]:
    notices: List[Notice] = []
    raw_saved: List[Tuple[str, str]] = []
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for i, url in enumerate(SH_URLS, 1):
        try:
            html = decode_bytes(fetch_url(url, timeout=20))
        except Exception as e:
            raw_saved.append((url, f"FETCH_ERROR: {e}"))
            continue

        # raw는 항상 일부 저장. SH 파서 보정할 때 쓰기 좋음.
        raw_name = OUT_DIR / f"raw_SH_{i}_{datetime.now():%Y%m%d_%H%M}.html"
        try:
            raw_name.write_text(html[:300000], encoding="utf-8")
        except Exception:
            pass

        parser = LinkCollector()
        try:
            parser.feed(html)
        except Exception:
            pass

        for href, text in parser.links:
            title = clean_title(text)
            blob = title
            # 너무 짧거나 메뉴 링크 제외
            if len(title) < 6:
                continue
            if title in {"로그인", "회원가입", "사이트맵", "검색", "바로가기", "더보기", "목록"}:
                continue
            # 사이트 메뉴 링크 제외 (게시판 글이 아님)
            if href and any(p in href for p in ("sublink.do", "contents.do", "index.do")):
                continue
            # SH에서 글감 될 만한 키워드만 통과
            if not hot_words(blob) and not any(k in blob for k in ("입주자", "모집", "공고", "임대", "주택", "잔여세대")):
                continue
            if not passes_region(blob) and not hot_words(blob):
                # SH 전체 공고는 지역명이 제목에 없을 수 있으니 HOT이면 통과, 아니면 제외
                continue
            start, close = date_near_link(html, title)
            full = abs_url(url, href)
            pid = f"SH:{title}"  # 제목 기반 -> 같은 공고가 두 게시판에 떠도 1건
            notices.append(Notice(
                org="SH",
                id=pid,
                name=title,
                type="SH 공고",
                region="서울",
                start=start,
                close=close,
                url=full,
                hot=hot_words(blob),
                source=url,
            ))

    # 중복 제거
    uniq: Dict[str, Notice] = {}
    for n in notices:
        key = n.id if n.url else f"SH:{n.name}"
        uniq[key] = n
    return list(uniq.values()), raw_saved


# ───────────────── 공통 ─────────────────
def load_seoul_notices():
    """서울주거포털 공고 수집 — SH 게시판에 안 뜨는 서울시 계열 공고 커버"""
    notices = []
    raw_saved = []
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for i, url in enumerate(SEOUL_URLS, 1):
        try:
            html = decode_bytes(fetch_url(url, timeout=20))
        except Exception as e:
            raw_saved.append((url, f"FETCH_ERROR: {e}"))
            continue
        try:
            (OUT_DIR / f"raw_SEOUL_{i}_{datetime.now():%Y%m%d_%H%M}.html").write_text(html[:300000], encoding="utf-8")
        except Exception:
            pass
        parser = LinkCollector()
        try:
            parser.feed(html)
        except Exception:
            pass
        got = 0
        for href, text in parser.links:
            title = clean_title(text)
            if len(title) < 8:
                continue
            if not re.search(r"모집|공고", title):
                continue
            hits = hot_words(title)
            if not hits:
                continue
            notices.append(Notice(
                org="서울주거포털",
                id=f"SEOUL_{abs(hash(title)) & 0xFFFFFFFF}",
                name=title,
                type="서울시 공고",
                region="서울",
                url=abs_url(url, href),
                hot=hits,
                source=url,
            ))
            got += 1
    uniq = {}
    for n in notices:
        uniq.setdefault(n.id, n)
    return list(uniq.values()), raw_saved


def load_seen() -> set:
    if SEEN_PATH.exists():
        try:
            return set(json.loads(SEEN_PATH.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def save_seen(seen: Iterable[str]) -> None:
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(json.dumps(sorted(set(seen)), ensure_ascii=False, indent=1), encoding="utf-8")


def notice_score(n: Notice) -> Tuple[int, str, str]:
    # HOT 많을수록 앞으로, LH/SH 섞어서 최신성은 문자열 정렬 보조
    return (len(n.hot), n.start, n.name)


def write_alert(fresh: List[Notice], checked_count: int, seen_count: int) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fn = OUT_DIR / f"notice_{datetime.now():%Y%m%d_%H%M}.txt"
    lines = [
        f"🚨 새 공고 {len(fresh)}건 — {datetime.now():%Y.%m.%d %H:%M}",
        f"확인 {checked_count}건 / 기존 기억 {seen_count}건",
        "다음 행동: 링크에서 공고문 PDF 내려받기 → 글감/초안/티스토리 대표이미지 제작",
        "",
    ]
    for n in sorted(fresh, key=notice_score, reverse=True):
        star = "★" * min(len(n.hot), 3)
        fire = "🔥" if n.hot else "·"
        lines.append(f"{fire} [{n.org} / {n.type or '공고'}] {n.name} {star}")
        lines.append(f"    지역 {n.region or '-'} / 게시 {n.start or '-'} / 마감 {n.close or '-'}")
        if n.hot:
            lines.append(f"    강조: {', '.join(n.hot)}")
        if n.url:
            lines.append(f"    링크: {n.url}")
        lines.append(f"    소스: {n.source}")
        lines.append("")

    text = "\n".join(lines)
    fn.write_text(text, encoding="utf-8")
    print(text)
    print(f"✓ 알림 저장: {fn}")


def main() -> None:
    seen = load_seen()
    all_notices: List[Notice] = []

    lh_notices, lh_raw = load_lh_notices()
    all_notices.extend(lh_notices)

    sh_notices, sh_errors = load_sh_notices()
    all_notices.extend(sh_notices)

    seoul_notices, seoul_errors = load_seoul_notices()
    all_notices.extend(seoul_notices)
    if seoul_errors:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / f"raw_SEOUL_errors_{datetime.now():%Y%m%d_%H%M}.txt").write_text(
            "\n\n".join(f"URL: {u}\n{e}" for u, e in seoul_errors), encoding="utf-8"
        )

    if LH_SERVICE_KEY and not lh_notices and lh_raw is not None:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / f"raw_LH_{datetime.now():%Y%m%d_%H%M}.json").write_text(
            json.dumps(lh_raw, ensure_ascii=False, indent=1)[:200000], encoding="utf-8"
        )

    if sh_errors:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / f"raw_SH_errors_{datetime.now():%Y%m%d_%H%M}.txt").write_text(
            "\n\n".join(f"URL: {u}\n{e}" for u, e in sh_errors), encoding="utf-8"
        )

    # 필터 후 새 공고만
    fresh: List[Notice] = []
    for n in all_notices:
        if not n.id or n.id in seen:
            continue
        fresh.append(n)

    if not fresh:
        print(f"새 공고 없음 (확인 {len(all_notices)}건, 기억 {len(seen)}건) — {datetime.now():%Y.%m.%d %H:%M}")
        if not all_notices:
            print("※ LH 키/SH URL을 확인하세요. SH는 raw_SH_*.html 저장 파일로 파서 보정 가능.")
        return

    write_alert(fresh, checked_count=len(all_notices), seen_count=len(seen))

    # 텔레그램 알림 (환경변수 설정 시에만 전송)
    tg_lines = [f"[공고알림] 새 공고 {len(fresh)}건 - {datetime.now():%m.%d %H:%M}"]
    for n in fresh[:15]:
        star = "*" * min(len(n.hot), 3)
        tg_lines.append(f"{star}[{n.org}] {n.name[:60]} (게시 {n.start or '-'})")
    if len(fresh) > 15:
        tg_lines.append(f"... 외 {len(fresh)-15}건")
    send_telegram("\n".join(tg_lines))
    seen |= {n.id for n in fresh}
    save_seen(seen)


if __name__ == "__main__":
    main()
