# fungis 개발 핸드오프

기준일: 2026-08-19
상태: 로컬 실사용 가능한 SwiftUI 개발 빌드

---

# 8/19 재부팅 뒤 안 되던 것 — 고쳤다

**벽이 넷이었다.** 앞의 것을 고칠 때마다 다음 것이 드러났다. 넷 다 같은 모양이다 —
**화면도 `/health` 도 멀쩡한데 안 된다.**

| | 벽 | 어디를 고쳤나 |
|---|---|---|
| 1 | daemon 이 cmux 실행 파일을 못 찾는다 | fungis 코드 (`2398fc4`) |
| 2 | 찾아도 cmux 소켓이 daemon 을 거부한다 | **cmux 설정** (저장소 밖) |
| 3 | 게이트 루프가 죽어도 아무도 모른다 | fungis 코드 (`1517c7f`) |
| 4 | 죽은 tty 이름표로 깨우기가 전부 막힌다 | fungis 코드 (`23927f6`) |

**4 가 PM 이 겪은 "메시지가 안 닿는다" 의 진짜 원인이다.** 1·2 는 그 앞을 막고
있던 것이고, 3 은 이런 일이 또 생겼을 때 사람이 먼저 알지 않게 하려고 중간에
끼운 것이다.

## 복구 확인 (12:31~12:32)

```
12:31:40 -> 12:32:21  processed  c00cddca
12:31:22 -> 12:32:25  processed  b9fe2c97   <- 10:40 부터 막혀 있던 것
12:31:20 -> 12:31:54  processed  05e9b33a
```

크래시 순간에 잡혀 안 풀리던 클레임도 게이트가 다시 돌자 복구 경로가 알아서
처리했다. DB 를 손으로 건드리지 않았다.

---

## 벽 4 — 죽은 tty 이름표 (`23927f6`)

깨우기가 한 건도 안 나갔다. 게이트는 2초마다 멀쩡히 돌면서 매번 이걸로 거부했다.

```
binding target failed PID/TTY verification: agent_tty_surface_tty_mismatch
```

fungis 는 깨우기 전에 "이 표면이 정말 그 에이전트의 터미널인가"를 tty 로 대조한다.
엉뚱한 터미널에 타이핑하지 않으려고 넣은 검사다. 그 대조가 이렇게 어긋나 있었다.

```
훅이 기록한 에이전트   pid 4087 -> ttys007   (실재)
cmux 가 말하는 표면    B453B3C9 -> ttys100   (/dev 에 없다)
```

**cmux 가 복원한 표면은 재부팅 전 tty 이름을 그대로 들고 있다.** 없는 이름과
살아 있는 프로세스를 견주면 영원히 안 맞는다.

### 표면 id 는 멀쩡했다 — 읽기로 증명

```
$ cmux read-screen --surface 8C7EBED9-...
Fermenting… (15s)     ← 내 지금 화면이 그대로 왔다
fungis-2
```

그래서 cmux 를 다시 띄우지 않았다. 그러면 에이전트 세션을 전부 잃는데, 잃어서
얻는 것이 이름표 하나 갱신이다.

**검사를 없애지 않았다.** 표면의 tty 가 `/dev` 에 살아 있는데 안 맞으면 여전히
거부한다 — 터미널이 진짜 옮겨간 경우다. 죽었을 때만 훅이 적은 표면 id 를 정본으로
본다. 그 id 는 에이전트 자신이 적은 것이라 죽은 이름표보다 낫다. codex 쪽에 이미
있던 `codex_process_tty_surface` 와 같은 판단이다.

## 벽 3 — 게이트가 죽어도 health 는 초록 (`1517c7f`)

`sends_wakes` 는 설정값이라 스레드가 죽어도 `true` 로 남고, `stale` 은 소스
지문이라 무관하다. 그 루프가 34분간 멈춰 있었는데 앱은 그 daemon 을 정상으로 보고
그대로 물었다. 로그는 앱이 버려서(`FileHandle.nullDevice`) 아무 데도 안 남았다.

- supervisor 가 한 바퀴를 **끝까지** 돈 뒤에만 시각을 남긴다
- 한 바퀴가 터졌다고 루프를 끝내지 않는다 — cmux 가 잠깐 막혔을 때 실제로 끝났다
- `/health` 가 `gate_age_seconds` 를 낸다. 못 읽으면 `None` (0 으로 뭉개면 죽은
  루프가 살아 보인다)
