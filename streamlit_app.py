"""팀 프로젝트 팀 편성 웹 앱(중간 과제).

화면 요소의 key는 공고 2.7절 표를 그대로 따른다. 결과 안내(result)는
'승인', '대기', '거절', '막힘' 가운데 하나로 시작한다.

다른 사람의 변경 보기: 어려운 해결. 로그인한 화면 전체를 st.fragment(run_every=3초)로 감싸
3초마다 그 부분만 다시 그린다(새로 고침 단추 없음).
"""
from __future__ import annotations

import time
from datetime import timezone, timedelta

import streamlit as st

import ai
import services
from db import get_secret, init_schema, make_pool, reload_env, secret_status
from rules import MAX_SIZE, Student, missing_required_text, validate_plan

REFRESH_SEC = 3  # 5초 이하(공고 2.7절)
KST = timezone(timedelta(hours=9))

st.set_page_config(page_title="팀 편성", page_icon="👥", layout="wide")
ss = st.session_state


# ---------------------------------------------------------------- DB 연결(앱 전체에서 한 번)

@st.cache_resource(show_spinner="DB에 연결하는 중…")
def get_pool():
    url = get_secret("DATABASE_URL")
    if not url:
        return None
    pool = make_pool(url)
    init_schema(pool)  # schema.sql 실행(create ... if not exists)
    admin_pw = get_secret("ADMIN_PASSWORD")
    if admin_pw:
        services.ensure_admin(pool, admin_pw)  # 관리자 계정을 만들거나 비밀번호를 갱신
    return pool


def kst(dt) -> str:
    return dt.astimezone(KST).strftime("%m/%d %H:%M:%S") if dt else ""


def run_action(fn) -> str:
    """단추 처리. 예상하지 못한 오류도 '막힘'으로 시작하는 안내로 바꾼다(키나 주소는 보여 주지 않음)."""
    try:
        return fn()
    except Exception as e:
        return f"막힘: 처리 중 오류가 남({type(e).__name__}). 잠시 뒤 다시 눌러 주세요"


def render_result():
    """단추를 누른 결과 안내. 나중에 바꿀 수 있게 자리(placeholder)를 돌려준다."""
    with st.container(key="result"):
        holder = st.empty()
    _show_result(holder, ss.get("result"))
    return holder


def _show_result(holder, msg: str | None) -> None:
    if not msg:
        holder.caption("단추를 누르면 결과가 여기에 보입니다.")
        return
    head = msg.split(":", 1)[0]
    {"승인": holder.success, "대기": holder.info, "거절": holder.warning}.get(head, holder.error)(msg)


def logout() -> None:
    for k in ("user", "result", "assign_waiting"):
        ss.pop(k, None)


def roles_line(s: Student) -> str:
    return f"{s.main}" + (f" · 보조 {', '.join(s.subs)}" if s.subs else " · 보조 없음")


def team_css(my_team: int | None) -> None:
    css = """
    <style>
    div[class*="st-key-team_"] {min-height: 12rem;}
    .tf-muted {color: var(--text-color); opacity: .65; font-size: .9rem;}
    """
    if my_team:
        css += f""".st-key-team_{my_team} {{outline: 2px solid #2e7d32; border-radius: .6rem;}}"""
    st.markdown(css + "</style>", unsafe_allow_html=True)


