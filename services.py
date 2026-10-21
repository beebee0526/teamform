"""앱의 동작(로그인, 명단 올리기, 지원, 투표, 마감, 배정 확정)을 DB 트랜잭션으로 처리한다.

바꾸는 동작은 모두 하나의 트랜잭션 안에서 전역 advisory lock을 잡고 한다.
그래서 두 학생이 같은 순간 단추를 눌러도 차례로 처리된다. 그와 별도로
tf_members의 유일성 제약이 '먼저 도착한 학생만 기록'을 DB에서 한 번 더 지킨다.

반환하는 안내는 모두 '승인', '대기', '거절', '막힘' 가운데 하나로 시작한다(공고 2.7절).
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from datetime import datetime

import bcrypt
from psycopg import errors as pg_errors
from psycopg_pool import ConnectionPool

from rules import MAX_SIZE, ROLES, Student, check_join, validate_plan

LOCK_KEY = 20261016  # pg_advisory_xact_lock 키(이 앱의 쓰기 동작을 차례로 처리)
BCRYPT_ROUNDS = 10
STUDENT_PASSWORD = "1234"  # 공고 2.2절: 가상 학생의 비밀번호는 모두 1234


# ---------------------------------------------------------------- 비밀번호 해시

def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("ascii")


def check_password(pw: str, hashed: str | None) -> bool:
    if not pw or not hashed:
        return False
    try:
        return bcrypt.checkpw(pw.encode("utf-8"), hashed.encode("ascii"))
    except ValueError:
        return False


# ---------------------------------------------------------------- 상태 읽기

@dataclass
class App:
    id: int
    team_id: int
    student_id: int
    status: str
    reason: str | None
    created_at: datetime | None


@dataclass
class State:
    phase: str = "open"  # open / closed / confirmed
    round: int = 0
    students: dict[int, Student] = field(default_factory=dict)  # 관리자 제외
    teams: dict[int, list[Student]] = field(default_factory=dict)  # 팀 번호 → 팀원(자리 순)
    team_names: dict[int, str] = field(default_factory=dict)
    member_of: dict[int, int] = field(default_factory=dict)  # 학생 id → 팀 번호
    apps: list[App] = field(default_factory=list)  # 지원(오래된 순)
    votes: dict[int, dict[int, str]] = field(default_factory=dict)  # 지원 id → {투표자 id: 찬성/거부}
    draft: dict | None = None
    assign_status: dict | None = None

    @property
    def is_open(self) -> bool:
        return self.phase == "open"

    def unassigned(self) -> list[Student]:
        return [s for s in sorted(self.students.values(), key=lambda s: s.no) if s.id not in self.member_of]

    def pending_of_student(self, sid: int) -> App | None:
        return next((a for a in self.apps if a.student_id == sid and a.status == "대기"), None)

    def last_app_of_student(self, sid: int) -> App | None:
        mine = [a for a in self.apps if a.student_id == sid]
        return mine[-1] if mine else None

    def pending_for_team(self, team_no: int) -> list[App]:
        return [a for a in self.apps if a.team_id == team_no and a.status == "대기"]


# 화면을 그릴 때마다 부르므로, 7개 테이블을 질의 한 번(왕복 한 번)으로 읽는다.
# 배포 서버(미국)와 DB(서울) 사이 왕복이 길어서 질의를 여러 번 나누면 화면이 느려지기 때문이다.
STATE_SQL = """
select json_build_object(
  'settings', (select coalesce(json_agg(json_build_array(key, value)), '[]') from tf_settings),
  'subs', (select coalesce(json_agg(json_build_array(student_id, role) order by student_id, role), '[]')
           from tf_student_sub_roles),
  'students', (select coalesce(json_agg(json_build_array(id, student_no, name, main_role) order by student_no), '[]')
               from tf_students where not is_admin),
  'teams', (select coalesce(json_agg(json_build_array(id, name) order by id), '[]') from tf_teams),
  'members', (select coalesce(json_agg(json_build_array(team_id, student_id) order by team_id, slot_no), '[]')
              from tf_members),
  'apps', (select coalesce(json_agg(json_build_array(id, team_id, student_id, status, reason, created_at)
                                    order by id), '[]') from tf_applications),
  'votes', (select coalesce(json_agg(json_build_array(application_id, voter_id, vote)), '[]') from tf_votes)
)
"""


def load_state(conn) -> State:
    data = conn.execute(STATE_SQL).fetchone()[0]
    if isinstance(data, str):
        data = json.loads(data)
    st = State()
    for key, value in data["settings"]:
        if key == "phase":
            st.phase = value or "open"
        elif key == "round":
            st.round = int(value or 0)
        elif key == "draft" and value:
            st.draft = json.loads(value)
        elif key == "assign_status" and value:
            st.assign_status = json.loads(value)

    subs: dict[int, list[str]] = {}
    for sid, role in data["subs"]:
        subs.setdefault(sid, []).append(role)
    for sid, no, name, main in data["students"]:
        st.students[sid] = Student(sid, no, name, main, _order_roles(subs.get(sid, [])))

    for tid, name in data["teams"]:
        st.teams[tid] = []
        st.team_names[tid] = name
    for tid, sid in data["members"]:
        if sid in st.students and tid in st.teams:
            st.teams[tid].append(st.students[sid])
            st.member_of[sid] = tid

    for aid, tid, sid, status, reason, created in data["apps"]:
        st.apps.append(App(aid, tid, sid, status, reason, _parse_ts(created)))
    for aid, voter, vote in data["votes"]:
        st.votes.setdefault(aid, {})[voter] = vote
    return st


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _order_roles(roles: list[str]) -> tuple[str, ...]:
    return tuple(sorted(roles, key=lambda r: ROLES.index(r) if r in ROLES else 99))


def _lock(conn) -> None:
    conn.execute("select pg_advisory_xact_lock(%s)", (LOCK_KEY,))


def _set(conn, key: str, value: str | None) -> None:
    conn.execute(
        "insert into tf_settings(key, value) values (%s, %s) "
        "on conflict (key) do update set value = excluded.value",
        (key, value),
    )


# ---------------------------------------------------------------- 계정

def ensure_admin(pool: ConnectionPool, admin_password: str) -> None:
    """관리자 계정을 만들거나, ADMIN_PASSWORD와 맞지 않으면 비밀번호를 갱신한다(공고 2.1절)."""
    with pool.connection() as conn, conn.transaction():
        _lock(conn)
        row = conn.execute(
            "select id, password_hash from tf_students where student_no = 'admin'"
        ).fetchone()
        if row is None:
            conn.execute(
                "insert into tf_students(student_no, name, main_role, password_hash, is_admin) "
                "values ('admin', '관리자', null, %s, true)",
                (hash_password(admin_password),),
            )
        elif not check_password(admin_password, row[1]):
            conn.execute(
                "update tf_students set password_hash = %s, is_admin = true where id = %s",
                (hash_password(admin_password), row[0]),
            )


def login(pool: ConnectionPool, student_no: str, password: str) -> dict | None:
    student_no = (student_no or "").strip()
    if not student_no or not password:
        return None
    with pool.connection() as conn:
        row = conn.execute(
            "select id, student_no, name, password_hash, is_admin from tf_students where student_no = %s",
            (student_no,),
        ).fetchone()
    if row is None or not check_password(password, row[3]):
        return None
    return {"id": row[0], "student_no": row[1], "name": row[2], "is_admin": bool(row[4])}


def account_exists(pool: ConnectionPool, user_id: int, is_admin: bool) -> bool:
    """명단을 다시 올리면 학생 id가 바뀌므로, 로그인한 계정이 아직 있는지 확인한다."""
    with pool.connection() as conn:
        row = conn.execute(
            "select 1 from tf_students where id = %s and is_admin = %s", (user_id, is_admin)
        ).fetchone()
    return row is not None


# ---------------------------------------------------------------- 명단 올리기(관리자)

def parse_roster(data: bytes) -> list[tuple[str, str, str, tuple[str, ...]]]:
    """명단 CSV를 읽는다. 엑셀의 BOM이 붙은 UTF-8도 읽도록 utf-8-sig로 디코드한다."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("CSV가 UTF-8이 아님") from None
    reader = csv.DictReader(io.StringIO(text))
    cols = [c.strip() for c in (reader.fieldnames or [])]
    reader.fieldnames = cols
    for need in ("student_no", "name", "main_role"):
        if need not in cols:
            raise ValueError(f"CSV에 {need} 열이 없음")
    rows: list[tuple[str, str, str, tuple[str, ...]]] = []
    seen: set[str] = set()
    for line_no, r in enumerate(reader, start=2):
        no = (r.get("student_no") or "").strip()
        name = (r.get("name") or "").strip()
        main = (r.get("main_role") or "").strip()
        if not no and not name and not main:
            continue  # 빈 줄
        if not no or not name:
            raise ValueError(f"{line_no}번째 줄에 학번이나 이름이 없음")
        if no.lower() == "admin":
            raise ValueError("학번 admin은 관리자용이라 쓸 수 없음")
        if no in seen:
            raise ValueError(f"학번 {no}이(가) 두 번 나옴")
        if main not in ROLES:
            raise ValueError(f"{line_no}번째 줄의 주 역할 '{main}'이(가) 6개 역할에 없음")
        subs: list[str] = []
        for part in (r.get("sub_roles") or "").split(";"):
            role = part.strip()
            if not role or role == main or role in subs:
                continue
            if role not in ROLES:
                raise ValueError(f"{line_no}번째 줄의 보조 역할 '{role}'이(가) 6개 역할에 없음")
            subs.append(role)
        if len(subs) > 2:
            raise ValueError(f"{line_no}번째 줄의 보조 역할이 2개보다 많음")
        seen.add(no)
        rows.append((no, name, main, _order_roles(subs)))
    if not rows:
        raise ValueError("CSV에 학생이 없음")
    return rows


