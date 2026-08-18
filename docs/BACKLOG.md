# 고칠 목록

2026-08-18 리팩토링 비판 웨이브(서버·노드·앱 3개 서브 + 회귀 검증)의 결과를 일감으로
굳힌 것. 각 항목은 비판 보고에서 위치까지 확인된 것만 담았다 — 추측으로 적은 줄이 없다.

읽는 법: **A는 이미 틀린 것**이라 먼저다. B는 다음 웨이브가 오면 실제로 갈라지는
자리다. C는 안 해도 당장 무해한 정리다. 맨 아래 "하지 않기로 한 것"은 제안이
나왔지만 반대 근거가 더 강했던 것 — 다시 제안하지 않는다.

이미 정해두고 안 만든 것(lead 안내, 소집 저장중 표시, 권한스코프)은 여기 없다.
[HANDOFF.md](HANDOFF.md)의 "정했으나 아직 안 만든 것"이 정본이다.

---

## A. 이미 틀린 것

### A1. 오류 매핑 드리프트 — 같은 조건이 409도 되고 404도 된다

- 위치: `fungis_server/app.py` 전반. 실증: `update_project`(113-118)는 없는 프로젝트에
  409, 바로 아래 `archive_project`(120-127)는 같은 조건에 404. `create_role`(287)의
  LookupError도 409로 뭉개진다.
- 원인: 같은 모양의 try/except 40여 벌을 손으로 베끼다 어긋났다. `except Exception →
  409, detail=str(error)`는 sqlite 오류·프로그래밍 버그까지 클라이언트 detail로 흘린다.
- 할 일: FastAPI 전역 exception handler로 `LookupError→404`, `ValueError→409`를 한
  곳에서 매핑하고, 엔드포인트의 try/except를 걷어낸다.
- 완료 기준: update/archive가 같은 조건에 같은 코드를 준다는 테스트. try/except가
  특별한 이유가 있는 자리에만 남는다.

### A2. 열람 경계 절반 누락 — attention·bookmarks·shared·work가 검사 없이 나간다

- 위치: `fungis_server/app.py` — `workspace_attention`(619-621), `bookmarks`(623-625),
  `timeline_pins`(653-655), `shared_values`(681-685), `work_items`(738-742),
  `GET /v1/messages`(527-531). 전부 caller 검사 없이 방 내용(pm_request 본문 포함)을
  돌려준다.
- 원인: 권한 웨이브가 timeline·message·send·members에만 caller를 얹었다. "어느 읽기가
  경계 안인가"를 감사할 방법이 코드에 없어서, 엔드포인트를 얹는 쪽이 매번 잊는다.
- 할 일: `guard_board_write`(app.py:450-454)와 같은 모양으로
  `guard_participant(workspace_id, caller)` 헬퍼를 만들고, 방 내용을 주는 모든 읽기가
  그것을 부르게 한다. 거절 문구는 기존 `participation_denied` 재사용.
- 완료 기준: 소속 아닌 에이전트가 위 여섯 경로에서 403 + 사유를 받는 테스트.
  "guard를 부르는가"가 곧 경계 감사 목록이 된다.

---

## B. 다음 웨이브에 갈라질 자리

### B1. lead 판정 SQL 4~5벌 (서버)

- 위치: `fungis_server/db.py` — `convened_leads`(583-590),
  `workspace_participant`의 HQ 분기(624-632), `is_any_lead`(648-653),
  `lead_of`(1108-1116), `connect_project` 인라인 검사(1144-1150).
- 문제: "소집된 방의 lead" 술어가 손 SQL 두 벌 + 조건 일부만 가진 변종 셋.
  `convened_leads`는 배달 명부고 `workspace_participant`는 읽기 허가라, 어긋나면
  **배달은 됐는데 못 읽는** 메시지가 생긴다. `is_any_lead`에 parent/archived 조건이
  없는 것이 의도인지(소집 안 된 방 lead도 명단은 본다) 코드에 안 적혀 있다.
