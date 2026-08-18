# Dispatch 제품 명세

문서 상태: 제품 SSOT 초안 1.0

기준일: 2026-08-15

구현 기준: `main` commit `944890a`

다음 목표: Server Extraction Ready → Multi-client 검증 → Windows Node

이 문서는 Dispatch가 최종적으로 지켜야 할 제품 계약을 정의한다. 현재 코드의 편의나
POC 한계를 최종 제약으로 간주하지 않는다. 실행 방법과 현재 착지 상태는
[HANDOFF.md](HANDOFF.md)를 따른다.

## 1. 명세 표기

- **불변조건**: 이후 구현과 플랫폼이 반드시 지켜야 하는 경계다.
- **CURRENT**: 2026-08-14 macOS 개발 빌드에서 동작하고 검증된 범위다.
- **M1**: 서버 모듈을 별도 장비로 즉시 분리할 수 있게 만드는 다음 단계다.
- **M2**: 두 대 이상의 실제 PC에서 공용 서버를 검증하는 단계다.
- **M3**: Windows Node와 터미널 어댑터 단계다.
- **M4**: Windows PM 클라이언트 단계다.
- **DEFERRED**: 방향을 확정하지 않았거나 선행 조건 뒤로 보류한 항목이다.

`MUST`, `MUST NOT`, `SHOULD`, `MAY`는 각각 필수, 금지, 권장, 선택을 뜻한다.

## 2. 제품 정의

Dispatch는 이미 실행 중인 터미널 에이전트에 협업 기능을 필요할 때 붙였다 떼는
지휘 장비다. 에이전트를 대신 실행하는 IDE나 오케스트레이터가 아니며, 채팅·역할 주소,
안전한 호출, 진행 가시성, 외부 기억을 제공한다.

핵심 경험은 다음과 같다.

1. 사용자는 평소처럼 터미널에서 에이전트 하나로 작업한다.
2. 필요할 때 기존 터미널을 종료하지 않고 Dispatch에 연결한다.
3. 역할을 만들고 현재 세션을 담당자로 배정한다.
4. PM과 여러 에이전트가 하나의 프로젝트 채팅 이력을 공유한다.
5. 호출은 새 메시지가 있다는 사실만 알리고, 본문은 에이전트가 서버에서 조회한다.
6. 필요하면 Dispatch를 중단하고 원래 터미널 작업 방식으로 즉시 돌아간다.
7. 다른 PC의 작업자와 Node가 같은 서버에 참가해도 동일한 역할·대화 이력을 사용한다.

## 3. 제품 불변조건

### 3.1 터미널 독립성

- Dispatch는 에이전트 프로세스나 PTY를 소유하지 않는다.
- attach와 detach는 기존 터미널 또는 에이전트를 종료하거나 재시작하지 않는다.
- 앱·Node·서버 장애가 기존 터미널 작업을 중단시켜서는 안 된다.
- 사용자는 언제든 실제 에이전트 터미널로 이동해 직접 보고 입력할 수 있어야 한다.
- 터미널 binding이 유일하게 검증되지 않으면 자동 입력을 보내서는 안 된다.

### 3.2 메시지 SSOT

- 메시지 본문, 수신자, 참조자, 역할 수신, reply 관계, track, tags와 전달 상태의
  SSOT는 Dispatch Server다.
- 터미널 호출 신호와 WebSocket 이벤트에는 메시지 본문이나 미리보기를 넣지 않는다.
- 연결이 끊겨도 history와 inbox로 메시지를 복구할 수 있어야 한다.
- 중복 신호는 허용하지만 메시지·ACK·온보딩의 중복 효과는 허용하지 않는다.

### 3.3 역할 주소

- 메시지의 지속 주소는 교체 가능한 세션이 아니라 프로젝트 역할이다.
- 역할 담당 세션이 교체되어도 역할로 보낸 대화와 대기 메시지는 유지된다.
- 같은 세션은 한 프로젝트에서 동시에 하나의 역할만 맡을 수 있다.
- 같은 세션이 서로 다른 프로젝트 역할을 동시에 맡는 것은 허용한다.
- 미할당 역할로 보낸 메시지는 버리지 않고 다음 담당자에게 전달한다.

### 3.4 입력 안전성