def reset_roster(pool: ConnectionPool, data: bytes | None, team_count: int) -> str:
    if not data:
        return "막힘: 명단 CSV 파일을 먼저 올려 주세요"
    try:
        team_count = int(team_count)
    except (TypeError, ValueError):
        return "막힘: 팀 수가 숫자가 아님"
    if not 1 <= team_count <= 50:
        return "막힘: 팀 수는 1~50 사이여야 함"
    try:
        rows = parse_roster(data)
    except ValueError as e:
        return f"막힘: {e}"

    hashes = [hash_password(STUDENT_PASSWORD) for _ in rows]  # 학생마다 다른 salt
    with pool.connection() as conn, conn.transaction():
        _lock(conn)
        st = load_state(conn)
        conn.execute("delete from tf_votes")
        conn.execute("delete from tf_applications")
        conn.execute("delete from tf_members")
        conn.execute("delete from tf_teams")
        conn.execute("delete from tf_students where not is_admin")  # 관리자 계정은 남김
        with conn.cursor() as cur:
            for (no, name, main, subs), h in zip(rows, hashes):
                cur.execute(
                    "insert into tf_students(student_no, name, main_role, password_hash, is_admin) "
                    "values (%s, %s, %s, %s, false) returning id",
                    (no, name, main, h),
                )
                sid = cur.fetchone()[0]
                for role in subs:
                    cur.execute(
                        "insert into tf_student_sub_roles(student_id, role) values (%s, %s)", (sid, role)
                    )
            cur.executemany(
                "insert into tf_teams(id, name) values (%s, %s)",
                [(i, f"팀{i}") for i in range(1, team_count + 1)],
            )
        _set(conn, "phase", "open")
        _set(conn, "round", str(st.round + 1))
        _set(conn, "draft", None)
        _set(conn, "assign_status", None)
    return f"승인: 학생 {len(rows)}명과 빈 팀 {team_count}개로 처음 상태를 만듦"