- 할 일: "소집된 lead" 술어를 SQL 조각 하나로 빼고, 의도된 변종은 이름으로 가른다.
  `is_any_lead`의 의도를 주석으로 못박는다.
- 완료 기준: 술어 정의가 한 곳. convened_leads와 participant HQ 분기가 같은 조각을 쓴다.

### B2. 이름 해석이 세 층에 사본 (노드·서버)

- 위치: `fungis_node/agent_cli.py:759-773`(addressing),
  `fungis_node/pm.py:128-168`(_reference_id/_role_id/_recipient_id),
  `fungis_server/db.py:1698-1711`(_recipient_or_room_lead). 방 해석도 이중:
  `agent_cli.py:719-740, 851-871` vs `db.py:934-956`.
- 문제: "이 이름이 누구/어느 방인가"를 세 층이 각자 다른 규칙·다른 데이터로 푼다.
  `_reference_id`의 마지막 방어선 `_targets`는 앱 발송 경로에서 늘 비어 있다고 주석이
  자인한다(pm.py:147-152). 별칭 규칙이 바뀌면 세 곳을 고쳐야 하고 하나 빠지면
  --to와 --cc가 같은 이름을 다른 사람으로 푼다.
- 할 일: 정본을 서버로 선언한다(명부는 서버에 있다). CLI는 to/to-id/cc/cc-id 네
  버킷으로 분류만 하고 해석은 서버가 한다. 노드의 resolve_room/resolve_project와
  2단 폴백을 걷어낸다. 후보 목록을 보여주는 오류 문구만 노드에 남긴다.
- 완료 기준: 이름→신원 해석 코드가 서버 한 곳. 발송 한 건의 HTTP 왕복이 준다
  (지금은 hq 2회 + roles 1회 + 역할당 roles 1회).

### B3. errorMessage 단일 채널 (앱)

- 위치: `AppModel.swift:45, 441-444, 495-504` / 소비처 `ContentView.swift:55`,
  `BoardView.swift:158`, `ConveneSheet.swift:24`.
- 문제: 한 문자열을 세 화면이 공유한다. 보드 에러가 채팅 하단 캡슐에도 뜨고,
  스냅샷 도착 전에는 지워지지 않아 조용한 방에서는 무한히 남는다. `runBoard`는
  `String(describing:)`(enum 덤프)을 그대로 노출한다. `refreshBoard`의 `try?`는
  실패를 통째로 삼킨다.
- 할 일: 에러에 출처(보드/방/전송)를 갈라 화면마다 자기 것만 보여주고 자기 타이밍에
  지운다. `String(describing:)`을 사람 문구로. `refreshBoard` 실패를 말하게 한다.
- 완료 기준: 보드 조작 실패가 채팅 화면에 안 뜬다. 성공한 다음 조작이 이전 에러를 지운다.

### B4. isHQ 3벌 + 보드 상태 문자열 맨몸 (앱)

- 위치: isHQ — `ChatView.swift:207-209, 445-447`, `RolesView.swift:14-16` (글자까지
  동일한 lookup 3벌). 상태 문자열 — `BoardView.swift:84~556` 사이 10여 곳 +
  `Models.swift:472-475`(status/state 둘 다 String).
- 문제: HQ 판정 기준이 바뀌면 한 곳만 고쳐져 "빈 화면은 HQ 안내인데 입력창은
  수신자를 요구하는" 반쪽 HQ가 된다. 상태는 오타가 컴파일되고, 서버가 이름을 바꾸면
  카드가 모든 칼럼에서 조용히 사라진다(칼럼이 등호 필터라서).
- 할 일: `AppModel`에 `selectedProjectIsHQ` computed 하나. 상태는 rawValue enum +
  미지 값 허용 디코드로.
- 완료 기준: isHQ lookup이 한 곳. 상태 리터럴 비교가 enum으로 바뀐다.

### B5. 줄 프로토콜의 따옴표·행 조립이 렌더러마다 다름 (노드)