- `running`·`unknown` 상태에는 자동 터미널 입력을 보내지 않는다.
- `needs_input`은 일반 빈 프롬프트임이 읽기 전용 검증된 경우에만 호출할 수 있다.
- 사용자 타이핑, 권한 확인, 선택 화면과 경쟁해서는 안 된다.
- 여러 새 메시지는 하나의 pager 호출로 병합한다.
- dangerous 권한을 가진 에이전트라도 Dispatch의 입력·승인 경계를 우회하지 않는다.
- 에이전트가 무엇을 기다리는지는 화면 파싱보다 에이전트가 알리는 신호를 우선한다.
  화면 판정은 신호가 없을 때의 마지막 수단이며, 판정할 수 없으면 `unknown`으로
  두고 입력하지 않는다.
- 승인은 사람이 한다. PM이 명시적으로 누른 답만 에이전트에게 돌려주며, 기다리다
  시간이 지나면 아무 판단도 하지 않고 비켜서 터미널에서 처리하게 둔다.

### 3.5 PM 통제와 원문 보존

- PM이 입력한 메시지, 역할 프롬프트, Shared 값은 묵시적으로 재작성하지 않는다.
- Pretty 표시는 화면 변환일 뿐 서버 원문을 바꾸지 않는다.
- 역할 온보딩 프롬프트는 PM이 직접 확인·편집할 수 있어야 한다.
- 자동 추론은 명시적 track·tag를 추가·수정·삭제하지 않는다.

## 4. 핵심 용어

### Project

프로젝트 하나가 채팅방 하나다. Chat, Roles, Shared, Work, Timeline Pin과 Message
Bookmark는 프로젝트 범위다. 프로젝트를 늘리는 대신 한 방의 관심사는 track과 tag로
나눈다.

### PM

사람 지휘자다. PM 프로필은 전역이며 이름과 아바타를 프로젝트 사이에서 공유한다.
PM은 프로젝트 전체 타임라인을 볼 수 있지만 알림은 직접 수신·참조·승인 요청 여부에
따라 달라진다.

### Node

한 PC에서 동작하는 로컬 장비 프로세스다. 서버 연결, 로컬 terminal adapter, binding,
pending queue, safe wake, 로컬 Git 매핑과 control API를 소유한다. Node는 다른 PC의
터미널을 직접 제어하지 않는다.

### Agent Session

Codex·Claude 등 실제 실행 중인 한 에이전트 세션이다. 세션 ID와 터미널 ID는 진단용
기술 식별자이며 장기 메시지 주소가 아니다.

### Role

프로젝트 안의 지속 업무 주소다. 이름, 아바타, 온보딩 프롬프트, 현재 assignment와 과거
assignment 이력을 가진다.

### Assignment

역할과 실제 에이전트 세션을 일정 기간 연결한 기록이다. 시작·종료 시각, 담당자,
온보딩 전송 여부를 보존한다.

### Track과 Tag

- `track`: 메시지의 주 작업 흐름 하나다. 보통 브랜치나 주요 작업 단위다.
- `tag`: 티켓, commit, 검토 단계 등 보조 인덱스 여러 개다.
- 부모 답장은 기본적으로 부모의 track과 tags를 상속한다.
- 새 독립 흐름의 첫 메시지만 명시하고 이후 답장은 상속하는 것이 기본 운용이다.

### Message Bookmark

특정 메시지 자체를 저장하는 PM 기능이다. 클릭하면 해당 메시지로 이동한다.

### Timeline Pin

두 메시지 사이의 구간 경계다. `after_message_seq`에 라벨을 저장하며 메시지 북마크와
합치지 않는다. 채팅 본문에는 divider로, 우측 rail에는 이동 인덱스로 표시한다.

## 5. 시스템 경계

```text
┌──────────────────── Dispatch Server ────────────────────┐
│ identity · membership · projects · roles · messages    │
│ inbox/ACK · history · bookmarks/pins · shared · work   │
│ node presence · websocket events                        │
└─────────────────────────┬───────────────────────────────┘
                          │ authenticated HTTP/WebSocket
             ┌────────────┴────────────┐
             │                         │
┌──────────── Node A ───────────┐  ┌── Node B / Windows ─────────┐
│ local control API            │  │ local control API            │
│ terminal adapter · gate      │  │ terminal adapter · gate      │
│ local registry · pending     │  │ local registry · pending     │
│ local project Git mapping    │  │ local project Git mapping    │
└────────────┬─────────────────┘  └────────────┬─────────────────┘
             │                                 │
       existing terminals                existing terminals
```

### 5.1 Server 소유 상태

Server는 다음을 권위 있게 저장해야 한다.

- 사용자·Node·에이전트 principal과 프로젝트 membership
- 프로젝트, 역할, assignment와 온보딩 전송 기록
- 메시지, recipient/reference/role delivery, reply metadata
- inbox received/processed cursor와 event cursor
- Attention 요청과 해결 관계
- Message Bookmark와 Timeline Pin
- Shared key-value와 version
- Work 시작·보고·종료 이력
- M1부터 Node와 binding presence의 마지막 heartbeat

