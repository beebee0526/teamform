# 팀 프로젝트 팀 편성 웹 앱

> ✏️ 표시가 있는 곳은 제출 전에 **직접** 채워야 합니다. 5~7항은 본인이 시험하며 찍은 화면·커밋으로만 적으세요(꾸며 내면 시나리오 밖 점수 0점, 공고 6.3절 20번).

## 1. 배포 주소
✏️ https://(앱 이름).streamlit.app

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
(Secrets의 `AI_MODELS`로 바꿀 수 있음. 바꿨다면 ✏️ 여기도 고칠 것)

## 5. 오픈라우터 AI 모델 호출 실패 사례
✏️ 관리자 화면의 "AI 호출 기록과 설정 확인"(DB `tf_ai_logs`)에 시각·모델·실패 이유가 남습니다. 실패가 기록된 화면을 `img/`에 저장하고 적으세요.
예: "1차 모델이 HTTP 429로 거절해 2차 모델로 넘어감(화면 `img/ai-429.png`)"

## 6. 클로드가 잘못된 추론이나 답변을 한 사례
✏️ 본인이 확인한 사례와 근거(화면 또는 커밋 해시)를 적으세요. `docs/개발 기록.md`에 이 코드를 만들며 실제로 발견해 고친 사례가 있으니, 본인이 직접 재현·확인한 경우에만 참고하세요.

## 7. 앱의 문제점이나 개선 필요 사항
✏️ 연습용 시나리오의 몇 단계에서 어떤 화면으로 발견했는지 적으세요("문제 없음"은 인정되지 않음).
후보(직접 확인 후 적을 것):
- AI 배정 중에는 관리자 화면이 최대 약 6분 동안 진행 안내만 갱신되고 다른 단추를 누를 수 없음
- 3초마다 다시 그리므로, 학생이 많은 수업에서 쓰면 DB 질의가 많아짐
- 앱이 다시 시작되면 진행 중이던 AI 배정이 끊겨 다시 눌러야 함

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
