from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .cmux import CmuxAdapter
from .completion import CompletionReconciler
from .demo import DaemonLauncher, DemoLauncher, StackLauncher
from .gate import IdleGate
from .inbox import InboxWatcher
from .install import install_agent_cli
from .pm import PMClient
from .pm_tui import run_pm_tui
from .registry import LocalRegistry
from .server_url import validate_server_url
from .supervisor import NodeSupervisor
from .tui import run_tui
from .web import run_web


def add_wake_flags(command: argparse.ArgumentParser) -> None:
    """깨우기는 기본으로 보낸다.

    예전에는 `--send`를 켜야 보냈다. 그래서 그 플래그 없이 손으로 띄운 daemon이
    판정만 하고 아무것도 보내지 않았고, health는 200이라 앱도 사람도 정상으로
    봤다. 조용히 아무것도 안 하는 것이 기본값이면 안 된다. 안 보내려면 이제
    `--dry-run`으로 말해야 한다. `--send`는 옛 앱 번들이 그대로 넘기므로
    받아만 주고 무시한다.
    """
    command.add_argument("--send", action="store_true", help=argparse.SUPPRESS)
    command.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="evaluate wakes without sending them",
    )


def sends_wakes(args: argparse.Namespace) -> bool:
    return not getattr(args, "dry_run", False)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="fungis-node")
    result.add_argument(
        "--registry",
        default=os.environ.get("FUNGIS_NODE_DB", ".fungis-node.db"),
    )
    subcommands = result.add_subparsers(dest="command", required=True)
    discover = subcommands.add_parser("discover", help="list running cmux agents")
    discover.add_argument("--diagnostic", action="store_true")
    attach = subcommands.add_parser("attach", help="attach a discovered agent")
    attach.add_argument("name")
    attach.add_argument("--index", type=int, required=True)
    detach = subcommands.add_parser("detach", help="detach without touching terminal")
    detach.add_argument("name")
    subcommands.add_parser("list", help="list local bindings")
    subcommands.add_parser("identity", help="show stable node and server identities")
    subcommands.add_parser("ui", help="select open cmux agents and manage connections")
    chat = subcommands.add_parser("chat", help="open the PM chat and delivery status UI")
    chat.add_argument("--server", default="http://127.0.0.1:8787")
    chat.add_argument("--pm-name", default="PM")
    gui = subcommands.add_parser("gui", help="open the local PM web control API")
    gui.add_argument("--server", default="http://127.0.0.1:8787")
    gui.add_argument("--host", default="127.0.0.1")
    gui.add_argument("--port", type=int, default=8790)
    demo = subcommands.add_parser(
        "demo", help="start local server, node supervisor, and PM chat together"
    )
    demo.add_argument("--server", default="http://127.0.0.1:8787")
    demo.add_argument("--server-db", default=".fungis-server.db")
    demo.add_argument("--pm-name", default="PM")
    add_wake_flags(demo)
    stack = subcommands.add_parser(
        "stack", help="run server and node independently from the PM chat UI"
    )
    stack.add_argument("--server", default="http://127.0.0.1:8787")
    stack.add_argument("--server-db", default=".fungis-server.db")
    add_wake_flags(stack)
    daemon = subcommands.add_parser(
        "daemon", help="run server, node supervisor, and local control API"
    )
    daemon.add_argument("--server", default="http://127.0.0.1:8787")
    daemon.add_argument("--server-db", default=".fungis-server.db")
    daemon.add_argument("--host", default="127.0.0.1")
    daemon.add_argument("--port", type=int, default=8790)
    add_wake_flags(daemon)
    install_cli = subcommands.add_parser(
        "install-agent-cli", help="install the short fungis command"
    )
    install_cli.add_argument("--server", default="http://127.0.0.1:8787")
    watch = subcommands.add_parser("watch", help="persist inbox events and ACK them")
    watch.add_argument("recipient_id")
    watch.add_argument("--server", default="http://127.0.0.1:8787")
    read = subcommands.add_parser(
        "read", help="read only new messages and claim them for this agent turn"
    )
    read.add_argument("recipient_id")
    read.add_argument("--server", default="http://127.0.0.1:8787")
    read_current = subcommands.add_parser(
        "read-current", help="read inbox for the current bound cmux context"
    )
    read_current.add_argument("--server", default="http://127.0.0.1:8787")
    reply_current = subcommands.add_parser(
        "reply-current", help="reply from the current bound agent"
    )
    reply_current.add_argument("body", nargs="+")
    reply_current.add_argument("--to", default="pm-local")
    reply_current.add_argument("--server", default="http://127.0.0.1:8787")
    completions = subcommands.add_parser(
        "watch-completions", help="ACK claimed messages after a completed agent turn"
    )
    completions.add_argument("--server", default="http://127.0.0.1:8787")
    run = subcommands.add_parser(
        "run", help="run inbox, idle gate, and completion ACK as one node"
    )
    run.add_argument("recipients", nargs="*")
    run.add_argument("--server", default="http://127.0.0.1:8787")
    run.add_argument("--gate-interval", type=float, default=2.0)
    run.add_argument("--settle-seconds", type=float, default=5.0)
    add_wake_flags(run)
    pending = subcommands.add_parser("pending", help="show durable pending events")
    pending.add_argument("--recipient")
    gate = subcommands.add_parser("gate", help="evaluate the idle wake gate")
    gate.add_argument("recipient_id")
    gate.add_argument("--settle-seconds", type=float, default=5.0)
    gate.add_argument(
        "--send",
        action="store_true",
        help="actually send one wake; default is dry-run",
    )
    processed = subcommands.add_parser(
        "ack-processed", help="ACK processed messages and clear local pending"
    )
    processed.add_argument("recipient_id")
    processed.add_argument("through_seq", type=int)
    processed.add_argument("--server", default="http://127.0.0.1:8787")
    return result