Server는 cmux, Windows Terminal, 로컬 PTY, 로컬 Git 경로, SwiftUI에 의존해서는 안 된다.

### 5.2 Node 소유 상태

Node는 다음 로컬 상태와 권한을 소유한다.

- 영구 Node identity와 인증 credential
- 이 PC의 에이전트 session ↔ terminal binding
- 로컬 pending queue와 마지막 server event cursor
- 에이전트가 읽은 메시지 claim과 completion 매칭
- terminal provider의 발견·focus·safe wake
- 에이전트가 보내는 세션 신호의 수신과 보관
- 프로젝트 ID ↔ 이 PC의 Git checkout 경로 매핑
- localhost control API와 로컬 진단 로그

Node registry는 Server SSOT의 대체 사본이 아니다.

### 5.3 PM 클라이언트 경계

- macOS SwiftUI 앱은 localhost control API에만 연결한다.
- PM 앱은 terminal control credential이나 adapter socket을 직접 소유하지 않는다.
- Server 주소 교체는 Node 설정으로 수행하며 앱 재빌드를 요구하지 않아야 한다.
- M4 Windows PM 클라이언트도 같은 control API 계약을 사용한다.

## 6. 메시지와 호출 계약

### 6.1 발신

메시지는 다음 정보를 원자적으로 저장한다.

- project, sender, body, kind, reply level
- direct recipients, role recipients, references
- `in_reply_to`, track, tags, inherit-context 여부
- 생성 시각과 단조 증가 `seq`
- 방마다 1부터 세는 표시 번호

`seq`는 저장과 정렬에 쓰는 전역 단조 번호이고, 표시 번호는 방 안에서 빈틈없이
이어진다. 사람과 에이전트가 부르는 번호는 언제나 표시 번호다. 전역 번호를
그대로 노출하면 한 방만 보는 참여자에게는 번호가 띄엄띄엄해 보이고, 그것을
메시지 누락으로 읽어 불필요한 확인을 하게 된다. 이 제품은 맥락 없는 새 에이전트
세션에게 반복해서 쓰이므로 안내로 보완하지 않는다.

inbox·history·발신 echo의 번호, `history --after`·`history --ref`의 입력,
`reply`의 위치 인자와 `send`·`request`의 `--reply` 입력, PM 화면의 메시지·핀·
북마크 번호가 모두 표시 번호다. 저장된 참조는 전역
`seq`를 유지하고 경계에서만 변환한다.

본문은 최대 20,000자다. 초과 본문은 자르지 않고 요청을 거절한다. 성공 응답은 실제로
저장한 본문 길이와 routing metadata를 되읽을 수 있어야 한다.

### 6.2 수신 pager

```text
Server에 메시지 저장
→ 수신자 inbox event 생성
→ 담당 Node가 event를 로컬 pending에 영속 저장
→ received ACK
→ safe gate 통과 시 고정 pager 한 번 전송
→ 에이전트가 fungis inbox/history 조회
→ 해당 세션 claim 기록
→ 같은 세션의 정상 턴 완료 확인
→ processed ACK
```

고정 pager는 다음 의미만 가진다.

```text
[fungis] inbox — run: fungis inbox
```

서버 URL, registry 경로, recipient ID, 메시지 본문을 터미널에 삽입하지 않는다.

### 6.3 Inbox와 History

- Inbox는 수신자를 위한 개인 증분 우편함이다.
- History는 프로젝트 참여자가 필요할 때 읽는 공용 외부 기억이다.
- 전체 history를 모든 에이전트에게 자동 fan-out하지 않는다.
- inbox stdout은 정확히 하나의 JSON document만 출력한다.
- 사용법과 복구 안내는 stderr로 분리한다.
- 출력 처리 실패 시 history의 `after seq`로 복구할 수 있어야 한다.
- history 조회는 메시지를 소비하거나 삭제하지 않는다.

### 6.4 역할 전달

- 역할이 할당되어 있으면 현재 assignment의 세션에 delivery를 만든다.
- 역할이 미할당이면 role delivery를 pending으로 남긴다.
- 새 assignment는 미전달 role delivery만 받는다.
- 이전 담당자가 이미 받은 메시지를 새 담당자에게 자동 재전송하지 않는다.
- 역할 삭제는 미전달 메시지가 없어야 하며 이력 보존형 soft delete다.

### 6.5 Attention과 PM 관계

- `r1`: 정보 공유
- `r2`: 검토 요청
- `r3`: PM 확인이 필요한 결정 또는 위험 작업