- 앱은 60초를 넘으면 갈아치운다. `None` 은 안 갈아치운다 — 이 칸이 없는 옛
  daemon 과 아직 첫 바퀴를 못 돈 daemon 이 같이 걸려 재시작 루프가 된다

---

## 벽 2 — cmux 소켓이 daemon 을 거부한다 (이게 진짜였다)

```
$ curl http://127.0.0.1:8790/api/state
{"detail":"Error: ERROR: Access denied - only processes started inside cmux can connect"}
```

cmux 는 유닉스 소켓으로 조종하고 접근을 모드로 가른다. 공식 스키마 기준:

```
automation.socketControlMode
  enum     off · cmuxOnly · automation · password · allowAll ·
           openAccess · fullOpenAccess · notifications · full
  default  "cmuxOnly"
automation.socketPassword   default ""
```

**기본값 `cmuxOnly` 는 cmux 안에서 시작된 프로세스만 붙게 한다.**
`fungis-node daemon` 은 `Fungis.app` 이 띄운다 — cmux 자손이 아니라 거부된다.
에러 문구가 이 모드를 그대로 말한다.

### 고친 방식 — `~/.config/cmux/cmux.json` (fungis 코드 변경 0)

```jsonc
"automation": {
  "socketControlMode": "password",
  "socketPassword": "1234"
}
```

cmux CLI 가 `--password` → `CMUX_SOCKET_PASSWORD` → **Settings 에 저장된 값**
순으로 찾는다. 마지막 폴백 덕분에 daemon 이 아무 환경변수 없이도 붙는다.
반영은 `cmux reload-config` (앱 재시작 불필요).

**값이 하나 있다**: 이 기계의 다른 로컬 프로세스도 이 파일을 읽으면 cmux 를
조종할 수 있다. PM 이 알고 고른 것이다. 비밀번호는 임시값이다.

### 여기서 크게 헤맸다 — 검증이 무효였던 이유

벽 1 을 고치고 "정상화 끝났다"고 보고했는데 **틀렸다.** 검증을 내 셸에서 했고
**내 셸은 cmux 안에 있다.** 그래서 `discover_agents()` 가 에이전트 6개를 찾았다.

`env -i` 로 환경변수는 지웠지만 **프로세스 혈통은 못 지운다.** 앱이 띄운 daemon 은
`Fungis.app` 자손이라 같은 코드가 거부당했다.

> **cmux 를 건드리는 것은 이 저장소 안에서 검증할 수 없다.**
> 반드시 도는 daemon 에 물어야 한다 — `curl http://127.0.0.1:8790/api/state`.
> 여기 `agents` 가 차 있으면 진짜 통과한 것이다.

### 확인한 것

```
$ curl http://127.0.0.1:8790/api/state
에이전트 6개 — claude ttys100/026/015/016/102/104
$ curl http://127.0.0.1:8790/health
{"status":"ok","sends_wakes":true,"stale":false}
$ fungis board        20 tickets, 5 waiting
```

---

## 벽 1 — daemon 이 cmux 실행 파일을 못 찾는다 (`2398fc4`)

### 원인 — 앱이 받는 PATH

cmux 실행 파일은 앱 번들 안에 있다.

```
/Applications/cmux.app/Contents/Resources/bin/cmux
```

셸은 프로필이 PATH 에 넣어 줘서 보이지만, Finder 나 로그인 항목으로 뜬 GUI 앱은
최소 PATH(`/usr/bin:/bin:/usr/sbin:/sbin`)만 물려받는다. **이 daemon 을 띄우는
것이 그 앱이다.** 재부팅 전에 됐던 것은 그때 앱이 셸에서 떴거나 daemon 이 이미
살아 있었기 때문이고, 재부팅이 그 우연을 지웠다.

### 고친 방식

`CmuxAdapter`가 생성 시점에 한 번 푼다 — PATH 다음에 아는 번들 자리
(`resolve_cmux`, `cmux.py`).

**검사만 고치면 안 된다.** 어댑터가 `self.executable` 로 cmux 를 부르는 자리가
여섯이라, 시작 검사만 통과시키면 실제 호출에서 죽는다. 그게 이 검사가 애초에
막으려던 "초록불인데 아무것도 안 되는 상태"다.

