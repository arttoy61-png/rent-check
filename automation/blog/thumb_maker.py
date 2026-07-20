# -*- coding: utf-8 -*-
"""Rent Check 강서 — 요일글 대문 자동생성 모듈 (1080x1080)
daily_content.py의 save_post()에서 호출됨.
폰트 자동 탐색: ①blog/fonts/NotoSansKR-*.otf ②윈도우 맑은고딕 ③리눅스 Noto ttc
"""
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
import random, os, sys

W = H = 1080
SAFE_W = 940

NAVY_TOP, NAVY_BOT = (8, 16, 40), (16, 33, 74)
GOLD, GOLD_DEEP = (240, 199, 94), (212, 167, 60)
WHITE, SOFT = (255, 255, 255), (196, 208, 232)
BOX_FILL = (24, 43, 92)

# ── 폰트 자동 탐색 ──────────────────────────────────────
_HERE = Path(__file__).parent

def _resolve_fonts():
    """weight명('Black','Bold','Medium','Regular') → (경로, ttc index) 매핑"""
    # 1) 프로젝트 fonts 폴더의 NotoSansKR otf (권장)
    f_dir = _HERE / "fonts"
    noto = {}
    for w in ("Black", "Bold", "Medium", "Regular"):
        for ext in (".otf", ".ttf"):  # Google Fonts는 .ttf로 배포
            p = f_dir / f"NotoSansKR-{w}{ext}"
            if p.exists():
                noto[w] = p; break
    if len(noto) == 4:
        return {w: (str(p), None) for w, p in noto.items()}
    # 2) 윈도우 맑은고딕 (Black·Medium 없음 → Bold·Regular로 대체)
    if sys.platform.startswith("win"):
        mb, mr = "C:/Windows/Fonts/malgunbd.ttf", "C:/Windows/Fonts/malgun.ttf"
        if os.path.exists(mb) and os.path.exists(mr):
            return {"Black": (mb, None), "Bold": (mb, None),
                    "Medium": (mr, None), "Regular": (mr, None)}
    # 3) 리눅스/맥 Noto CJK ttc
    ttc = "/usr/share/fonts/opentype/noto/NotoSansCJK-%s.ttc"
    if os.path.exists(ttc % "Bold"):
        return {w: (ttc % w, 1) for w in ("Black", "Bold", "Medium", "Regular")}
    raise FileNotFoundError(
        "한글 폰트를 찾지 못했습니다. blog/fonts/ 폴더에 NotoSansKR-Black/Bold/Medium/Regular.otf를 넣어주세요.")

_FONTS = None

def F(weight, size):
    global _FONTS
    if _FONTS is None:
        _FONTS = _resolve_fonts()
    path, idx = _FONTS[weight]
    if idx is None:
        return ImageFont.truetype(path, size)
    return ImageFont.truetype(path, size, index=idx)

# ── 그리기 헬퍼 ──────────────────────────────────────
def gradient_bg():
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        t = y / H
        c = tuple(int(NAVY_TOP[i] + (NAVY_BOT[i] - NAVY_TOP[i]) * t) for i in range(3))
        for x in range(W):
            px[x, y] = c
    return img

def skyline(d, base_y=1080):
    random.seed(11)
    x = -20
    while x < W + 20:
        bw, bh = random.randint(60, 130), random.randint(50, 140)
        d.rectangle([x, base_y - bh, x + bw, base_y], fill=(10, 20, 50))
        for wy in range(base_y - bh + 18, base_y - 10, 38):
            for wx in range(x + 14, x + bw - 12, 34):
                if random.random() < 0.3:
                    d.rectangle([wx, wy, wx + 7, wy + 11], fill=(82, 94, 138))
        x += bw + random.randint(6, 22)

