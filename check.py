# -*- coding: utf-8 -*-
"""
공개 사이트(solar-nowcast-live) 감시 → 텔레그램 알림 (2026-10-31 연구자 요청: 서버가 멈추면 폰으로, 원인까지).
GitHub Actions 가 10분마다 돌린다(서버 · 이 PC 가 꺼져도 돎).
- 멈춤 판단: 마지막으로 나온 예측 결과의 발표 시각(낮 초단기 index.json latest, 밤 단기 night.json 최신)이
  낮(05:40 – 17:10) 60분, 밤(17:40 – 05:40) 110분 넘게 지나면 한 번 알리고, 새 결과가 나오면 '복구'를 한 번 알린다.
  (평소 낮은 30분마다 · 밤은 매시 결과가 나오므로 한 번 빠지는 정도는 알리지 않음. 17:10 – 17:40 은 원래 발표 사이, 11-03부터 새벽 N1 05:00 ~)
- 원인: 서버가 올리는 data/status.json(마지막 성공 · 최근 실패 · 디스크 · 받기 양). 이 파일까지 오래됐으면 '서버 응답 없음'으로 본다.
- 하루 한 번(07:30 뒤 첫 실행) '정상' 요약: 지난밤 단기 · 어제 초단기 발표 수, 마지막 결과, 서버 상태.
비밀 값(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)은 저장소 Secrets 에서 환경 변수로만 받고 출력하지 않는다. 상태는 state.json(이 저장소).
"""
import datetime as dt
import json
import os
import urllib.parse
import urllib.request

# ==============================================================
# 설정 블록 (여기만 고친다)
# ==============================================================
SITE = os.environ.get("WATCH_SITE", "https://161-33-191-137.sslip.io/live/")   # 2026-11-02: 사이트가 로그인 뒤로(11-01) → 감시용 상태 주소 SITE + watch.json 만 읽음(로그인 없이)
WATCH = {"data/index.json": "index", "data/night.json": "night", "data/status.json": "status", "data/wind.json": "wind", "data/sat_notice.json": "notice"}   # 예전 파일 이름 → watch.json 안 칸
NOTICE_LIM, NOTICE_FAIL = 150, 6                # 위성센터 공지 확인(서버 30분마다): 이 분 넘게 멈췄거나 이 횟수 연속 실패면 알림(약 3시간, 2026-11-04 연구자)
REMIND_EVE = 17                                 # 점검 전날 이 시 뒤 첫 실행에 한 번 더 알림. 당일은 HB 시각 뒤 첫 실행
KEEP = ("hb", "walert", "wsince", "nalert", "nsince", "nseen", "nrem", "jbad", "jpend", "rota")   # 멈춤 · 복구 때도 지키는 상태 칸
DAY = ((5, 40), (17, 10), 60)                   # (시작, 끝, 멈춤 판단 분: 마지막 결과의 발표 시각부터) 한국 시각
NIGHT = ((17, 40), (5, 40), 110)                # 자정을 넘김
DAWN_START, DAWN_FROM = 5, "2026-11-03"         # 새벽 N1 첫 시 · 시작 날짜(2026-11-03 연구자: 05시부터)
SERVER_SILENT_MIN = 40                          # status.json 이 이것과 멈춤 판단 분 중 큰 값보다 오래됐으면 서버 응답 없음으로 봄(판단하지 않는 시간엔 보지 않음)
WIND_LIM = 60                                   # 풍력 바람 자료(data/wind.json, 서버 20분마다)가 이 분보다 오래되면 멈춤 알림(2026-10-31 연구자)
JOBS = (("demand", "수요 실적", 60), ("target", "수요 기온 · 예보", 180), ("windcf", "전국 풍력", 60), ("hazard", "위험기상", 150), ("kim", "KIM 예보", 180), ("ens", "밤 앙상블", 150),
        ("ecm", "ECMWF 예보", 840), ("ecmd", "ECMWF 일일 표", 1620), ("kpx", "KPX 실적", 3000), ("sat", "유사일 위성 사진", 1620), ("days", "유사일 후보 날", 1620), ("score", "예측 성적표", 1620), ("snap", "수요 예측 저장", 840))   # (칸, 이름, 멈춤 판단 분) 평소 주기의 2 ~ 3배(2026-11-06 연구자)