PM 알림은 본문 추론이 아니라 서버 routing metadata로 결정한다.

- `CONFIRM`: PM에게 직접 온 r3 요청, 소리 있는 알림
- `DIRECT`: PM 직접 수신, 소리 있는 알림
- `REFERENCE`: PM 참조, 무음 알림
- `AMBIENT`: PM 비수신·비참조, 타임라인만 표시
- `SELF`: PM 자신의 발신, 알림 없음

### 6.6 권한 승인

에이전트가 터미널에서 권한 확인을 받으면 PM은 그것을 알 수 있어야 하고, 원하면
Dispatch에서 답할 수 있어야 한다. 터미널을 계속 띄워 두지 않으면 막힌 것을 모른
채 지나가기 때문이다.

- 무엇을 요청하는지는 에이전트 도구가 주는 신호에서 얻는다. 도구 이름과 입력을
  그대로 PM에게 보여준다.
- PM 화면에는 어느 세션이 무엇을 하려는지 드러난다. 승인 대상을 감추지 않는다.
- 승인과 거절은 신호 경로로 되돌린다. 터미널에 키를 넣지 않는다.
- 먼저 도착한 답을 지킨다. 사람이 누른 답과 시간 초과가 겹쳐도 뒤집히지 않는다.
- 답이 없으면 요청을 만료로 두고 비켜선다. 대신 승인하지 않는다.
- 자율 승인은 여전히 보류 범위다. 사람의 확인 없이 통과시키는 경로를 두지 않는다.

## 7. 역할·할당·온보딩

### 7.1 역할 카드

역할 카드에는 이름, 이니셜 또는 아바타, assignment 상태, 실제 세션 이름과 연결 상태를
표시한다.

- `ASSIGNED + ONLINE`
- `ASSIGNED + SESSION OFFLINE`
- `UNASSIGNED`

### 7.2 할당

- 할당 화면은 세션이 현재 어느 프로젝트·역할에 속했는지 표시한다.
- 같은 프로젝트의 다른 역할에 이미 할당된 세션은 재할당 경고가 필요하다.
- 다른 프로젝트 assignment는 유지한다.
- assignment 시작 순간부터 Work와 이력에서 담당 기간을 추적할 수 있어야 한다.

### 7.3 온보딩

- 역할은 PM 소유의 원문 onboarding prompt를 가질 수 있다.
- assignment 시 체크한 경우에만 한 번 전달한다.
- 동일 assignment 요청의 재시도는 중복 전달하지 않는다.
- 역할 안내와 CLI 사용법은 분리한다.
- CLI 사용법은 짧은 `fungis init --project ...` 호출 뒤 bootstrap API에서 읽는다.

## 8. Chat UI 계약

### 8.1 타임라인과 history

- 프로젝트 첫 진입은 최신 메시지 위치에서 시작한다.
- 최신 10건을 먼저 표시한다.
- 직후 이전 50건을 백그라운드로 병합한다.
- 상단 5건 근처에 도달하면 다음 50건을 선로딩한다.
- 페이지는 `seq`로 정렬·중복 제거한다.
- 핀·북마크 등 먼 위치 이동은 필요한 history를 먼저 로드한다.
- 긴 거리 이동은 중간 메시지를 애니메이션 렌더링하지 않고 즉시 점프한다.
- 최신 위치 복귀 버튼을 제공한다.
- 타임라인은 최신이 스크롤 원점이 되도록 역순으로 쌓는다. 과거를 앞에 붙일 때
  이미 배치된 메시지를 다시 재는 구조를 두지 않는다.
- 방을 떠나도 최신 10건은 보관해 다시 들어올 때 네트워크를 기다리지 않는다.
- 새 메시지는 사용자가 최신 위치에 있을 때만 따라 내려간다. 과거를 읽는 중이면
  현재 위치를 유지한다.

### 8.2 메시지 카드

메시지에는 최소한 다음을 표시한다.

- 발신자 프로필과 역할
- 생성 시각
- 수신 역할·직접 수신자
- 전달 대기 여부
- reply 관계
- track, tags, 검증된 Git context
- 메시지 `seq`

`processed`는 메시지별 주 시각 요소로 사용하지 않는다. 에이전트의 작업·대기·오프라인
상태는 참여자 영역에서 보여준다.

### 8.3 Pretty와 원문

- `■`, `①`~`⑳`, `✓` 등 구조 표식은 화면에서 읽기 좋은 줄바꿈으로 변환할 수 있다.
- `**문구**`는 별표 대신 안정적인 형광펜 스타일로 표시한다.
- 메시지마다 Pretty/원문을 독립 전환할 수 있다.
- 색은 메시지와 문구에서 결정되는 안정 값이며 재실행 시 바뀌지 않는다.
- 긴 입력 draft 상태는 타임라인 렌더 상태와 분리한다.

