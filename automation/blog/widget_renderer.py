"""
시세 매트릭스 HTML 표 생성기

aggregated_rent.json → 네이버 블로그 붙여넣기용 HTML 표

특징:
- 인라인 스타일 (네이버 블로그 에디터가 외부 CSS 제거함)
- JS 없음 (네이버 블로그는 스크립트 제거)
- 모바일 친화 (네이버 블로그 독자 80%가 모바일)
- 색상 강조 (셀별 가독성)
"""
import json
from pathlib import Path
from datetime import datetime

NAVY = "#0d1f3c"
BLUE = "#1565c0"
BLUE_LIGHT = "#e8f1fb"
GOLD = "#d4a73a"
RED = "#c62828"
GREEN = "#2e7d32"
GRAY_TXT = "#4a4a4a"
GRAY_LITE = "#f4f4f4"
GRAY_BORDER = "#e0e0e0"


def cell_color(avg_rent: int, all_rents: list) -> str:
    """월세 셀의 배경색 (구간 내 위치에 따라 그라데이션)"""
    if not all_rents or not avg_rent:
        return "#fff"
    lo, hi = min(all_rents), max(all_rents)
    if hi == lo:
        return "#fff"
    pos = (avg_rent - lo) / (hi - lo)  # 0~1
    # 낮을수록 초록(저렴), 높을수록 빨강(비쌈) — 단, 부드러운 채도
    if pos < 0.33:
        return "#e8f5e9"  # 연한 초록
    elif pos < 0.67:
        return "#fff8e1"  # 연한 노랑
    else:
        return "#ffebee"  # 연한 빨강


def render_matrix_table(report: dict) -> str:
    """월세 매트릭스 메인 표"""
    matrix = report.get("matrix", {})
    region = report.get("region", "")
    period = report.get("period", "")

    # 보증금 컬럼 순서 (집계 모듈과 동일, 1억 이상은 별도 준전세 섹션으로 분리)
    dep_names = ["500", "1,000", "2,000", "3,000", "5,000", "7,000"]
    py_names = list(matrix.keys())

    # 모든 월세값 모아 색상용
    all_rents = []
    for py in py_names:
        for d in dep_names:
            c = matrix.get(py, {}).get(d)
            if c:
                all_rents.append(c["median"])

    html = f"""
<div style="margin:24px 0;font-family:'Malgun Gothic','맑은 고딕',sans-serif">
  <div style="background:linear-gradient(135deg,{NAVY},{BLUE});color:#fff;padding:18px 22px;border-radius:10px 10px 0 0">
    <div style="font-size:13px;opacity:.85;letter-spacing:.5px;margin-bottom:4px">📊 RENT MATRIX</div>
    <div style="font-size:20px;font-weight:700">{region} 월세 시세표</div>
    <div style="font-size:12px;opacity:.8;margin-top:4px">분석 기간: {period} · 총 {report.get('total_monthly',0)}건 (월세) + {report.get('total_jeonse',0)}건 (전세)</div>
  </div>
  <table style="width:100%;border-collapse:collapse;font-size:13px;background:#fff;box-shadow:0 2px 8px rgba(0,0,0,.06)">
    <thead>
      <tr style="background:{NAVY};color:#fff">
        <th style="padding:12px 8px;text-align:center;font-weight:600;border-right:1px solid rgba(255,255,255,.15)">평형 / 보증금</th>
"""
    for d in dep_names:
        html += f'        <th style="padding:12px 8px;text-align:center;font-weight:600;border-right:1px solid rgba(255,255,255,.15)">보증금<br>{d}만원</th>\n'
    html += "      </tr>\n    </thead>\n    <tbody>\n"

    for py in py_names:
        html += f'      <tr>\n'
        html += f'        <td style="padding:12px 10px;background:{BLUE_LIGHT};font-weight:600;color:{NAVY};text-align:center;border-bottom:1px solid {GRAY_BORDER}">{py}</td>\n'
        for d in dep_names:
            c = matrix.get(py, {}).get(d)
            if c:
                bg = cell_color(c["median"], all_rents)
                html += (
                    f'        <td style="padding:12px 8px;text-align:center;'
                    f'background:{bg};border-bottom:1px solid {GRAY_BORDER};border-left:1px solid {GRAY_BORDER}">'
                    f'<div style="font-size:16px;font-weight:700;color:{NAVY};line-height:1.2">{c["median"]}<span style="font-size:11px;font-weight:400">만원</span></div>'
                    f'<div style="font-size:10px;color:{GRAY_TXT};margin-top:3px">{c["count"]}건 · {c["min"]}~{c["max"]}</div>'
                    f'</td>\n'
                )
            else:
                html += (
                    f'        <td style="padding:12px 8px;text-align:center;background:#fafafa;color:#bbb;'
                    f'border-bottom:1px solid {GRAY_BORDER};border-left:1px solid {GRAY_BORDER};font-size:13px">—</td>\n'
                )
        html += "      </tr>\n"

    html += """    </tbody>
  </table>
  <div style="background:#f8f8f8;padding:12px 16px;font-size:11px;color:#777;border-radius:0 0 10px 10px;line-height:1.7">
    💡 <strong>읽는 법</strong>: 본인 계약의 평형과 보증금 칸을 찾으세요. 굵은 숫자는 중위(가운데값) 월세, 작은 글씨는 실거래 건수와 범위입니다.<br>
    🎨 <strong>색상</strong>: <span style="background:#e8f5e9;padding:2px 8px;border-radius:3px">저렴</span> · <span style="background:#fff8e1;padding:2px 8px;border-radius:3px">중간</span> · <span style="background:#ffebee;padding:2px 8px;border-radius:3px">비쌈</span> (해당 표 내 상대적)<br>
    ⚠️ <strong>출처</strong>: 국토교통부 실거래가 공개시스템 (3건 미만 거래는 표시 제외)
  </div>
</div>
"""
    return html


