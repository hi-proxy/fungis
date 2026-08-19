from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .cmux import CmuxAdapter
from .inbox import InboxWatcher
from .pm import PMClient, PMServerError
from .registry import LocalRegistry
from .server_url import validate_server_url


DEFAULT_CONFIG = Path.home() / ".config" / "fungis" / "agent.json"


BOARD_PROTOCOL = """How to read the board

  rooms                       prefix, room name, and room id, once each
  you       FUNG @role        which room is yours
  summary   5 tickets, ...    the whole board in one line
  ARCH-12  waiting  "title"  blockedBy MEI-31  blocks FUNG-11

Three rules are enough.
  Split on spaces outside quotes; read anything inside quotes whole.
  A newline ends a ticket.
  A ticket name such as ARCH-12 already says which room and which number.

blockedBy is what this ticket waits for. blocks is what waits for this ticket:
finish it and those are freed, so tell them.

Naming a ticket
  ARCH-12   any room
  12        your own room

Commands
  fungis board                     read
  fungis board add "..."           put one on your track
  fungis board start ARCH-12
  fungis board done ARCH-12
  fungis board wait ARCH-12 MEI-31    ARCH-12 waits for MEI-31
  fungis board unwait ARCH-12 MEI-31
Every command echoes the ticket line it produced, so you do not read the board again.
"""


def load_config(path: Path | None = None) -> dict:
    path = path or Path(os.environ.get("FUNGIS_AGENT_CONFIG", DEFAULT_CONFIG))
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError as error:
        raise RuntimeError("Fungis agent CLI is not configured") from error
    if not value.get("registry") or not value.get("server"):
        raise RuntimeError("invalid Fungis agent configuration")
    try:
        value["server"] = validate_server_url(value["server"])
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"invalid Fungis agent configuration: {error}") from error
    return value


def current_binding(registry: LocalRegistry, adapter: CmuxAdapter) -> dict:
    context_surface = adapter.current_surface_id()
    if context_surface:
        direct = registry.binding_for_surface(context_surface)
        if direct is not None:
            return direct
    canonical_surface = (
        adapter.canonical_surface_for_context(context_surface)
        if context_surface
        else None
    )
    binding = (
        registry.binding_for_surface(canonical_surface)
        if canonical_surface
        else None
    )
    if binding is None:
        raise RuntimeError("current cmux context is not connected to Fungis")
    return binding


# 옛 문법은 조용히 죽으면 안 된다. argparse 기본 오류는 무엇이 없는지만
# 말하고 무엇으로 바뀌었는지는 말하지 않아서, 새 맥락에서 시작한 에이전트는
# 같은 것을 다시 친다.
LEGACY_FLAGS = {
    "--role": '--role 은 --to 로 바뀌었다.  fungis reply --to ROLE "..."',
    "--reference": '--reference 는 --cc 로 바뀌었다.  fungis reply --cc ROLE "..."',
    "--in-reply-to": '--in-reply-to 는 --reply 로 바뀌었다.  '
                     'fungis send --reply N "..."',
}

LEGACY_ASK = 'ask 는 없어졌다.  fungis send --project HQ --to <방> "..."'

LEGACY_REPLY_PROJECT = (
    "reply 의 --project 는 없어졌다. 답하려면 "
    'fungis send --project ... --reply N 을 쓴다'
)


def legacy_hint(argv: list[str]) -> str | None:
    """옛 문법이면 무엇으로 바뀌었는지 한 줄로 돌려준다."""
    command = next((item for item in argv if not item.startswith("-")), None)
    if command == "ask":
        return LEGACY_ASK
    for item in argv:
        name = item.split("=", 1)[0]
        if name in LEGACY_FLAGS:
            return LEGACY_FLAGS[name]
    if command == "reply" and any(
        item.split("=", 1)[0] in ("--project", "-p") for item in argv
    ):
        return LEGACY_REPLY_PROJECT
    return None


class Parser(argparse.ArgumentParser):
    """옛 이름을 치면 새 이름을 알려주고 멈춘다."""

    def parse_args(self, args=None, namespace=None):  # type: ignore[override]
        given = list(sys.argv[1:] if args is None else args)
        hint = legacy_hint(given)
        if hint is not None:
            raise SystemExit(hint)
        return super().parse_args(args, namespace)

    def error(self, message):  # type: ignore[override]
        # argparse는 자리 인자가 옵션으로 두 토막 나면 남은 토막을 모른다고만
        # 한다. 참조 번호와 본문 사이에 옵션을 끼운 것이 그 경우다.
        if message.startswith("unrecognized arguments"):
            message += (
                "\n참조 번호와 본문은 붙여서 쓴다. 그 사이에 옵션을 끼우지 "
                '마라: fungis reply 42 "..." --track ...'
            )
        super().error(message)


ADDRESSING = """Addressing
  --to ROLE       who receives it, by role; repeat for more
  --to-id ID      who receives it, by absolute id; dies when the session changes
  --cc ROLE       who only listens, by role; repeat for more
  --cc-id ID      who only listens, by absolute id

--to NARROWS the default recipient, it does not add to it. With --to the
default is gone: name every recipient you want.

Default recipient when you name none
  send in a normal room     nobody; it stays in the timeline only
  reply in a normal room    PM
  request in a normal room  PM
  anything in HQ            every lead convened there
"""