### 8.4 수신자 입력

- 참여자 역할 칩은 입력창 바로 위 한 줄에 둔다.
- 칩 선택과 직접 recipient 메뉴를 모두 지원한다.
- `@role1 @role2 본문`으로 시작한 draft는 Enter로 전송한다.
- 선두 mention은 본문에서 제거하고 역할명을 세션 별칭보다 우선 해석한다.
- 알 수 없거나 중복된 호칭은 발송하지 않고 오류를 표시한다.
- 일반 본문의 Enter는 줄바꿈이다.

### 8.5 Bookmark와 Timeline Pin

- Message Bookmark는 특정 메시지에 붙는다.
- Timeline Pin은 두 메시지 사이에 붙는다.
- gap hover의 `+ Pin`으로 구간 경계를 만든다.
- 우측 rail의 pin 행 전체 폭을 클릭 영역으로 보장한다.
- pin 클릭은 `after_message_seq`를 로드한 뒤 해당 위치로 이동한다.
- 도착한 채팅 본문의 divider가 한 번 깜박인다.
- divider가 현재 viewport에 들어와 있을 때만 rail에서 위치 점등한다.
- 삭제는 명시적 context menu 동작이다.

## 9. 관심사와 Git 추론

### 9.1 명시적 메타

- 필요한 branch, ticket, commit, review 상태는 가능한 한 track/tag로 명시한다.
- 새 흐름의 첫 메시지에만 메타를 주고 reply 상속을 사용한다.
- 메타는 채팅 본문에 규칙 문자열로 삽입하지 않는다.

### 9.2 자동 추론 엄격 모드

- 프로젝트에 이 PC의 Git checkout이 명시적으로 연결된 경우에만 추론한다.
- 다른 프로젝트 또는 다른 에이전트 cwd의 Git을 fallback으로 섞지 않는다.
- `/`가 포함된 branch는 저장소에 실제 존재하고 본문에 정확히 나타날 때만 인정한다.
- `main` 같은 일반 단어형 branch는 `branch:main`, `브랜치 main`, `` `main` ``처럼
  명시한 경우에만 인정한다.
- commit 후보는 `git rev-parse --verify <sha>^{commit}`을 통과해야 한다.
- ticket key와 미검증 hex는 자동 context로 만들지 않는다.
- 전역 필터에는 `verified == true`인 자동 context만 포함한다.

Git checkout 경로는 PC마다 다르므로 Server 프로젝트 데이터가 아니라 Node 로컬
매핑이다. M2에서는 같은 프로젝트의 여러 Node가 서로 다른 checkout 경로를 가질 수 있다.

## 10. Shared와 Work

### 10.1 Shared

- 프로젝트 범위 `key → text` 저장소다.
- 필요한 키만 선택 조회해 프롬프트 토큰을 아낀다.
- 수정 시 version을 증가시키고 삭제는 명시 동작이다.
- 토큰, 비밀번호, 개인키를 저장하는 비밀 저장소가 아니다.

### 10.2 Work

- 한 에이전트에는 active Work 하나만 허용한다.
- start, report, done 시각과 보고 원문을 보존한다.
- 역할 assignment 기간과 함께 세션의 업무 맥락을 추적할 수 있어야 한다.
- 신뢰할 수 없는 token 수치는 추정하지 않고 `unknown`으로 표시한다.

## 11. 토큰 절약 원칙

- pager는 본문을 포함하지 않는다.
- 새 에이전트 onboarding은 긴 채팅 메시지 대신 짧은 init과 최신 bootstrap API를 쓴다.
- 에이전트는 필요한 때만 project history를 읽는다.
- history 기본 조회량은 작게 유지하고 `after seq`로 복원한다.
- PM 앱은 10건 즉시 표시와 50건 페이지를 사용한다.
- track/tag/reply routing은 본문 밖 구조화 필드로 유지한다.
- Shared는 전체가 아니라 요청한 key만 반환한다.
- 자동 요약이 도입되더라도 원문 history를 대체하거나 삭제하지 않는다.
- 요약 모델 도입과 token accounting은 DEFERRED다.

## 12. M1 — Server Extraction Ready

M1의 목표는 현재 localhost 사용성을 유지하면서, Server 프로세스만 다른 장비로 옮겨도
코드 변경이나 앱 재빌드 없이 동작하게 만드는 것이다. M1 완료 전에는 non-loopback에
노출하지 않는다.