def render_jeonse_table(report: dict) -> str:
    """전세 매트릭스 (간단한 가로 표)"""
    jeonse = report.get("jeonse_matrix", {})
    region = report.get("region", "")

    rows = [(py, c) for py, c in jeonse.items() if c]
    if not rows:
        return ""

    html = f"""
<div style="margin:24px 0;font-family:'Malgun Gothic','맑은 고딕',sans-serif">
  <div style="background:linear-gradient(135deg,#2e7d32,#43a047);color:#fff;padding:14px 20px;border-radius:8px 8px 0 0">
    <div style="font-size:16px;font-weight:700">🏠 {region} 전세 시세표</div>
  </div>
  <table style="width:100%;border-collapse:collapse;font-size:13px;background:#fff;box-shadow:0 2px 8px rgba(0,0,0,.06)">
    <thead>
      <tr style="background:#1b5e20;color:#fff">
        <th style="padding:10px 12px;text-align:center;font-weight:600">평형</th>
        <th style="padding:10px 12px;text-align:center;font-weight:600">중위 전세금</th>
        <th style="padding:10px 12px;text-align:center;font-weight:600">최저~최고</th>
        <th style="padding:10px 12px;text-align:center;font-weight:600">거래 건수</th>
      </tr>
    </thead>
    <tbody>
"""
    for i, (py, c) in enumerate(rows):
        bg = "#fff" if i % 2 == 0 else "#f9f9f9"
        html += (
            f'      <tr style="background:{bg}">'
            f'<td style="padding:10px 12px;text-align:center;font-weight:600;color:{NAVY}">{py}</td>'
            f'<td style="padding:10px 12px;text-align:center;font-size:15px;font-weight:700;color:#1b5e20">{c["median"]:,}<span style="font-size:11px;font-weight:400">만원</span></td>'
            f'<td style="padding:10px 12px;text-align:center;color:{GRAY_TXT};font-size:12px">{c["min"]:,}~{c["max"]:,}</td>'
            f'<td style="padding:10px 12px;text-align:center">{c["count"]}건</td>'
            f'</tr>\n'
        )
    html += """    </tbody>
  </table>
</div>
"""
    return html


def render_jeonse_like_table(report: dict) -> str:
    """준전세 매트릭스 (보증금 1억+ 월세, 평형별 가로 표)"""
    matrix = report.get("jeonse_like_matrix", {})
    region = report.get("region", "")

    rows = [(py, c) for py, c in matrix.items() if c]
    if not rows:
        return ""

    PURPLE = "#5e35b1"
    PURPLE_DARK = "#4527a0"
    html = f"""
<div style="margin:24px 0;font-family:'Malgun Gothic','맑은 고딕',sans-serif">
  <div style="background:linear-gradient(135deg,{PURPLE},#7e57c2);color:#fff;padding:14px 20px;border-radius:8px 8px 0 0">
    <div style="font-size:16px;font-weight:700">💎 {region} 준전세 시세표 <span style="font-size:11px;font-weight:400;opacity:.8;margin-left:6px">보증금 1억 이상 + 월세</span></div>
  </div>
  <table style="width:100%;border-collapse:collapse;font-size:13px;background:#fff;box-shadow:0 2px 8px rgba(0,0,0,.06)">
    <thead>
      <tr style="background:{PURPLE_DARK};color:#fff">
        <th style="padding:10px 8px;text-align:center;font-weight:600">평형</th>
        <th style="padding:10px 8px;text-align:center;font-weight:600">중위 보증금</th>
        <th style="padding:10px 8px;text-align:center;font-weight:600">중위 월세</th>
        <th style="padding:10px 8px;text-align:center;font-weight:600">보증금 범위</th>
        <th style="padding:10px 8px;text-align:center;font-weight:600">거래 건수</th>
      </tr>
    </thead>
    <tbody>
"""
    for i, (py, c) in enumerate(rows):
        bg = "#fff" if i % 2 == 0 else "#f9f7fd"
        html += (
            f'      <tr style="background:{bg}">'
            f'<td style="padding:10px 8px;text-align:center;font-weight:600;color:{NAVY}">{py}</td>'
            f'<td style="padding:10px 8px;text-align:center;font-size:15px;font-weight:700;color:{PURPLE}">{c["median_deposit"]:,}<span style="font-size:11px;font-weight:400">만원</span></td>'
            f'<td style="padding:10px 8px;text-align:center;font-weight:600">{c["median_rent"]}<span style="font-size:11px;font-weight:400">만원</span></td>'
            f'<td style="padding:10px 8px;text-align:center;color:{GRAY_TXT};font-size:12px">{c["min_deposit"]:,}~{c["max_deposit"]:,}</td>'
            f'<td style="padding:10px 8px;text-align:center">{c["count"]}건</td>'
            f'</tr>\n'
        )
    html += f"""    </tbody>
  </table>
  <div style="background:#f3f0fa;border-left:3px solid {PURPLE};padding:10px 14px;font-size:11px;color:#555;line-height:1.7;margin-top:0;border-radius:0 0 8px 8px">
    💡 <strong>준전세란?</strong> 보증금이 1억원 이상으로 높고 월세는 명목상으로만 책정된 계약 형태입니다. 
    순수 전세와 월세의 중간 형태이며, 화곡동 빌라 시장에서 흔히 볼 수 있습니다.
  </div>
</div>
"""
    return html


