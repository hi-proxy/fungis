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


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="fungis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Read and write the shared Fungis project room from an attached agent.",
        epilog="""Typical flow:
  fungis init --project PROJECT_ID
  fungis inbox
  fungis history 20
  fungis reply \"implementation complete\"
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
    board_actions = board.add_subparsers(dest="board_command")
    board_add = board_actions.add_parser("add", help="put one item on your track")
    board_add.add_argument("title", nargs="+")
    board_start = board_actions.add_parser("start", help="mark an item as in progress")
    board_start.add_argument("node_id")
    board_done = board_actions.add_parser("done", help="mark an item as finished")
    board_done.add_argument("node_id")
    ask = commands.add_parser(
        "ask", help="ask another project's lead a question on the board"
    )
    ask.add_argument("project", help="the project whose lead should answer")
    ask.add_argument("body", nargs="+")
    history = commands.add_parser("history", help="read shared project history")
    history.add_argument("count", nargs="?", type=int, default=20)
    history.add_argument("--after", type=int)
    history.add_argument("--project")
    reply = commands.add_parser(
        "reply", help="send a message (defaults to PM)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  fungis reply \"done\"
  fungis reply --role reviewer \"please review\"
  fungis reply --track feature/login --tag commit/abc123 \"implemented\"
  fungis reply --in-reply-to 42 \"verified\"

--in-reply-to inherits the parent message's track and tags by default.
Successful output echoes the exact body stored by the server.""",
    )
    add_message_arguments(reply)
    request = commands.add_parser(
        "request", help="request attention or approval",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Levels:
  r1  informational request
  r2  review or intervention requested (default)
  r3  explicit PM confirmation or approval required

Examples:
  fungis request --level r2 --role reviewer \"review this change\"
  fungis request --level r3 --track release/1.0 --tag commit/abc123 \"approve release\"

--in-reply-to inherits the parent message's track and tags by default.
Successful output echoes the exact body stored by the server.""",
    )
    add_message_arguments(request, include_level=True)
    shared = commands.add_parser("shared", help="read selected shared SSOT keys")
    shared.add_argument("keys", nargs="+")
    work = commands.add_parser("work", help="track structured work time and reports")
    work_commands = work.add_subparsers(dest="work_command", required=True)
    for name in ("start", "report", "done"):
        item = work_commands.add_parser(name)
        item.add_argument("text", nargs="+")
    return result


def add_message_arguments(
    command: argparse.ArgumentParser, *, include_level: bool = False
) -> None:
    command.add_argument("body", nargs="+", help="message body; maximum 20,000 characters")
    if include_level:
        command.add_argument(
            "--level", choices=("r1", "r2", "r3"), default="r2",
            help="attention level: r1 info, r2 review, r3 PM approval",
        )
    command.add_argument("--to", help="direct recipient local name or principal ID")
    command.add_argument(
        "--role", action="append", default=[],
        help="stable role recipient; repeat for multiple roles",
    )
    command.add_argument(
        "--reference", action="append", default=[],
        help="CC without inbox delivery; repeat for multiple references",
    )
    command.add_argument(
        "--track",
        help="one primary work thread, usually a branch such as feature/login",
    )
    command.add_argument(
        "--tag", action="append",
        help="secondary metadata; repeat, e.g. commit/abc123 or ticket/ARC-42",
    )
    command.add_argument(
        "--in-reply-to", type=int,
        help="parent message seq; inherits its track and tags unless disabled",
    )
    command.add_argument(
        "--project", help="project ID override; defaults to init/inbox project",
    )
    command.add_argument(
        "--no-inherit-context", action="store_true",
        help="do not inherit track and tags from --in-reply-to",
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
            f"- reply PM: {usage['reply_pm']}",
            f"- message role: {usage['message_role']}",
            # CLI는 소스를 매번 읽지만 서버는 재시작해야 바뀐다. 그 사이에
            # 새 CLI가 옛 서버를 만나므로 없는 키로 죽지 않게 둔다.
            "- copy role (listen only): "
            + usage.get("copy_role", 'fungis reply --ref ROLE "..."'),
            f"- request review/approval: {usage['request_review']} / {usage['request_approval']}",
            f"- work: {usage['work_start']} / {usage['work_report']} / {usage['work_done']}",
            # 보드에 올리는 것은 그 방의 몫이다. 안 알려주면 PM이 대신 쳐 넣게
            # 되고, 그러면 보드는 PM이 이미 아는 것만 담는다.
            '- board: fungis board / fungis board add "..." / '
            "fungis board start ID / fungis board done ID",
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
                "여러 방에서 왔다. 답할 때 --project로 방을 지정한다. "
                "각 메시지의 project 값을 쓰면 된다.",
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


def compact_board(board: list[dict]) -> dict:
    """보드를 에이전트가 읽을 모양으로 줄인다.

    막힌 노드에는 무엇을 기다리는지와 **그 방에 물어보는 명령**을 같이 싣는다.
    "archivia를 기다림"까지만 주면 에이전트가 누구에게 어떻게 물을지 한 번 더
    생각해야 한다. 명령이 같이 오면 생각할 것이 없다.
    """
    titles = {
        node["id"]: (track["project_id"], node["title"], node["status"])
        for track in board
        for node in track["nodes"]
    }
    tracks = []
    for track in board:
        nodes = []
        for node in track["nodes"]:
            item = {
                "id": node["id"],
                "title": node["title"],
                "state": node["state"],
            }
            blocked = []
            for blocker_id in node.get("blocked_by", []):
                project_id, title, status = titles.get(
                    blocker_id, (None, blocker_id, "unknown")
                )
                entry = {"project": project_id, "title": title, "status": status}
                if project_id and project_id != track["project_id"]:
                    entry["ask"] = f'fungis ask {project_id} "..."'
                blocked.append(entry)
            if blocked:
                item["waiting_for"] = blocked
            nodes.append(item)
        tracks.append({"project": track["project_id"], "nodes": nodes})
    return {"board": tracks}


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


def write_error_message(error: Exception) -> str:
    return (
        f"{error}\n"
        "Hint: initialize or refresh project context first: "
        "fungis init --project PROJECT_ID. "
        "Then retry reply/request; use fungis history 20 to verify room context."
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
            if len(rooms) == 1:
                registry.set_state(
                    f"active_project:{binding['principal_id']}", rooms.pop()
                )
        elif args.command == "history":
            if not 1 <= args.count <= 500:
                raise RuntimeError("history count must be between 1 and 500")
            workspace_id = args.project or active_project(
                registry, binding["principal_id"]
            )
            # 에이전트가 자기 이름으로 읽는다. PM 이름을 빌리면 아무 방이나
            # 열린다.
            client = PMClient(
                config["server"], registry, workspace_id=workspace_id,
                caller_id=binding["principal_id"],
            )
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
            if args.board_command == "add":
                node = client.create_board_node(
                    project_id=active_project(registry, binding["principal_id"]),
                    title=" ".join(args.title),
                )
                print(json.dumps(
                    {"added": node["id"], "title": node["title"]},
                    ensure_ascii=False, separators=(",", ":"),
                ))
            elif args.board_command in ("start", "done"):
                status = "active" if args.board_command == "start" else "done"
                node = client.update_board_node(args.node_id, status=status)
                print(json.dumps(
                    {"node": node["id"], "state": status},
                    ensure_ascii=False, separators=(",", ":"),
                ))
            else:
                print(json.dumps(
                    compact_board(client.board()),
                    ensure_ascii=False, separators=(",", ":"),
                ))
        elif args.command == "ask":
            client = PMClient(
                config["server"], registry,
                caller_id=binding["principal_id"],
            )
            hq = client.hq()
            if hq is None:
                raise RuntimeError("no board to ask on yet")
            lead = client.lead_of(args.project)
            if lead is None or not lead.get("agent_id"):
                # lead 자리가 비면 PM이 받는다. HQ에 남으니 PM이 거기서 본다.
                raise RuntimeError(
                    f"{args.project} has no lead right now — ask the PM instead"
                )
            client.workspace_id = hq["id"]
            result = client.send_as(
                binding["local_name"], lead["agent_id"], " ".join(args.body)
            )
            print(json.dumps(stored_echo(result), ensure_ascii=False))
        elif args.command == "permission-gate":
            print(json.dumps(permission_gate(config, registry, binding, args.wait)))
        elif args.command == "permission-clear":
            print(json.dumps(permission_clear(config, registry, binding)))
        elif args.command == "reply":
            workspace_id = args.project or active_project(
                registry, binding["principal_id"]
            )
            client = PMClient(config["server"], registry, workspace_id=workspace_id)
            recipient = args.to or (None if args.role else str(client.pm_id))
            result = client.send_as(
                binding["local_name"], recipient, " ".join(args.body),
                reference_ids=args.reference,
                in_reply_to_project_seq=args.in_reply_to,
                track=args.track,
                tags=args.tag,
                inherit_context=not args.no_inherit_context,
                role_ids=args.role,
            )
            print(json.dumps(
                stored_echo(result, roles=args.role, in_reply_to=args.in_reply_to),
                ensure_ascii=False,
            ))
        elif args.command == "request":
            workspace_id = args.project or active_project(
                registry, binding["principal_id"]
            )
            client = PMClient(config["server"], registry, workspace_id=workspace_id)
            recipient = args.to or (None if args.role else str(client.pm_id))
            result = client.send_as(
                binding["local_name"],
                recipient,
                " ".join(args.body),
                kind="pm_request",
                reply_level=args.level,
                reference_ids=args.reference,
                in_reply_to_project_seq=args.in_reply_to,
                track=args.track,
                tags=args.tag,
                inherit_context=not args.no_inherit_context,
                role_ids=args.role,
            )
            print(json.dumps(
                stored_echo(result, roles=args.role, in_reply_to=args.in_reply_to),
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
        if "args" in locals() and args.command in {"reply", "request"}:
            raise SystemExit(write_error_message(error)) from error
        raise SystemExit(str(error)) from error
    except RuntimeError as error:
        raise SystemExit(str(error)) from error
    finally:
        if "registry" in locals():
            registry.close()


if __name__ == "__main__":
    main()
