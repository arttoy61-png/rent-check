# Seoul isolated data

이 폴더는 서울 25개 구 실거래 테스트 수집용입니다.

- 기존 `molit_kangseo.csv`와 분리
- Rent Check 홈과 연결하지 않음
- 기존 도구/글/위젯에서 읽지 않음
- 첫 테스트는 최근 3개월
- `raw/`에 구별 CSV 저장
- `seoul_summary.json`에 구·월·거래유형별 요약 저장
- `collection_status.json`에 구별 수집 성공/실패 상태 저장

데이터가 안정적으로 쌓이는 것을 확인한 뒤에만 수집 기간 확대나 홈 연결을 검토합니다.