못 찾으면 이름을 그대로 돌려준다. **조용히 성공시키지 않는다** — 시작 검사가
지금처럼 걸려서 죽어야 한다.

### 검증 — 실패하던 그 환경으로

```
$ env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin ...
최소 PATH 에서 which: None
resolve_cmux()      : /Applications/cmux.app/Contents/Resources/bin/cmux
어댑터가 든 값      : /Applications/cmux.app/Contents/Resources/bin/cmux
discover_agents()   : 에이전트 6개 발견
```

검사 통과만 본 것이 아니라 **그 경로로 cmux 를 실제로 불러 에이전트를 찾는
것까지** 확인했다. `pytest 175`.

## 크래시가 fungis 탓인가 — 지금까지 나온 것

**단정할 근거가 없다.** 사용자 공간 앱(Python daemon · SwiftUI)이 macOS 를
패닉시키는 것은 일반적으로 드라이버·하드웨어 쪽 일이라 가능성이 낮다.

재부팅 8분 뒤 실측한 부하가 비정상으로 높았다.

```
load average  5.89  24.91  19.31   (uptime 8분)
상위 프로세스  WindowServer 50.5% · logioptionsplus_agent 16.4% · cmux 16.1%
```

fungis 는 이때 떠 있지도 않았다. **Logitech Options+ 와 WindowServer 가 눈에
띄고, Karabiner 도 돈다.** 조사한다면 이쪽이 먼저다.

- 볼 곳: `/Library/Logs/DiagnosticReports/` (커널 패닉은 시스템 쪽에 쌓인다.
  사용자 쪽 `~/Library/Logs/DiagnosticReports/` 가 아니다). 이번에는 접근이
  막혀 목록을 못 읽었다 — 손으로 확인이 필요하다
- 재부팅 시각: `last reboot` 기준 2026-08-19 19:40

---

제품 명세: [PRODUCT_SPEC.md](PRODUCT_SPEC.md)
보드 프로토콜: [BOARD_PROTOCOL.md](BOARD_PROTOCOL.md) — 에이전트가 보드를 읽고 쓰는 법
에이전트 CLI: [CLI.md](CLI.md) — 동사 셋, 주소 넷, 자리별 기본 수신자
고칠 목록: [BACKLOG.md](BACKLOG.md) — 리팩토링 비판 웨이브의 일감. 위치·완료 기준까지
저장소 상태: 로컬 Git repository, branch `cross-project`. 구현 기준 SHA는 아래 착지 정보에 기록한다.

# 2026-08-19 착지

## 이날 고친 것

| commit | 무엇 |
|---|---|
| `74617e9` | 역할을 **화면에 보이는 대로**(`@이름`) 칠 수 있게. HQ 타임라인 `to` 가 `claude-난수` 로 뜨던 것도 같은 뿌리라 함께 |
| `6e6c351` | 아무도 안 받았을 때 그렇다고 말한다. 일반 방 `send` 는 지목이 없으면 아무도 안 받는데 성공 출력이 실패처럼 안 보였다 |

`pytest 173` · `swift 26` · 앱 번들 갱신됨.

둘 다 **루프 손 시행 중에 실사용으로 드러난 것**이다. 앞의 것은 내가 `state`
화면에서 읽은 값을 그대로 쳤다가 `409 FOREIGN KEY constraint failed` 를 맞았고,
뒤의 것은 한 리드가 세 번 연속 아무에게도 안 가는 메시지를 보냈다.

**공통 교훈**: 화면이 보여주는 형태와 칠 수 있는 형태가 다르면 베껴 친 사람은
이유를 알 수 없다.

## 루프 시행 기록 — 이 저장소 밖에 있다

`~/kr.homil/dispatch-ops/` (git 밖)

- `LOOP.md` — 운영 루프 설계 (8/18, 485줄, "남은 미결: 없음")
- `LOOP-RUN-1.md` — 첫 시행 완결본. ARCH-3/TICKET-044, **완주** (잎 6·약 1시간·정지 0회)
- `LOOP-RUN-2.md` — 둘째 시행(문서 대수선 에픽) 진행 중. **정지 1시간 사례**
- `RETRO-hq.md` · `CROSS-PROJECT.md` — HQ 웨이브 회고와 기획

