"""설정 값 읽기와 DB 연결.

- 키와 비밀번호는 코드에 적지 않는다. 로컬에서는 .env, 배포한 앱에서는 Streamlit Secrets에서 읽는다.
- DB 연결은 연결 풀(ConnectionPool) 하나를 앱 전체가 다시 쓴다(공고 4.2절).
  화면을 한 번 그릴 때는 연결 하나를 빌려 여러 질의를 한다.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from psycopg_pool import ConnectionPool

APP_DIR = Path(__file__).resolve().parent
ENV_FILE = APP_DIR / ".env"


def reload_env() -> None:
    """.env를 다시 읽는다. 앱을 켠 뒤 .env를 고쳐도 앱을 다시 켜지 않고 반영된다(배포 앱에는 .env가 없음)."""
    if ENV_FILE.exists():
        load_dotenv(ENV_FILE, override=True)


reload_env()


def get_secret(name: str) -> str | None:
    """os.getenv → Streamlit Secrets 순으로 찾는다. 값은 절대 화면이나 로그에 출력하지 않는다."""
    value = os.getenv(name)
    if value:
        return value
    try:
        import streamlit as st

        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return None


def secret_status() -> dict[str, str]:
    """키가 있는지만 '있음/없음'으로 알려 준다(값은 보여 주지 않음)."""
    names = ("OPENROUTER_API_KEY", "DATABASE_URL", "ADMIN_PASSWORD")
    return {n: ("있음" if get_secret(n) else "없음") for n in names}


def make_pool(database_url: str) -> ConnectionPool:
    pool = ConnectionPool(
        conninfo=database_url,
        min_size=1,
        # 학생 여러 명이 동시에 화면을 다시 그려도 기다리지 않게 넉넉히(Supabase 풀러 한도 안)
        max_size=10,
        # Supabase 연결 풀러(6543, transaction 모드)는 prepared statement를 지원하지 않으므로 끈다
        kwargs={"prepare_threshold": None, "connect_timeout": 10},
        # 풀러가 오래 쉰 연결을 끊어도 빌려줄 때 다시 확인한다
        check=ConnectionPool.check_connection,
        max_idle=300,
        open=False,
        name="tf_pool",
    )
    pool.open(wait=True, timeout=30)
    return pool


def init_schema(pool: ConnectionPool) -> None:
    sql = (APP_DIR / "schema.sql").read_text(encoding="utf-8")
    with pool.connection() as conn:
        conn.execute(sql)
        conn.execute(
            "insert into tf_settings(key, value) values ('phase', 'open'), ('round', '0') "
            "on conflict (key) do nothing"
        )