# ---------------------------------------------------------------- 지원과 투표(학생)

def _free_slot(conn, team_no: int) -> int | None:
    used = {r[0] for r in conn.execute("select slot_no from tf_members where team_id = %s", (team_no,))}
    return next((s for s in range(1, MAX_SIZE + 1) if s not in used), None)


def _join(conn, team_no: int, student_id: int) -> bool:
    """팀원으로 넣는다. 유일성 제약에 걸리면(자리 없음, 이미 소속) False."""
    slot = _free_slot(conn, team_no)
    if slot is None:
        return False
    try:
        with conn.transaction():  # savepoint
            conn.execute(
                "insert into tf_members(team_id, slot_no, student_id) values (%s, %s, %s)",
                (team_no, slot, student_id),
            )
    except (pg_errors.UniqueViolation, pg_errors.CheckViolation):
        return False
    return True


def _decide(conn, app_id: int, status: str, reason: str | None) -> None:
    conn.execute(
        "update tf_applications set status = %s, reason = %s, decided_at = now() where id = %s",
        (status, reason, app_id),
    )


def apply(pool: ConnectionPool, student_id: int, team_no: int) -> str:
    with pool.connection() as conn, conn.transaction():
        _lock(conn)
        st = load_state(conn)
        me = st.students.get(student_id)
        if me is None:
            return "막힘: 학생 계정으로 로그인해야 지원할 수 있음"
        if not st.is_open:
            return "막힘: 모집이 마감됨"
        if team_no not in st.teams:
            return "막힘: 없는 팀임"
        if student_id in st.member_of:
            return "막힘: 이미 팀이 있음"
        if st.pending_of_student(student_id):
            return "막힘: 결과를 기다리는 지원이 있음"
        members = st.teams[team_no]
        reason = check_join(members, me)
        if reason:
            return f"막힘: {reason}"

        name = st.team_names[team_no]
        if not members:
            # 빈 팀: 투표할 팀원이 없으므로 바로 승인
            if not _join(conn, team_no, student_id):
                return "막힘: 팀이 꽉 참"
            conn.execute(
                "insert into tf_applications(team_id, student_id, status, reason, decided_at) "
                "values (%s, %s, '승인', '빈 팀이라 바로 승인', now())",
                (team_no, student_id),
            )
            return f"승인: {name}에 들어감(빈 팀이라 바로 승인)"
        try:
            with conn.transaction():
                conn.execute(
                    "insert into tf_applications(team_id, student_id, status) values (%s, %s, '대기')",
                    (team_no, student_id),
                )
        except pg_errors.UniqueViolation:
            return "막힘: 결과를 기다리는 지원이 있음"
        return f"대기: {name}에 지원함. 팀원 {len(members)}명의 투표를 기다림"