def main() -> None:
    argument_parser = parser()
    args = argument_parser.parse_args()
    if hasattr(args, "server"):
        try:
            args.server = validate_server_url(args.server)
        except ValueError as error:
            argument_parser.error(str(error))
    adapter = CmuxAdapter()
    registry = LocalRegistry(Path(args.registry))
    if args.command == "discover":
        candidates = adapter.discover_agents(include_hidden=args.diagnostic)
        output = [
            {"index": index, **candidate.public_dict(diagnostic=args.diagnostic)}
            for index, candidate in enumerate(candidates, start=1)
        ]
        print(json.dumps(output, ensure_ascii=False, indent=2))
    elif args.command == "attach":
        candidates = adapter.discover_agents()
        if args.index < 1 or args.index > len(candidates):
            raise SystemExit(f"invalid index: {args.index}")
        result = registry.attach(args.name, candidates[args.index - 1])
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "detach":
        if not registry.detach(args.name):
            raise SystemExit(f"active binding not found: {args.name}")
        print(json.dumps({"detached": args.name}))
    elif args.command == "list":
        print(json.dumps(registry.list(), ensure_ascii=False, indent=2))
    elif args.command == "identity":
        print(
            json.dumps(
                {
                    "node_id": registry.node_id(),
                    "pm_principal_id": registry.pm_principal_id(),
                    "agents": [
                        {
                            "local_name": binding["local_name"],
                            "principal_id": binding["principal_id"],
                        }
                        for binding in registry.list()
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "ui":
        run_tui(registry, adapter)
    elif args.command == "chat":
        run_pm_tui(PMClient(args.server, registry, pm_name=args.pm_name), adapter)
    elif args.command == "gui":
        registry.close()
        run_web(Path(args.registry), args.server, args.host, args.port)
    elif args.command == "demo":
        registry.close()
        try:
            DemoLauncher(
                registry_path=Path(args.registry),
                server_db_path=Path(args.server_db),
                server_url=args.server,
                send_wakes=sends_wakes(args),
                pm_name=args.pm_name,
            ).run()
        except RuntimeError as error:
            raise SystemExit(str(error)) from error
    elif args.command == "stack":
        registry.close()
        try:
            StackLauncher(
                registry_path=Path(args.registry),
                server_db_path=Path(args.server_db),
                server_url=args.server,
                send_wakes=sends_wakes(args),
            ).run()
        except RuntimeError as error:
            raise SystemExit(str(error)) from error
    elif args.command == "daemon":
        registry.close()
        try:
            DaemonLauncher(
                registry_path=Path(args.registry),
                server_db_path=Path(args.server_db),
                server_url=args.server,
                send_wakes=sends_wakes(args),
                control_host=args.host,
                control_port=args.port,
            ).run()
        except RuntimeError as error:
            raise SystemExit(str(error)) from error
    elif args.command == "install-agent-cli":
        registry.close()
        print(
            json.dumps(
                install_agent_cli(
                    registry_path=Path(args.registry), server_url=args.server
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "watch":
        InboxWatcher(args.server, args.recipient_id, registry).run_forever()
    elif args.command == "read":
        messages = InboxWatcher(
            args.server, args.recipient_id, registry
        ).read_messages(adapter.current_surface_id())
        print(json.dumps(messages, ensure_ascii=False, indent=2))
    elif args.command in ("read-current", "reply-current"):
        context_surface = adapter.current_surface_id()
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
            raise SystemExit("current cmux context is not connected to Fungis")
        if args.command == "read-current":
            messages = InboxWatcher(
                args.server, binding["local_name"], registry
            ).read_messages(canonical_surface)
            concise = [
                {
                    "seq": message["seq"],
                    "from": message.get("sender_name", message["sender_id"]),
                    "body": message["body"],
                }
                for message in messages
            ]
            print(json.dumps({"messages": concise}, ensure_ascii=False))
            if concise:
                prefix = (
                    f"{Path(sys.executable)} -m fungis_node.cli --registry "
                    f"{Path(args.registry).resolve()} reply-current --server {args.server}"
                )
                print(f"Reply with: {prefix} YOUR_MESSAGE")
        else:
            client = PMClient(args.server, registry)
            result = client.send_as(
                binding["local_name"], args.to, " ".join(args.body)
            )
            print(json.dumps({"sent": result["seq"], "to": args.to}))
    elif args.command == "watch-completions":
        reconciler = CompletionReconciler(args.server, registry)
        for event in adapter.agent_events():
            result = reconciler.handle_event(event)
            if result:
                print(json.dumps(result, ensure_ascii=False), flush=True)
    elif args.command == "run":
        registry.close()
        NodeSupervisor(
            registry_path=Path(args.registry),
            server_url=args.server,
            cmux=adapter,
            recipients=set(args.recipients) if args.recipients else None,
            gate_interval=args.gate_interval,
            settle_seconds=args.settle_seconds,
            send_wakes=sends_wakes(args),
        ).run_forever()
    elif args.command == "pending":
        print(
            json.dumps(
                registry.pending(args.recipient), ensure_ascii=False, indent=2
            )
        )
    elif args.command == "gate":
        decision = IdleGate(
            registry, adapter, settle_seconds=args.settle_seconds
        ).run(args.recipient_id, send=args.send)
        output = decision.as_dict()
        output["mode"] = "send" if args.send else "dry-run"
        print(json.dumps(output, ensure_ascii=False, indent=2))
    elif args.command == "ack-processed":
        state = InboxWatcher(
            args.server, args.recipient_id, registry
        ).ack_processed(args.through_seq)
        print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