### 12.1 실행·설정 계약

Server는 다음 설정을 런타임에 받아야 한다.

- listen host와 port
- public server URL
- DB 경로 또는 DB connection 설정
- credential/secret 위치
- log level과 data directory
- migration·backup·restore 대상

Node는 server URL과 credential을 로컬 설정으로 받아야 한다. SwiftUI 앱은 여전히
localhost control API만 사용한다. localhost 단일 장비 모드는 별도 코드 경로가 아니라
같은 구성의 기본값이어야 한다.

### 12.2 인증과 권한

- non-loopback Server는 인증 없이는 시작할 수 없어야 한다.
- User와 Node credential을 분리한다.
- credential은 Node별 발급·폐기 가능해야 한다.
- 프로젝트 membership을 읽기, 발신, 역할 관리, PM 승인 권한과 연결한다.
- Server는 요청의 actor를 본문이나 클라이언트 제공 display name으로 신뢰하지 않는다.
- TLS는 필수다. 폐쇄 VPN에서 시작할 수 있지만 인증을 생략하지 않는다.
- Shared는 인증 뒤에도 secret vault로 승격하지 않는다.

구체적인 token 형식과 TLS termination 방식은 구현 설계에서 선택할 수 있지만 위 경계는
변경할 수 없다.

### 12.3 Node presence

Node는 Server에 다음을 heartbeat로 보고한다.

- node ID, client version, platform, last seen
- 연결된 agent principal과 binding revision
- lifecycle과 safe-wake 가능 여부
- assignment delivery를 받을 수 있는 상태

Server는 heartbeat 만료로 Node/Agent를 offline 처리한다. 다른 PC의 PM도 동일한 전역
presence를 보아야 하며, 로컬 Node에 없다는 이유로 원격 세션을 offline으로 표시해서는
안 된다. 원격 PM은 다른 Node의 terminal control 권한을 직접 얻지 않는다.

### 12.4 이벤트와 복구

- Node는 자기 binding에 필요한 inbox event만 받는다.
- event에는 본문이 아니라 recipient와 through-seq만 포함한다.
- 재접속은 마지막 event cursor와 Server cursor를 대조한다.
- received ACK 전에 Node pending 영속화가 완료되어야 한다.
- 동일 event, message send, assignment, onboarding 재시도는 멱등이어야 한다.
- Server·Node의 독립 재시작 순서를 모두 지원한다.

### 12.5 데이터 운영

- M1은 단일 Server 인스턴스와 SQLite를 허용한다.
- schema migration은 시작 전에 검증되고 실패 시 기존 DB를 손상시키지 않는다.
- backup은 일관된 snapshot을 만들고 restore 검증 명령을 제공한다.
- data directory와 runtime code를 분리한다.
- health는 프로세스 생존, readiness는 DB migration과 쓰기 가능 상태를 구분한다.
- 여러 Server 인스턴스와 PostgreSQL은 DEFERRED다.

### 12.6 M1 인수 조건

다음을 모두 만족해야 `Server Extraction Ready`다.

1. 기존 한 Mac localhost cold start가 동일하게 동작한다.
2. Server를 별도 LAN/VPN 장비에서 한 명령 또는 패키지로 실행한다.
3. Mac Node는 설정만 바꿔 원격 Server에 접속하고 앱 재빌드를 요구하지 않는다.
4. Server 설치물에는 cmux, SwiftUI, local terminal adapter 의존성이 없다.
5. 인증 없는 non-loopback bind가 거부된다.
6. Node token 폐기 후 해당 Node의 HTTP·WebSocket 재접속이 거부된다.
7. Server와 Node를 임의 순서로 재시작해도 message·cursor·pending이 복구된다.
8. backup → 빈 설치 restore 뒤 projects, roles, messages, pins, Shared, Work가 일치한다.
9. Server 장애가 기존 agent terminal을 종료하거나 입력하지 않는다.

## 13. M2 — 실제 다중 클라이언트 검증

검증 토폴로지는 최소 다음과 같다.

```text
공용 Server 1대
├─ Mac A: PM 1 + cmux agents 2
└─ PC B: PM 2 + agent 1
```

### M2 인수 조건