### 두 시행이 같은 결핍의 두 얼굴을 보여줬다

```
044 ③  걸음이 보고 왕복보다 짧다   정지와 작업 중 구분 불가  → 오탐 핑 2회
045 ①  착수만 선언하고 턴이 없다   일이 있는데 턴이 없음     → 진짜 정지 1시간
```

044 에서는 일하고 있는데 죽은 줄 알았고, 045 에서는 죽어 있는데 일하는 줄 알았다.
**밖에서 보이는 침묵 하나로 둘을 가를 수 없다.**

그리고 archivia 와 mei 가 서로 모르고 같은 값을 가리켰다 — archivia 는 정지를 겪고
`wake --in`(자기 예약)이 그 자리라고 했고, mei 는 같은 날 `next_wake_at` 이 어댑터
경계에서 제일 값진 값이라고 했다. **둘이 같은 값이다.**

→ 루프 v0 의 항목 둘(상태 판정기 · wake 예약)이 하나로 접힌다.
어댑터가 내는 것은 상태 5종이 아니라 **`(state, next_wake_at)` 쌍**이다.

## PM 답을 기다리는 것

1. **`LOOP.md` 반영 여부** — v0 상태 판정기의 출력에 `next_wake_at` 추가하고,
   어댑터 경계의 질문을 "밀어 넣어도 되나"에서 **"언제 반응하나"**로 바꿀지.
   mei 에서는 앞 질문의 답이 항상 예라서 뜻이 없다. 045 정지 1시간이 실측 근거다
2. ~~보조인력의 승인 층위~~ — **PM 과 동등한 권한이 기본**이다 (8/19 답).
   `r2`·`r3` 를 가르지 않는다
3. ~~보조인력이 맥인가 윈도우인가~~ — **윈도우 작업자, 추후 WSL 환경까지** (8/19 답).
   앱이 SwiftUI 라 화면이 없는 것이 벽이고 M1 인증이 선행이다
4. ~~cmux PATH 폴백~~ — 넣었다 (`2398fc4`)

## 미보고

에픽 초안(`EPIC-participation.md`)과 보드 등록(`FUNG-3`~`FUNG-8`) 결과를 PM 에게
아직 못 보냈다. **서버가 내려가 있어 보낼 수 없었다.** 서버가 살아나면 이것부터
보낸다.

---

## 읽는 순서

1. 이 문서의 "제품 경계"와 "하지 말 것"
2. `git log --oneline cfcf170~1..` — 직전 웨이브가 무엇을 왜 바꿨는지 커밋 본문에 있다
3. 필요한 범위만 "주요 코드 위치"에서 찾는다

## Git 착지 정보

- 원격 저장소: `git@github.com:hi-proxy/fungis.git`
- 기준 branch: `cross-project`
- 구현 기준 commit: `23927f6` (`fix: a surface whose tty device is gone is still the agent's terminal`)

### 브랜치 운용

- **main 이 바깥 얼굴이다.** 웨이브가 끝나면 통짜 한 커밋을 만들어 main 에만
  push 한다. 작업 브랜치는 push 하지 않는다. 두 번째 웨이브부터 `--squash` 는
  항상 충돌한다 — 첫 통짜에서 이력이 갈라져 같은 변경이 두 번 온 것으로 보인다.
  내용을 다시 풀지 말고 작업 브랜치의 트리를 그대로 커밋으로 만든다:

  ```bash
  NEW=$(git commit-tree cross-project^{tree} -p main -F msg.txt)
  git update-ref refs/heads/main "$NEW"
  git diff main cross-project --stat   # 비어야 한다
  ```
- **작업 브랜치는 실험 노트다.** 시행착오 커밋까지 그대로 남긴다 — 이 문서와
  BACKLOG 가 그 커밋 본문을 참조한다. 지우면 다음 맥락의 읽을거리가 죽는다.
- **`cross-project` 는 2026-09-02 만료다.** HQ 웨이브가 닫혔고 당분간 이대로
  쓰기로 했다. main `3de4f19` 가 같은 트리를 통짜로 담고 있으므로 코드는 잃을
  것이 없다. 만료로 잃는 것은 시행착오 커밋 본문뿐이며, 그 요지는 main 통짜
  본문과 이 문서에 남아 있다. 보드의 FUNG 티켓이 만료 알림이다. 그 전에 새
  웨이브를 시작하면 main 에서 새 브랜치를 파고 이것은 그때 지워도 된다.