def render_checker_guide(report: dict) -> str:
    """임차인 시세 검증 가이드 (표 + 사용법)"""
    region = report.get("region", "")
    html = f"""
<div style="margin:24px 0;font-family:'Malgun Gothic','맑은 고딕',sans-serif;background:linear-gradient(135deg,#fff8e1,#fffde7);padding:20px 22px;border-radius:10px;border-left:4px solid {GOLD}">
  <div style="font-size:16px;font-weight:700;color:{NAVY};margin-bottom:10px">🔍 내 월세, 적정한가요?</div>
  <div style="font-size:13px;color:{GRAY_TXT};line-height:1.8">
    위 시세표에서 본인 계약 조건을 찾아 비교해보세요.
    <ol style="margin:10px 0 10px 18px;padding:0">
      <li>본인 집의 <strong>전용면적을 평수로 환산</strong>합니다. (㎡ ÷ 3.3 = 평)<br>
        <span style="font-size:11px;color:#999">예: 49.5㎡ ÷ 3.3 ≈ 15평 → "15~20평" 행</span></li>
      <li>본인 계약의 <strong>보증금에 가장 가까운 컬럼</strong>을 선택합니다.</li>
      <li>그 칸의 <strong>중위(가운데값) 월세</strong>와 본인 월세를 비교합니다.</li>
    </ol>
    <div style="background:#fff;padding:10px 14px;border-radius:6px;margin-top:8px;font-size:12px">
      ✅ <strong>표 안 범위 내</strong> → 적정<br>
      🟡 <strong>중위 대비 ±5만원</strong> → 합리적<br>
      🔴 <strong>최댓값보다 높음</strong> → 시세보다 비쌀 가능성 (재협상 검토)<br>
      🟢 <strong>최솟값보다 낮음</strong> → 시세보다 저렴 (가성비 좋은 계약)
    </div>
  </div>
</div>
"""
    return html


def render_full_widget(report: dict) -> str:
    """블로그 글에 통째로 박을 완성 위젯 (월세 매트릭스 + 준전세 + 전세 + 사용법)"""
    return (
        render_matrix_table(report)
        + render_jeonse_like_table(report)
        + render_jeonse_table(report)
        + render_checker_guide(report)
    )


def save_widget(report: dict, out_path: str = "outputs/widget/rent_matrix.html"):
    """완성 위젯 단독 HTML 파일로 저장 (미리보기·테스트용)"""
    full = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{report.get('region','')} 월세 시세표</title>
<style>body{{max-width:780px;margin:20px auto;padding:0 16px;background:#fafafa}}</style>
</head>
<body>
{render_full_widget(report)}
</body>
</html>"""

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(full)
    print(f"저장: {out_path}")


def save_widget_naver_snippet(report: dict, out_path: str = "outputs/widget/naver_snippet.html"):
    """네이버 블로그 본문 붙여넣기용 HTML 조각 (<body> 부분만)"""
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(render_full_widget(report))
    print(f"저장: {out_path}")


if __name__ == "__main__":
    json_path = "data/aggregated_rent.json"
    with open(json_path, encoding="utf-8") as f:
        report = json.load(f)

    save_widget(report)
    save_widget_naver_snippet(report)
    print(f"\n미리보기: outputs/widget/rent_matrix.html (브라우저로 열기)")
    print(f"네이버 붙여넣기용: outputs/widget/naver_snippet.html")
