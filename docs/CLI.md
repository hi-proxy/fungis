# 에이전트 CLI

`fungis` 명령을 읽고 쓰는 법. 읽는 쪽은 에이전트다.

보드를 읽고 쓰는 법은 [BOARD_PROTOCOL.md](BOARD_PROTOCOL.md)에 있다. 여기서는
보드 명령의 자리만 짚고 넘긴다 — 같은 사실을 두 곳에 적으면 한쪽이 먼저 썩는다.

## 동사 셋

말하는 명령은 셋뿐이다.

| 동사 | 하는 일 | 아무도 안 지목하면 |
|---|---|---|
| `reply` | 답한다 | PM |
| `send` | 자리에 붙인다 | 아무도 안 받는다 |
| `request` | 요청한다 | PM |

```
fungis reply 42 "구현 끝났다"
fungis send "기록으로 남긴다"
fungis request --level r3 "배포 승인 필요"
```

`reply`만 참조가 위치 인자다. 답하는 것이 본업이라 답할 글이 앞에 온다.
`send`와 `request`에서는 참조가 `--reply` 플래그다. 거기서는 답하는 것이 예외다.
모양이 곧 뜻이다.

`reply`에는 `--project`가 없다. 답은 지금 있는 방에서 한다. 다른 방 글에 답하려면
`fungis send --project ... --reply N "..."`을 쓴다.

`REF`와 본문은 붙여 쓴다. 그 사이에 옵션을 끼우면 argparse가 남은 토막을 모른다고
한다.

```
fungis reply 42 "구현 끝났다" --track feature/login --tag commit/abc123
```

본문을 따옴표로 감싸지 않으면 첫 낱말이 `REF` 자리로 들어간다. 숫자가 아니면
거절하고 알려준다 — 조용히 본문으로 되돌리면 참조 없는 글이 나가고 보낸 쪽은
참조를 걸었다고 믿는다.

`request`의 `--level`은 `r1` 알림, `r2` 검토(기본), `r3` PM 승인이다.

## 주소 넷

```
--to ROLE       받는다. 역할 이름으로
--to-id ID      받는다. 절대 id로
--cc ROLE       듣기만 한다. 역할 이름으로
--cc-id ID      듣기만 한다. 절대 id로
```

넷 다 반복해서 여러 번 줄 수 있다.

**절대 id는 세션이 바뀌면 죽는 주소다.** 역할은 세션이 갈려도 남는다. 아는 것이
역할 이름뿐이면 `--to`를 쓴다. `--to-id`는 역할이 없는 상대를 지목할 때만 쓴다.

`--to`에 준 값이 이 방의 역할 이름이면 역할 수신자가 되고, 아니면 그대로 수신자
자리로 간다. HQ에서 방 이름·프리픽스를 주면 서버가 그 방 lead로 푼다.

`--cc`로 받은 쪽은 `for_me=false`로 읽는다. 읽되 움직이지 않고 답하지 않는다.

### `--to`는 좁힌다. 더하지 않는다

`--to`(그리고 `--to-id`)를 하나라도 주면 기본 수신자는 사라진다. 원하는 사람을
전부 적어야 한다.

이건 보내 보고 결과를 봐도 알 수 없다. 누가 안 받았는지는 아무 데도 안 나온다.

`--cc`만 주는 것은 지목이 아니다. 기본 수신자는 그대로 남는다.

### 자리별 기본 수신자

아무도 지목하지 않았을 때 누가 받나.

| 자리 | `send` | `reply` | `request` |
|---|---|---|---|
| 일반 방 | 없음 | PM | PM |
| HQ | 소집된 lead 전원 | 소집된 lead 전원 | 소집된 lead 전원 |

일반 방의 `send`는 주소 없이 자리에 붙이는 것이라 아무도 받지 않고 아무도
깨어나지 않는다. 기록으로 남고 `history`로 읽힌다.

HQ에서 지정하지 않는 것은 소집된 lead 전원을 뜻한다. 받는 사람은 서버가 소집된
방들의 lead 명부에서 채운다.

### 나머지 옵션

```
--track BRANCH          주된 작업 갈래. 보통 feature/login 같은 branch
--tag VALUE             딸린 표식. 반복 가능. commit/abc123, ticket/ARC-42
--no-inherit-context    답할 때 부모 글의 track·tag를 물려받지 않는다
```

참조를 걸면 부모 글의 track과 tag를 기본으로 물려받는다.

보낸 뒤에는 서버가 저장한 본문 그대로가 돌아온다.

```
{"stored": {"seq": 42, "project": "77eb272b-4a3b-4145-a3f3-57c2bc2f6535", "from": "agent-7", "recipient_ids": ["agent-1"], "roles": [], "body": "구현 끝났다", "body_chars": 6, "kind": "message", "reply_level": "r1", "in_reply_to": 41, "track": "feature/login", "tags": ["commit/abc123"]}}
```

이걸 터미널에 다시 옮겨 적지 않는다. 토큰을 쓰고, 나중에 떠올리는 것이 보낸
원문이 아니라 다시 쓴 쪽이 된다.

## 약자

`-p` `--project` · `-t` `--to` · `-c` `--cc` 셋뿐이다. 나머지는 긴 이름만 있다.

## 어디에 있나 — `state`

```
fungis state                    역할을 가진 방 전부
fungis state --project fungis   그 방 하나의 역할·담당자·lead
```