def parser() -> argparse.ArgumentParser:
    result = Parser(
        prog="fungis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Read and write the shared Fungis project room from an attached agent.",
        epilog="""Typical flow:
  fungis init --project PROJECT_ID
  fungis state
  fungis inbox
  fungis history 20
  fungis reply 42 \"implementation complete\"
  fungis send \"note for the record\"
  fungis request --level r3 \"approval required\"

inbox is the agent's new-message feed. history is the shared project room.
Use role names as stable addresses; session names may change.""",
    )
    commands = result.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser(
        "init", help="read current project roles and Fungis usage"
    )
    initialize.add_argument("--project", required=True)
    commands.add_parser("inbox", help="read new messages for this agent")
    gate = commands.add_parser(
        "permission-gate",
        help="hand a PermissionRequest hook payload to the PM and wait for the answer",
    )
    gate.add_argument(
        "--wait", type=int, default=110,
        help="kept for compatibility; the gate never waits",
    )
    commands.add_parser(
        "permission-clear",
        help="mark this session's pending permission notice as handled",
    )
    board = commands.add_parser(
        "board", help="read the cross-project board, or put your work on it"
    )
    # 노드는 그 방이 올린다. PM이 대신 쳐 넣으면 PM이 이미 아는 것을 옮겨 적는
    # 일이 되고, 보드는 아무것도 알려주지 않는 화면이 된다.
    board.epilog = BOARD_PROTOCOL
    board.formatter_class = argparse.RawDescriptionHelpFormatter
    board_actions = board.add_subparsers(dest="board_command")
    board_add = board_actions.add_parser("add", help="put one item on your track")
    board_add.add_argument("title", nargs="+")
    board_start = board_actions.add_parser("start", help="mark a ticket as in progress")
    board_start.add_argument("ticket", help="ticket name such as ARCH-12")
    board_done = board_actions.add_parser("done", help="mark a ticket as finished")
    board_done.add_argument("ticket", help="ticket name such as ARCH-12")
    board_wait = board_actions.add_parser(
        "wait", help="make a ticket wait for another one"
    )
    board_wait.add_argument("ticket", help="the one that waits")
    board_wait.add_argument("blocker", help="the one it waits for")
    board_unwait = board_actions.add_parser("unwait", help="cut that dependency")
    board_unwait.add_argument("ticket")
    board_unwait.add_argument("blocker")
    state = commands.add_parser(
        "state", help="read who you are and which rooms you sit in",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""state only reads. It changes nothing, so it is safe to call
whenever you have lost track of where you are. init is not: it switches your
active project.

  fungis state                    every room you hold a role in
  fungis state --project fungis   one room's roles, who holds them, who leads

NONE means the value is empty. - means it does not apply.
Another room's member list is for its lead only.""",
    )
    state.add_argument(
        "-p", "--project",
        help="one room in detail: its ticket prefix (ARCH), name, or id",
    )
    history = commands.add_parser(
        "history", help="read shared project history",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""  fungis history 20
  fungis history 20 --project HQ
  fungis history --ref 42          pull one message out by its number""",
    )
    history.add_argument("count", nargs="?", type=int, default=20)
    history.add_argument("--after", type=int)
    history.add_argument(
        "--ref", type=int, nargs="+", metavar="N",
        help="only these messages, named by their number in the room",
    )
    history.add_argument(
        "-p", "--project",
        help="room to read: its ticket prefix (ARCH), name, or id",
    )
    reply = commands.add_parser(
        "reply", help="answer a message; defaults to PM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Answering is the job, so what you answer comes first.

  fungis reply \"done\"
  fungis reply 42 \"verified\"
  fungis reply --to reviewer \"please review\"
  fungis reply 42 \"implemented\" --track feature/login --tag commit/abc123

REF and the body sit next to each other. Do not put an option between them.

REF is the message's number in this room. The room is the one you are in;
reply has no --project. To answer inside another room use
fungis send --project ... --reply N.

Answering inherits the parent message's track and tags by default.
Successful output echoes the exact body stored by the server.

"""
        + ADDRESSING,
    )
    reply.add_argument(
        "ref", nargs="?", help="the message you answer: its number in this room"
    )
    add_addressing(reply)
    send = commands.add_parser(
        "send", help="leave a message at a place; no recipient by default",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""send puts a message somewhere rather than addressing someone.

  fungis send \"note for the record\"        your room; wakes nobody
  fungis send --project HQ \"heads up\"      every lead convened in HQ
  fungis send --project HQ --to ARCH \"…\"  that room's lead
  fungis send --reply 42 \"…\"               answering is the exception here

"""
        + ADDRESSING,
    )
    add_addressing(send, project=True, reply=True)
    request = commands.add_parser(
        "request", help="request attention or approval; defaults to PM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Levels:
  r1  informational request
  r2  review or intervention requested (default)
  r3  explicit PM confirmation or approval required

Examples:
  fungis request --level r2 --to reviewer \"review this change\"
  fungis request --level r3 --track release/1.0 --tag commit/abc123 \"approve release\"

--reply inherits the parent message's track and tags by default.
Successful output echoes the exact body stored by the server.

"""
        + ADDRESSING,
    )
    add_addressing(request, project=True, reply=True, level=True)
    shared = commands.add_parser("shared", help="read selected shared SSOT keys")
    shared.add_argument("keys", nargs="+")
    work = commands.add_parser("work", help="track structured work time and reports")
    work_commands = work.add_subparsers(dest="work_command", required=True)
    for name in ("start", "report", "done"):
        item = work_commands.add_parser(name)
        item.add_argument("text", nargs="+")
    return result