def render_teams(state: services.State, me: Student | None = None) -> None:
    """팀 목록. 학생 화면이면 지원 단추(apply_N)를 함께 둔다."""
    my_team = state.member_of.get(me.id) if me else None
    pending = state.pending_of_student(me.id) if me else None
    can_apply = bool(me) and state.is_open and my_team is None and pending is None
    nos = sorted(state.teams)
    per_row = min(4, max(1, len(nos)))
    for i in range(0, len(nos), per_row):
        cols = st.columns(per_row)
        for col, t in zip(cols, nos[i:i + per_row]):
            ms = state.teams[t]
            with col:
                with st.container(key=f"team_{t}", border=True):
                    tag = " · **내 팀**" if my_team == t else ""
                    st.markdown(f"#### {state.team_names[t]}{tag}")
                    st.caption(f"{len(ms)}/{MAX_SIZE}명")
                    if ms:
                        for m in ms:
                            st.markdown(f"- **{m.name}** — {roles_line(m)}")
                        miss = missing_required_text(ms)
                        st.caption(f"필수 포함 역할: {miss}" if miss else "필수 포함 역할: 기획자·웹 프로그래머 채움")
                    else:
                        st.caption("아직 팀원이 없음(지원하면 바로 승인)")
                    waiting = state.pending_for_team(t)
                    if waiting:
                        st.caption("대기 중인 지원: " + ", ".join(state.students[a.student_id].name for a in waiting))
                    if me is not None:
                        st.button(
                            f"{state.team_names[t]}에 지원",
                            key=f"apply_{t}",
                            on_click=on_apply,
                            args=(t,),
                            disabled=not can_apply,
                            width="stretch",
                        )


# ---------------------------------------------------------------- 단추 처리(콜백)

def on_login() -> None:
    pool = get_pool()
    user = services.login(pool, ss.get("login_student_no", ""), ss.get("login_password", ""))
    if user is None:
        ss.result = "막힘: 학번 또는 비밀번호가 틀림"
        return
    ss.user = user
    ss.result = f"승인: {user['name']}(으)로 로그인함"


def on_apply(team_no: int) -> None:
    ss.result = run_action(lambda: services.apply(get_pool(), ss.user["id"], team_no))


def on_vote(applicant_no: str, approve: bool) -> None:
    ss.result = run_action(lambda: services.vote(get_pool(), ss.user["id"], applicant_no, approve))


def _admin_only(fn):
    if not ss.get("user", {}).get("is_admin"):
        ss.result = "막힘: 관리자만 할 수 있음"
        return
    ss.result = run_action(fn)


def on_start() -> None:
    def go():
        f = ss.get("admin_roster_file")
        data = f.getvalue() if f is not None else None
        return services.reset_roster(get_pool(), data, ss.get("admin_team_count", 0))
    _admin_only(go)


def on_close() -> None:
    _admin_only(lambda: services.close_recruiting(get_pool()))


def on_assign() -> None:
    _admin_only(lambda: ai.start_assignment(get_pool()))
    ss.assign_waiting = ss.get("result", "").startswith("승인: AI 배정을 시작함")


def on_confirm() -> None:
    _admin_only(lambda: services.confirm_draft(get_pool()))


# ---------------------------------------------------------------- 학생 화면

