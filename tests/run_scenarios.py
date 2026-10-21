"""부록의 연습용 시나리오 3개를 실제 PostgreSQL에서 돌려 결과 안내의 첫 단어를 확인한다.

실행: TEST_DATABASE_URL=postgresql://... python tests/run_scenarios.py
(Supabase가 아니라 로컬/테스트용 DB를 쓰세요. tf_ 테이블을 지우고 다시 만듭니다.)
"""
from __future__ import annotations

import os
import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ai  # noqa: E402
import services  # noqa: E402
from db import init_schema, make_pool  # noqa: E402
from rules import validate_final  # noqa: E402

URL = os.environ["TEST_DATABASE_URL"]
DATA = ROOT / "tests" / "data"
FAILS: list[str] = []
# 시험 때마다 새로 만드는 값(실제 비밀번호나 키가 아님)
TEST_ADMIN_PW = secrets.token_urlsafe(12)


def reset_db(pool) -> None:
    with pool.connection() as conn:
        conn.execute(
            "drop table if exists tf_votes, tf_applications, tf_members, tf_teams, "
            "tf_student_sub_roles, tf_students, tf_settings, tf_ai_logs cascade"
        )
    init_schema(pool)
    services.ensure_admin(pool, TEST_ADMIN_PW)


class Runner:
    def __init__(self, pool, csv_name: str, teams: int, base: int):
        self.pool, self.base = pool, base
        msg = services.reset_roster(pool, (DATA / csv_name).read_bytes(), teams)
        self.check(f"명단 올리기 {csv_name}", msg, "승인")
        with pool.connection() as conn:
            st = services.load_state(conn)
        self.ids = {int(s.no) - base + 1: s.id for s in st.students.values()}

    def no(self, n: int) -> str:
        return str(self.base + n - 1)

    def check(self, label: str, msg: str, want: str) -> None:
        ok = msg.split(":", 1)[0] == want
        print(f"  {'OK ' if ok else 'XX '} {label:<34} 기대 {want:<3} → {msg}")
        if not ok:
            FAILS.append(f"{label}: 기대 {want}, 실제 {msg}")

    def apply(self, n: int, team: int, want: str) -> None:
        self.check(f"학생{n} → 팀{team} 지원", services.apply(self.pool, self.ids[n], team), want)

    def vote(self, voter: int, applicant: int, yes: bool, want: str) -> None:
        msg = services.vote(self.pool, self.ids[voter], self.no(applicant), yes)
        self.check(f"학생{voter}이 학생{applicant}에 {'찬성' if yes else '거부'}", msg, want)

    def state(self):
        with self.pool.connection() as conn:
            return services.load_state(conn)

    def close(self, want_unassigned: set[int] | None = None) -> None:
        self.check("관리자 마감", services.close_recruiting(self.pool), "승인")
        if want_unassigned is not None:
            got = {int(s.no) - self.base + 1 for s in self.state().unassigned()}
            ok = got == want_unassigned
            print(f"  {'OK ' if ok else 'XX '} 팀이 없는 학생 {sorted(got)} (기대 {sorted(want_unassigned)})")
            if not ok:
                FAILS.append(f"unassigned {got} != {want_unassigned}")

    def assign_and_confirm(self) -> None:
        msg = ai.start_assignment(self.pool)
        self.check("관리자 AI 배정", msg, "승인")
        t0 = time.monotonic()
        while ai.JOB.snapshot()["running"] and time.monotonic() - t0 < 30:
            time.sleep(0.1)
        st = self.state()
        print(f"     배정 결과: {st.assign_status['message']} / 출처 {st.draft and st.draft['source']}")
        for e in st.assign_status.get("events", []):
            print(f"       - {e}")
        self.check("관리자 배정안 확정", services.confirm_draft(self.pool), "승인")
        st = self.state()
        errs = validate_final(st.teams)
        ok = not errs and not st.unassigned()
        print(f"  {'OK ' if ok else 'XX '} 확정 뒤 규칙 검사: {errs or '모두 지킴'}")
        for t, ms in st.teams.items():
            print(f"       팀{t}: " + ", ".join(m.name for m in ms))
        if not ok:
            FAILS.append(f"final rules: {errs}")


def easy(pool) -> None:
    print("\n[부록 1. 쉬움]")
    r = Runner(pool, "쉬움.csv", 3, 2026101)
    r.apply(1, 1, "승인"); r.apply(2, 2, "승인"); r.apply(3, 3, "승인")
    r.apply(4, 1, "대기"); r.vote(1, 4, True, "승인")
    r.apply(5, 2, "대기"); r.vote(2, 5, True, "승인")
    r.apply(7, 1, "대기"); r.vote(1, 7, True, "대기"); r.vote(4, 7, False, "거절")
    r.apply(6, 3, "대기"); r.vote(3, 6, True, "승인")
    r.close({7, 8})
    r.assign_and_confirm()


