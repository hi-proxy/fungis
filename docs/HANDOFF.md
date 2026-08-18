# fungis 개발 핸드오프

기준일: 2026-08-18
상태: 로컬 실사용 가능한 SwiftUI 개발 빌드

제품 명세: [PRODUCT_SPEC.md](PRODUCT_SPEC.md)
저장소 상태: 로컬 Git repository, branch `cross-project`. 구현 기준 SHA는 아래 착지 정보에 기록한다.

## 읽는 순서

1. 이 문서의 "제품 경계"와 "하지 말 것"
2. `git show 9f55888` — 직전 웨이브가 무엇을 왜 바꿨는지 커밋 본문에 있다
3. 필요한 범위만 "주요 코드 위치"에서 찾는다

## Git 착지 정보

- 원격 저장소: `git@github.com:hi-proxy/dispatch.git` (아직 push 하지 않음. 통짜 하나로 올린다)
- 기준 branch: `cross-project`
- 구현 기준 commit: `9f55888` (`feat: give cross-project work one board and one lead per room`)
- 런타임 DB, `.venv`, Swift 빌드 산출물, 로컬 권한 설정은 Git에서 제외

## 제품 경계

fungis는 이미 실행 중인 cmux 에이전트 터미널에 협업 기능을 붙였다 떼는 장비다.
에이전트 프로세스와 PTY를 소유하지 않으며 attach, detach, 앱 종료가 기존 터미널을
종료하지 않는다. 채팅 본문과 전달 상태의 SSOT는 fungis Server이고 터미널 호출은
본문 없이 inbox 존재만 알리는 고정 신호다.

```text
Global
├─ Projects (HQ 포함)
└─ Agents

Selected Project
├─ Chat
├─ Board   (HQ에서만)
├─ Roles
├─ Shared
└─ Work
```

- 프로젝트 하나가 하나의 채팅방이다.
- HQ는 만들지 않는다. 처음부터 목록에 있는 프로젝트 위 방이다.
- Agents와 PM 프로필은 전역이다.
- 메시지, 역할, Shared, Work는 프로젝트별이다.
- 같은 세션은 한 프로젝트에서 하나의 역할만 맡는다.
- 같은 세션이 서로 다른 프로젝트 역할을 동시에 맡는 것은 허용한다.
- 역할은 세션 교체 뒤에도 유지되는 메시지 주소다.
- 방마다 lead 하나. `workspace_roles.is_lead`의 부분 유니크 인덱스로 강제하며,
  옮길 때는 앞의 것을 먼저 비워 오류가 아니라 이동이 되게 한다.
- 방 열람은 필터가 아니라 경계다. timeline은 caller를 필수로 받는다. 선택으로
  두면 안 싣는 쪽이 곧 우회로가 된다. 참가 판정은 별도 명부 테이블이 아니라
  `db.workspace_participant()`가 `role_assignments`에서 파생한다 — 사람은 무조건
  통과하고, 에이전트는 그 방에 활성 배정이 있어야 통과한다. 실패는 403이며
  빈 목록으로 뭉개지지 않는다.

## 현재 완료 범위

