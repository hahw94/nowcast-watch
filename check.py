# -*- coding: utf-8 -*-
"""
공개 사이트(solar-nowcast-live) 갱신 감시 → 텔레그램 알림 (2026-10-31 연구자 요청: 서버가 멈추면 폰으로 알림).
GitHub Actions 가 10분마다 돌린다(서버 · 이 PC 가 꺼져도 돎). 사이트의 data/index.json 'updated'(마지막으로 올린 한국 시각)가
낮(07:20 – 17:10) 40분, 밤(17:30 – 05:40) 90분 넘게 멈추면 한 번 알리고, 다시 갱신되면 '복구'를 한 번 알린다.
05:40 – 07:20 · 17:10 – 17:30 은 원래 발표 사이라 멈춤 알림을 보내지 않는다. 상태는 state.json(이 저장소)에 둔다.
비밀 값(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)은 저장소 Secrets 에서 환경 변수로만 받고 출력하지 않는다.
"""
import datetime as dt
import json
import os
import urllib.parse
import urllib.request

# ==============================================================
# 설정 블록 (여기만 고친다)
# ==============================================================
SITE = "https://hahw94.github.io/solar-nowcast-live/"
DAY = ((7, 20), (17, 10), 40)                   # (시작, 끝, 멈춤 판단 분) 한국 시각
NIGHT = ((17, 30), (5, 40), 90)                 # 자정을 넘김
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
# ==============================================================

KST = dt.timezone(dt.timedelta(hours=9))


def send(text):
    tok, chat = os.environ.get("TELEGRAM_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not tok or not chat:
        print("텔레그램 비밀 값이 없어 보내지 않음:", text); return
    data = urllib.parse.urlencode(dict(chat_id=chat, text=text)).encode()
    urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=data), timeout=20).read()
    print("보냄:", text)


def limit(now):
    """지금 시각의 멈춤 판단 분(None = 판단하지 않는 시간)"""
    m = now.hour * 60 + now.minute
    (a, b, lim) = DAY
    if a[0] * 60 + a[1] <= m <= b[0] * 60 + b[1]:
        return lim
    (a, b, lim) = NIGHT
    if m >= a[0] * 60 + a[1] or m <= b[0] * 60 + b[1]:
        return lim
    return None


def main():
    now = dt.datetime.now(KST)
    st = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {"alert": False}
    try:
        j = json.loads(urllib.request.urlopen(f"{SITE}data/index.json?t={int(now.timestamp())}", timeout=30).read())
        up = dt.datetime.strptime(j["updated"], "%Y-%m-%d %H:%M").replace(tzinfo=KST); age = (now - up).total_seconds() / 60; why = f"마지막 갱신 {up:%m-%d %H:%M} ({age:.0f}분 전)"
    except Exception as e:
        age, why = 1e9, f"사이트를 읽지 못함({type(e).__name__})"
    lim = limit(now); stale = lim is not None and age > lim
    print(f"{now:%m-%d %H:%M} KST · {why} · 기준 {lim}분 · 멈춤 {stale}")
    if stale and not st.get("alert"):
        send(f"⚠ 태양광 예측 사이트 갱신 멈춤\n{why}\n서버(오라클) 확인 필요: {SITE}"); st = {"alert": True, "since": now.strftime("%Y-%m-%d %H:%M")}
    elif st.get("alert") and age <= (lim or 90):
        send(f"✅ 사이트 갱신 복구\n{why} (멈춤 알림 {st.get('since', '')})"); st = {"alert": False}
    st["day"] = now.strftime("%Y-%m-%d")                      # 하루 한 번 상태 파일이 바뀌어 커밋됨(GitHub 은 60일 동안 저장소 활동이 없으면 정기 작업을 멈추므로)
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)


if __name__ == "__main__":
    main()