NIGHT_ONLY = ("ens",)                           # 밤에만 도는 작업: 낮에는 안 봄
PORTS = (("analog", "유사일 서버"), ("notes", "근무노트"), ("energy", "에너지기상"))   # 내부 서비스. 재시작 때 오알림이 없게 두 번 연속일 때만 알림
ROTA_WARN = 30                                  # 근무표가 끝나기 이 날 전에 한 번 알림
HB = (7, 30)                                    # 하루 한 번 '정상' 요약을 보내는 시각(이 시각 뒤 첫 실행)
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


_W = {}


def rd(u, now):
    """watch.json 을 한 번만 받아 예전 파일 이름(data/index.json 등)으로 나눠 줌. 저장소 Secret WATCH_KEY 가 있으면 머리글로 보냄"""
    if "w" not in _W:
        h = {"User-Agent": "nowcast-watch"}; k = os.environ.get("WATCH_KEY", "")
        if k:
            h["x-watch-key"] = k
        _W["w"] = json.loads(urllib.request.urlopen(urllib.request.Request(f"{SITE}watch.json?t={int(now.timestamp())}", headers=h), timeout=30).read())
    return _W["w"][WATCH[u]]


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


def latest(now):
    """마지막으로 나온 예측 결과(발표 번호 YYYYMMDD_HHMM)와 그 발표 시각, 종류"""
    ix = rd("data/index.json", now); d = ix.get("latest") or ""; n = ""
    try:
        nj = rd("data/night.json", now); ds = nj.get("dates") or []; n = nj["by_date"][ds[0]][-1]["run"] if ds else ""
    except Exception:
        pass
    r, kind = (n, "밤 단기") if n > d else (d, "초단기")
    t = dt.datetime.strptime(r, "%Y%m%d_%H%M").replace(tzinfo=KST) if r else None
    return t, kind, ix


def cause(now):
    """서버 상태 파일 → 원인 몇 줄"""
    try:
        s = rd("data/status.json", now)
    except Exception as e:
        return [f"· 서버 상태 파일을 못 읽음({type(e).__name__})"], True
    up = dt.datetime.strptime(s["updated"], "%Y-%m-%d %H:%M").replace(tzinfo=KST); lim = limit(now)
    silent = lim is not None and (now - up).total_seconds() / 60 > max(SERVER_SILENT_MIN, lim)   # 발표 없는 시간(05:40 – 07:40 등)엔 오래돼도 정상(2026-10-31)
    L = [f"· 서버 {'응답 없음 추정' if silent else '정상 작동 중'} (서버 마지막 기록 {up:%m-%d %H:%M})"]
    for e in (s.get("errors") or [])[-2:]:
        L.append(f"· 최근 실패: {e[:150]}")
    last = s.get("last") or {}
    for k, nm in (("b5", "B5 업데이트"), ("l1", "단기 업데이트")):                              # 문구: 2026-10-31 연구자
        if last.get(k):
            L.append(f"· 마지막 {nm}: {last[k][1:15]}")
    L.append(f"· 마지막 업데이트: {up:%m-%d %H:%M}")                                            # 사이트에 올린 시각(낮 · 밤 모두)
    L.append(f"· 남은 디스크 용량 {s.get('disk_free_gb')} GB · 오늘 기상청 API 사용량 {s.get('kma_today_gb')} GB")
    wt, wa = wind_age(now)
    L.append(f"· 풍력 바람 {'멈춤 추정' if wa > WIND_LIM else '정상'} (마지막 수집 {wt:%m-%d %H:%M})" if wt else "· 풍력 바람 자료를 못 읽음")
    jl = jobs_line(now)
    if jl:
        L.append(jl)
    return L, (silent or bool(s.get("errors")) or wa > WIND_LIM or bool(jl and jl.startswith("⚠")))


def wind_age(now):
    """풍력 바람 자료의 마지막 수집 시각과 지난 분(못 읽으면 None, 큰 값)"""
    try:
        m = rd("data/wind.json", now).get("made"); t = dt.datetime.strptime(m[:16], "%Y-%m-%dT%H:%M").replace(tzinfo=KST)
        return t, (now - t).total_seconds() / 60
    except Exception:
        return None, 1e9


def hm(m):
    """분 → 읽기 쉬운 글"""
    return f"{m}분" if m < 120 else f"{m / 60:.0f}시간"