def add_addressing(
    command: argparse.ArgumentParser,
    *,
    project: bool = False,
    reply: bool = False,
    level: bool = False,
) -> None:
    """주소 문법은 넷뿐이다. 같은 일을 다른 낱말로 부르지 않는다."""
    command.add_argument("body", nargs="+", help="message body; maximum 20,000 characters")
    if level:
        command.add_argument(
            "--level", choices=("r1", "r2", "r3"), default="r2",
            help="attention level: r1 info, r2 review, r3 PM approval",
        )
    command.add_argument(
        "-t", "--to", action="append", default=[],
        help="recipient by role; repeat for more. In HQ a room name works too. "
             "It NARROWS the default recipient, it does not add to it",
    )
    command.add_argument(
        "--to-id", action="append", default=[],
        help="recipient by absolute id; dies when the session changes. "
             "It narrows the default recipient just like --to",
    )
    command.add_argument(
        "-c", "--cc", action="append", default=[],
        help="copy by role: delivered, but marked listen-only; repeat for more",
    )
    command.add_argument(
        "--cc-id", action="append", default=[],
        help="copy by absolute id; dies when the session changes",
    )
    command.add_argument(
        "--track",
        help="one primary work thread, usually a branch such as feature/login",
    )
    command.add_argument(
        "--tag", action="append",
        help="secondary metadata; repeat, e.g. commit/abc123 or ticket/ARC-42",
    )
    if reply:
        command.add_argument(
            "--reply", type=int,
            help="the message you answer: its number in this room; "
                 "inherits its track and tags unless disabled",
        )
    if project:
        command.add_argument(
            "-p", "--project",
            help="room to speak in: its ticket prefix (ARCH), name, or id; "
                 "defaults to your own room",
        )
    command.add_argument(
        "--no-inherit-context", action="store_true",
        help="do not inherit track and tags from the message you answer",
    )


def format_bootstrap(value: dict) -> str:
    own_role = value.get("own_role")
    own = f"@{own_role['name']}" if own_role else "direct session (no role)"
    roles = []
    for role in value.get("roles", []):
        if role.get("self"):
            owner = "you"
        elif role.get("agent_name"):
            owner = role["agent_name"]
        else:
            owner = "unassigned"
        roles.append(f"@{role['name']}={owner}")
    usage = value["usage"]
    return "\n".join(
        [
            f"Fungis init {value['revision']}",
            f"project: {value['project']['name']}",
            f"you: {own}",
            f"pm: {value['pm']['display_name']}",
            "roles: " + (", ".join(roles) if roles else "none"),
            "commands:",
            f"- read: {usage['inbox']}",
            f"- restore context: {usage['history']}",
            # CLI는 소스를 매번 읽지만 서버는 재시작해야 바뀐다. 그 사이에
            # 새 CLI가 옛 서버를 만나므로 없는 키로 죽지 않게 둔다.
            "- where you are: " + usage.get("state", "fungis state"),
            f"- reply PM: {usage['reply_pm']}",
            f"- message role: {usage['message_role']}",
            "- copy role (listen only): "
            + usage.get("copy_role", 'fungis reply --cc ROLE "..."'),
            "- leave a note without addressing anyone: "
            + usage.get("send", 'fungis send "..."'),
            f"- request review/approval: {usage['request_review']} / {usage['request_approval']}",
            f"- work: {usage['work_start']} / {usage['work_report']} / {usage['work_done']}",
            # 보드에 올리는 것은 그 방의 몫이다. 안 알려주면 PM이 대신 쳐 넣게
            # 되고, 그러면 보드는 PM이 이미 아는 것만 담는다.
            '- board: fungis board / fungis board add "..." / '
            "fungis board start TICKET / fungis board done TICKET / "
            # 기다린다는 것을 못 적으면 막힌 일이 보드에서 안 막힌 것으로 보인다.
            "fungis board wait TICKET BLOCKER_TICKET / fungis board unwait TICKET BLOCKER_TICKET",
            f"- recovery: {usage['recovery']}",
            "Use role names as stable addresses. Report results and blockers through Fungis.",
            # 명령 목록만으로는 언제 쓰는지 모른다. 새 세션마다 맥락 없이
            # 시작하므로 규범을 함께 준다. 규범은 지시문이라 영어로 둔다.
            "Ask before anything risky or irreversible: "
            f"{usage['request_approval']}. "
            "Waiting at the terminal permission prompt tells PM nothing.",
            # 참조가 지시로 읽히면 서로 답장을 물고 늘어진다. 남에게 보낼 때도
            # 받을 때도 같은 규칙이라 한 줄로 묶어 둔다.
            "for_me=false means you were copied. Read it, do not act on it, "
            "do not reply. Correct it only if a fact is wrong. "
            "When someone only needs to know, copy them instead of addressing them.",
            "chain counts agent turns since PM last spoke. It is not a limit: "
            "keep going if it helps, stop when you have nothing to add.",
            # 터미널에 다시 쓰면 원문과 미묘하게 달라진다. 나중에 에이전트가
            # 떠올리는 것은 보낸 쪽이 아니라 다시 쓴 쪽이라, 기록이 둘이 된다.
            "Sending echoes the exact stored text back to you. Do not restate "
            "it in the terminal: it spends tokens and leaves a second, slightly "
            "different version as what you remember saying.",
            # 언어를 못박으면 PM이 바뀔 때마다 고쳐야 한다. PM을 따라가게 둔다.
            "Write messages in the language PM uses.",
        ]
    )