def medium(pool) -> None:
    print("\n[부록 2. 중간]")
    r = Runner(pool, "중간.csv", 4, 2026201)
    r.apply(1, 1, "승인")
    r.apply(5, 1, "대기"); r.vote(1, 5, True, "승인")
    r.apply(2, 1, "막힘")  # 주 역할 중복 → 뒤의 투표는 건너뜀
    r.apply(6, 2, "승인")
    r.apply(9, 2, "대기"); r.vote(6, 9, True, "승인")
    r.apply(10, 2, "대기"); r.vote(6, 10, True, "대기"); r.vote(9, 10, False, "거절")
    r.apply(3, 3, "승인"); r.apply(11, 4, "승인")
    r.apply(12, 4, "대기"); r.apply(12, 3, "막힘")
    r.close({2, 3, 4, 7, 8, 10, 11, 12})
    last = r.state().last_app_of_student(r.ids[12])
    r.check("학생12의 팀4 지원(마감 뒤)", f"{last.status}: {last.reason}", "거절")
    r.apply(2, 3, "막힘")
    r.assign_and_confirm()


def hard(pool) -> None:
    print("\n[부록 3. 어려움]")
    r = Runner(pool, "어려움.csv", 4, 2026301)
    r.apply(1, 1, "승인")
    r.apply(4, 1, "대기"); r.vote(1, 4, True, "승인")
    r.apply(7, 1, "대기"); r.vote(1, 7, True, "대기"); r.vote(4, 7, True, "승인")
    r.apply(2, 2, "승인")
    r.apply(8, 2, "대기"); r.vote(2, 8, True, "승인")
    r.apply(5, 2, "대기"); r.apply(6, 2, "대기")
    r.vote(2, 5, True, "대기"); r.vote(8, 5, True, "승인")
    r.vote(2, 6, True, "대기"); r.vote(8, 6, True, "대기"); r.vote(5, 6, True, "거절")  # 승인 직전 검사
    r.apply(11, 3, "승인")
    r.apply(15, 3, "대기"); r.vote(11, 15, True, "승인")
    r.apply(14, 3, "막힘")  # 필수 포함 역할 자리
    r.apply(10, 4, "승인")
    r.apply(13, 1, "대기"); r.vote(1, 13, True, "대기")
    r.close({3, 6, 9, 10, 12, 13, 14, 16})
    last = r.state().last_app_of_student(r.ids[13])
    r.check("학생13의 팀1 지원(마감 뒤)", f"{last.status}: {last.reason}", "거절")
    r.assign_and_confirm()


def extra(pool) -> None:
    print("\n[추가 확인]")
    r = Runner(pool, "쉬움.csv", 3, 2026101)
    r.check("마감 전 AI 배정", ai.start_assignment(pool), "막힘")
    r.check("마감 전 확정", services.confirm_draft(pool), "막힘")
    r.apply(1, 1, "승인"); r.apply(1, 2, "막힘")        # 이미 팀이 있음
    r.apply(4, 1, "대기"); r.apply(4, 2, "막힘")        # 결과를 기다리는 지원
    r.vote(1, 4, True, "승인"); r.vote(1, 4, True, "승인")  # 이미 승인된 지원에 다시 투표
    r.apply(7, 1, "대기"); r.vote(1, 7, False, "거절"); r.vote(4, 7, True, "거절")
    r.apply(8, 1, "대기"); r.apply(2, 1, "막힘")        # 학생2(PL)는 학생1(PL)과 중복
    r.vote(1, 8, True, "대기"); r.vote(1, 8, True, "대기")  # 투표는 바꿀 수 없음
    # 필수 포함 역할 자리: 테스터+발표자 팀(2명)에 보고서 작성자 지원 → 막힘(공고 3장 예)
    from rules import Student, check_join
    tt, pt, rw = Student(1, "a", "a", "테스터"), Student(2, "b", "b", "발표자"), Student(3, "c", "c", "보고서 작성자")
    both = Student(4, "d", "d", "기획자", ("웹 프로그래머",))
    r.check("TT+PT 팀에 RW (규칙 함수)", "막힘: " + str(check_join([tt, pt], rw)), "막힘")
    # 팀 {PL;WP, TT, PT}에 RW가 들어가면 자리 0, 필수 역할 1개 못 채움 → 막힘
    # 팀 {PL;WP}에 RW가 들어가면 자리 2, 필수 역할 1개 못 채움 → 허용
    ok_r = (check_join([tt, pt], rw) and check_join([both, tt, pt], rw) and not check_join([both], rw)
            and not check_join([both, tt], rw))
    print(f"  {'OK ' if ok_r else 'XX '} PL;WP 한 학생은 필수 역할 1개로만 셈")
    if not ok_r:
        FAILS.append("required slot rule")
    # 지원 막힘: 잘못된 비밀번호
    ok = services.login(pool, r.no(1), "0000") is None and services.login(pool, r.no(1), "1234") is not None
    print(f"  {'OK ' if ok else 'XX '} 비밀번호가 틀리면 로그인 안 됨, 1234면 됨")
    adm = services.login(pool, "admin", TEST_ADMIN_PW)
    ok2 = adm is not None and adm["is_admin"] and services.login(pool, "admin", "1234") is None
    print(f"  {'OK ' if ok2 else 'XX '} admin 로그인은 ADMIN_PASSWORD로만")
    with pool.connection() as conn:
        h = conn.execute("select password_hash from tf_students where student_no=%s", (r.no(1),)).fetchone()[0]
    ok3 = h.startswith("$2") and "1234" not in h
    print(f"  {'OK ' if ok3 else 'XX '} 비밀번호는 bcrypt 해시로 저장됨")
    if not (ok and ok2 and ok3):
        FAILS.append("login/hash")

    # 마지막 자리 동시 입력: DB 유일성 제약이 막는지
    from psycopg import errors
    with pool.connection() as conn:
        try:
            with conn.transaction():
                conn.execute("insert into tf_members(team_id, slot_no, student_id) "
                             "select 1, 1, id from tf_students where student_no=%s", (r.no(3),))
            blocked = False
        except errors.UniqueViolation:
            blocked = True
    print(f"  {'OK ' if blocked else 'XX '} 같은 자리(team_id, slot_no)에 두 번째 학생은 DB가 막음")
    if not blocked:
        FAILS.append("unique slot")

    # 규칙을 지키는 배정이 없음: 쉬움 명단, 팀 4개(8명)인데 WP 3명뿐
    print("  -- 규칙을 지키는 배정이 없는 경우")
    r = Runner(pool, "쉬움.csv", 4, 2026101)
    r.close()
    r.check("관리자 AI 배정", ai.start_assignment(pool), "막힘")
    print("     ", r.state().assign_status["message"])