def jobs_bad(now):
    """멈춘 자동 작업 · 내부 서비스 {칸: 글}. 서버를 못 읽으면 None(그때는 예측 멈춤 알림이 감)"""
    try:
        rd("data/status.json", now); W = _W["w"]
    except Exception:
        return None
    if "jobs" not in W:
        return None
    bad = {}
    for k, nm, lim in JOBS:
        a = W["jobs"].get(k)
        if k in NIGHT_ONLY and 6 <= now.hour < 18:
            continue
        if a is None or a > lim:
            bad[k] = f"{nm}: " + ("결과 없음" if a is None else f"{hm(a)} 전이 마지막")
    for k, nm in PORTS:
        if not W.get("ports", {}).get(k):
            bad["p:" + k] = f"{nm}: 응답 없음"
    return bad


def jobs_line(now):
    """요약 한 줄(못 읽으면 None)"""
    b = jobs_bad(now)
    if b is None:
        return None
    return f"· 자동 작업 · 서비스 {len(JOBS) + len(PORTS)}개 정상" if not b else "⚠ 자동 작업 멈춤: " + ", ".join(b.values())


def jobs(now, st):
    """유사일 · 수요 · KIM · ECMWF · 위험기상 등 멈춤 · 복구 알림, 근무표 끝 알림(2026-11-06 연구자)"""
    bad = jobs_bad(now)
    if bad is None:
        return
    was, pend = st.get("jbad") or {}, st.get("jpend") or []
    new = {k: v for k, v in bad.items() if k not in was and (not k.startswith("p:") or k in pend)}
    back = {k: v for k, v in was.items() if k not in bad}
    if new:
        send(f"⚠ 자동 작업 멈춤 · {now:%m-%d %H:%M} KST\n모니터링 결과 : 확인 필요\n" + "\n".join(f"· {v}" for v in new.values()))
    if back:
        send(f"✅ 자동 작업 복구 · {now:%m-%d %H:%M} KST\n모니터링 결과 : 문제 없음\n" + "\n".join(f"· {v.split(':')[0]}" for v in back.values()))
    st["jbad"] = {**{k: v for k, v in was.items() if k in bad}, **new}; st["jpend"] = list(bad)
    end = _W["w"].get("rota_end")
    if end and st.get("rota") != end:
        left = (dt.date.fromisoformat(end) - now.date()).days
        if left <= ROTA_WARN:
            send(f"📅 근무표가 {end} 에 끝납니다 · {left}일 남음\n· 다음 근무표를 운영에 보내 주세요"); st["rota"] = end


def notice_text(it, head):
    """위성 공지 한 건 → 알림 글"""
    W = "일월화수목금토"; w = "일시 미정(공지 확인)"
    if it.get("start"):
        s, e = [dt.datetime.strptime(it[k], "%Y-%m-%d %H:%M") for k in ("start", "end")]
        w = f"{s.month}월 {s.day}일({W[(s.weekday() + 1) % 7]}) {s:%H:%M}~" + (f"{e:%H:%M}" if s.date() == e.date() else f"{e.month}월 {e.day}일 {e:%H:%M}")
    return f"{head}\n· {w}\n· {it.get('title', '')}\n{it.get('url', '')}"


def notice(now, st):
    """위성센터 공지: 새 공지 · 전날 · 당일 알림, 공지 확인이 멈추면 알림(2026-11-04 연구자)"""
    try:
        n = rd("data/sat_notice.json", now)
        ck = dt.datetime.strptime(n["checked"], "%Y-%m-%d %H:%M").replace(tzinfo=KST)
    except Exception as e:
        print("공지 상태를 못 읽음:", type(e).__name__); return
    age = (now - ck).total_seconds() / 60; bad = age > NOTICE_LIM or (n.get("fail") or 0) >= NOTICE_FAIL
    if bad and not st.get("nalert"):
        send(f"⚠ 위성 공지 확인 멈춤 · {now:%m-%d %H:%M} KST\n모니터링 결과 : 확인 필요\n· 마지막 확인 {ck:%m-%d %H:%M} ({age:.0f}분 전) · 연속 실패 {n.get('fail') or 0}번\n· 점검 공지를 놓칠 수 있습니다\nhttps://nmsc.kma.go.kr/homepage/html/base/bbs/listBbs.do?bbsCd=00237"); st["nalert"] = True; st["nsince"] = now.strftime("%Y-%m-%d %H:%M")
    elif st.get("nalert") and not bad:
        send(f"✅ 위성 공지 확인 복구 · {now:%m-%d %H:%M} KST\n· 멈춤 알림 {st.get('nsince', '')}"); st["nalert"] = False
    seen, rem = st.get("nseen") or [], st.get("nrem") or []
    for it in n.get("items") or []:
        u = it.get("id")
        if u not in seen:
            send(notice_text(it, "🛰 위성 점검 공지" if it.get("start") else "🛰 위성 공지 확인 필요")); seen.append(u)
        if it.get("start"):
            t = dt.datetime.strptime(it["start"], "%Y-%m-%d %H:%M").replace(tzinfo=KST); d = (t.date() - now.date()).days
            k = f"{u}-{d}"; hm = now.hour * 60 + now.minute
            if k not in rem and ((d == 1 and now.hour >= REMIND_EVE) or (d == 0 and now < t and hm >= HB[0] * 60 + HB[1])):
                send(notice_text(it, "🛰 내일 위성 점검" if d == 1 else "🛰 오늘 위성 점검")); rem.append(k)
    st["nseen"], st["nrem"] = seen[-30:], rem[-30:]