- 위치: `agent_cli.py` — `_quote`(889-891)는 티켓 제목(640)에만. 방 이름은
  611, 691, 705에서 escape 없이 `f'"{name}"'`. 열 계산도 board(두 칸 join)와
  state/members(_columns 너비 계산)가 다르다. `ticket_line`(874-881)은 자기 렌더
  출력물을 `startswith` 문자열 검색한다 — 행 형식이 바뀌면 에코가 소리 없이 퇴화.
- 할 일: 따옴표로 감싸는 모든 자리를 `_quote` 하나로. 행 하나를 만드는 함수를
  render_board와 ticket_line이 공유. 티켓 이름 맵도 `ticket_names` 하나로
  (지금 render_board 613-617이 따로 만든다).
- 완료 기준: 방 이름에 따옴표가 든 보드가 규칙("따옴표 안은 통으로")대로 파싱된다는
  테스트. 형식 변경 시 ticket_line이 같이 움직인다.

### B6. caller_id 들쑥날쑥 — shared·work가 PM 신원으로 간다 (노드)

- 위치: `agent_cli.py` main — history는 caller_id=binding에 주석까지 달았는데(1001-1002),
  shared(1086-1088)·work(1102-1105)·permission-gate/clear(467, 501)는 caller_id 없이
  생성 → PMClient가 pm_id로 대체(pm.py:34).
- 문제: 서버가 shared/work 읽기 검사를 조이는 날 이 명령들이 깨지거나, 더 나쁘게
  계속 PM 신원으로 검사를 우회한다. A2를 고치면 이 자리가 바로 터진다 — **A2와
  같은 웨이브로 묶어야 한다.**
- 할 일: `client_for(config, registry, binding, *, project=...)` 헬퍼 하나로 PMClient
  생성을 모으고 caller_id를 항상 싣는다.
- 완료 기준: main에 PMClient() 직접 생성이 없다. 전 명령이 자기 신원으로 간다.

### B7. web.py의 daemon 수명 관리 이탈 (노드)

- 위치: `fungis_node/web.py:30-72, 161-176, 315-338`. `_exit_after_response`는
  demo.py:199의 SIGTERM handler가 있어야 뜻대로 동작하는데 그 계약이 주석에만 있다.
- 문제: 다음 daemon 기능도 create_web_app 인자로 들어오고, demo↔web의 암묵 계약을
  모르는 수정이 shutdown을 고아 서버 상태로 되돌린다 — 40f7325가 잡은 병의 재발 경로.
- 할 일: 지문·shutdown·health 구성을 작은 모듈로 빼고 web은 제공자 하나만 받는다.
  demo와의 SIGTERM 계약을 코드 경계(타입/명시적 등록)로 옮긴다.
- 완료 기준: create_web_app 인자에서 daemon 관심사가 빠진다.

---

## C. 정리 (안 해도 당장 무해 — 큰 작업에 곁들일 것)