def gold_rule(d, cx, y, w=320):
    d.line([cx - w // 2, y, cx - 22, y], fill=GOLD_DEEP, width=3)
    d.line([cx + 22, y, cx + w // 2, y], fill=GOLD_DEEP, width=3)
    d.polygon([(cx, y - 8), (cx + 8, y), (cx, y + 8), (cx - 8, y)], fill=GOLD)

def fit_font(d, text, weight, max_size, max_w):
    size = max_size
    while size > 30:
        f = F(weight, size)
        if d.textlength(text, font=f) <= max_w:
            return f, size
        size -= 4
    return F(weight, size), size

# ── 대문 생성 (대문생성기.py v2와 동일) ──────────────────
def make_thumb(badge_text, title_lines, sub, point, fname, accent=None, day_label=None, tagline=None,
               brand_main="Rent Check", brand_suffix=" 강서", brand_tagline="실거래로 더 정확하게"):
    accent = accent or GOLD
    accent_deep = tuple(max(0, c-28) for c in accent)
    img = gradient_bg()
    d = ImageDraw.Draw(img)
    skyline(d, 1080)

    f_badge = F("Medium", 36)
    tw = d.textlength(badge_text, font=f_badge)
    d.rounded_rectangle([W/2 - tw/2 - 34, 92, W/2 + tw/2 + 34, 158],
                        radius=33, outline=accent_deep, width=3)
    d.text((W/2, 125), badge_text, font=f_badge, fill=accent, anchor="mm")

    # 우상단 요일 원형 배지 (기존 대문 시그니처)
    if day_label:
        cx_d, cy_d, r_d = W - 110, 124, 62
        d.ellipse([cx_d-r_d, cy_d-r_d, cx_d+r_d, cy_d+r_d], outline=accent, width=4)
        d.ellipse([cx_d-r_d+8, cy_d-r_d+8, cx_d+r_d-8, cy_d+r_d-8], outline=accent_deep, width=2)
        d.text((cx_d, cy_d), day_label, font=F("Bold", 30), fill=accent, anchor="mm")

    base = 132 if len(title_lines) <= 2 else 108
    sizes = [fit_font(d, t, "Black", base, SAFE_W)[1] for t, _ in title_lines]
    size = min(sizes)
    f_title = F("Black", size)
    line_h = int(size * 1.32)
    ty = 420 - (len(title_lines) * line_h) // 2
    for text, color in title_lines:
        draw_color = accent if color == GOLD else color
        d.text((W/2, ty), text, font=f_title, fill=draw_color, anchor="ma")
        ty += line_h

    rule_y = ty + 30
    d.line([W/2 - 160, rule_y, W/2 - 22, rule_y], fill=accent_deep, width=3)
    d.line([W/2 + 22, rule_y, W/2 + 160, rule_y], fill=accent_deep, width=3)
    d.polygon([(W/2, rule_y-8), (W/2+8, rule_y), (W/2, rule_y+8), (W/2-8, rule_y)], fill=accent)

    f_sub, _ = fit_font(d, sub, "Medium", 48, SAFE_W - 60)
    d.text((W/2, rule_y + 44), sub, font=f_sub, fill=SOFT, anchor="ma")
    if tagline:
        d.text((W/2, rule_y + 116), tagline, font=F("Regular", 32), fill=(150,165,195), anchor="ma")

    if point:
        f_pt, _ = fit_font(d, point, "Bold", 50, SAFE_W - 120)
        pw = d.textlength(point, font=f_pt)
        bx = W/2 - pw/2 - 48
        d.rounded_rectangle([bx, 768, W - bx, 886], radius=28,
                            fill=BOX_FILL, outline=accent_deep, width=3)
        d.text((W/2, 827), point, font=f_pt, fill=accent, anchor="mm")

    f_logo, f_tag = F("Bold", 44), F("Regular", 28)
    t1, t2 = brand_main, brand_suffix
    w1 = d.textlength(t1, font=f_logo); w2 = d.textlength(t2, font=f_logo) if t2 else 0
    sx = W/2 - (w1 + w2) / 2
    d.text((sx, 948), t1, font=f_logo, fill=WHITE)
    if t2:
        d.text((sx + w1, 948), t2, font=f_logo, fill=GOLD)
    d.text((W/2, 1022), brand_tagline, font=f_tag, fill=SOFT, anchor="ma")

    img.save(fname)
    return fname

# ── 자동글용: post → 대문 (제목 자동 분리) ─────────────────
# 요일별 스타일: 액센트 컬러 + 배지 + 요일 라벨 + 컨셉 태그라인
WEEKDAY_STYLE = {
    # 2026.7.4 카테고리 대개편: 요일 배지·요일색 폐지 → 배지=카테고리명, 색=대분류색
    # 자동글 월~토 6종=실거래·시세(보라), 일=임차인 가이드(청록). day=None(요일 원형배지 없음)
    "weekly_summary":     {"badge": "주간 거래결산",      "accent": (122, 63, 176), "day": None, "tagline": None},
    "rent_check":         {"badge": "화곡동 시세",        "accent": (122, 63, 176), "day": None, "tagline": None},
    "building_spotlight": {"badge": "단지별 시세",        "accent": (122, 63, 176), "day": None, "tagline": None},
    "jeonse_vs_monthly":  {"badge": "전세 vs 월세",       "accent": (122, 63, 176), "day": None, "tagline": None},
    "value_picks":        {"badge": "단지별 실거래 추적", "accent": (122, 63, 176), "day": None, "tagline": None},
    "neighborhood":       {"badge": "거래활발 단지",      "accent": (122, 63, 176), "day": None, "tagline": None},
    "tenant_guide":       {"badge": "임차인 가이드",      "accent": (13, 138, 138), "day": None, "tagline": None},
    "monthly_report":     {"badge": "월간 시세 리포트",   "accent": (122, 63, 176), "day": None, "tagline": None},
    "monthly_apt":        {"badge": "월간 시세 리포트",   "accent": (122, 63, 176), "day": None, "tagline": None},
    "monthly_villa":      {"badge": "월간 시세 리포트",   "accent": (122, 63, 176), "day": None, "tagline": None},
    "monthly_officetel":  {"badge": "월간 시세 리포트",   "accent": (122, 63, 176), "day": None, "tagline": None},
}

# 손글·대분류용 (대문.py에서 사용) — 컬럼 기본은 골드(CAT=None)
CATEGORY_STYLE = dict(WEEKDAY_STYLE)
CATEGORY_STYLE.update({
    "실거래":  {"badge": "실거래·시세",    "accent": (122, 63, 176), "day": None, "tagline": None},
    "청년":    {"badge": "청년·공공주택",  "accent": (21, 101, 192), "day": None, "tagline": None},
    "빌라":    {"badge": "빌라 매수 체크", "accent": (46, 125, 50),  "day": None, "tagline": None},
    "재개발":  {"badge": "재개발",         "accent": (196, 102, 31), "day": None, "tagline": None},
    "임차인":  {"badge": "임차인 가이드",  "accent": (13, 138, 138), "day": None, "tagline": None},
    "정책":    {"badge": "정책·시장",      "accent": (192, 57, 43),  "day": None, "tagline": None},
})

import re as _re

def _split_title(title: str):
    """제목 → (제목부, 부제부). ｜·하이픈·콜론 기준 첫 분리."""
    title = _re.sub(r"\s*\(\d{4}[-.]\d{2}(?:[-.]\d{2})?\)\s*$", "", title)  # 끝의 (YYYY-MM) 또는 (YYYY-MM-DD) 제거
    for sep in ("｜", " | ", " - ", " — ", ": "):
        if sep in title:
            a, b = title.split(sep, 1)
            return a.strip(), b.strip()
    return title.strip(), ""

def _two_lines(text: str):
    """제목부를 2줄로: 마지막 줄만 골드. 짧으면 1줄(골드)."""
    words = text.split()
    if len(words) <= 2 or len(text) <= 9:
        return [(text, GOLD)]
    # 글자 길이가 가장 균형 잡히는 지점에서 분할 (어절 보존)
    total = len(text)
    best_i, best_gap = 1, 10**9
    for i in range(1, len(words)):
        l1len = len(" ".join(words[:i]))
        gap = abs(l1len - (total - l1len))
        if gap < best_gap:
            best_i, best_gap = i, gap
    l1, l2 = " ".join(words[:best_i]), " ".join(words[best_i:])
    return [(l1, WHITE), (l2, GOLD)]

def auto_thumb(post: dict, date_str: str, folder) -> str:
    """save_post에서 호출. post의 title/category로 요일 스타일 대문 PNG 생성."""
    head, tail = _split_title(post.get("title", ""))
    title_lines = _two_lines(head)
    sub = tail if tail else f"{date_str[:4]}년 {int(date_str[5:7])}월 실거래 기준"
    style = WEEKDAY_STYLE.get(post.get("category", ""),
                              {"accent": GOLD, "badge": "렌트체크 강서", "day": None, "tagline": None})
    point = post.get("thumb_point")
    fname = str(Path(folder) / f"{post['category']}_대문.png")
    return make_thumb(style["badge"], title_lines, sub, point, fname,
                      accent=style["accent"], day_label=style["day"], tagline=style["tagline"])
