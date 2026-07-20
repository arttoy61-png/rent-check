"""
블로그 글 본문에 박는 위젯 링크 박스 생성기

본문 매트릭스 표 아래에:
"더 정확하게 본인 조건으로 확인하고 싶다면 → [내 월세 시세 확인하기]"
링크 박스를 자동 생성.

GitHub Pages URL은 사용자가 설정한 후 BLOG_WIDGET_URL 만 바꾸면 됨.
"""

# TODO: GitHub Pages 설정 후 실제 URL로 변경
BLOG_WIDGET_URL = "https://arttoy61-png.github.io/rent-check/"


def render_link_box(widget_url: str = BLOG_WIDGET_URL, region: str = "강서구 화곡동") -> str:
    """블로그 본문 어디든 박을 수 있는 링크 박스 HTML"""
    return f"""
<div style="margin:30px 0;font-family:'Malgun Gothic',sans-serif">
  <a href="{widget_url}" target="_blank" rel="noopener" style="display:block;text-decoration:none;color:inherit">
    <div style="background:linear-gradient(135deg,#0d1f3c,#1565c0);color:#fff;padding:28px 26px;border-radius:14px;position:relative;overflow:hidden;box-shadow:0 8px 24px rgba(13,31,60,.2);transition:transform .2s">
      <div style="position:absolute;top:-40%;right:-15%;width:280px;height:280px;background:radial-gradient(circle,rgba(212,167,58,.2) 0%,transparent 65%)"></div>
      <div style="position:relative">
        <div style="font-size:11px;letter-spacing:3px;opacity:.85;margin-bottom:8px">🔍 RENT CHECKER</div>
        <div style="font-size:20px;font-weight:700;margin-bottom:10px;line-height:1.4">내 월세, 정말 적정한가요?</div>
        <div style="font-size:13px;opacity:.92;line-height:1.7;margin-bottom:16px">
          {region} 실거래 데이터로 본인 계약 조건을 입력하면<br>
          ✅ 적정한지 / ⚠️ 비싼지 / 💎 저렴한지 즉시 확인됩니다.
        </div>
        <div style="display:inline-flex;align-items:center;gap:8px;background:rgba(255,255,255,.15);padding:10px 18px;border-radius:30px;font-size:13px;font-weight:600;border:1px solid rgba(255,255,255,.25)">
          <span>👉 내 월세 시세 확인하러 가기</span>
          <span style="font-size:16px">→</span>
        </div>
      </div>
    </div>
  </a>
  <div style="font-size:11px;color:#999;text-align:center;margin-top:8px">
    💡 본 도구는 국토교통부 실거래가 공개자료를 기반으로 합니다 · 무료 · 회원가입 불필요
  </div>
</div>
"""


def render_compact_link(widget_url: str = BLOG_WIDGET_URL) -> str:
    """짧은 인라인 링크 (글 본문 중간에 박을 때)"""
    return f"""
<div style="background:#fff8e1;border-left:4px solid #d4a73a;padding:14px 18px;margin:20px 0;border-radius:6px;font-family:'Malgun Gothic',sans-serif">
  <div style="font-size:13px;color:#666;line-height:1.7">
    💡 <strong>본인 조건으로 직접 시세 검증하기:</strong> 
    <a href="{widget_url}" target="_blank" rel="noopener" style="color:#1565c0;font-weight:600;text-decoration:none">→ 시세 검증 도구 바로가기</a>
  </div>
</div>
"""


if __name__ == "__main__":
    print("=== 메인 링크 박스 (블로그 글 하단용) ===")
    print(render_link_box())
    print()
    print("=== 인라인 링크 (글 본문 중간용) ===")
    print(render_compact_link())