def permission_gate(
    config: dict, registry: LocalRegistry, binding: dict, wait_seconds: int
) -> dict:
    """권한 요청이 무엇인지 PM에게 알리고 곧바로 비켜선다.

    이 hook은 권한 화면이 뜨기 **전에** 돈다. 여기서 기다리면 그만큼 터미널에
    질문조차 뜨지 않는다. 예전에는 답을 기다리느라 최대 110초를 붙잡아, 결정을
    돌려주지도 못하면서 터미널만 얼렸다.

    돌려줄 수도 없다. 이 provider의 명령형 hook에는 PermissionRequest용 결정
    형식이 없다(permissionDecision은 PreToolUse 전용이다). 그래서 판단은 늘
    터미널에서 사람이 한다. 여기서 하는 일은 "무엇을 묻고 있는지"를 PM 화면에
    올리는 것뿐이다. 그거면 원래 풀려던 문제 — 자리에 없으면 무엇을 묻는지 알
    수 없다 — 는 풀린다.

    wait_seconds는 예전 계약이라 받기만 하고 쓰지 않는다.
    """
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return {}
    tool_name = payload.get("tool_name")
    session_id = payload.get("session_id")
    if not tool_name or not session_id:
        return {}

    workspace_id = active_project(registry, binding["principal_id"])
    client = PMClient(config["server"], registry, workspace_id=workspace_id)
    try:
        client.create_permission_request(
            session_id=session_id,
            agent_id=binding.get("principal_id"),
            tool_name=str(tool_name),
            tool_input=json.dumps(payload.get("tool_input"), ensure_ascii=False),
            suggestions=json.dumps(
                payload.get("permission_suggestions"), ensure_ascii=False
            ),
        )
    except Exception:
        pass
    return {}


def permission_clear(
    config: dict, registry: LocalRegistry, binding: dict
) -> dict:
    """도구가 실제로 실행됐으니 권한 알림을 걷는다.

    사람이 터미널에서 답한 것을 서버는 알 수 없어서, 지금까지는 시간이 지나야
    걷혔다. 그동안 PM 화면의 입력창이 괜히 막혀 있었다. 도구가 돌았다는 것은
    답이 끝났다는 뜻이라 그 순간이 정확하다.
    """
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    session_id = payload.get("session_id")
    if not session_id:
        return {}

    workspace_id = active_project(registry, binding["principal_id"])
    client = PMClient(config["server"], registry, workspace_id=workspace_id)
    cleared = []
    try:
        for request in client.pending_permission_requests():
            if request.get("session_id") != session_id:
                continue
            client.resolve_permission_request(request["id"], "expired")
            cleared.append(request["id"])
    except Exception:
        pass
    return {"cleared": cleared}


def active_project(registry: LocalRegistry, principal_id: str) -> str:
    return registry.state(f"active_project:{principal_id}") or "local"


def display_seq(message: dict) -> int:
    """에이전트에게 보이는 번호는 방마다 1부터 센다.

    전역 seq를 그대로 노출하면 한 방만 보는 에이전트에게는 번호가 띄엄띄엄
    보이고, 그걸 누락으로 읽어 확인 작업을 하게 된다.
    """
    value = message.get("project_seq")
    return int(value) if value is not None else int(message["seq"])


# 프론티어 모델은 대개 2-3왕복에서 스스로 멈춘다. 그 언저리부터 알려주면
# 유용한 왕복을 막지 않으면서 늘어지는 것만 짚어 준다.
CHAIN_NOTICE = 5


def emit_inbox(messages: list[dict]) -> None:
    payload = {
        "messages": [
            {
                "seq": display_seq(message),
                "project": message.get("workspace_id"),
                "from": message.get("sender_name", message["sender_id"]),
                # 나에게 온 말인지 옆에서 듣는 말인지. 이 구분이 없으면 참조로
                # 받은 것까지 지시로 읽고 조사에 들어간다.
                "for_me": not message.get("is_reference"),
                # PM이 마지막으로 말한 뒤 에이전트끼리 오간 횟수.
                "chain": int(message.get("agent_chain") or 0),
                # 무엇에 대한 답인지. 이게 빠지면 에이전트는 답글을 걸 수는
                # 있는데 받은 글이 무엇에 대한 답인지 못 본다.
                "in_reply_to": message.get("in_reply_to_project_seq"),
                "body": message["body"],
                "track": message.get("track"),
                "tags": message.get("tags", []),
            }
            for message in messages
        ]
    }
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    if messages:
        print('Reply with: fungis reply "YOUR MESSAGE"', file=sys.stderr)
        if any(message.get("is_reference") for message in messages):
            print(
                "for_me=false means you were copied. Read it, do not act on it, "
                "do not reply. Correct it only if a fact is wrong.",
                file=sys.stderr,
            )
        rooms = {
            message.get("workspace_id")
            for message in messages
            if message.get("workspace_id")
        }
        if len(rooms) > 1:
            print(
                # reply 에는 --project 가 없다. 다른 방에 답하는 것은 send 다.
                "여러 방에서 왔다. 자기 방이 아닌 곳에 답할 때는 "
                'fungis send --project <project> --reply <seq> "..." 를 쓴다. '
                "각 메시지의 project 와 seq 값을 그대로 넣으면 된다.",
                file=sys.stderr,
            )
        chain = max(int(message.get("agent_chain") or 0) for message in messages)
        if chain >= CHAIN_NOTICE:
            print(
                f"{chain} agent turns since PM last spoke. "
                "Stop here unless you have a fact to add. "
                "Ask PM with fungis request if a decision is needed.",
                file=sys.stderr,
            )
        print(
            "If inbox output was not captured, recover with: fungis history 20",
            file=sys.stderr,
        )


