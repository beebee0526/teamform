# 팀 프로젝트 팀 편성 웹 앱

## 1. 배포 주소
https://teamform-2026.streamlit.app

## 2. PRD와 구현 계획의 위치
- `docs/PRD.md`
- `docs/구현 계획.md`

## 3. 고른 해결 방법
| 기능 | 고른 해결 | 구현 |
|---|---|---|
| 다른 사람의 변경 보기 | 어려운 해결 | 로그인한 화면 전체를 `st.fragment(run_every=3)`로 3초마다 다시 그림(새로 고침 단추 없음) |
| 필수 포함 역할을 지킬 수 없는 팀 | 어려운 해결 | 지원할 때 `남는 자리 < 못 채운 필수 역할 수`이면 막음(`rules.check_join`). 기획자·웹 프로그래머를 함께 할 수 있는 학생은 하나로만 셈 |
| 같은 팀에 대기 중인 지원이 여럿 | 어려운 해결 | 모든 팀원이 찬성한 순간 정원·주 역할·필수 역할 자리를 다시 검사(`services.vote`). 투표가 끝난 순서대로 처리 |
| 마지막 자리에 두 명이 동시에 들어감 | 어려운 해결 | `tf_members`의 `(team_id, slot_no)` 기본 키, `slot_no between 1 and 4`, `student_id unique`(`schema.sql`) + 쓰기 동작은 advisory lock으로 차례로 처리 |
| AI 응답이 늦을 때 | 어려운 해결 | 스트리밍 요청을 별도 스레드에서 받고, 모델마다 부른 때부터 120초 전체 시간을 재서 끊고 다음 모델로(`ai.call_model`) |
| 3차 모델까지 실패 | 어려운 해결 | 백트래킹으로 검사 다섯 가지를 모두 통과하는 배정을 모든 경우를 따져 찾음(`rules.solve`). 규칙을 지키는 배정이 없으면 AI를 부르지 않고 이유를 보여 줌 |
| AI 대기 중 화면 | 어려운 해결 | `assign_status`에 "2차 모델(…)의 응답을 기다리는 중… 35초 / 최대 120초"와 앞 모델의 실패 이유를 1초마다 보여 줌 |
| DB 일시정지 막기 | 어려운 해결 | GitHub Actions 예약 작업(`.github/workflows/keepalive.yml`)이 매일 09:17(KST) `tf_settings`에 질의 |

## 4. 쓴 모델 3개
1차 `nvidia/nemotron-3-super-120b-a12b:free`, 2차 `google/gemma-4-31b-it:free`, 3차 `nvidia/nemotron-3.5-lightning:free`

## 5. 오픈라우터 AI 모델 호출 실패 사례
시각은 모두 한국 시각이며, 관리자 화면의 "AI 호출 기록과 설정 확인"(DB `tf_ai_logs`)에도 같은 기록이 남음.

1. **1·2차 실패 뒤 3차 성공** — 쉬움 시나리오 6단계(10/22 01:40, 로컬 실행). 1차 모델이 HTTP 404(오픈라우터 쪽에서 모델을 찾지 못함)로 1초 만에 실패, 2차 모델이 HTTP 429(사용량 초과로 일시 거절)로 실패해 3차 모델로 넘어갔고, 3차 모델의 배정안이 검사 다섯 가지를 통과함(44초). 화면 `img/ai-fallback.jpg`
2. **3차까지 모두 실패해 앱이 스스로 배정** — 중간 시나리오 11단계(10/22 01:48, 로컬 실행). 1차 404, 2차 429에 이어 3차 모델이 76초 동안 생각한 뒤 빈 응답을 내서 실패. 앱이 모든 경우를 따져 찾은 배정을 보여 주었고, 확정한 4팀 모두 규칙을 지킴. 화면 `img/ai-all-failed.jpg`
3. **대기 중 화면** — 어려움 시나리오 15단계(10/22 01:55). 1·2차 실패 뒤 "3차 모델의 응답을 기다리는 중… 59초 / 최대 120초"가 보임(이 3차 모델도 85초 뒤 빈 응답으로 실패해 앱이 배정함). 화면 `img/ai-waiting.jpg`
4. 배포한 앱에서 쉬움 시나리오 6단계(10/22 03:00)에도 1차 404, 2차 429 뒤 3차 모델이 30초 만에 성공함

## 6. 클로드가 잘못된 추론이나 답변을 한 사례
1. **실행 중인 앱에 맞지 않게 코드를 고쳐 앱이 멈춤** — 로컬에서 앱이 `.env`를 읽지 못하는 문제를 고치려고, 클로드가 `db.py`에 `reload_env` 함수를 더하고 `streamlit_app.py`에서 그 함수를 가져오게 고침. 그런데 이미 실행 중이던 Streamlit은 예전 `db.py` 모듈을 메모리에 그대로 두고 있어서, 새로 고침하자 `ImportError: cannot import name 'reload_env' from 'db'`가 나며 앱이 멈춤(10/22 00:16, 화면 `img/claude-importerror.jpg`). 클로드는 "파일만 바꾸면 Streamlit이 다시 읽는다"고 잘못 가정했음. 앱 화면을 열어 오류를 보고 알아챘고, 앱을 다시 켜서(새 프로세스가 새 `db.py`를 읽음) 해결함
2. **시험 기대값을 잘못 세움** — 클로드가 "기획자·웹 프로그래머를 함께 하는 학생과 테스터만 있는 2명 팀에 보고서 작성자가 지원하면 막혀야 한다"는 시험을 썼으나, 공고 3장의 자리 계산(남는 자리 1개 ≥ 못 채운 필수 역할 1개)대로라면 막지 않는 것이 맞음. 시험이 실패해 공고를 다시 읽고 알아챘고, 앱 코드가 아니라 시험의 기대값을 고침. 이 밖의 사례는 `docs/개발 기록.md`