- 두 PM이 같은 프로젝트 history와 Attention 해결 상태를 본다.
- 세 에이전트의 전역 presence와 프로젝트 assignment가 양쪽 PM에 동일하게 보인다.
- 같은 로컬 세션 이름을 양쪽 Node에서 사용해도 principal이 충돌하지 않는다.
- 한 Node가 offline이어도 다른 Node와 Server는 계속 동작한다.
- offline Node의 역할 메시지는 유실되지 않고 재접속 뒤 전달된다.
- PM 동시 발신과 Attention 동시 해결은 일관된 결과를 낸다.
- 각 Node의 local Git 경로가 달라도 프로젝트 데이터는 충돌하지 않는다.
- LAN 단절·WebSocket 중복·재연결 동안 메시지 중복 처리와 누락이 없다.
- 최소 8시간 soak 동안 터미널 입력 경쟁과 watcher 누수가 없어야 한다.

M2의 첫 운영은 직접 인터넷 공개가 아니라 폐쇄 LAN 또는 VPN을 사용한다.

## 14. M3 — Windows Node

M3의 목표는 공용 Server와 프로토콜을 그대로 사용하면서 Windows PC의 기존 에이전트
터미널을 Dispatch에 연결하는 것이다. macOS UI나 cmux 동작을 Windows에 억지로 이식하지
않고 OS 중립 Node core와 terminal adapter를 분리한다.

### 14.1 Windows 범위

- Windows용 Node 설치·업데이트·자동 시작
- Node identity와 Server 인증
- native PowerShell agent와 WSL agent 등록
- Windows Terminal용 discovery/attach/focus/lifecycle adapter
- 소켓 기반 out-of-band pager를 우선 사용
- terminal 입력 fallback이 필요한 경우 macOS와 동일한 safe gate 적용
- local pending, cursor, claim, completion ACK 복구
- Windows와 WSL의 Git worktree 검색·검증
- Windows path와 WSL path의 명시적 로컬 매핑
- 프로젝트 역할 할당과 presence heartbeat
- localhost control API

터미널 어댑터는 다음을 각자 책임진다. 한 provider의 화면이나 명령이 바뀌어도
다른 어댑터가 깨지지 않아야 한다.

- 실행 중인 세션 발견과 안정 식별자
- 세션과 터미널 창의 연결
- lifecycle 판정
- 창 focus
- 사람 확인이 필요한 상황의 전달

에이전트가 세션 신호를 직접 주는 경우 그것을 우선한다. 그때는 화면 파싱과
터미널 입력이 모두 불필요하며, 이는 14.1이 소켓 pager를 우선한다고 정한 것과
같은 이유다.

### 14.2 Windows 터미널 원칙

- 기존 터미널을 종료해야만 attach할 수 있는 설계는 허용하지 않는다.
- 기존 세션을 유일하게 발견할 수 없으면 임의 binding하지 않고 명시적 선택을 요구한다.
- terminal title이나 PID 하나만 영구 identity로 사용하지 않는다.
- attach/detach와 Node 종료는 PowerShell·WSL·에이전트 프로세스를 종료하지 않는다.
- 사용자 입력 중 또는 agent running 상태에는 terminal injection을 금지한다.
- 실제 작업 출력은 기존 터미널에 계속 보여야 한다.
- 지원할 수 없는 기존 세션은 이유를 표시하고 wrapper-managed 신규 세션과 구분한다.

### 14.3 Windows/WSL 경계

- Server에는 Windows 또는 WSL의 절대 로컬 경로를 저장하지 않는다.
- Node는 `project ID → local checkout` 매핑을 플랫폼별로 관리한다.
- WSL distribution과 Windows host를 Node 내부에서 명시적으로 식별한다.
- 같은 repository의 Windows checkout과 WSL checkout을 동일 경로로 가정하지 않는다.
- Git 검증은 실제 agent가 작업하는 환경에서 수행한다.

### 14.4 M3 인수 조건

1. Windows Node가 공용 Server에 등록되고 heartbeat를 유지한다.
2. 이미 열린 지원 대상 PowerShell 또는 WSL agent를 종료 없이 attach/detach한다.
3. Mac PM이 Windows agent의 online/working/needs-input 상태와 역할을 본다.
4. PM 발신 → Windows pager → inbox → agent reply 왕복이 성립한다.
5. running 중 메시지 여러 건은 입력되지 않고 pager 한 번으로 병합된다.
6. Node 또는 Windows Terminal 재시작 뒤 pending과 binding을 안전하게 복구하거나
   명시적으로 재선택을 요구한다.
7. local Git branch·commit 추론이 strict Git 규칙을 동일하게 따른다.
8. Dispatch 제거·장애 뒤에도 기존 terminal agent를 계속 사용할 수 있다.

## 15. M4 — Windows PM Client

Windows PM Client는 M3 이후 별도 모듈이다.