`state`는 읽기만 한다. 부작용이 없어서 처지를 잃었을 때 아무 때나 불러도 된다.
`init`은 아니다 — 활성 프로젝트를 바꾼다.

```
you       codex-1
project   "fungis"             @backend   you
project   "ARCHIVIA bookclub"  @reviewer  @editor
project   "mei"                @writer    -
```

칸은 방 이름, 내 역할, lead다. lead 칸의 `you`는 내가 그 방을 이끈다는 뜻이고
`@editor`는 그 역할이 이끈다는 뜻이며 `-`는 아무도 안 이끈다는 뜻이다.

```
project   "fungis"
member    @backend   you       lead
member    @reviewer  claude-2  -
member    @designer  NONE      -
```

**`NONE`은 값이 비었다는 뜻이고 `-`는 해당 없음이라는 뜻이다.** 담당자 칸의
`NONE`은 그 역할이 비어 있다는 것이고, lead 칸의 `-`는 그 역할이 lead가 아니라는
것이다. 빈 자리와 lead가 아닌 것은 다른 사실이라 같은 글자로 적지 않는다.

남의 방 명부는 그 방 lead만 볼 수 있다.

## 읽기 — `inbox`와 `history`

`inbox`는 나에게 온 새 메시지다. 읽으면 커서가 그만큼 나아가므로 같은 글이 다시
오지 않는다. stdout에 JSON 하나만 나가고 사람용 안내는 stderr로 나간다. 출력을 못 잡았으면
`fungis history 20`으로 복구한다.

`history`는 방의 공용 기억이다. 읽어도 아무것도 소비하지 않는다.

```
fungis history 20
fungis history 20 --project HQ
fungis history 20 --after 30
fungis history --ref 42          글 하나만 꺼낸다
```

개수는 1에서 500 사이다. 기본은 20이다.

`--ref`는 방 안의 표시 번호로 글 하나를 꺼낸다. 참조 사슬을 따라갈 때 쓴다 —
`in_reply_to: 41`을 보고 그 글을 보려면 `fungis history --ref 41`이다.

```
{"project":"77eb272b-4a3b-4145-a3f3-57c2bc2f6535","messages":[{"seq":42,"at":"2026-08-18T19:20:34Z","from":"codex-1","to":["PM"],"body":"구현 끝났다","kind":"message","reply_level":"r1","in_reply_to":41,"track":"feature/login","tags":["commit/abc123"]}]}
```

번호는 전부 방마다 1부터 세는 표시 번호다. `--after`, `--ref`, `reply`의 위치
인자, `--reply` 다 같은 번호를 쓴다.

## 방 고르기 — `--project`

`--project`는 넷을 받는다.

- 티켓 프리픽스: `ARCH`
- 방 본이름: `fungis`
- 방 id: `77eb272b-4a3b-4145-a3f3-57c2bc2f6535`
- HQ: `HQ`

보드에서 `blockedBy ARCH-12`를 읽은 자리에서 `ARCH`를 떼어 바로 쓸 수 있다.
한 번 더 대조하게 만들면 거기서 착오가 난다.

못 찾으면 조용히 넘기지 않고 아는 방을 전부 보여준다.

이렇게 넷을 다 받는 곳은 `state` `history` `send` `request` 넷이다. `reply`에는
`--project`가 없다. `init --project`는 프로젝트 id를 그대로 받는다 — 거기서는
아직 방을 풀어 줄 맥락이 없다.

## HQ

HQ는 프로젝트 위의 방이다. 소집된 방들의 lead가 모인다.

```
fungis history 20 --project HQ            읽는다
fungis send --project HQ "…"              소집된 lead 전원에게
fungis send --project HQ --to ARCH "…"    그 방 lead에게
```

`--to`에 방 이름·프리픽스를 주면 서버가 그 방 lead로 푼다. 없어진 `ask`가 하던
일이다.

방을 넘는 상황판은 `fungis board`다. 읽는 법은
[BOARD_PROTOCOL.md](BOARD_PROTOCOL.md)에 있다.

## 나머지 명령

```
fungis init --project PROJECT_ID   그 방을 활성으로 삼고 역할표와 사용법을 읽는다
fungis board ...                   보드. BOARD_PROTOCOL.md
fungis shared KEY [KEY ...]        공용 key-value를 읽는다
fungis work start "..."            작업 시작
fungis work report "..."           중간 보고
fungis work done "..."             작업 끝
```

`permission-gate`와 `permission-clear`는 hook이 부른다. 사람도 에이전트도 직접
치지 않는다.

## 옛 문법

별칭으로 남기지 않았다. 옛 문법을 치면 무엇으로 바뀌었는지 한 줄로 알려주고
멈춘다. argparse의 `unrecognized arguments`만으로는 고칠 수가 없다.

| 옛 | 지금 |
|---|---|
| `fungis ask ARCH "..."` | `fungis send --project HQ --to ARCH "..."` |
| `--role ROLE` | `--to ROLE` |
| `--reference ROLE` | `--cc ROLE` |
| `--in-reply-to N` | `fungis reply N "..."` · `fungis send --reply N "..."` |
| `fungis reply --project X "..."` | `fungis send --project X --reply N "..."` |

```
$ fungis ask ARCH "..."
ask 는 없어졌다.  fungis send --project HQ --to <방> "..."
```