@st.fragment(run_every=REFRESH_SEC)
def student_view() -> None:
    pool = get_pool()
    with pool.connection() as conn:
        state = services.load_state(conn)
    me = state.students.get(ss.user["id"])
    if me is None:  # 관리자가 명단을 다시 올려 계정이 바뀜
        logout()
        ss.result = "막힘: 명단이 바뀌어 다시 로그인해야 함"
        st.rerun(scope="app")

    render_result()
    my_team = state.member_of.get(me.id)
    team_css(my_team)

    phase = {"open": "모집 중", "closed": "모집 마감(지원과 투표를 할 수 없음)", "confirmed": "배정 확정"}[state.phase]
    pending = state.pending_of_student(me.id)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**모집 상태:** {phase}")
        st.markdown(f"**내 역할:** {roles_line(me)}")
        st.markdown(f"**내 팀:** {state.team_names[my_team] if my_team else '아직 팀이 없음'}")
    with c2:
        if pending:
            ms = state.teams[pending.team_id]
            yes = sum(1 for v in state.votes.get(pending.id, {}).values() if v == "찬성")
            st.markdown(f"**내 지원:** {state.team_names[pending.team_id]} — 대기(찬성 {yes}/{len(ms)}명)")
        else:
            last = state.last_app_of_student(me.id)
            if last:
                st.markdown(
                    f"**최근 지원:** {state.team_names.get(last.team_id, '')} — {last.status}"
                    + (f"({last.reason})" if last.reason else "")
                )

    if my_team:
        st.subheader("내 팀에 들어온 지원")
        apps = state.pending_for_team(my_team)
        if not apps:
            st.caption("투표할 지원이 없음")
        members = state.teams[my_team]
        for a in apps:
            who = state.students[a.student_id]
            votes = state.votes.get(a.id, {})
            yes = sum(1 for m in members if votes.get(m.id) == "찬성")
            with st.container(border=True):
                c1, c2, c3 = st.columns([5, 2, 2])
                c1.markdown(f"**{who.name}** ({who.no}) — {roles_line(who)}  \n찬성 {yes}/{len(members)}명")
                mine = votes.get(me.id)
                if mine:
                    c2.markdown(f"내 투표: {mine}")
                elif state.is_open:
                    c2.button("찬성", key=f"vote_yes_{who.no}", on_click=on_vote, args=(who.no, True),
                              type="primary", width="stretch")
                    c3.button("거부", key=f"vote_no_{who.no}", on_click=on_vote, args=(who.no, False),
                              width="stretch")

    st.subheader("팀 목록")
    render_teams(state, me)
    st.caption(f"{REFRESH_SEC}초마다 자동으로 다시 그림 · 마지막 갱신 {time.strftime('%H:%M:%S', time.gmtime(time.time() + 9 * 3600))}")


# ---------------------------------------------------------------- 관리자 화면

@st.fragment(run_every=REFRESH_SEC)
def admin_view() -> None:
    pool = get_pool()
    with pool.connection() as conn:
        state = services.load_state(conn)

    result_holder = render_result()
    team_css(None)

    st.subheader("1. 명단 올리기")
    c1, c2, c3 = st.columns([4, 2, 2])
    with c1:
        st.file_uploader("명단 CSV(student_no,name,main_role,sub_roles)", type=["csv"], key="admin_roster_file")
    with c2:
        st.number_input("팀 수", min_value=1, max_value=50, value=3, step=1, key="admin_team_count")
    with c3:
        st.write("")
        st.button("명단으로 처음 상태 만들기", key="admin_start", on_click=on_start, type="primary",
                  width="stretch")
    st.caption("누르면 지원, 투표, 팀원, 마감 표시를 모두 지우고 명단의 학생과 빈 팀을 만듭니다. 관리자 계정은 남습니다.")

    st.subheader("2. 모집")
    phase = {"open": "모집 중", "closed": "모집 마감", "confirmed": "배정 확정"}[state.phase]
    c1, c2 = st.columns([6, 2])
    c1.markdown(f"**모집 상태:** {phase} · 학생 {len(state.students)}명 · 팀 {len(state.teams)}개")
    c2.button("모집 마감", key="admin_close", on_click=on_close, width="stretch")

    if state.teams:
        render_teams(state)

    with st.container(key="unassigned", border=True):
        st.markdown("**팀이 없는 학생**")
        un = state.unassigned()
        if un:
            for s in un:
                st.markdown(f"- {s.name} ({s.no}) — {roles_line(s)}")
        else:
            st.caption("없음")

    st.subheader("3. 배정")
    st.button("AI 배정", key="admin_assign", on_click=on_assign, type="primary")
    with st.container(key="assign_status", border=True):
        status_holder = st.empty()
        job = ai.JOB.snapshot()
        if job["running"] and job["round"] == state.round:
            # 배경 작업이 끝날 때까지 진행 상황을 1초마다 보여 준다
            while job["running"]:
                status_holder.info(_progress_text(job))
                time.sleep(1)
                job = ai.JOB.snapshot()
            with pool.connection() as conn:
                state = services.load_state(conn)
        if ss.pop("assign_waiting", False) and not job["running"]:
            # 내가 누른 배정이 끝났으면 결과 안내를 최종 결과로 바꾼다
            ss.result = _final_result(state)
            _show_result(result_holder, ss.result)
        _render_status(status_holder, state, job)

    if state.draft and state.phase == "closed":
        render_draft(state)
        st.button("배정안 확정", key="admin_confirm", on_click=on_confirm, type="primary")
    elif state.phase == "confirmed":
        st.success("배정을 확정했습니다. 위 팀 목록이 확정한 구성입니다.")

    with st.expander("AI 호출 기록과 설정 확인"):
        st.markdown("**쓰는 모델:** " + " → ".join(f"{i + 1}차 `{m}`" for i, m in enumerate(ai.get_models())))
        st.markdown("**비밀 값:** " + ", ".join(f"{k} {v}" for k, v in secret_status().items()))
        logs = services.recent_ai_logs(pool)
        if logs:
            st.table([
                {"시각(KST)": kst(r[0]), "차수": f"{r[2]}차" if r[2] else "앱", "모델": r[3], "결과": r[4],
                 "걸린 시간(초)": float(r[5] or 0), "내용": (r[6] or "")[:120]}
                for r in logs
            ])
        else:
            st.caption("아직 기록이 없음")