- cmux Codex·Claude 세션 자동 발견과 기존 터미널 무중단 attach/detach
- 에이전트 process TTY와 cmux surface의 유일 매핑 검증
- 서버 inbox, WebSocket 알림, received/processed ACK와 재연결 커서
- running/idle/needs_input gate와 고정 pager 호출
- SwiftUI 앱의 프로젝트 생성·선택·이름 변경
- 프로젝트별 로컬 Git 저장소 선택·검증과 branch/SHA 파싱 기준 연결
- 전역 Agents와 모든 프로젝트 역할 소속 표시
- 프로젝트별 역할 생성·편집·삭제·할당·교체·이력
- 미할당 역할 메시지 대기와 다음 담당 세션 전달
- 선택적 1회 온보딩 프롬프트
- PM 및 역할 아바타
- 여러 수신자와 역할 주소를 지원하는 카드형 Chat
- 입력창 위 한 줄의 역할 참여자 칩과 수신자 선택
- 선두 `@역할 @역할 본문`의 역할 우선 해석과 Enter 전송, 일반 Enter 줄바꿈
- 원문 보존형 메시지 Pretty 줄바꿈·`**형광펜**`과 메시지별 원문/Pretty 토글
- 프로젝트 SSOT 메시지 북마크
- 메시지 사이 구간 경계로 저장되는 Timeline Pin, 화면에 보이는 divider만 점등하는 인스펙터 Pins 탭, 메시지 seq 기반 과거 페이지 점프와 최신 복귀 버튼
- 짧은 `fungis init` 호출문만 보내고 agent별 bootstrap API로 사용법·역할표를 읽는 Chat `Initialize`
- agent local name을 server principal ID로 변환하는 Reply/Request와 프로젝트 문맥 기억
- agent 공용 `history` 맥락 복원, 발신 저장 원문·track·tags echo, 무음절단 없는 20,000자 상한
- inbox stdout 단일 JSON, 사람 안내 stderr 분리, claim 누락 시 history 복구 규칙
- reply/request `--help`의 역할·track·tag·상속·프로젝트·저장 echo 예시
- reply/request 409의 `init` 선행 및 `history` 복구 안내
- PM confirm/direct/reference/ambient 알림 구분
- track, tags, reply context, 프로젝트 지정 Git의 실재 branch·commit만 허용하는 엄격 관심사 탐지·필터
- 폭 기반 태그 필터 `+` 펼치기와 여러 줄 `접기`
- Chat 최신 10건 즉시 표시, 이전 50건 백그라운드 병합, 상단 접근 시 50건 선로딩
- Shared key-value와 Work 보고·경과 시간
- 기존 `local` workspace를 기본 프로젝트로 승격하는 SQLite migration
- 채팅방 목록을 중심에 둔 사이드바. 프로젝트 아바타·저장소 branch·검색과 행
  context menu의 이름 변경·저장소 지정, 하단 agent 상태줄
- Pins·Roles·Shared·Work를 담는 우측 인스펙터와 시트로 여는 전역 Agents 화면
- 최신이 스크롤 원점이 되는 역순 타임라인. 과거를 앞에 붙여도 이미 배치된
  메시지를 다시 재지 않는다
- 방을 떠나도 최신 10건을 보관해 재진입 시 네트워크를 기다리지 않는 방별 캐시
- 프로젝트 전환 시 옛 스트림을 끊고 곧바로 새 방에 다시 붙는 재연결
- 방마다 1부터 세는 표시 번호. 저장과 정렬은 전역 seq를 쓰고 사람과 에이전트가
  부르는 번호만 방별로 보여준다
- 목록과 상세로 나눈 Agents 패널
- 새 메시지가 온 방에 점을 켜는 사이드바 표시
- 빈 프롬프트가 아닌 채 멈춘 세션을 표시하고 알리는 조작 대기 감지
- 권한 요청을 PM 앱에서 승인·거절하는 경로. 무엇을 요청하는지 도구와 입력을
  그대로 보여주며 터미널에는 아무것도 넣지 않는다. `PermissionRequest` hook 등록
  완료. 목록을 쌓지 않고 최신 하나만 입력창 위 레이어로 얹으며, 답을 받을 수 없는
  카드는 띄우지 않는다
- 참조(CC) 수신자. 듣기만 하는 주소로 배달되며 누가 참조됐는지 메시지에 표시하고
  stop hook을 지나도 표식이 유지된다
- 에이전트 규범: 보낸 메시지를 터미널에 다시 말하지 않기, 체인이 얼마나 돌았는지
  알리기, PM의 발화 언어를 따르기
- 방별 수신자 기억. 앱을 재시작해도 유지되고 비운 상태도 비운 채로 남는다.
  자동 선택이 기억을 덮지 않는다
- 프로젝트를 넘어 보는 HQ 방과 상황보드. 트랙·노드·간선이며 노드는 그 방이 올리고
  간선은 PM이 잇는다. 상태는 저장하지 않고 간선에서 뽑으며 잇기 전에 DFS로 순환을
  막는다
- 방당 lead 하나의 지정과 이동
- 한 터미널 창에 에이전트 하나. 새 세션이 창을 가져가면 앞의 것을 놓아준다
- daemon이 내려가면 서버도 같이 내려간다

## 실행

최초 설치:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

앱 빌드와 실행:

```bash
FungisMac/build-app.sh
open FungisMac/build/Fungis.app
```

릴리스 빌드는 `.build-release`를 scratch로 쓴다. 디버그 산출물과 섞으면 Foundation을
못 찾는다.

앱은 `http://127.0.0.1:8790/health`를 확인하고 control daemon이 없으면 다음과 같은
구성으로 자동 시작한다.