def render_board(board: list[dict], you: str | None = None, role: str | None = None) -> str:
    """보드를 줄 프로토콜로 그린다.

    JSON을 주면 한 줄에 uuid가 아홉 번 나오고, 그중 셋이 같은 제목을 가리킨다.
    에이전트는 그걸 눈으로 맞춰야 하고 그래서 보드를 두 번 읽는다.

    읽는 규칙은 셋이다.
      따옴표 밖은 공백으로 자르고 따옴표 안은 통으로 읽는다
      줄바꿈이 티켓 경계다
      티켓 이름 ARCH-12 하나가 어느 방 몇 번인지 다 말한다

    프리픽스가 방을 들고 다니므로 본문에 방 이름을 다시 적지 않는다. 방 본이름과
    uuid가 필요하면 rooms 블록에서 찾는다 — 본문에 뿌리지 않는 이유는 그것이
    사람도 에이전트도 안 읽는 문자열이기 때문이다.
    """
    names = {}
    lines = ["rooms"]
    for track in board:
        prefix = track.get("ticket_prefix") or "?"
        names[track["project_id"]] = prefix
        lines.append(f'  {prefix}  "{track["project_name"]}"  {track["project_id"]}')

    ticket = {
        node["id"]: f'{names[track["project_id"]]}-{node.get("number", "?")}'
        for track in board
        for node in track["nodes"]
    }

    total = sum(len(track["nodes"]) for track in board)
    waiting = sum(
        1 for track in board for node in track["nodes"] if node["state"] == "waiting"
    )
    crossing = sum(
        1
        for track in board
        for node in track["nodes"]
        for other in node.get("blocked_by", [])
        if ticket.get(other, "").split("-")[0] != names[track["project_id"]]
    )
    lines.append("")
    if you:
        lines.append(f'you       {names.get(you, "?")} {role or ""}'.rstrip())
    lines.append(f"summary   {total} tickets, {waiting} waiting, {crossing} crossRoom")
    lines.append("")

    for track in board:
        # 번호 순으로 낸다. 만든 시각 순으로 내면 같은 밀리초에 만든 둘이
        # 뒤집혀서 FUNG-2가 FUNG-1보다 먼저 나온다.
        for node in sorted(track["nodes"], key=lambda item: item.get("number") or 0):
            row = [ticket[node["id"]], node["state"], f'"{_quote(node["title"])}"']
            blocked = [ticket[i] for i in node.get("blocked_by", []) if i in ticket]
            if blocked:
                row.append("blockedBy " + " ".join(blocked))
            holding = [ticket[i] for i in node.get("blocks", []) if i in ticket]
            if holding:
                row.append("blocks " + " ".join(holding))
            lines.append("  ".join(row))
    return "\n".join(lines)


STATE_LABEL = 10


def _columns(rows: list[list[str]]) -> list[str]:
    """마지막 칸을 뺀 나머지를 그 칸의 최대 너비 + 2로 맞춘다."""
    if not rows:
        return []
    widths = [
        max(len(row[index]) for row in rows) + 2
        for index in range(len(rows[0]) - 1)
    ]
    return [
        "".join(
            cell if index == len(row) - 1 else f"{cell:<{widths[index]}}"
            for index, cell in enumerate(row)
        ).rstrip()
        for row in rows
    ]


def render_state(you: str, agent_id: str, rooms: list[dict]) -> str:
    """내가 누구이고 어느 방에 어떤 자격으로 앉아 있나.

    render_board와 같은 줄 프로토콜이다. 방 이름은 따옴표로 감싼다 — 이름에
    공백이 있으면 칸이 밀린다.

    lead 칸이 you 면 그 방을 내가 이끈다는 뜻이고, - 는 아무도 이끌지 않는다는
    뜻이다. 이 둘을 같은 글자로 적으면 소집을 누가 받는지 알 수 없다.
    """
    lines = [f"{'you':<{STATE_LABEL}}{you}"]
    rows = []
    for room in rooms:
        role = " ".join(f"@{name}" for name in room.get("my_roles", [])) or "-"
        lead = room.get("lead")
        if not lead:
            mark = "-"
        elif lead.get("agent_id") and lead["agent_id"] == agent_id:
            mark = "you"
        else:
            mark = f"@{lead['name']}"
        rows.append([f'"{room["project_name"]}"', role, mark])
    lines.extend(f"{'project':<{STATE_LABEL}}{line}" for line in _columns(rows))
    return "\n".join(lines)


def render_members(members: dict, agent_id: str) -> str:
    """방 하나의 역할·담당자·lead 여부.

    NONE은 값이 비었다는 뜻이고 -는 해당 없음이라는 뜻이다. 빈 자리와 lead가
    아닌 것은 다른 사실이라 같은 글자로 적지 않는다.

    역할과 lead를 다시 적지 않는다. 목록에 이미 있는 것을 상세에 또 적으면
    어느 쪽이 최신인지 재는 일이 생긴다.
    """
    lines = [f"{'project':<{STATE_LABEL}}\"{members['project_name']}\""]
    rows = []
    for role in members.get("roles", []):
        if not role.get("agent_id"):
            owner = "NONE"
        elif role["agent_id"] == agent_id:
            owner = "you"
        else:
            owner = role.get("agent_name") or role["agent_id"]
        rows.append([f"@{role['name']}", owner, "lead" if role.get("is_lead") else "-"])
    lines.extend(f"{'member':<{STATE_LABEL}}{line}" for line in _columns(rows))
    return "\n".join(lines)