def _progress_text(job: dict) -> str:
    label = f"{job['attempt']}차" if job["attempt"] else "준비"
    lines = [f"⏳ {label} 모델(`{job['model']}`)의 응답을 기다리는 중… {job['elapsed']:.0f}초 / 최대 120초"]
    lines += [f"- {e}" for e in job["events"]]
    return "  \n".join(lines)


def _render_status(holder, state: services.State, job: dict) -> None:
    info = state.assign_status
    if state.phase == "open":
        holder.caption("모집을 마감한 뒤 AI 배정을 누르세요.")
        return
    if not info:
        holder.caption("AI 배정을 누르면 진행 상황이 여기에 보입니다.")
        return
    events = "".join(f"  \n- {e}" for e in info.get("events", []))
    kind = info.get("kind")
    if kind == "ok":
        holder.success(f"배정안 준비됨: {info['message']}{events}")
    elif kind == "none":
        holder.error(info["message"])
    elif kind == "running":
        holder.warning("이전 배정 작업이 끊김(앱이 다시 시작됨). AI 배정을 다시 눌러 주세요.")
    else:
        holder.error(f"{info.get('message', '')}{events}")


def _final_result(state: services.State) -> str:
    info = state.assign_status or {}
    if info.get("kind") == "ok" and state.draft:
        return f"승인: 배정안을 만듦 — {info['message']}. 아래 배정안을 확인하고 확정하세요"
    return f"막힘: 배정안을 만들지 못함 — {info.get('message', '알 수 없는 이유')}"


def render_draft(state: services.State) -> None:
    pairs = [(str(no), int(t)) for no, t in state.draft.get("pairs", [])]
    by_no = {s.no: s for s in state.students.values()}
    st.markdown(f"**배정안** (만든 곳: {state.draft.get('source', '')})")
    if pairs:
        st.table([
            {"이름": by_no[no].name, "학번": no, "주 역할": by_no[no].main,
             "보조 역할": ", ".join(by_no[no].subs) or "없음", "배정된 팀": state.team_names.get(t, t)}
            for no, t in pairs if no in by_no
        ])
    errs, after = validate_plan(state.teams, state.unassigned(), pairs)
    st.markdown("**배정 뒤 팀 구성**")
    cols = st.columns(min(4, max(1, len(after))))
    new = {no for no, _ in pairs}
    for i, t in enumerate(sorted(after)):
        with cols[i % len(cols)]:
            names = ", ".join(f"{m.name}{' (새로)' if m.no in new else ''}" for m in after[t])
            st.markdown(f"- **{state.team_names[t]}**: {names}")
    if errs:
        st.error("지금 상태와 맞지 않음: " + "; ".join(errs))
    else:
        st.caption("검사 다섯 가지(빠짐없이 배정, 한 팀에만, 2~4명, 주 역할 중복 없음, 필수 포함 역할)를 모두 통과함")