```bash
.venv/bin/fungis-node daemon --send
```

개발 포트:

- `127.0.0.1:8787`: fungis chat server
- `127.0.0.1:8790`: localhost control API

에이전트 터미널 연결은 앱의 글로벌 Agents 화면에서 수행한다. CLI 진단이 필요하면
`.venv/bin/fungis-node ui` 또는 `.venv/bin/fungis-node discover --diagnostic`을 쓴다.

## 하지 말 것

- **daemon을 손으로 띄우지 않는다.** 앱이 소유한다. 손으로 띄우면 앱이 자기 것이
  아닌 daemon을 보게 되고 조용히 어긋난다.
- **health 200을 근거로 쓰지 않는다.** 아무것도 보증하지 않는다. 앱이 실제로 쓰는
  길로 확인한다.
- 훅 경로는 `.claude/settings.local.json` 한 곳에만 등록되어 있다. 레포를 옮기면
  여기를 고쳐야 하며, 다른 settings 파일을 고쳐봐야 아무 일도 일어나지 않는다.

## 검증

현재 착지 시점에 다음을 통과했다.

### 에이전트 신호와 화면 파싱

에이전트 도구가 세션 신호를 직접 주면 그것을 쓴다. 화면을 읽어 상태를 추론하는
규칙은 provider와 버전에 따라 조용히 깨지고, 깨졌다는 것을 아무도 모른다. 지금
`prompt_ready`가 강건한 이유는 "빈 프롬프트인가"만 보기 때문이며, 더 읽으려 할수록
약해진다.

이 저장소에서 확인한 것은 다음과 같다.

- 세션 발견: hook의 `session_id`·`cwd`·`transcript_path`
- 세션과 창의 연결: `ps`에 나오는 `--session-id`로 tty를 찾는다
- 무엇을 기다리는지: `PermissionRequest` hook이 도구 이름과 입력을 준다
- 에이전트에게 전하기: stop hook의 `hookSpecificOutput.additionalContext`.
  `decision: block`은 화면에 오류로 뜨고 `systemMessage`는 컨텍스트에 닿지 않는다

### UI 성능 원칙

성능 변경을 판정할 때는 비교 대상을 **같은 조건에서 다시 측정한다**. 예전에
적어둔 수치를 기준선으로 그대로 쓰면 정반대 결론이 나올 수 있다.

사용자 입력처럼 매 타이핑마다 변하는 고빈도 상태는 가장 작은 컴포넌트에 격리한다.
입력 한 번이 타임라인·목록·파싱·필터·네트워크 상태의 재계산이나 재렌더를 유발해서는
안 되며, 긴 입력과 누적된 긴 이력을 함께 둔 상태에서 검증한다. 기능 구현 전 상태 변경의
전파 범위를 확인하고 고빈도 경로에는 전체 화면 상태를 두지 않는다.

```bash
.venv/bin/pytest -q
# 110 passed

cd FungisMac && swift test
# 15 passed

FungisMac/build-app.sh
# production build complete, ad-hoc signed Fungis.app
```

방 전환 성능은 메시지가 긴 방 재방문 기준 876ms에서 374ms로 줄었다. 타임라인을
역순으로 쌓아 과거를 앞에 붙여도 이미 배치된 행을 다시 재지 않게 한 결과다.
남은 374ms는 태그 트레이도 프리페치도 아니고 뷰 트리 구성 비용이다.

실행 중인 두 health endpoint도 `status: ok`를 반환했다. 실제 개발 DB에는 프로젝트
테이블과 `role_assignments.workspace_id`가 migration 되었으며 기존 역할·메시지 이력은
보존되었다.

## 주요 코드 위치

- `fungis_server/db.py`: SQLite schema, migration, 프로젝트·역할·PM·메시지 SSOT
- `fungis_server/app.py`: chat server HTTP/WebSocket API
- `fungis_node/cmux.py`: cmux 발견, lifecycle, surface 검증
- `fungis_node/supervisor.py`: watcher와 safe wake 수명 관리
- `fungis_node/web.py`: SwiftUI용 localhost control API와 snapshot
- `fungis_node/pm.py`: PM server client
- `scripts/hooks/fungis-hook.sh`: session-start와 stop hook 진입점
- `FungisMac/Sources/FungisMac/AppModel.swift`: 선택 프로젝트와 앱 상태
- `FungisMac/Sources/FungisMac/ContentView.swift`: 채팅방 목록 사이드바와 Agents 시트
- `FungisMac/Sources/FungisMac/ChatView.swift`: 역순 타임라인, 수신자, 관심사 필터, 우측 인스펙터
- `FungisMac/Sources/FungisMac/BoardView.swift`: HQ 상황보드의 트랙·노드·간선
- `FungisMac/Sources/FungisMac/RolesView.swift`: PM 카드와 역할 할당, lead 지정
- `FungisMac/Sources/FungisMac/AgentsView.swift`: 글로벌 세션과 프로젝트 소속