def resolve_project(client, given: str | None, fallback: str) -> str:
    """프리픽스도 방 이름도 id도 받는다. HQ는 그 이름 그대로 받는다.

    보드에 붙지 않은 방은 트랙이 없어서 resolve_room이 모른다. 그럴 때만 방
    목록을 한 번 더 본다 — 못 찾으면 조용히 넘기지 않고 아는 방을 보여준다.
    조용히 넘기면 오타 하나가 "너는 그 방 소속이 아니다"로 되돌아온다.
    """
    if not given:
        return fallback
    wanted = given.strip()
    hq = client.hq()
    if hq is not None and wanted.casefold() in {
        "hq", str(hq["id"]).casefold(), str(hq.get("name") or "").casefold()
    }:
        return str(hq["id"])
    try:
        return resolve_room(client.board(), wanted)
    except RuntimeError as error:
        for project in client.projects():
            if wanted == project["id"] or wanted.casefold() == project["name"].casefold():
                return str(project["id"])
        raise error


def reply_reference(ref: str | None) -> int | None:
    """reply의 첫 인자는 답할 글 번호다.

    본문을 따옴표 없이 치면 첫 낱말이 여기로 들어온다. 그때 조용히 본문으로
    되돌리면 참조 없는 글이 나가고, 보낸 쪽은 참조를 걸었다고 믿는다.
    """
    if ref is None:
        return None
    if not ref.isdigit():
        raise RuntimeError(
            f'reply 의 첫 인자는 답할 글 번호다. "{ref}" 는 번호가 아니다. '
            '본문은 따옴표로 감싼다: fungis reply "..." 또는 fungis reply 42 "..."'
        )
    return int(ref)


def addressing(client, args) -> tuple[list[str], list[str], list[str], list[str]]:
    """--to/--to-id/--cc/--cc-id를 서버가 아는 네 자리로 가른다.

    --to는 먼저 이 방의 역할로 읽는다. 역할이 아니면 그대로 수신자 자리에
    넣는다 — HQ에서 방 이름을 주면 서버가 그 방 lead로 푼다.

    state·board·init이 역할을 @이름으로 보여주므로 @를 붙여 치는 것을 받는다.
    안 받으면 화면에서 읽은 것을 그대로 쳤을 때 수신자 자리로 새고, 서버가
    그런 id는 없다며 외래키 오류를 뱉는다. 읽은 그대로 칠 수 있어야 한다.
    """
    known: dict[str, str] = {}
    if args.to or args.cc:
        for role in client.roles():
            known[role["name"]] = role["name"]
            known[role["id"]] = role["id"]

    def as_role(value: str) -> str | None:
        return known.get(value) or known.get(value.lstrip("@"))

    role_ids, direct = [], []
    for value in args.to:
        name = as_role(value)
        (role_ids if name else direct).append(name or value)
    direct.extend(args.to_id)
    return (
        role_ids,
        direct,
        [as_role(value) or value for value in args.cc],
        list(args.cc_id),
    )


def default_recipients(client, command: str) -> list[str]:
    """아무도 지목하지 않았을 때 누가 받나.

    HQ에서 지정하지 않는 것은 소집된 lead 전원을 뜻한다. 그건 서버가 푸는
    것이라 여기서는 빈 목록을 그대로 보낸다.

    일반 방의 send는 주소 없이 자리에 붙이는 것이라 아무도 받지 않는다.
    reply와 request는 답과 요청이라 받을 사람이 없으면 하지 않은 것과 같다.
    """
    hq = client.hq()
    if hq is not None and str(hq["id"]) == client.workspace_id:
        return []
    if command == "send":
        return []
    return [str(client.pm_id)]


def read_state(client, binding: dict, given: str | None) -> str:
    """부작용 없이 처지만 읽는다. init은 활성 프로젝트를 바꾸므로 못 쓴다."""
    agent_id = binding["principal_id"]
    if given:
        room = resolve_project(client, given, client.workspace_id)
        return render_members(client.members(room), agent_id)
    rooms: dict[str, dict] = {}
    for membership in client.agent_role_memberships():
        if membership["agent_id"] != agent_id:
            continue
        room = rooms.setdefault(
            membership["project_id"],
            {"project_name": membership["project_name"], "my_roles": []},
        )
        room["my_roles"].append(membership["role_name"])
    for project_id, room in rooms.items():
        room["lead"] = client.members(project_id).get("lead")
    return render_state(
        binding.get("local_name") or agent_id, agent_id, list(rooms.values())
    )


def ticket_names(board: list[dict]) -> dict[str, str]:
    """노드 id -> ARCH-12. 한 군데서만 만든다."""
    return {
        node["id"]: f'{track.get("ticket_prefix") or "?"}-{node.get("number", "?")}'
        for track in board
        for node in track["nodes"]
    }


def resolve_ticket(board: list[dict], given: str, own_project: str | None) -> str:
    """ARCH-12도 12도 받는다. 애매하면 거절하고 후보를 보여준다.

    맨 숫자는 자기 방으로 읽는다. 남의 방 티켓을 맨 숫자로 부르면 어느 방인지
    알 수 없으므로 거절한다 — 조용히 아무거나 고르면 엉뚱한 방의 일이 바뀐다.
    """
    names = ticket_names(board)
    wanted = given.strip().upper()
    exact = [node_id for node_id, name in names.items() if name.upper() == wanted]
    if len(exact) == 1:
        return exact[0]
    if wanted.isdigit() and own_project:
        here = [
            node["id"]
            for track in board
            if track["project_id"] == own_project
            for node in track["nodes"]
            if str(node.get("number")) == wanted
        ]
        if len(here) == 1:
            return here[0]
    candidates = sorted(names.values())
    raise RuntimeError(
        f"no ticket named {given}. known tickets: {', '.join(candidates) or 'none'}"
    )