- **죽은 엔드포인트 둘**: `GET /v1/timeline/{principal_id}`, `GET /v1/attention/{principal_id}`
  (app.py:533-541). 테스트만 부른다. attention 변종 판정("자기가 답했나" vs "사람이
  답했나", db.py 2129 vs 2159)도 같이 정리된다. A2를 할 때 지우면 경계 감사 대상이 준다.
- **옛 세대 명령**: `read-current`/`reply-current`(cli.py:103-112, 252-290). docs/CLI.md에
  없고 새 CLI가 대체. 지우면 pm.py `send_as`의 위치 인자 잔재도 같이 죽는다.
- **hydration 복붙 5벌**: db.py의 recipients/references/tags/role_recipients 채우기
  (1908, 1955, 1982, 2143, 2174). `_hydrate(row, *, full=)` 하나로. `messages_after`의
  부분판이 의도인지도 인자 이름으로 적힌다. 이미 `in_reply_to_project_seq`가
  timeline(1894)에 빠진 실사례가 있다(죽은 경로라 안 터졌을 뿐).
- **`_work_dict`가 행마다 in-memory sqlite를 연다**(db.py:2320-2333).
  `datetime.fromisoformat` 두 줄이면 된다. `token_usage = None` 예약석도 같이 지울 후보.
- **pm.py 미호출 메서드**: `targets()`(170-171), `lead_of()`(457-467),
  `permission_request()`(606-611). `send_many`/`send_as` 페이로드 두 벌도 빌더 하나로.
- **앱 죽은 코드**: `Models.swift` `blocks`(471, 디코드만 하고 안 읽음),
  `DeliveryState`(408-420, 전체 미사용), `BoardView.swift` `projectName(of:)`(461-464),
  ConveneSheet의 죽은 분기(77-78)와 `}}` 절단 자국(116), 중복 doc 주석
  (BoardView:132-133, AppModel:544-547).
- **잇기 문구·경로 잔재**: BoardView:220 배너 "왼쪽 포트에 놓거나"는 드래그 세대의
  말이다(지금은 클릭). 잇기 완료 경로가 둘(포트 탭 485, "여기가 뒤" 365)이고 거절
  판정이 두 자리(363, 476)에서 각각 돈다. 476의 `linking!` 강제 언랩은 크래시 후보.
- **태그 필터 UX 구멍**: 트레이 미노출로 필터가 켜져도 표시·해제 수단이 없다
  (ChatView — contextFilter는 메시지 태그 버튼 :73으로 켜진다). 트레이 재마운트냐
  "필터 중 · 해제" 배너냐를 정해야 한다. 죽은 사슬 110줄(369-374, 1287-1398)의
  거취도 그 결정에 딸린다.
- **`messageTime`이 호출마다 formatter 생성**(ChatView:1275-1285). static 캐시 한 줄.
- **runBoard가 isMutating을 안 세움**(AppModel:495-504) — 보드 버튼 연타 경합.
  `connectTrack`(446-451)의 말없는 false도 같이.
- **legacy 폴백의 만료 조건 미기재**: format_bootstrap의 `.get` 폴백(398-406),
  compact_history의 전역 seq 폴백(917-918), LEGACY_* 묘비(88-115). stale 교체가
  자리잡으면 "새 CLI가 옛 서버를 만나는 창"이 닫히므로 회수 대상 — 그 사실을
  각 자리에 적어둘 것.
- **`"args" in locals()`**(agent_cli.py:1126) — 항상 참인 가드.

## 회귀 검증에서 나온 것 하나

- `4747b04`가 약속한 **우클릭 잇기가 조용히 사라졌다**(현 BoardView에 contextMenu 없음).
  목적은 클릭 방식이 대신 채우므로 복원할지 말지만 정하면 된다. 복원 안 하면
  그 커밋 본문이 남긴 약속의 소멸을 여기 기록한 것으로 갈음한다.

---

## 하지 않기로 한 것 — 다시 제안하지 않는다

- **db.py 기계적 도메인 5분할.** 코어(projects·board·roles·messages·participation)는
  진짜로 얽혀 있어 가르면 클래스 간 호출만 는다. 값이 있는 것은 (a) 결합 0인 덩어리
  (permission·bindings·bookmarks·shared·work·inbox)만 떼는 것과 (b) 코어 안에서
  정책(판정·거절 문구)과 저장(CRUD)을 가르는 것. 이 조건부로만 다시 논의한다.
- **한국어 UI 문구의 문자열 카탈로그.** 단일 사용자 도그푸딩에 지역화 대상이 없다.
  진짜 냄새는 혼용(같은 화면의 "Add role"과 "소집")이며, 고칠 거면 방향 통일이지
  추상화가 아니다.
- **agent_cli.py 즉시 분할.** 결이 또렷해서(parser/render/resolve/main) 가르는 것
  자체는 기계적이지만, B6(main 정리)을 먼저 해야 이사가 안전해진다. 순서만의 문제.
- **project_id/workspace_id/room 전면 개명.** churn이 값보다 크다. 새 코드에서
  네 번째 이름을 만들지 않는 선만 지킨다.
