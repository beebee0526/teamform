"""마감 뒤 남은 학생의 배정(공고 2.5절).

흐름
1. 앱이 먼저 모든 경우를 따져(rules.solve) 규칙을 지키는 배정이 있는지 본다.
   없으면 AI를 부르지 않고 '규칙을 지키는 배정이 없음'과 이유를 보여 준다(2.5절 8항).
2. 있으면 오픈라우터의 1차 → 2차 → 3차 모델 순으로 배정안을 받는다.
   모델마다 부른 때부터 응답을 다 받을 때까지 120초 전체 대기 시간을 재서 넘으면 끊는다.
   응답 없음, 빈 응답, 오류, 검사 실패는 모두 실패로 보고 다음 모델로 넘어간다.
3. 3차 모델까지 실패하면 1에서 찾은 앱 자체 배정(백트래킹)을 쓴다.

배정 작업은 Streamlit 화면 실행과 떨어진 배경 스레드에서 돌린다.
그래서 화면이 다시 그려져도 작업이 끊기지 않고, 화면은 JOB의 진행 상황을 읽어 보여 준다.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field

import requests

from db import get_secret
from rules import MAX_SIZE, MIN_SIZE, PL, ROLES, WP, Student, explain_infeasible, solve, validate_plan
import services

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL_TIMEOUT_SEC = 120.0  # 모델 하나의 전체 대기 시간(공고 2.5절 5항)

# 1차, 2차, 3차 모델. Secrets/.env의 AI_MODELS(쉼표로 구분)로 바꿀 수 있다.
# openrouter/free(무료 모델 자동 연결 라우터)는 쓰지 않는다(공고 4.3절).
DEFAULT_MODELS = (
    "nvidia/nemotron-3-super-120b-a12b:free",
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3.5-lightning:free",
)


def get_models() -> list[str]:
    raw = get_secret("AI_MODELS")
    models = [m.strip() for m in raw.split(",") if m.strip()] if raw else list(DEFAULT_MODELS)
    models = [m for m in models if m != "openrouter/free"]
    return (models + [d for d in DEFAULT_MODELS if d not in models])[:3]


# ---------------------------------------------------------------- 프롬프트

def _student_json(s: Student) -> dict:
    return {"student_no": s.no, "name": s.name, "main_role": s.main, "sub_roles": list(s.subs)}


def build_prompt(teams: dict[int, list[Student]], pool: list[Student]) -> list[dict]:
    data = {
        "teams": [
            {"team": t, "members": [_student_json(m) for m in teams[t]]} for t in sorted(teams)
        ],
        "unassigned_students": [_student_json(s) for s in pool],
    }
    rules = f"""역할은 6개: {", ".join(ROLES)}.