def vote(pool: ConnectionPool, voter_id: int, applicant_no: str, approve: bool) -> str:
    with pool.connection() as conn, conn.transaction():
        _lock(conn)
        st = load_state(conn)
        voter = st.students.get(voter_id)
        if voter is None:
            return "막힘: 학생 계정으로 로그인해야 투표할 수 있음"
        team_no = st.member_of.get(voter_id)
        applicant = next((s for s in st.students.values() if s.no == str(applicant_no)), None)
        if team_no is None or applicant is None:
            return "막힘: 투표할 지원이 없음"
        apps = [a for a in st.apps if a.team_id == team_no and a.student_id == applicant.id]
        if not apps:
            return "막힘: 투표할 지원이 없음"
        app = apps[-1]
        who = applicant.name
        if app.status != "대기":
            return f"{app.status}: {who}의 지원은 이미 {app.status}됨({app.reason or ''})"
        if not st.is_open:
            return "막힘: 모집이 마감됨"
        votes = st.votes.get(app.id, {})
        if voter_id in votes:
            return f"대기: 이미 투표함(내 투표: {votes[voter_id]})"

        choice = "찬성" if approve else "거부"
        conn.execute(
            "insert into tf_votes(application_id, voter_id, vote) values (%s, %s, %s)",
            (app.id, voter_id, choice),
        )
        if not approve:
            # 한 명이라도 거부하면 다른 팀원의 투표를 기다리지 않고 바로 거절
            _decide(conn, app.id, "거절", f"팀원 {voter.name}의 거부")
            return f"거절: {who}의 지원이 거절됨(팀원의 거부)"

        votes = {**votes, voter_id: "찬성"}
        members = st.teams[team_no]  # 투표하는 때의 팀원
        yes = sum(1 for m in members if votes.get(m.id) == "찬성")
        if yes < len(members):
            return f"대기: {who}의 지원 — 찬성 {yes}/{len(members)}명"

        # 모든 팀원이 찬성 → 승인 직전에 규칙을 다시 검사(3장 어려운 해결)
        reason = check_join(members, applicant)
        if reason:
            _decide(conn, app.id, "거절", f"승인 직전 검사: {reason}")
            return f"거절: {who}의 지원이 거절됨(승인 직전 검사: {reason})"
        if not _join(conn, team_no, applicant.id):
            _decide(conn, app.id, "거절", "승인 직전 검사: 팀이 꽉 참")
            return f"거절: {who}의 지원이 거절됨(승인 직전 검사: 팀이 꽉 참)"
        _decide(conn, app.id, "승인", "모든 팀원이 찬성")
        return f"승인: {who}의 지원이 승인되어 {st.team_names[team_no]}의 팀원이 됨"