def resolve_room(board: list[dict], given: str) -> str:
    """ARCH 도 방 이름도 uuid도 받는다.

    막힌 티켓에서 프리픽스를 그대로 읽어 물어볼 수 있어야 한다. 한 번 더
    대조하게 만들면 그 대조에서 착오가 난다.
    """
    wanted = given.strip()
    for track in board:
        if wanted == track["project_id"]:
            return track["project_id"]
    for track in board:
        if wanted.upper() == (track.get("ticket_prefix") or "").upper():
            return track["project_id"]
    for track in board:
        if wanted.casefold() == track["project_name"].casefold():
            return track["project_id"]
    known = ", ".join(
        f'{track.get("ticket_prefix") or "?"} ({track["project_name"]})'
        for track in board
    )
    raise RuntimeError(f"no room named {given}. known rooms: {known or 'none'}")


def ticket_line(board: list[dict], node_id: str) -> str:
    """바뀐 티켓 한 줄만 돌려준다. 보드를 다시 읽게 하지 않는다."""
    body = render_board(board)
    name = ticket_names(board).get(node_id)
    for line in body.split("\n"):
        if name and line.startswith(name + " "):
            return line
    return name or node_id


def own_role_name(binding: dict) -> str | None:
    role = binding.get("role_name")
    return f"@{role}" if role else None