- **dev 브랜치를 두고 main 에 merge 커밋을 반복하는 방식으로 돌아가지 않는다.**
  예전 이력이 `Merge branch 'dev'` 로 도배됐던 그 방식이다. 브랜치는 웨이브
  단위로 만들고, 끝나면 squash 로 main 에 얹고, 노트로 남긴다.
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
  통과하고, 에이전트는 그 방에 활성 배정이 있어야 통과한다. HQ(`kind='hq'`)만
  규칙이 하나 더 있다. 소집된 어느 방에서든 lead인 에이전트는 통과한다. HQ에는
  역할이 0건이라 배정만 보면 아무도 못 읽는다. 더하는 것이지 대신하는 것이
  아니어서 HQ에 직접 역할을 가진 경우도 그대로 통과한다. 실패는 403이며
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
- reply/send/request `--help`의 주소·track·tag·상속·기본 수신자·저장 echo 예시
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
  간선은 PM이 잇는다. 상태는 저장하고 간선에서 뽑는 것은 `waiting` 하나뿐이며
  잇기 전에 DFS로 순환을 막는다
- 방당 lead 하나의 지정과 이동
- 방마다 1부터 세는 티켓 번호와 방 프리픽스를 앞에 붙인 티켓 이름 `ARCH-12`.
  프리픽스는 방을 만들 때 붙고 방 이름을 바꿔도 따라가지 않으며 HQ는 `HQ`를 쓴다
- 보드를 JSON이 아니라 줄 프로토콜로 읽는 `fungis board`. `blocks` 역방향을 같이
  실어 끝낸 직후에 누구에게 알릴지가 같은 줄에 있고, 모든 명령이 바뀐 줄 하나를
  돌려주므로 보드를 다시 읽지 않는다. `board start/done/wait/unwait`은 `ARCH-12`도
  `12`도 받고 방을 고르는 자리는 프리픽스를 그대로 받는다
- 보드 첫 열은 `예정`. `대기`는 선행에 막힌 것만 가리킨다
- HQ에서는 수신자를 지정하지 않는 것이 곧 전원이다. 받는 사람은 서버가 소집된
  방들의 lead 명부에서 채운다. 읽는 쪽도 같은 명부로 열리며, 소집되지 않은
  에이전트는 그대로 403이다
- 한 터미널 창에 에이전트 하나. 새 세션이 창을 가져가면 앞의 것을 놓아준다
- daemon이 내려가면 서버도 같이 내려간다
- 수신자 없는 글. 일반 방에서는 그대로 저장되고 아무도 배달받지 않으며 아무도
  깨어나지 않는다. 저장·조회·타임라인·웹소켓의 0명 경로를 테스트로 못박았다
- 수신자 자리가 방 이름을 받는다. 아는 신원이 아니면 방 id·프리픽스·이름 순으로
  풀어 그 방 lead에게 보낸다. HQ에서 남의 방을 지목하는 길이다
- 방 안의 표시 번호로 글 하나를 꺼내는 `history --ref`. 열람 경계를 그대로 지난다.
  받는 쪽 메시지에도 `in_reply_to`를 실어 참조 사슬을 따라갈 수 있다
- 쓰기 권한 둘. 남의 방에 글을 쓰는 것은 그 방 소속만, 남의 방 명부를 보는 것은
  lead만. 보드 쓰기는 그 방 lead와 PM으로 좁히고 읽기는 열어 둔다. 노드 수정과
  간선 끊기는 actor를 필수로 받는다. 거절은 한 군데서 만들며 왜 막혔는지와 다음에
  무엇을 할지를 같이 준다
- 동사 셋으로 가른 에이전트 CLI. `reply`는 답하고 `send`는 자리에 붙이며
  `request`는 요청한다. `reply`만 참조가 위치 인자고 `send`·`request`에서는
  `--reply` 플래그다
- 주소 넷으로 통일. `--to`·`--cc`가 역할을 받고 `--to-id`·`--cc-id`가 절대 id를
  받는다. `--to`는 기본 수신자를 좁히며 더하지 않는다. 약자는 `-p -t -c` 셋뿐이다