- Server API에 직접 terminal 권한을 추가하지 않는다.
- Windows localhost control API와 동일한 snapshot/mutation 계약을 사용한다.
- Chat, Roles, Agents, Shared, Work의 의미와 상태 표현은 macOS와 같아야 한다.
- 프레임워크 선택은 WinUI, Avalonia 등 구현 비교 뒤 결정한다.
- 브라우저 의존 없이 독립 데스크톱 창을 제공한다.
- UI 프레임워크 선택이 Server·Node·protocol 변경을 요구해서는 안 된다.

## 16. 성능과 자원 기준

- draft 타이핑은 Chat timeline 전체 재렌더를 유발해서는 안 된다.
- snapshot은 최신 10건만 push하고 history는 cursor pagination을 사용한다.
- cmux/terminal discovery와 Git inspection은 고빈도 message polling과 분리·캐시한다.
- 검증되지 않은 branch/hash 후보를 전역 필터에 쌓지 않는다.
- 장거리 scrollTo는 애니메이션 없이 이동한다.
- 앱이 background일 때 불필요한 polling과 렌더를 최소화한다.
- 자원 수치는 짧은 표본이 아니라 M2 soak에서 다시 측정한다.

CURRENT 참고 측정치는 SwiftUI 약 75MB RSS, daemon 약 41MB, Server 약 36MB였으나
제품 보증치가 아니다.

## 17. 실패·복구 시험표

다음 실패는 자동화 테스트 또는 M2/M3 실기 시험에 포함한다.

- message commit 직후 Server 종료
- inbox event 수신 직후 Node 종료
- received/processed ACK 응답 유실
- WebSocket 중복·순서 변경·장시간 단절
- agent running 중 메시지 다건 도착
- terminal surface/pane 이동과 title 변경
- binding 대상 종료와 동일 이름 신규 세션 등장
- role assignment 도중 Node offline
- 미할당 역할에 메시지 누적 후 담당자 배정
- 두 PM의 동시 Attention 해결
- history 페이지 로드 중 앱 종료
- DB migration 실패와 backup restore
- Node credential 폐기
- Windows host와 WSL 중 한쪽만 종료

어떤 경우에도 서버 메시지 유실, 잘못된 세션 입력, 기존 터미널 종료가 발생해서는 안 된다.

## 18. 마일스톤 순서

### CURRENT — Local Product Baseline

- macOS SwiftUI PM 앱
- localhost Server·Node daemon
- cmux Codex/Claude attach와 safe pager
- 프로젝트·역할·메시지·history·Shared·Work
- Bookmark·Timeline Pin·Pretty·관심사 필터
- strict local Git inference

### M1 — Server Extraction Ready

- runtime configuration
- authentication·membership
- Server/Node package boundary
- Node heartbeat와 전역 presence
- migration·backup·restore·readiness
- remote Server integration tests

### M2 — Multi-client Validation

- Mac 두 대 또는 Mac + 두 번째 PC
- 복수 PM·Node·Agent 실운영
- offline delivery·동시성·8시간 soak

### M3 — Windows Node

- Windows/WSL terminal adapters
- Node service·installer
- socket pager와 safe fallback
- cross-platform Git mapping

### M4 — Windows PM Client

- 독립 Windows desktop UI
- macOS와 동일한 control contract

## 19. 의도적으로 보류한 범위

- Server 다중 인스턴스와 PostgreSQL
- 공용 hosted control plane 강제 의존
- 이메일 기반 계정 복구
- 인터넷 직접 공개
- 신뢰할 수 없는 token 비용 추정
- 자동 요약을 원문 history의 대체물로 사용
- 에이전트 프로세스 생성·종료·재시작 소유
- 자율적인 위험 작업 승인
- iTerm2·Terminal.app adapter
- Windows PM UI 프레임워크 확정

## 20. 현재 열린 설계 결정

M1 구현 전에 다음은 별도 기술 설계로 확정한다.

- User/Node credential 형식과 enrollment 흐름
- project membership의 최소 role 집합
- TLS direct termination과 reverse proxy 지원 범위
- heartbeat 주기와 offline 판정 시간
- SQLite online backup과 restore 검증 방식
- Server public URL과 Node local config schema
- protocol version 협상과 구버전 Node 차단 기준

M3 구현 전에 다음을 확정한다.

- Windows Terminal에서 지원 가능한 안정 식별자와 lifecycle event
- native PowerShell과 WSL의 completion 경계
- 기존 세션 attach와 wrapper-managed 세션의 기능 차이
- Windows service 권한과 사용자 session 간 IPC
- Windows/WSL socket transport와 credential 보관 방식

이 결정들은 현재 구현의 우연한 제약이 아니라 제품 불변조건과 인수 기준을 우선해
선택해야 한다.