def _quote(text: str) -> str:
    """줄바꿈은 티켓 경계라 제목 안에 있으면 안 된다. 따옴표는 escape 한다."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")


def compact_history(project_id: str, messages: list[dict]) -> dict:
    items = []
    for message in messages:
        role_agent_ids = {
            role.get("delivered_agent_id")
            for role in message.get("role_recipients", [])
            if role.get("delivered_agent_id")
        }
        to = [f"@{role['name']}" for role in message.get("role_recipients", [])]
        to.extend(
            recipient["display_name"]
            for recipient in message.get("recipients", [])
            if recipient["recipient_id"] not in role_agent_ids
        )
        items.append(
            {
                "seq": display_seq(message),
                "at": message["created_at"],
                "from": message["sender_name"],
                "to": to,
                "body": message["body"],
                "kind": message["kind"],
                "reply_level": message["reply_level"],
                "in_reply_to": message.get("in_reply_to_project_seq")
                or message.get("in_reply_to"),
                "track": message.get("track"),
                "tags": message.get("tags", []),
            }
        )
    return {"project": project_id, "messages": items}


def stored_echo(
    result: dict, *, roles: list[str], in_reply_to: int | None = None
) -> dict:
    return {
        "stored": {
            "seq": display_seq(result),
            "project": result["workspace_id"],
            "from": result["sender_id"],
            "recipient_ids": result.get("recipient_ids", []),
            "roles": roles,
            "body": result["body"],
            "body_chars": len(result["body"]),
            "kind": result["kind"],
            "reply_level": result["reply_level"],
            # 되돌려 보여주는 값도 에이전트가 준 방별 번호 그대로다.
            "in_reply_to": in_reply_to,
            "track": result.get("track"),
            "tags": result.get("tags", []),
        }
    }


def warn_if_nobody_received(result: dict, role_ids: list[str]) -> None:
    """아무도 안 받은 것은 성공 출력 안에서 실패처럼 보이지 않는다.

    일반 방의 send 는 지목이 없으면 아무도 받지 않는다. 자리에 붙이는 쓰임이
    실제로 있어 실패로 만들지는 않는다. 그런데 recipient_ids 가 빈 것을
    보내는 쪽이 못 알아채서, 한 리드가 사흘 사이 세 번을 갔다고 믿었다.
    그 자리만 말로 짚는다. HQ 는 서버가 lead 전원으로 풀어 돌려주므로
    여기 걸리지 않는다.
    """
    if result.get("recipient_ids") or role_ids:
        return
    print(
        "아무도 받지 않았다. 방에는 남지만 누구도 깨우지 않는다.  "
        "받을 사람을 지목하려면 --to ROLE",
        file=sys.stderr,
    )


def write_error_message(error: Exception) -> str:
    return (
        f"{error}\n"
        "Hint: initialize or refresh project context first: "
        "fungis init --project PROJECT_ID. "
        "Then retry reply/send/request; "
        "use fungis state to see where you are and fungis history 20 to verify "
        "room context."
    )


def main() -> None:
    args = parser().parse_args()
    try:
        config = load_config()
        registry = LocalRegistry(Path(config["registry"]))
        adapter = CmuxAdapter()
        binding = current_binding(registry, adapter)
        if args.command == "init":
            client = PMClient(config["server"], registry, workspace_id=args.project)
            value = client.project_bootstrap(
                args.project, binding["principal_id"]
            )
            registry.set_state(f"active_project:{binding['principal_id']}", args.project)
            print(format_bootstrap(value))
        elif args.command == "inbox":
            messages = InboxWatcher(
                config["server"], binding["principal_id"], registry
            ).read_messages(binding["surface_id"])
            emit_inbox(messages)
            # 여러 방에서 왔으면 기본 목적지를 건드리지 않는다. 마지막 것으로
            # 뒤집으면, 다른 방 얘기를 하려던 답장이 방금 읽은 방으로 간다.
            # 방이 하나뿐일 때만 따라간다.
            rooms = {
                message.get("workspace_id")
                for message in messages
                if message.get("workspace_id")
            }
            # HQ 로는 안 따라간다. HQ 방송은 lead 전원이 받으므로, 따라가면
            # 방송 한 번에 모든 lead 의 활성 방이 HQ 로 뒤집힌다. 그 뒤의 맨
            # reply 는 자기 방 대신 HQ 에 붙고 board add 는 HQ 트랙을 노린다.
            # lead 는 자기 방에 서서 HQ 를 읽는 것이지 HQ 로 이사가는 것이 아니다.
            if len(rooms) == 1 and (room := rooms.pop()) != "hq":
                registry.set_state(
                    f"active_project:{binding['principal_id']}", room
                )
        elif args.command == "state":
            client = PMClient(
                config["server"], registry,
                workspace_id=active_project(registry, binding["principal_id"]),
                caller_id=binding["principal_id"],
            )
            print(read_state(client, binding, args.project))
        elif args.command == "history":
            if not 1 <= args.count <= 500:
                raise RuntimeError("history count must be between 1 and 500")
            mine = active_project(registry, binding["principal_id"])
            # 에이전트가 자기 이름으로 읽는다. PM 이름을 빌리면 아무 방이나
            # 열린다.
            client = PMClient(
                config["server"], registry, workspace_id=mine,
                caller_id=binding["principal_id"],
            )
            workspace_id = resolve_project(client, args.project, mine)
            client.workspace_id = workspace_id
            if args.ref:
                # 번호 여러 개를 한 번에 받는다. 밀려 있던 것을 훑을 때 한 건씩
                # 왕복하면 그 자체가 소음이다. 방 번호 순으로 돌려준다 — 친
                # 순서가 아니라 방에서 일어난 순서로 읽는 것이 맞다.
                messages = [client.message(ref) for ref in sorted(set(args.ref))]
            else:
                messages = client.timeline(args.count, after_project_seq=args.after)
            print(
                json.dumps(
                    compact_history(workspace_id, messages),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        elif args.command == "board":
            client = PMClient(
                config["server"], registry,
                caller_id=binding["principal_id"],
            )
            board = client.board()
            mine = active_project(registry, binding["principal_id"])
            if args.board_command == "add":
                node = client.create_board_node(
                    project_id=mine, title=" ".join(args.title),
                )
                print(ticket_line(client.board(), node["id"]))
            elif args.board_command in ("start", "done"):
                status = "active" if args.board_command == "start" else "done"
                node_id = resolve_ticket(board, args.ticket, mine)
                client.update_board_node(node_id, status=status)
                print(ticket_line(client.board(), node_id))
            elif args.board_command in ("wait", "unwait"):
                node_id = resolve_ticket(board, args.ticket, mine)
                blocker_id = resolve_ticket(board, args.blocker, mine)
                if args.board_command == "wait":
                    client.link_board_nodes(node_id=node_id, waits_for=blocker_id)
                else:
                    client.unlink_board_nodes(node_id=node_id, waits_for=blocker_id)
                print(ticket_line(client.board(), node_id))
            else:
                print(render_board(board, you=mine, role=own_role_name(binding)))
        elif args.command == "permission-gate":
            print(json.dumps(permission_gate(config, registry, binding, args.wait)))
        elif args.command == "permission-clear":
            print(json.dumps(permission_clear(config, registry, binding)))
        elif args.command in ("reply", "send", "request"):
            if args.command == "reply":
                in_reply_to = reply_reference(args.ref)
                given_project = None
            else:
                in_reply_to = args.reply
                given_project = args.project
            mine = active_project(registry, binding["principal_id"])
            client = PMClient(
                config["server"], registry, workspace_id=mine,
                caller_id=binding["principal_id"],
            )
            client.workspace_id = resolve_project(client, given_project, mine)
            role_ids, direct, cc, cc_ids = addressing(client, args)
            if not role_ids and not direct:
                direct = default_recipients(client, args.command)
            result = client.send_as(
                binding["local_name"], None, " ".join(args.body),
                recipient_ids=direct,
                role_ids=role_ids,
                reference_ids=cc,
                absolute_reference_ids=cc_ids,
                kind="pm_request" if args.command == "request" else "message",
                reply_level=args.level if args.command == "request" else "r1",
                in_reply_to_project_seq=in_reply_to,
                track=args.track,
                tags=args.tag,
                inherit_context=not args.no_inherit_context,
            )
            warn_if_nobody_received(result, role_ids)
            print(json.dumps(
                stored_echo(result, roles=role_ids, in_reply_to=in_reply_to),
                ensure_ascii=False,
            ))
        elif args.command == "shared":
            values = PMClient(
                config["server"], registry,
                workspace_id=active_project(registry, binding["principal_id"]),
            ).shared(args.keys)
            found = {item["key"] for item in values}
            print(
                json.dumps(
                    {
                        "shared": {
                            item["key"]: item["value"] for item in values
                        },
                        "missing": [key for key in args.keys if key not in found],
                    },
                    ensure_ascii=False,
                )
            )
        else:
            client = PMClient(
                config["server"], registry,
                workspace_id=active_project(registry, binding["principal_id"]),
            )
            text = " ".join(args.text)
            if args.work_command == "start":
                result = client.start_work(binding["local_name"], text)
            else:
                result = client.update_work(
                    binding["local_name"],
                    text,
                    done=args.work_command == "done",
                )
            print(
                json.dumps(
                    {
                        "work": result["id"],
                        "status": result["status"],
                        "elapsed_seconds": result["elapsed_seconds"],
                        "token_usage": result["token_usage"],
                    }
                )
            )
    except PMServerError as error:
        if "args" in locals() and args.command in {"reply", "send", "request"}:
            raise SystemExit(write_error_message(error)) from error
        raise SystemExit(str(error)) from error
    except RuntimeError as error:
        raise SystemExit(str(error)) from error
    finally:
        if "registry" in locals():
            registry.close()


if __name__ == "__main__":
    main()