def heartbeat(now, why):
    """아침 요약: 지난밤 단기(전날 17 ~ 그날 05시, 13개) · 어제 낮 초단기(05:00 ~ 16:30, 24개 — 11-02까지는 07:00 ~, 20개) 발표 수와 빠진 시각, 마지막 결과, 서버 상태"""
    today, yday = now.strftime("%Y-%m-%d"), (now - dt.timedelta(days=1)).strftime("%Y-%m-%d"); lines = []
    try:
        nj = rd("data/night.json", now); got = {r["run"][9:11] for r in nj.get("by_date", {}).get(today, [])}
        want = [f"{h:02d}" for h in list(range(17, 24)) + list(range(0, 6))]; miss = [h for h in want if h not in got]
        lines.append(f"{'⚠' if miss else '·'} 밤 단기 {len(want) - len(miss)}/{len(want)}" + (f" ({', '.join(m + '시' for m in miss)} 빠짐)" if miss else ""))
    except Exception as e:
        lines.append(f"⚠ 밤 단기 확인 못 함({type(e).__name__})")
    try:
        ix = rd("data/index.json", now); got = {r[9:13] for r in ix.get("runs", {}).get(yday, [])}
        want = [f"{h:02d}{m:02d}" for h in range(DAWN_START if yday >= DAWN_FROM else 7, 17) for m in (0, 30)]; miss = [w for w in want if w not in got]
        lines.append(f"{'⚠' if miss else '·'} 어제 초단기 {len(want) - len(miss)}/{len(want)}" + (f" ({', '.join(m[:2] + ':' + m[2:] for m in miss[:6])}{' …' if len(miss) > 6 else ''} 빠짐)" if miss else ""))
    except Exception as e:
        lines.append(f"⚠ 초단기 확인 못 함({type(e).__name__})")
    try:
        s = rd("data/status.json", now); r = s.get("res24") or {}; j = r.get("job_sec") or {}
        lines.append(f"· 디스크 {s.get('disk_used_gb', '?')} / {s.get('disk_total_gb', '?')} GB 사용 · 오늘 기상청 API 사용량 {s.get('kma_today_gb')} GB")   # 대략적인 서버 상태(2026-10-31 연구자)
        if r.get("cpu_avg") is not None:
            lines.append(f"· CPU(24시간) 평균 {r['cpu_avg']} % · 95 % 지점 {r.get('cpu_p95')} % · 바쁨(90 % 넘음) {r.get('cpu_busy_min')}분")
        if r.get("mem_max_gb") is not None:
            lines.append(f"· 메모리 최대 {r['mem_max_gb']} / {r.get('mem_total_gb', 12)} GB")
        if any(v for v in j.values()):
            lines.append("· 계산 시간(중앙): " + " · ".join(f"{nm} {j[k]}초" for k, nm in (("b5", "B5"), ("w", "W"), ("l1", "밤 단기")) if j.get(k)))
        lines.append(f"{'⚠' if s.get('errors') else '·'} 최근 실패 {len(s.get('errors') or [])}건")
        jl = jobs_line(now)
        if jl:
            lines.append(jl)
        w = s.get("wind") or {}; wt, wa = wind_age(now)
        lines.append(f"{'⚠' if wa > WIND_LIM or w.get('stale') else '·'} 풍력 바람: 마지막 수집 {wt:%H:%M}" if wt else "⚠ 풍력 바람: 자료를 못 읽음")
        if wt:
            lines[-1] += (f" · 오늘 API {w['calls_today']}회" if w.get("calls_today") else "") + (f" · 지연 {', '.join(w['stale'])}" if w.get("stale") else "")
    except Exception:
        pass
    bad = any(x.startswith("⚠") for x in lines)
    return f"☀ 아침 점검 · {now:%m-%d %H:%M} KST\n모니터링 결과 : {'확인 필요' if bad else '문제 없음'}\n" + "\n".join(lines) + f"\n· {why}"