- 부작용 없이 처지만 읽는 `fungis state`. 역할을 가진 방 전부, 또는 한 방의
  역할·담당자·lead. `NONE`은 값이 비었다는 뜻이고 `-`는 해당 없음이라는 뜻이다
- 옛 문법은 별칭으로 남기지 않고 무엇으로 바뀌었는지 한 줄로 알려주고 멈춘다.
  `ask`는 없앴다 — `send --project HQ --to <방>`이 같은 일을 한다. `init`이
  돌려주는 사용법도 새 문법으로 바꿨다
- **lead 지정·해제 안내.** lead 로 세워지거나 내려갈 때 그 에이전트에게 한 번 간다.
  소집 모달에서 여럿을 세워도 **모달이 닫힐 때 한 번에** 보낸다 — 즉시 보내면 모달
  안에서 lead 를 바꿨을 때 앞사람이 이미 안내를 받아 놓고 lead 가 아니게 된다.
  둘 다 "회신 불요"를 붙인다. 안 붙이면 소집 한 번에 방마다 "알겠다"가 돌아온다.

  ```
  너는 ARCHIVIA bookclub 프로젝트의 lead 로 선택되었다.
  HQ 는 lead 들이 소속된 상위 프로젝트이다.

    fungis history 20 --project HQ            읽는다
    fungis send --project HQ "..."             발행한다. lead 전원이 받는다
    fungis send --project HQ --to <project> "..."   그 프로젝트 lead 에게만
    fungis board                               상황보드를 읽는다
    fungis board add "..."                     네 프로젝트 트랙에 올린다
    fungis board start / done <ticket>
    fungis board wait / unwait <ticket> <blocker>

  HQ 글은 네 프로젝트 타임라인에 안 뜬다. 전할지는 네가 정한다.
  회신 불요.
  ```

  해제 시에는 두 줄만 보낸다.

  ```
  너는 더 이상 ARCHIVIA bookclub 프로젝트의 lead 가 아니다.
  HQ 접근이 닫혔다.
  회신 불요.
  ```

  전문을 보내지 않는 이유: 세션은 기억을 이어가므로 매번 설명을 실으면 이미 아는
  것을 계속 다시 산다. 명령만 주면 현황과 사용법은 알아서 부른다.
- **소집 모달의 저장중 표시.** lead 를 세우는 동안 표시가 없다. `AppModel.isMutating`
  이 이미 있으니 `ConveneSheet` 이 그것을 쓰면 된다. 취소는 만들지 않는다 — 누르는
  즉시 반영되므로 닫는 것이 곧 확정이다.

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
- **앱 창을 닫았다 여는 것으로는 서버 코드가 안 갈린다.** 앱은 이미 도는 daemon을
  다시 쓰고, 서버는 그 daemon의 자식이다. 그래서 파이썬을 고치고 앱만 다시 열면
  화면은 새것인데 서버는 옛 코드로 답한다. 오늘 세 번 이 자리에서 시간을 버렸다.

  고친 코드가 실제로 도는지는 **시각으로 확인한다.** 서버 기동 시각이 커밋보다
  앞서면 그 코드는 안 돌고 있다.

  ```bash
  pgrep -f 'fungis_server.main' | head -1 | xargs -I{} ps -o lstart= -p {}
  git log -1 --format=%cd
  ```

  특히 `init`이 에이전트에게 주는 사용법 문자열은 **서버가 만든다.** CLI를 아무리
  고쳐도 서버가 옛 프로세스면 에이전트는 계속 옛 문법을 배우고, 배운 대로 쳐서
  실패한다. CLI 쪽에서 나오는 항목만 새것으로 섞여 보여서 더 헷갈린다.
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
# 160 passed

cd FungisMac && swift test
# 21 passed

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

### 잡아 둔 에픽 — 사람과 여러 클라이언트

초안: [EPIC-participation.md](EPIC-participation.md) · 보드: `FUNG-3` ~ `FUNG-8`

보조인력(PM2) 투입을 검토하다 나왔다. 같은 빈자리가 **두 번** 드러났다 — HQ 를
만들 때 한 번(구성원이 역할이 아니라 소집된 방의 lead 라 특례 분기를 넣었다),
사람을 방 하나에만 넣으려 할 때 또 한 번. 빠진 개념은 **참가**다.