규칙(모두 지켜야 함):
1. unassigned_students의 학생만 팀에 넣는다. 이미 팀에 있는 학생은 옮기지 않는다.
2. unassigned_students의 학생은 빠짐없이, 한 학생은 정확히 한 팀에만 넣는다.
3. 배정이 끝난 뒤 모든 팀의 인원은 {MIN_SIZE}~{MAX_SIZE}명이다(빈 팀 없음).
4. 한 팀 안에서 main_role이 같은 학생이 2명 이상이면 안 된다(sub_roles는 이 규칙과 상관없음).
5. 모든 팀에 '{PL}'를 맡을 학생과 '{WP}'를 맡을 학생이 서로 다른 사람으로 1명씩 있어야 한다.
   main_role 또는 sub_roles에 그 역할이 있으면 맡을 수 있다. 한 학생이 둘 다 맡을 수는 없다."""
    fmt = (
        '답은 JSON 하나만 쓴다. 설명, 코드 블록 표시는 쓰지 않는다. 형식:\n'
        '{"assignments": [{"student_no": "학번", "team": 팀번호}, ...]}'
    )
    return [
        {"role": "system", "content": "너는 팀 편성 규칙을 정확히 지키는 배정 도우미다. " + fmt},
        {
            "role": "user",
            "content": f"{rules}\n\n지금 상태:\n{json.dumps(data, ensure_ascii=False, indent=1)}\n\n{fmt}",
        },
    ]


def parse_assignment(text: str) -> list[tuple[str, int]]:
    """AI 응답에서 (학번, 팀 번호) 목록을 꺼낸다. 형식이 틀리면 ValueError."""
    if not text or not text.strip():
        raise ValueError("빈 응답")
    s = re.sub(r"```(?:json)?", "", text)
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("응답에 JSON이 없음")
    try:
        obj = json.loads(s[start:end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON을 읽을 수 없음({e.msg})") from None
    items = obj.get("assignments") if isinstance(obj, dict) else None
    if not isinstance(items, list):
        raise ValueError("assignments 목록이 없음")
    pairs: list[tuple[str, int]] = []
    for it in items:
        if not isinstance(it, dict) or "student_no" not in it or "team" not in it:
            raise ValueError("assignments 항목의 형식이 틀림")
        team = it["team"]
        if isinstance(team, str):
            m = re.search(r"\d+", team)
            if not m:
                raise ValueError(f"팀 번호를 읽을 수 없음({team})")
            team = int(m.group())
        pairs.append((str(it["student_no"]).strip(), int(team)))
    return pairs


# ---------------------------------------------------------------- 오픈라우터 호출

class ModelFailure(Exception):
    pass


def _stream_completion(model: str, messages: list[dict], api_key: str, cancel: threading.Event,
                       out: dict) -> None:
    """스트리밍으로 응답을 받는다. 줄을 받을 때마다 cancel을 확인해 120초가 지나면 끊는다."""
    body = {
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": 0.2,
        "max_tokens": 8000,  # 추론 모델이 생각에 한도를 다 쓰지 않게 넉넉히
        "reasoning": {"effort": "low"},  # 빈 응답을 줄임(공고 4.3절)
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Title": "Team Formation App",
    }
    try:
        with requests.post(OPENROUTER_URL, headers=headers, json=body, stream=True,
                           timeout=(10, MODEL_TIMEOUT_SEC)) as resp:
            out["resp"] = resp
            if resp.status_code != 200:
                msg = resp.text[:300] if not cancel.is_set() else ""
                out["error"] = f"HTTP {resp.status_code} {_error_message(msg)}".strip()
                return
            parts: list[str] = []
            for raw in resp.iter_lines(decode_unicode=False):
                if cancel.is_set():
                    out["error"] = "취소됨"
                    return
                if not raw:
                    continue
                line = raw.decode("utf-8", "replace")
                if line.startswith(":"):
                    continue  # ": OPENROUTER PROCESSING" 같은 keep-alive
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if chunk.get("error"):
                    out["error"] = "응답 중 오류: " + _error_message(json.dumps(chunk["error"]))
                    return
                for ch in chunk.get("choices") or []:
                    delta = ch.get("delta") or {}
                    if delta.get("content"):
                        parts.append(delta["content"])
            out["text"] = "".join(parts)
    except requests.exceptions.Timeout:
        out["error"] = "응답 없음(연결 또는 읽기 시간 초과)"
    except Exception as e:  # 연결 끊김 등
        if not cancel.is_set():
            out["error"] = f"요청 실패({type(e).__name__})"


def _error_message(text: str) -> str:
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            err = obj.get("error", obj)
            if isinstance(err, dict):
                return str(err.get("message") or err.get("code") or "")[:200]
    except Exception:
        pass
    return text[:200]


def _quiet_close(resp) -> None:
    try:
        resp.close()
    except Exception:
        pass


def call_model(model: str, messages: list[dict], api_key: str,
               on_tick=None, timeout: float = MODEL_TIMEOUT_SEC) -> str:
    """모델 하나를 불러 응답 글자를 돌려준다. 전체 대기 시간이 timeout을 넘으면 ModelFailure."""
    cancel = threading.Event()
    out: dict = {}
    worker = threading.Thread(target=_stream_completion, args=(model, messages, api_key, cancel, out),
                              daemon=True)
    start = time.monotonic()
    worker.start()
    while worker.is_alive():
        elapsed = time.monotonic() - start
        if elapsed >= timeout:
            cancel.set()
            resp = out.get("resp")
            if resp is not None:
                # 연결 닫기는 읽고 있는 스레드와 잠금을 다투어 오래 걸릴 수 있으므로 따로 돌리고,
                # 여기서는 기다리지 않고 바로 다음 모델로 넘어간다
                threading.Thread(target=_quiet_close, args=(resp,), daemon=True).start()
            raise ModelFailure(f"{int(timeout)}초 안에 응답을 다 받지 못해 끊음")
        if on_tick:
            on_tick(elapsed)
        worker.join(timeout=min(0.5, timeout - elapsed))
    if out.get("error"):
        raise ModelFailure(out["error"])
    text = out.get("text", "")
    if not text.strip():
        raise ModelFailure("빈 응답")
    return text


# ---------------------------------------------------------------- 배정 작업

@dataclass
class AssignJob:
    """배경 스레드에서 도는 배정 작업 하나의 진행 상황(화면이 읽음)."""
    lock: threading.Lock = field(default_factory=threading.Lock)
    running: bool = False
    round: int = -1
    attempt: int = 0
    model: str = ""
    attempt_started: float = 0.0
    events: list[str] = field(default_factory=list)
    thread: threading.Thread | None = None

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "running": self.running,
                "round": self.round,
                "attempt": self.attempt,
                "model": self.model,
                "elapsed": (time.monotonic() - self.attempt_started) if self.running and self.attempt else 0.0,
                "events": list(self.events),
            }


JOB = AssignJob()


def start_assignment(pool) -> str:
    """관리자가 'AI 배정'을 눌렀을 때. 결과 안내(승인/막힘)를 돌려준다."""
    with pool.connection() as conn:
        st = services.load_state(conn)
    if st.phase == "confirmed":
        return "막힘: 이미 배정을 확정함"
    if st.phase != "closed":
        return "막힘: 모집을 먼저 마감해야 함"
    with JOB.lock:
        if JOB.running:
            return "막힘: AI 배정이 이미 진행 중임"

    teams, pool_students, round_no = st.teams, st.unassigned(), st.round
    solution = solve(teams, pool_students)
    if solution is None:
        reason = explain_infeasible(teams, pool_students)
        services.save_assign_status(pool, round_no, {
            "kind": "none",
            "message": f"규칙을 지키는 배정이 없음: {reason}",
            "events": [],
        })
        return f"막힘: 규칙을 지키는 배정이 없음 — {reason}"

    if not pool_students:
        draft = {"round": round_no, "source": "배정할 학생 없음", "pairs": []}
        services.save_draft(pool, round_no, draft, {
            "kind": "ok", "message": "팀이 없는 학생이 없음. 지금 구성이 규칙을 지키므로 그대로 확정할 수 있음",
            "events": [],
        })
        return "승인: 배정할 학생이 없음(지금 구성을 그대로 확정할 수 있음)"

    api_key = get_secret("OPENROUTER_API_KEY")
    models = get_models()
    # 진행 표시를 스레드 시작 전에 저장한다(스레드가 먼저 끝나 배정안을 저장한 뒤 덮어쓰지 않게)
    services.save_assign_status(pool, round_no, {"kind": "running", "message": "AI 배정 진행 중", "events": []})
    with JOB.lock:
        JOB.running = True
        JOB.round = round_no
        JOB.attempt = 0
        JOB.model = ""
        JOB.events = []
        JOB.thread = threading.Thread(
            target=_run_job, args=(pool, round_no, teams, pool_students, solution, models, api_key),
            daemon=True, name="tf-assign",
        )
        JOB.thread.start()
    return "승인: AI 배정을 시작함"


def _event(msg: str) -> None:
    with JOB.lock:
        JOB.events.append(msg)


def _run_job(pool, round_no, teams, pool_students, solution, models, api_key) -> None:
    labels = ("1차", "2차", "3차")
    try:
        messages = build_prompt(teams, pool_students)
        for i, model in enumerate(models[:3]):
            label = labels[i]
            with JOB.lock:
                JOB.attempt = i + 1
                JOB.model = model
                JOB.attempt_started = time.monotonic()
            t0 = time.monotonic()
            try:
                if not api_key:
                    raise ModelFailure("OPENROUTER_API_KEY 없음")
                text = call_model(model, messages, api_key)
                pairs = parse_assignment(text)
                errs, _ = validate_plan(teams, pool_students, pairs)
                if errs:
                    raise ModelFailure("배정안 검사 실패: " + "; ".join(errs[:4]))
            except (ModelFailure, ValueError) as e:
                elapsed = time.monotonic() - t0
                _event(f"{label} 모델({model}) 실패 — {e} ({elapsed:.0f}초)")
                services.log_ai(pool, round_no, i + 1, model, "실패", elapsed, str(e))
                continue
            elapsed = time.monotonic() - t0
            _event(f"{label} 모델({model})의 배정안이 검사 다섯 가지를 모두 통과함 ({elapsed:.0f}초)")
            services.log_ai(pool, round_no, i + 1, model, "성공", elapsed, json.dumps(pairs, ensure_ascii=False))
            _finish(pool, round_no, pairs, f"{label} 모델({model})", f"{label} 모델의 배정안이 검사를 통과함")
            return

        # 3차 모델까지 실패 → 앱이 모든 경우를 따져 찾은 배정(3장 어려운 해결)
        pairs = []
        for t, ms in solution.items():
            orig = {m.no for m in teams[t]}
            pairs.extend((m.no, t) for m in ms if m.no not in orig)
        _event("3차 모델까지 실패하여 앱이 스스로 배정함(모든 경우를 따져 찾은 배정)")
        services.log_ai(pool, round_no, 0, "앱 자체 배정", "성공", 0.0, json.dumps(pairs, ensure_ascii=False))
        _finish(pool, round_no, pairs, "앱 자체 배정(백트래킹)", "3차 모델까지 실패하여 앱이 스스로 배정함")
    except Exception as e:  # 예상하지 못한 오류도 화면에 남긴다
        _event(f"배정 작업 오류: {type(e).__name__}")
        services.save_assign_status(pool, round_no, {
            "kind": "error", "message": "배정 작업 중 오류가 남. AI 배정을 다시 눌러 주세요",
            "events": JOB.snapshot()["events"],
        })
    finally:
        with JOB.lock:
            JOB.running = False


def _finish(pool, round_no, pairs, source, message) -> None:
    events = JOB.snapshot()["events"]
    draft = {"round": round_no, "source": source, "pairs": [[no, t] for no, t in pairs]}
    services.save_draft(pool, round_no, draft, {"kind": "ok", "message": message, "events": events})
