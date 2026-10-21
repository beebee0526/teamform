-- 팀 편성 앱(중간 과제)의 테이블. 모든 테이블 이름은 tf_ 로 시작한다.
-- 앱이 시작할 때 이 파일을 그대로 실행하므로(create ... if not exists),
-- 이 파일이 곧 앱이 실제로 쓰는 테이블, 열, 제약이다.
-- Supabase SQL 편집기에 붙여 넣어 직접 만들어도 된다.

-- 학생과 관리자. 비밀번호는 bcrypt 해시(password_hash)로만 저장한다.
create table if not exists tf_students (
    id            bigserial primary key,
    student_no    text not null unique,
    name          text not null,
    main_role     text check (main_role in ('기획자', '웹 프로그래머', '프론트엔드 디자이너', '테스터', '보고서 작성자', '발표자')),
    password_hash text not null,
    is_admin      boolean not null default false,
    created_at    timestamptz not null default now(),
    -- 학생은 주 역할이 꼭 있고, 관리자(admin)만 주 역할이 없다
    constraint tf_students_role_required check (is_admin or main_role is not null)
);

-- 보조 역할. 학생 1명에 0~2행
create table if not exists tf_student_sub_roles (
    student_id bigint not null references tf_students(id) on delete cascade,
    role       text not null check (role in ('기획자', '웹 프로그래머', '프론트엔드 디자이너', '테스터', '보고서 작성자', '발표자')),
    primary key (student_id, role)
);

-- 팀. id가 곧 팀 번호(팀1 → 1)이며 화면 key team_1, apply_1 의 번호와 같다
create table if not exists tf_teams (
    id   integer primary key check (id >= 1),
    name text not null unique
);

-- 팀원.
--  (team_id, slot_no) 기본 키 + slot_no 1~4 check: 한 자리에 한 명만, 다섯 번째 자리 없음.
--    마지막 자리에 두 명이 동시에 들어가면 먼저 도착한 학생만 기록된다(3장 어려운 해결).
--  student_id unique: 한 학생은 한 팀에만 소속
create table if not exists tf_members (
    team_id    integer not null references tf_teams(id) on delete cascade,
    slot_no    integer not null check (slot_no between 1 and 4),
    student_id bigint  not null unique references tf_students(id) on delete cascade,
    joined_at  timestamptz not null default now(),
    primary key (team_id, slot_no)
);

-- 지원
create table if not exists tf_applications (
    id         bigserial primary key,
    team_id    integer not null references tf_teams(id) on delete cascade,
    student_id bigint  not null references tf_students(id) on delete cascade,
    status     text not null default '대기' check (status in ('대기', '승인', '거절')),
    reason     text,
    created_at timestamptz not null default now(),
    decided_at timestamptz
);
-- 한 학생은 결과를 기다리는 지원을 하나만 가질 수 있다
create unique index if not exists tf_applications_one_pending
    on tf_applications (student_id) where status = '대기';
create index if not exists tf_applications_team_status
    on tf_applications (team_id, status);

-- 투표. 한 사람은 한 지원에 한 번만 투표한다(바꿀 수 없음)
create table if not exists tf_votes (
    application_id bigint not null references tf_applications(id) on delete cascade,
    voter_id       bigint not null references tf_students(id) on delete cascade,
    vote           text not null check (vote in ('찬성', '거부')),
    created_at     timestamptz not null default now(),
    primary key (application_id, voter_id)
);

-- 설정: phase(open/closed/confirmed), round(명단을 올릴 때마다 1 증가),
--       draft(검사를 통과한 배정안 JSON), assign_status(마지막 배정 결과 JSON)
create table if not exists tf_settings (
    key   text primary key,
    value text
);

-- AI 호출 기록(README의 실패 사례 근거로 씀)
create table if not exists tf_ai_logs (
    id          bigserial primary key,
    created_at  timestamptz not null default now(),
    round       integer,
    attempt     integer,
    model       text,
    outcome     text,
    elapsed_sec numeric(7, 1),
    detail      text
);

-- Supabase는 public 스키마의 테이블을 REST API(anon 키)로도 내보낸다.
-- 행 수준 보안(RLS)을 켜고 정책을 두지 않으면 REST API로는 읽고 쓸 수 없다.
-- 앱은 테이블 주인(postgres)으로 직접 접속하므로 RLS의 영향을 받지 않는다.
alter table tf_students          enable row level security;
alter table tf_student_sub_roles enable row level security;
alter table tf_teams             enable row level security;
alter table tf_members           enable row level security;
alter table tf_applications      enable row level security;
alter table tf_votes             enable row level security;
alter table tf_settings          enable row level security;
alter table tf_ai_logs           enable row level security;