`db.py:601` 주석이 스스로 그렇게 말한다 — "참가 = 역할 보유로 본다. 지금 모델에서
방에 있다는 것을 말하는 다른 수단이 없다."

`membership(workspace_id, principal_id)` 을 일급으로 놓으면 특례 둘(human 무조건
통과 · HQ 분기)이 **지워진다.** 코드가 느는 게 아니라 준다. 잎 순서와 닫힘 판정은
초안 문서에 있다. **착수 전이고 PM 승인 대기다.**

한 가지는 임시로 하지 않기로 했다 — 사람을 `role_assignments` 에 먼저 앉혀 두고
나중에 걷어내는 것. 굳으면 훨씬 비싸다. **PM2 를 넣는 시점 = membership 하는
시점**으로 묶는다.

### 정했으나 아직 안 만든 것

문구와 동작까지 합의됐고 구현만 남았다. 다시 논의하지 말고 그대로 만든다.

- **매니페스트의 권한스코프.** lead 안내에 "하위 에이전트의 권한스코프"를 실기로 했으나
  지금 제품에 권한 개념이 역할 배정밖에 없어 실을 것이 없다. 권한 모델이 생길 때 함께
  정한다. 그때까지 보류.

### 실사용에서 살펴야 할 것

- **소속 없는 직결 세션은 이제 글도 못 쓴다.** 읽기는 원래 403이었으므로 계약이
  일관돼진 것이지만, 역할 없이 붙여 쓰던 세션이 있었다면 그 자리가 막힌다.
- **새 서버 기능은 daemon이 새로 뜨기 전까지 없다.** 티켓 프리픽스 migration도 그때
  돌고, `fungis state`는 그 전까지 404다(`/v1/workspaces/{id}/members`를 모른다).
  앱 창만 다시 여는 것으로는 부족하다 — 위 "하지 말 것"의 시각 확인을 쓴다.
  이 문서를 쓰는 시점에도 서버는 아직 옛 코드로 돌고 있었다.

### 열려 있는 것

실사용 중 발견한 UI 마찰은 불변조건을 깨지 않는 작은 변경으로 병행할 수 있다.

- 사이드바 안읽음 배지와 마지막 메시지 미리보기. control API가 프로젝트별
  요약(`last_message_preview`·`last_at`·`unread_count`)을 주지 않아 미구현이다.
  UI 쪽은 행 구조가 이미 이를 받을 수 있다.
- 방 전환에 남은 374ms. 후보는 매 렌더마다 전체를 순회하는 `filteredTimeline`·
  `contexts`·`bookmarkedSequences`와 `MessagePrettyPrinter`의 재파싱이다.
- 터미널 어댑터 분리. cmux 의존이 `fungis_node/cmux.py`에 모여 있으나 인터페이스로
  갈라져 있지는 않다. 경계를 그으면 일반 터미널 지원과 M3 Windows 어댑터 자리가
  같이 생기고, 파싱 규칙이 어댑터별 책임이 된다.
- HQ 실증. 서버·CLI·앱이 다 붙었지만 사람이 실제로 돌려본 적이 없다. 소집을 한 번
  하고 나면 다음이 한꺼번에 확인된다 — 보드의 노드·간선·순환 차단, lead 안내가
  가는 것, lead 가 HQ 를 읽는 것, `send --project HQ` 가 전원에게 가는 것,
  `--to <방>` 이 그 방 lead 하나로 좁혀지는 것.
- 빈 방 표시(`No messages`)는 프로젝트 삭제가 보류 범위라 실증하지 못했다.
  방향 의존을 코드에서 없애 두었으므로 새 프로젝트를 만들 일이 생기면 함께
  확인한다.
- 스탠드얼론 전환. 현재 제품은 cmux에 종속되어 있고, 그래서 이미 안정적인
  Claude Desktop·Codex Desktop 옆에서 설 자리가 애매하다. 지인에게 권할 수준이
  되려면 앱이 터미널을 직접 호스팅해야 한다. Codex·Claude 혼합 단체채팅이라는
  컨셉은 그대로 둔다.

기존 터미널 독립성, 메시지 SSOT, running 상태 무입력 원칙은 이후 변경에서도 유지한다.