# ---------------------------------------------------------------- 로그인과 페이지

def login_page() -> None:
    st.title("👥 팀 프로젝트 팀 편성")
    st.caption("학번과 비밀번호로 로그인하세요. 관리자는 학번 admin으로 로그인합니다.")
    render_result()
    with st.form("login_form", clear_on_submit=False):
        st.text_input("학번", key="login_student_no")
        st.text_input("비밀번호", type="password", key="login_password")
        st.form_submit_button("로그인", key="login_submit", on_click=on_login, type="primary")


def _db_error_hint(msg: str) -> str:
    """psycopg 오류 글에서 원인만 골라 안내한다(주소나 비밀번호는 보여 주지 않음)."""
    m = msg.lower()
    if "password authentication failed" in m:
        return "원인: DB 비밀번호가 틀림. Supabase에서 정한 DB 비밀번호와 같은지 확인하세요."
    if "tenant or user not found" in m:
        return "원인: 주소의 사용자 이름(postgres.프로젝트ID)이나 aws-0/aws-1 부분이 Supabase 화면과 다름."
    if "could not translate host name" in m or "name or service not known" in m or "getaddrinfo" in m:
        return "원인: 주소의 호스트 이름을 찾을 수 없음. 비밀번호에 @ # / : ? 같은 문자가 있으면 주소가 깨집니다."
    if "timeout" in m or "timed out" in m:
        return "원인: DB 서버가 응답하지 않음. 인터넷 연결과 Supabase 프로젝트가 일시정지되지 않았는지 확인하세요."
    if "invalid" in m and "uri" in m or "missing" in m and "=" in m:
        return "원인: DATABASE_URL 꼴이 틀림. Session pooler 주소를 통째로 다시 붙여 넣으세요."
    return ""


def main() -> None:
    reload_env()
    url = get_secret("DATABASE_URL") or ""
    if "[YOUR-PASSWORD]" in url or "DB비밀번호" in url:
        st.error("DATABASE_URL에 비밀번호 자리 표시([YOUR-PASSWORD])가 그대로 있음. "
                 "대괄호까지 지우고 Supabase DB 비밀번호를 넣은 뒤 저장하세요.")
        return
    try:
        pool = get_pool()
    except Exception as e:
        st.error(f"DB에 연결하지 못함({type(e).__name__}). DATABASE_URL과 Supabase 상태를 확인하세요.")
        hint = _db_error_hint(str(e))
        if hint:
            st.caption(hint)
        st.caption("비밀 값: " + ", ".join(f"{k} {v}" for k, v in secret_status().items()))
        return
    if pool is None:
        st.error("DATABASE_URL이 없음. .env 또는 Streamlit Secrets에 넣어 주세요.")
        st.caption("비밀 값: " + ", ".join(f"{k} {v}" for k, v in secret_status().items()))
        return
    if not get_secret("ADMIN_PASSWORD"):
        st.warning("ADMIN_PASSWORD가 없어 관리자 계정을 만들지 못함")

    user = ss.get("user")
    if not user:
        login_page()
        return
    if not services.account_exists(pool, user["id"], user["is_admin"]):
        logout()
        ss.result = "막힘: 명단이 바뀌어 다시 로그인해야 함"
        st.rerun()

    head, out = st.columns([8, 1])
    with head:
        with st.container(key="whoami"):
            if user["is_admin"]:
                st.markdown(f"### 👤 {user['name']} (admin)")
            else:
                st.markdown(f"### 👤 {user['name']} ({user['student_no']})")
    with out:
        st.button("로그아웃", key="logout", on_click=logout, width="stretch")

    if user["is_admin"]:
        admin_view()
    else:
        student_view()


main()
