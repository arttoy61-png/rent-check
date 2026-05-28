"""
GitHub Actions 자동화용 설정.
API 키는 저장소에 넣지 않고 GitHub Secret(환경변수)에서 읽는다.
(저장소가 공개여도 키가 노출되지 않음)
"""
import os

# 국토부 실거래 API 키 → GitHub Secret: MOLIT_API_KEY
MOLIT_API_KEY = os.environ.get("MOLIT_API_KEY", "")

# 수집할 거래유형 (PC 버전과 동일)
DEAL_TYPES = [
    "오피스텔_전월세",
    "연립다세대_전월세",
    "아파트_전월세",
    "오피스텔_매매",
    "연립다세대_매매",
    "아파트_매매",
]

# 최근 몇 개월
MONTHS_BACK = int(os.environ.get("MONTHS_BACK", "6"))

# Actions는 매 실행 새 러너 → 캐시 끄고 항상 최신 수집
USE_CACHE = False
