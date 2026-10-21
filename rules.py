"""팀 규칙(공고 2.3절)과 배정안 검사(2.5절 4항), 앱 자체 배정(3장 어려운 해결).

이 모듈은 DB나 Streamlit을 쓰지 않는 순수 함수만 둔다. 그래서 테스트하기 쉽고,
지원(apply), 승인 직전 재검사(vote), AI 배정안 검사, 백트래킹 배정이 모두
같은 규칙 함수를 공유한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

ROLES = ("기획자", "웹 프로그래머", "프론트엔드 디자이너", "테스터", "보고서 작성자", "발표자")
PL, WP = ROLES[0], ROLES[1]
REQUIRED = (PL, WP)
MIN_SIZE, MAX_SIZE = 2, 4

ABBR = {
    "기획자": "PL",
    "웹 프로그래머": "WP",
    "프론트엔드 디자이너": "FD",
    "테스터": "TT",
    "보고서 작성자": "RW",
    "발표자": "PT",
}


@dataclass(frozen=True)
class Student:
    id: int
    no: str
    name: str
    main: str
    subs: tuple[str, ...] = field(default=())

    @property
    def caps(self) -> frozenset[str]:
        """맡을 수 있는 역할(주 역할 + 보조 역할)."""
        return frozenset((self.main, *self.subs))

    def role_text(self) -> str:
        subs = ", ".join(self.subs) if self.subs else "없음"
        return f"주 역할 {self.main} · 보조 역할 {subs}"


# ---------------------------------------------------------------- 필수 포함 역할

def required_filled(members: list[Student]) -> int:
    """기획자와 웹 프로그래머를 서로 다른 학생에게 맡길 때 채울 수 있는 최대 개수(0~2).

    한 학생이 둘 다 할 수 있어도 하나로만 센다(공고 3장 '필수 포함 역할 자리 계산').
    역할이 2개뿐이므로 이분 매칭을 직접 따진다.
    """
    pl = [m for m in members if PL in m.caps]
    wp = [m for m in members if WP in m.caps]
    if any(a.id != b.id for a in pl for b in wp):
        return 2
    return 1 if (pl or wp) else 0


def unfilled_required(members: list[Student]) -> int:
    return len(REQUIRED) - required_filled(members)


def missing_required_text(members: list[Student]) -> str:
    """채우지 못한 필수 포함 역할을 사람이 읽을 말로."""
    has_pl = any(PL in m.caps for m in members)
    has_wp = any(WP in m.caps for m in members)
    if not has_pl and not has_wp:
        return f"{PL}와 {WP}를 맡을 학생이 없음"
    if not has_pl:
        return f"{PL}를 맡을 학생이 없음"
    if not has_wp:
        return f"{WP}를 맡을 학생이 없음"
    if required_filled(members) < 2:
        return f"{PL}와 {WP}를 한 학생만 맡을 수 있음(서로 다른 학생이 맡아야 함)"
    return ""


# ---------------------------------------------------------------- 지원 검사

def check_join(members: list[Student], applicant: Student) -> str | None:
    """applicant가 members 팀에 들어가도 되는지. 안 되면 이유, 되면 None.

    지원할 때(막힘)와 승인 직전 재검사(거절) 모두 이 함수를 쓴다.
    """
    if len(members) >= MAX_SIZE:
        return "팀이 꽉 참"
    if any(m.main == applicant.main for m in members):
        return "같은 주 역할의 팀원이 있음"
    after = [*members, applicant]
    remaining = MAX_SIZE - len(after)
    if remaining < unfilled_required(after):
        return "필수 포함 역할을 맡을 학생의 자리가 모자람"
    return None


# ---------------------------------------------------------------- 최종 구성 검사

def team_errors(team_no: int, members: list[Student]) -> list[str]:
    errs: list[str] = []
    n = len(members)
    if not (MIN_SIZE <= n <= MAX_SIZE):
        errs.append(f"팀{team_no}의 인원이 {n}명임(2~4명이어야 함)")
    mains = [m.main for m in members]
    dups = sorted({r for r in mains if mains.count(r) > 1})
    for r in dups:
        errs.append(f"팀{team_no}에 주 역할이 {r}인 학생이 2명 이상임")
    if n and required_filled(members) < 2:
        errs.append(f"팀{team_no}에 {missing_required_text(members)}")
    return errs


def validate_final(teams: dict[int, list[Student]]) -> list[str]:
    errs: list[str] = []
    for no in sorted(teams):
        errs.extend(team_errors(no, teams[no]))
    return errs


def validate_plan(
    teams: dict[int, list[Student]],
    unassigned: list[Student],
    pairs: list[tuple[str, int]],
) -> tuple[list[str], dict[int, list[Student]]]:
    """배정안 검사(공고 2.5절 4항의 다섯 가지).

    pairs: (학번, 팀 번호) 목록. AI 응답을 그대로 넣는다(중복 확인을 위해 dict가 아닌 list).
    반환: (오류 목록, 배정 후 팀 구성)
    """
    errs: list[str] = []
    pool = {s.no: s for s in unassigned}
    placed: dict[str, int] = {}
    members_no = {m.no for ms in teams.values() for m in ms}

    for no, team in pairs:
        no = str(no).strip()
        if no in members_no:
            errs.append(f"이미 팀에 있는 학생({no})을 옮기려 함")
            continue
        if no not in pool:
            errs.append(f"명단에 없거나 배정 대상이 아닌 학번({no})이 있음")
            continue
        if team not in teams:
            errs.append(f"{pool[no].name}을(를) 없는 팀({team})에 배정함")
            continue
        if no in placed:
            # 검사 2: 한 학생은 한 팀에만
            errs.append(f"{pool[no].name}이(가) 두 번 이상 배정됨")
            continue
        placed[no] = team

    # 검사 1: 팀이 없는 학생이 빠짐없이 배정됨
    missing = [s.name for s in unassigned if s.no not in placed]
    if missing:
        errs.append("배정되지 않은 학생이 있음: " + ", ".join(missing))

    after = {t: list(ms) for t, ms in teams.items()}
    for no, t in placed.items():
        after[t].append(pool[no])
    # 검사 3~5: 인원, 주 역할 중복, 필수 포함 역할
    errs.extend(validate_final(after))
    return errs, after


# ---------------------------------------------------------------- 앱 자체 배정(백트래킹)

def solve(teams: dict[int, list[Student]], pool: list[Student]) -> dict[int, list[Student]] | None:
    """팀이 없는 학생(pool)만 팀에 넣어 모든 규칙을 지키는 구성을 모든 경우를 따져 찾는다.

    없으면 None. 이미 팀에 있는 학생은 옮기지 않는다.
    """
    team_nos = sorted(teams)
    cur = {t: list(teams[t]) for t in team_nos}

    # 기존 구성만으로 이미 불가능한 팀(정원 초과, 주 역할 중복)은 바로 실패
    for t in team_nos:
        mains = [m.main for m in cur[t]]
        if len(cur[t]) > MAX_SIZE or len(mains) != len(set(mains)):
            return None

    # 선택지가 적은 학생(필수 역할을 맡을 수 있는 학생)부터 놓으면 가지치기가 빨라진다
    order = sorted(pool, key=lambda s: (-(len(s.caps & set(REQUIRED))), s.no))

    def feasible(rest: int) -> bool:
        need_min = 0
        free = 0
        for t in team_nos:
            ms = cur[t]
            cap = MAX_SIZE - len(ms)
            if cap < unfilled_required(ms):
                return False
            need_min += max(0, MIN_SIZE - len(ms))
            free += cap
        return need_min <= rest <= free

    def rec(i: int) -> bool:
        if not feasible(len(order) - i):
            return False
        if i == len(order):
            return not validate_final(cur)
        s = order[i]
        tried_empty = False
        for t in team_nos:
            ms = cur[t]
            if len(ms) >= MAX_SIZE or any(m.main == s.main for m in ms):
                continue
            if not ms:
                # 빈 팀끼리는 서로 바꿔도 같으므로 첫 빈 팀만 시도한다
                if tried_empty:
                    continue
                tried_empty = True
            ms.append(s)
            if rec(i + 1):
                return True
            ms.pop()
        return False

    return cur if rec(0) else None


def explain_infeasible(teams: dict[int, list[Student]], pool: list[Student]) -> str:
    """규칙을 지키는 배정이 없을 때 그 이유(공고 2.5절 8항)."""
    reasons: list[str] = []
    free = sum(max(0, MAX_SIZE - len(ms)) for ms in teams.values())
    total = sum(len(ms) for ms in teams.values()) + len(pool)
    if len(pool) > free:
        reasons.append(f"팀이 없는 학생({len(pool)}명)이 남은 자리({free}개)보다 많음")
    if total < MIN_SIZE * len(teams):
        reasons.append(f"전체 학생({total}명)이 모든 팀을 2명 이상으로 채우기에 모자람")
    everyone = [m for ms in teams.values() for m in ms] + list(pool)
    n_teams = len(teams)
    for role in REQUIRED:
        n = sum(1 for s in everyone if role in s.caps)
        if n < n_teams:
            reasons.append(f"{role}를 맡을 수 있는 학생({n}명)이 팀 수({n_teams}개)보다 적음")
    either = sum(1 for s in everyone if s.caps & set(REQUIRED))
    if either < 2 * n_teams and not reasons:
        reasons.append(f"기획자나 웹 프로그래머를 맡을 수 있는 학생({either}명)이 팀마다 2명씩 필요한 수({2 * n_teams}명)보다 적음")
    for t in sorted(teams):
        ms = teams[t]
        mains = [m.main for m in ms]
        if len(mains) != len(set(mains)):
            reasons.append(f"팀{t}에 주 역할이 같은 팀원이 이미 있음")
            continue
        cap = MAX_SIZE - len(ms)
        need = unfilled_required(ms)
        if need and cap == 0:
            reasons.append(f"팀{t}에 {missing_required_text(ms)}(4명이 다 차서 더 넣을 수 없음)")
            continue
        if cap < need:
            reasons.append(f"팀{t}은 남은 자리 {cap}개로 필수 포함 역할 {need}개를 채울 수 없음")
            continue
        # 이 팀 하나만 놓고 봐도 채울 수 없는지(다른 팀은 무시)
        if not _team_alone_possible(ms, pool):
            cands = [s for s in pool if s.main not in mains]
            why = missing_required_text([*ms, *cands])
            if why:
                reasons.append(f"팀{t}에 {why}(넣을 수 있는 학생 가운데에도 없음)")
            else:
                reasons.append(f"팀{t}을 규칙에 맞게 채울 학생 조합이 없음")
    if not reasons:
        reasons.append(
            "팀마다 기획자와 웹 프로그래머를 서로 다른 학생으로 채우면서 주 역할이 겹치지 않게 "
            "나눌 방법이 없음(필수 포함 역할을 맡을 학생이 모자람)"
        )
    return "; ".join(reasons)


def _team_alone_possible(members: list[Student], pool: list[Student]) -> bool:
    """다른 팀을 무시하고 pool에서 몇 명을 골라 이 팀 하나를 규칙에 맞출 수 있는지."""
    cap = MAX_SIZE - len(members)

    def rec(i: int, cur: list[Student]) -> bool:
        if MIN_SIZE <= len(cur) <= MAX_SIZE and not team_errors(0, cur):
            return True
        if i == len(pool) or len(cur) - len(members) >= cap:
            return False
        s = pool[i]
        if all(m.main != s.main for m in cur):
            if rec(i + 1, [*cur, s]):
                return True
        return rec(i + 1, cur)

    return rec(0, list(members))


def short(s: Student) -> str:
    """학생1(PL;RW) 같은 짧은 표시. 이름에 이미 약자가 있으면 이름만."""
    if "(" in s.name:
        return s.name
    tags = ";".join(ABBR.get(r, r) for r in (s.main, *s.subs))
    return f"{s.name}({tags})"