## 7. 앱의 문제점이나 개선 필요 사항
**1차 모델이 사실상 매번 실패해 AI 배정이 느리고 자주 앱 자체 배정으로 끝남** — 세 시나리오의 배정 단계(쉬움 6단계, 중간 11단계, 어려움 15단계)에서 1차 모델 `nvidia/nemotron-3-super-120b-a12b:free`가 매번 HTTP 404를, 2차 모델이 매번 HTTP 429를 냄. 3차 모델은 두 번 1분 넘게 생각한 뒤 빈 응답을 냄. 그래서 관리자가 결과를 30초~1분 반 기다리고, 중간·어려움은 앱 자체 배정으로 끝남(화면 `img/ai-all-failed.jpg`, `img/ai-waiting.jpg`). 지금 동작하는 무료 모델로 1·2차를 바꾸고, 404처럼 바로 실패하는 모델은 다음 배정 때 건너뛰게 고쳐야 함

---

## 실행 방법(로컬)
가장 쉬운 방법: 압축을 푼 폴더에서
- Windows: `setup_windows.bat` 더블클릭 → 메모장에 값 세 개 넣고 저장 → `run_windows.bat` 더블클릭
- Mac: `setup_mac.command` 더블클릭 → 편집기에 값 세 개 넣고 저장 → `run_mac.command` 더블클릭

직접 하려면:
```bash
pip install -r requirements.txt
cp .env.example .env      # 값을 채움(파일 이름은 정확히 .env)
streamlit run streamlit_app.py
```
앱이 시작할 때 `schema.sql`을 실행해 `tf_` 테이블을 만들고(이미 있으면 그대로), `ADMIN_PASSWORD`로 관리자 계정을 만들거나 갱신합니다.

## 배포(Streamlit Community Cloud)
1. 깃허브 비공개 저장소에 올림(`.env`가 올라가지 않았는지 `git status`로 확인)
2. Streamlit Workspace settings > Linked accounts > Private access "Connect here"
3. Create app: Branch `main`(또는 `master`), Main file path `streamlit_app.py`
4. Advanced settings > Secrets에 `.streamlit/secrets.toml.example`의 꼴로 세 값을 넣음
5. 배포 뒤 Settings > Sharing > "This app is public and searchable" > Save changes, 시크릿 창으로 로그인 화면 확인

### DATABASE_URL
Supabase 프로젝트 > Connect > **Session pooler**의 URI를 쓰세요(포트 5432, IPv4 지원). Direct connection(IPv6 전용)은 Streamlit Cloud에서 연결되지 않을 수 있습니다.

### DB 일시정지 막기
저장소 Settings > Secrets and variables > Actions > New repository secret에 `DATABASE_URL`을 넣고, Actions 탭에서 "Supabase keepalive"를 한 번 수동 실행(Run workflow)해 성공하는지 확인하세요. 그래도 마감 날(11/2)에는 배포한 앱으로 명단 올리기·지원·투표를 몇 번 해 두세요.

## 시험
연습용 시나리오 3개(부록 1~3)의 모든 단계와 추가 경우를 실제 PostgreSQL에서 확인합니다. **Supabase가 아닌 테스트용 DB**에서 돌리세요(tf_ 테이블을 지우고 다시 만듦).
```bash
TEST_DATABASE_URL="postgresql://postgres@localhost/tf_test" python tests/run_scenarios.py
```
확인하는 것: 단계마다 결과 안내의 첫 단어, 마감 뒤 팀이 없는 학생, 확정 뒤 규칙, 비밀번호 해시, 유일성 제약, 규칙을 지키는 배정이 없을 때, AI 1차 시간 초과 → 2차 검사 실패 → 3차 성공, 120초 전체 대기 제한(축소한 3초로)

## 보안
- 키·DB 주소·관리자 비밀번호는 코드에 없음. `.env`(로컬)와 Streamlit Secrets(배포)에서 `os.getenv` → `st.secrets` 순으로 읽음
- 화면에는 비밀 값이 "있음/없음"으로만 보임. 오류 안내에도 예외 종류만 보임
- 비밀번호는 bcrypt 해시(`tf_students.password_hash`)
- 관리자 기능은 관리자 화면에만 있고, 콜백에서 한 번 더 관리자인지 확인
- 모든 `tf_` 테이블에 RLS를 켜서 Supabase REST API(anon 키)로는 읽을 수 없게 함

## 제출용 압축 파일
폴더를 그대로 압축하지 말고 커밋한 파일만 압축합니다(`.env`가 들어가지 않게).
```bash
git archive --format=zip -o 학번.zip HEAD
unzip -l 학번.zip | grep -c "\.env$"   # 0이어야 함
```