# ---------------------------------------------------------------- 마감과 배정(관리자)

def close_recruiting(pool: ConnectionPool) -> str:
    with pool.connection() as conn, conn.transaction():
        _lock(conn)
        st = load_state(conn)
        if not st.teams:
            return "막힘: 명단을 먼저 올려 주세요"
        if not st.is_open:
            return "막힘: 이미 모집이 마감됨"
        pending = conn.execute(
            "update tf_applications set status = '거절', reason = '모집 마감', decided_at = now() "
            "where status = '대기' returning id"
        ).fetchall()
        alone = [ms[0] for ms in st.teams.values() if len(ms) == 1]
        for s in alone:
            conn.execute("delete from tf_members where student_id = %s", (s.id,))
        _set(conn, "phase", "closed")
        _set(conn, "draft", None)
        _set(conn, "assign_status", None)
    names = ", ".join(s.name for s in alone) or "없음"
    return (
        f"승인: 모집을 마감함(대기 중이던 지원 {len(pending)}건 거절, "
        f"혼자 있던 팀에서 빠진 학생: {names})"
    )


def save_draft(pool: ConnectionPool, round_no: int, draft: dict, status: dict) -> bool:
    """검사를 통과한 배정안을 저장한다. 그사이 명단이 바뀌었으면 저장하지 않는다."""
    with pool.connection() as conn, conn.transaction():
        _lock(conn)
        st = load_state(conn)
        if st.round != round_no or st.phase != "closed":
            return False
        _set(conn, "draft", json.dumps(draft, ensure_ascii=False))
        _set(conn, "assign_status", json.dumps(status, ensure_ascii=False))
        return True


def save_assign_status(pool: ConnectionPool, round_no: int, status: dict) -> None:
    with pool.connection() as conn, conn.transaction():
        _lock(conn)
        st = load_state(conn)
        if st.round == round_no:
            _set(conn, "assign_status", json.dumps(status, ensure_ascii=False))
            _set(conn, "draft", None)


def confirm_draft(pool: ConnectionPool) -> str:
    with pool.connection() as conn, conn.transaction():
        _lock(conn)
        st = load_state(conn)
        if st.phase == "confirmed":
            return "막힘: 이미 배정을 확정함"
        if st.phase != "closed":
            return "막힘: 모집을 먼저 마감해야 함"
        if not st.draft:
            return "막힘: 확정할 배정안이 없음(AI 배정을 먼저 누르세요)"
        pairs = [(str(no), int(t)) for no, t in st.draft.get("pairs", [])]
        errs, _ = validate_plan(st.teams, st.unassigned(), pairs)
        if errs:
            return "막힘: 배정안이 지금 상태와 맞지 않음 — " + "; ".join(errs)
        by_no = {s.no: s for s in st.students.values()}
        for no, t in pairs:
            if not _join(conn, t, by_no[no].id):
                raise RuntimeError("팀원 저장 실패")  # 트랜잭션 전체를 되돌림
        _set(conn, "phase", "confirmed")
        _set(conn, "draft", None)
    return f"승인: 배정안을 확정함(학생 {len(pairs)}명을 팀에 넣고 DB에 저장함)"


def log_ai(pool: ConnectionPool, round_no: int, attempt: int, model: str, outcome: str,
           elapsed: float, detail: str) -> None:
    try:
        with pool.connection() as conn:
            conn.execute(
                "insert into tf_ai_logs(round, attempt, model, outcome, elapsed_sec, detail) "
                "values (%s, %s, %s, %s, %s, %s)",
                (round_no, attempt, model, outcome, round(elapsed, 1), detail[:2000]),
            )
    except Exception:
        pass  # 기록 실패가 배정을 막지 않게 함


def recent_ai_logs(pool: ConnectionPool, limit: int = 12) -> list[tuple]:
    with pool.connection() as conn:
        return conn.execute(
            "select created_at, round, attempt, model, outcome, elapsed_sec, detail "
            "from tf_ai_logs order by id desc limit %s",
            (limit,),
        ).fetchall()