def ai_paths(pool) -> None:
    """AI 경로: 1차 시간 초과 → 2차 검사 실패 → 3차 성공. 실제 호출 대신 가짜 함수를 씀."""
    print("\n[AI 실패 처리(가짜 모델)]")
    r = Runner(pool, "쉬움.csv", 3, 2026101)
    for a, b in ((1, 1), (2, 2), (3, 3)):
        r.apply(a, b, "승인")
    r.apply(4, 1, "대기"); r.vote(1, 4, True, "승인")
    r.apply(5, 2, "대기"); r.vote(2, 5, True, "승인")
    r.apply(6, 3, "대기"); r.vote(3, 6, True, "승인")
    r.close({7, 8})

    real = ai.call_model
    calls = []

    def fake(model, messages, api_key, on_tick=None, timeout=ai.MODEL_TIMEOUT_SEC):
        calls.append(model)
        if len(calls) == 1:  # 1차: 120초 제한에 걸림(실제 제한은 timeout_check에서 따로 확인)
            raise ai.ModelFailure("120초 안에 응답을 다 받지 못해 끊음")
        if len(calls) == 2:  # 2차: 학생8을 빠뜨리고, 학생7(FD)을 학생4(보조 FD)가 아닌 주 역할 기준으로는 괜찮은 팀1에 넣음
            return '{"assignments":[{"student_no":"2026107","team":1}]}'
        return '```json\n{"assignments":[{"student_no":"2026107","team":2},{"student_no":"2026108","team":"팀3"}]}\n```'

    os.environ["OPENROUTER_API_KEY"] = secrets.token_hex(8)  # 가짜 모델만 부르므로 아무 값
    ai.call_model = fake
    try:
        r.assign_and_confirm()
    finally:
        ai.call_model = real
        os.environ.pop("OPENROUTER_API_KEY", None)
    print("     모델 호출 순서:", calls)


def timeout_check() -> None:
    """call_model의 전체 대기 시간 제한이 실제로 끊는지(느린 가짜 서버)."""
    import http.server
    import threading

    class Slow(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                for _ in range(100):  # 1초마다 keep-alive만 보내 응답을 끝내지 않음
                    self.wfile.write(b": OPENROUTER PROCESSING\n\n")
                    self.wfile.flush()
                    time.sleep(1)
            except Exception:
                pass

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    old = ai.OPENROUTER_URL
    ai.OPENROUTER_URL = f"http://127.0.0.1:{srv.server_address[1]}/"
    t0 = time.monotonic()
    try:
        ai.call_model("x", [], "k", timeout=3)
        msg = "끊지 않음"
    except ai.ModelFailure as e:
        msg = str(e)
    dt = time.monotonic() - t0
    ai.OPENROUTER_URL = old
    srv.shutdown()
    ok = 2.9 <= dt <= 4.0
    print(f"\n[전체 대기 시간 제한] keep-alive만 오는 응답을 {dt:.1f}초에 끊음 → {msg}  {'OK' if ok else 'XX'}")
    if not ok:
        FAILS.append("timeout")


if __name__ == "__main__":
    os.environ.pop("OPENROUTER_API_KEY", None)  # 시나리오는 키 없이 → 3차까지 실패 → 앱 자체 배정
    pool = make_pool(URL)
    reset_db(pool)
    easy(pool); medium(pool); hard(pool); extra(pool); ai_paths(pool)
    timeout_check()
    print("\n" + ("모두 통과" if not FAILS else f"실패 {len(FAILS)}건:\n- " + "\n- ".join(FAILS)))
    sys.exit(1 if FAILS else 0)