## 의도적으로 보류한 범위

- 프로젝트 삭제·archive lifecycle
- 인증과 권한 모델
- LAN 또는 인터넷 노출
- 두 번째 PC의 실제 운영 연결
- iTerm2와 Terminal.app adapter
- 토큰·비용의 신뢰 가능한 수집
- 앱 번들 내부 Python runtime과 LaunchAgent 패키징
- 장시간 soak 및 사람 입력 경쟁 조건 시험

현재 서버는 인증 없이 localhost만 신뢰하는 개발용이다. 인증과 TLS가 생기기 전에는
외부 인터페이스에 bind하지 않는다.

## 다음 작업 우선순위

제품 엔드스펙과 인수 조건은 [PRODUCT_SPEC.md](PRODUCT_SPEC.md)를 따른다. 다음 구조 작업은
아래 마일스톤 순서를 바꾸지 않는다.

1. M1 Server Extraction Ready: 설정·인증·membership·presence·backup
2. M2 실제 다중 클라이언트 LAN/VPN 검증과 soak
3. M3 Windows Node와 PowerShell/WSL terminal adapter
4. M4 Windows PM desktop client

실사용 중 발견한 UI 마찰은 불변조건을 깨지 않는 작은 변경으로 병행할 수 있다.
현재 열려 있는 것은 다음 셋이다.

- 사이드바 안읽음 배지와 마지막 메시지 미리보기. control API가 프로젝트별
  요약(`last_message_preview`·`last_at`·`unread_count`)을 주지 않아 미구현이다.
  UI 쪽은 행 구조가 이미 이를 받을 수 있다.
- 방 전환에 남은 374ms. 후보는 매 렌더마다 전체를 순회하는 `filteredTimeline`·
  `contexts`·`bookmarkedSequences`와 `MessagePrettyPrinter`의 재파싱이다.
- 터미널 어댑터 분리. cmux 의존이 `fungis_node/cmux.py`에 모여 있으나 인터페이스로
  갈라져 있지는 않다. 경계를 그으면 일반 터미널 지원과 M3 Windows 어댑터 자리가
  같이 생기고, 파싱 규칙이 어댑터별 책임이 된다.
- HQ 상황보드 실증. 코드와 테스트는 착지했으나 연결된 트랙이 0이라 실제 화면을
  본 적이 없다. 앱에서 HQ 소집을 한 번 돌려 노드·간선·순환 차단을 확인해야 한다.
- **HQ 방을 에이전트가 되읽지 못한다.** 열람 경계는 그 방의 활성 배정으로
  판정하는데 HQ에는 `workspace_roles`가 0건이라 어떤 에이전트도 HQ timeline에서
  403을 받는다(실측). `fungis ask`는 HQ에 글을 쓰지만 받은 lead는 그 방을 읽을 수
  없다. 지금 HQ 메시지가 0건이라 아직 안 터졌을 뿐이다. HQ 참가 판정을 따로
  두거나, 소집된 프로젝트의 lead를 HQ 참가자로 인정해야 한다.
- 빈 방 표시(`No messages`)는 프로젝트 삭제가 보류 범위라 실증하지 못했다.
  방향 의존을 코드에서 없애 두었으므로 새 프로젝트를 만들 일이 생기면 함께
  확인한다.
- 스탠드얼론 전환. 현재 제품은 cmux에 종속되어 있고, 그래서 이미 안정적인
  Claude Desktop·Codex Desktop 옆에서 설 자리가 애매하다. 지인에게 권할 수준이
  되려면 앱이 터미널을 직접 호스팅해야 한다. Codex·Claude 혼합 단체채팅이라는
  컨셉은 그대로 둔다.

기존 터미널 독립성, 메시지 SSOT, running 상태 무입력 원칙은 이후 변경에서도 유지한다.
