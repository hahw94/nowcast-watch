# nowcast-watch

태양광 초단기 · 단기 예측 공개 사이트(https://hahw94.github.io/solar-nowcast-live/) 감시 → 텔레그램 알림.

- GitHub Actions 가 10분마다 `check.py` 를 돌린다(예측 서버 · 연구 PC 가 꺼져도 돎).
- 마지막 예측 결과의 발표 시각이 낮 60분 · 밤 110분 넘게 지나면 `⚠ 멈춤`(원인 = 사이트의 `data/status.json`), 다시 나오면 `✅ 복구`.
- 매일 07:30 뒤 첫 실행에서 `☀ 정상` 요약(발표 수 · 디스크 · CPU · 메모리 · 계산 시간 · 실패).
- 시험: Actions → site-watch → Run workflow → "시험 알림 보내기".
- 비밀 값: Settings → Secrets 의 `TELEGRAM_TOKEN` · `TELEGRAM_CHAT_ID` (코드에는 없음).
- 상태: `state.json` (하루 한 번 바뀌어 저장소 활동이 유지됨).

관리: 운영 담당(원본 `C:\work\nowcast_ops\watch`). 2026-10-31 시작.