def main():
    now = dt.datetime.now(KST)
    if os.environ.get("TEST") == "1":                         # Actions 에서 손으로 돌릴 때 '시험 알림 보내기'를 고르면 폰으로 한 번 보냄(토큰 · 번호 확인용)
        L, bad = cause(now); send(f"🔔 시험 알림 · {now:%m-%d %H:%M} KST\n모니터링 결과 : {'확인 필요' if bad else '문제 없음'}\n" + "\n".join(L)); return
    st = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {"alert": False}
    try:
        t, kind, _ = latest(now); age = (now - t).total_seconds() / 60 if t else 1e9; why = f"마지막 결과 {kind} {t:%m-%d %H:%M} 발표 ({age:.0f}분 전)" if t else "결과 없음"
    except Exception as e:
        age, why = 1e9, f"사이트를 읽지 못함({type(e).__name__})"
    lim = limit(now); stale = lim is not None and age > lim
    print(f"{now:%m-%d %H:%M} KST · {why} · 기준 {lim}분 · 멈춤 {stale}")
    if stale and not st.get("alert"):
        send(f"⚠ 태양광 예측이 멈춤 · {now:%m-%d %H:%M} KST\n모니터링 결과 : 확인 필요\n· {why}\n" + "\n".join(cause(now)[0]) + f"\n{SITE}"); st = {"alert": True, "since": now.strftime("%Y-%m-%d %H:%M"), **{k: v for k, v in st.items() if k in KEEP}}
    elif st.get("alert") and age <= (lim or 110):
        send(f"✅ 예측 복구 · {now:%m-%d %H:%M} KST\n모니터링 결과 : 문제 없음\n· {why} (멈춤 알림 {st.get('since', '')})"); st = {"alert": False, **{k: v for k, v in st.items() if k in KEEP}}
    wt, wa = wind_age(now); wwhy = f"풍력 바람 마지막 수집 {wt:%m-%d %H:%M} ({wa:.0f}분 전)" if wt else "풍력 바람 자료를 못 읽음"
    if wa > WIND_LIM and not st.get("walert"):                 # 풍력은 밤낮 없이 20분마다라 시간대 구분 없이 봄
        send(f"⚠ 풍력 바람 자료 멈춤 · {now:%m-%d %H:%M} KST\n모니터링 결과 : 확인 필요\n· {wwhy}\n{SITE}wind.html"); st["walert"] = True; st["wsince"] = now.strftime("%Y-%m-%d %H:%M")
    elif st.get("walert") and wa <= WIND_LIM:
        send(f"✅ 풍력 바람 자료 복구 · {now:%m-%d %H:%M} KST\n모니터링 결과 : 문제 없음\n· {wwhy} (멈춤 알림 {st.get('wsince', '')})"); st["walert"] = False
    try:
        jobs(now, st)
    except Exception as e:
        print("자동 작업 확인 실패:", e)
    try:
        notice(now, st)
    except Exception as e:
        print("공지 알림 실패:", e)
    if now.hour * 60 + now.minute >= HB[0] * 60 + HB[1] and st.get("hb") != now.strftime("%Y-%m-%d"):   # 하루 한 번 '정상' 요약(감시가 살아 있다는 확인도 겸함)
        try:
            send(heartbeat(now, why)); st["hb"] = now.strftime("%Y-%m-%d")
        except Exception as e:
            print("하루 요약 실패:", e)
    st["day"] = now.strftime("%Y-%m-%d")                      # 하루 한 번 상태 파일이 바뀌어 커밋됨(GitHub 은 60일 동안 저장소 활동이 없으면 정기 작업을 멈추므로)
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)


if __name__ == "__main__":
    main()
