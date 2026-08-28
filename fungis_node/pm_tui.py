from __future__ import annotations

import curses
import time
from dataclasses import dataclass, field
from typing import Any

from .pm import PMClient, delivery_status
from .tui import ConnectionController, _put
from .terminal import TerminalAdapter


@dataclass
class PMController:
    client: PMClient
    connections: ConnectionController | None = None
    targets: list[dict] = field(default_factory=list)
    agent_statuses: list[dict] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)
    attention: list[dict] = field(default_factory=list)
    attention_selected: int = 0
    shared_values: list[dict] = field(default_factory=list)
    shared_selected: int = 0
    work_items: list[dict] = field(default_factory=list)
    selected: int = 0
    status: str = ""
    view: str = "chat"

    def refresh(self, *, sync: bool = False) -> None:
        try:
            if sync or not self.targets:
                self.targets = self.client.sync_connections()
                self.selected = min(self.selected, max(0, len(self.targets) - 1))
            self.messages = self.client.timeline()
            self.attention = self.client.attention()
            self.attention_selected = min(
                self.attention_selected, max(0, len(self.attention) - 1)
            )
            self.agent_statuses = self.client.agent_statuses()
            self.shared_values = self.client.shared()
            self.work_items = self.client.work_items()
            self.shared_selected = min(
                self.shared_selected, max(0, len(self.shared_values) - 1)
            )
            self.status = "connected"
        except Exception as error:
            self.status = f"server unavailable: {error}"

    def move(self, delta: int) -> None:
        if self.targets:
            self.selected = (self.selected + delta) % len(self.targets)

    def move_attention(self, delta: int) -> None:
        if self.attention:
            self.attention_selected = (
                self.attention_selected + delta
            ) % len(self.attention)

    def send(self, body: str) -> None:
        if not self.targets:
            self.status = "connect an agent in fungis-node ui first"
            return
        if not body.strip():
            return
        try:
            target = self.targets[self.selected]["local_name"]
            self.client.send(target, body.strip())
            self.messages = self.client.timeline()
            self.status = f"sent to {target}"
        except Exception as error:
            self.status = f"send failed: {error}"

    def toggle_view(self) -> None:
        views = ("chat", "agents", "shared", "work")
        self.view = views[(views.index(self.view) + 1) % len(views)]
        if self.view == "agents" and self.connections:
            self.connections.refresh()

    def move_shared(self, delta: int) -> None:
        if self.shared_values:
            self.shared_selected = (
                self.shared_selected + delta
            ) % len(self.shared_values)

    def put_shared(self, key: str, value: str) -> None:
        if not key.strip() or not value.strip():
            return
        try:
            self.client.put_shared(key.strip(), value.strip())
            self.shared_values = self.client.shared()
            self.status = f"saved shared key {key.strip()}"
        except Exception as error:
            self.status = f"shared save failed: {error}"

    def delete_selected_shared(self) -> None:
        if not self.shared_values:
            return
        key = self.shared_values[self.shared_selected]["key"]
        try:
            self.client.delete_shared(key)
            self.shared_values = self.client.shared()
            self.shared_selected = min(
                self.shared_selected, max(0, len(self.shared_values) - 1)
            )
            self.status = f"deleted shared key {key}"
        except Exception as error:
            self.status = f"shared delete failed: {error}"

    def toggle_connection(self) -> None:
        if not self.connections:
            return
        self.connections.toggle_selected()
        self.status = self.connections.message
        self.refresh(sync=True)

    def answer_attention(self, body: str) -> None:
        if not self.attention or not body.strip():
            return
        request = self.attention[self.attention_selected]
        try:
            self.client.send(
                request["sender_id"], body.strip(), in_reply_to=request["seq"]
            )
            self.refresh()
            self.status = f"answered request #{request['seq']}"
        except Exception as error:
            self.status = f"answer failed: {error}"


def _input(screen: Any, prompt: str) -> str | None:
    height, width = screen.getmaxyx()
    value: list[str] = []
    screen.timeout(-1)
    curses.curs_set(1)
    try:
        while True:
            _put(screen, height - 2, 0, " " * max(0, width - 1))
            _put(screen, height - 2, 0, prompt + "".join(value), curses.A_BOLD)
            screen.move(height - 2, min(width - 1, len(prompt) + len(value)))
            screen.refresh()
            key = screen.get_wch()
            if key in ("\n", "\r"):
                return "".join(value)
            if key == "\x1b":
                return None
            if key in ("\b", "\x7f") or key == curses.KEY_BACKSPACE:
                if value:
                    value.pop()
            elif isinstance(key, str) and key.isprintable():
                value.append(key)
    finally:
        curses.curs_set(0)
        screen.timeout(500)


def _draw(screen: Any, controller: PMController) -> None:
    screen.erase()
    height, width = screen.getmaxyx()
    _put(screen, 0, 0, "Fungis · PM control", curses.A_BOLD)
    _put(
        screen,
        1,
        0,
        "Tab chat/agents/shared/work   arrows select   Enter act   a answer   e edit   d delete   f focus   q quit",
    )
    if controller.view == "agents":
        _draw_agents(screen, controller)
        return
    if controller.view == "shared":
        _draw_shared(screen, controller)
        return
    if controller.view == "work":
        _draw_work(screen, controller)
        return
    target_names = [target["local_name"] for target in controller.targets]
    if target_names:
        targets = "  ".join(
            f"[{name}]" if index == controller.selected else name
            for index, name in enumerate(target_names)
        )
    else:
        targets = "No connected agents"
    _put(screen, 3, 0, f"TO  {targets}", curses.A_REVERSE)
    _put(screen, 5, 0, f"ATTENTION ({len(controller.attention)})", curses.A_BOLD)
    if controller.attention:
        request = controller.attention[controller.attention_selected]
        labels = {"r1": "INFO", "r2": "REVIEW", "r3": "PM CONFIRM"}
        _put(
            screen,
            6,
            0,
            f"[{labels[request['reply_level']]}] #{request['seq']} "
            f"{request['sender_name']}: {request['body']}",
            curses.A_REVERSE if request["reply_level"] == "r3" else 0,
        )
    else:
        _put(screen, 6, 0, "No requests")
    _put(screen, 8, 0, "AGENT STATUS", curses.A_BOLD)
    status_text = "  ".join(
        f"{item['id']}:{item['lifecycle']} p={item['local_pending']} done={item['processed_seq']}"
        for item in controller.agent_statuses
    ) or "No connected agents"
    _put(screen, 9, 0, status_text)
    _put(screen, 11, 0, "TIMELINE", curses.A_BOLD)
    usable_rows = max(0, height - 15)
    visible = controller.messages[-usable_rows:]
    row = 12
    for message in visible:
        recipients = ",".join(
            recipient["display_name"] for recipient in message.get("recipients", [])
        )
        route = f"{message['sender_name']} → {recipients}"
        status = (
            "incoming"
            if message["sender_id"] != controller.client.pm_id
            else delivery_status(message)
        )
        _put(
            screen,
            row,
            0,
            f"#{message['seq']:<4} {route:<32} [{status:<9}] {message['body']}",
        )
        row += 1
    _put(screen, height - 1, 0, controller.status, curses.A_DIM)
    screen.refresh()


def _draw_agents(screen: Any, controller: PMController) -> None:
    height, _ = screen.getmaxyx()
    connections = controller.connections
    _put(screen, 3, 0, "OPEN AGENT TERMINALS", curses.A_BOLD)
    row = 4
    if not connections or not connections.candidates:
        _put(screen, row, 2, "No open agent terminal discovered")
        row += 1
    elif connections:
        for index, candidate in enumerate(connections.candidates):
            binding = connections.binding_for(candidate)
            marker = "●" if binding else "○"
            line = (
                f"{marker} {candidate.provider:<7} {candidate.lifecycle:<11} "
                f"{candidate.title}  {candidate.cwd or '-'}"
            )
            style = curses.A_REVERSE if index == connections.selected else 0
            _put(screen, row, 0, line, style)
            row += 1
            if row >= height - 6:
                break
    row += 1
    _put(screen, row, 0, "CONNECTED TO FUNGIS", curses.A_BOLD)
    row += 1
    for target in controller.targets:
        status = next(
            (
                item for item in controller.agent_statuses
                if item["id"] == target["local_name"]
            ),
            {},
        )
        _put(
            screen,
            row,
            0,
            f"● {target['local_name']:<20} {target['provider']:<7} "
            f"{status.get('lifecycle', target['lifecycle']):<11} "
            f"pending={status.get('local_pending', 0)}",
        )
        row += 1
    _put(screen, height - 1, 0, controller.status, curses.A_DIM)
    screen.refresh()


def _draw_shared(screen: Any, controller: PMController) -> None:
    height, width = screen.getmaxyx()
    _put(screen, 3, 0, "SHARED SSOT", curses.A_BOLD)
    _put(screen, 4, 0, "Key", curses.A_UNDERLINE)
    _put(screen, 4, min(28, width // 3), "Value", curses.A_UNDERLINE)
    row = 5
    if not controller.shared_values:
        _put(screen, row, 2, "No shared values. Press e to add one.")
    for index, item in enumerate(controller.shared_values):
        style = curses.A_REVERSE if index == controller.shared_selected else 0
        _put(screen, row, 0, f"{item['key']:<26} {item['value']}", style)
        row += 1
        if row >= height - 3:
            break
    _put(screen, height - 1, 0, controller.status, curses.A_DIM)
    screen.refresh()


def _elapsed(seconds: int) -> str:
    hours, remainder = divmod(max(0, seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _draw_work(screen: Any, controller: PMController) -> None:
    height, _ = screen.getmaxyx()
    _put(screen, 3, 0, "WORK TRACKING", curses.A_BOLD)
    _put(
        screen,
        4,
        0,
        "Agent                Status   Elapsed   Tokens    Title / Last report",
        curses.A_UNDERLINE,
    )
    row = 5
    if not controller.work_items:
        _put(screen, row, 2, "No work reports")
    for item in controller.work_items:
        token_text = (
            str(item["token_usage"])
            if item.get("token_usage") is not None
            else "unknown"
        )
        detail = item.get("last_report") or item["title"]
        _put(
            screen,
            row,
            0,
            f"{item['agent_name']:<20} {item['status']:<8} "
            f"{_elapsed(item['elapsed_seconds']):<9} {token_text:<9} "
            f"{item['title']} — {detail}",
        )
        row += 1
        if row >= height - 2:
            break
    _put(screen, height - 1, 0, controller.status, curses.A_DIM)
    screen.refresh()


def run_pm_tui(client: PMClient, cmux: TerminalAdapter | None = None) -> None:
    controller = PMController(
        client,
        ConnectionController(client.registry, cmux) if cmux else None,
    )

    def session(screen: Any) -> None:
        curses.curs_set(0)
        screen.keypad(True)
        screen.timeout(500)
        controller.refresh(sync=True)
        refreshed_at = time.monotonic()
        while True:
            _draw(screen, controller)
            key = screen.getch()
            if key in (ord("q"), 27):
                return
            if key == 9:
                controller.toggle_view()
            elif key in (curses.KEY_LEFT, curses.KEY_UP, ord("h"), ord("k")):
                if controller.view == "chat":
                    controller.move(-1)
                elif controller.view == "agents" and controller.connections:
                        controller.connections.move(-1)
                elif controller.view == "shared":
                    controller.move_shared(-1)
            elif key in (curses.KEY_RIGHT, curses.KEY_DOWN, ord("l"), ord("j")):
                if controller.view == "chat":
                    controller.move(1)
                elif controller.view == "agents" and controller.connections:
                        controller.connections.move(1)
                elif controller.view == "shared":
                    controller.move_shared(1)
            elif key in (curses.KEY_ENTER, 10, 13):
                if controller.view == "chat":
                    body = _input(screen, "message> ")
                    if body is not None:
                        controller.send(body)
                else:
                    if controller.view == "agents":
                        controller.toggle_connection()
            elif key == ord("[") and controller.view == "chat":
                controller.move_attention(-1)
            elif key == ord("]") and controller.view == "chat":
                controller.move_attention(1)
            elif key == ord("a") and controller.view == "chat":
                if controller.attention:
                    body = _input(screen, "answer> ")
                    if body is not None:
                        controller.answer_attention(body)
            elif key == ord("e") and controller.view == "shared":
                existing = (
                    controller.shared_values[controller.shared_selected]
                    if controller.shared_values
                    else None
                )
                key_text = _input(
                    screen, f"key [{existing['key'] if existing else ''}]> "
                )
                if key_text is not None:
                    key_text = key_text.strip() or (existing["key"] if existing else "")
                    value = _input(screen, "value> ")
                    if value is not None:
                        controller.put_shared(key_text, value)
            elif key == ord("d") and controller.view == "shared":
                if controller.shared_values:
                    key_text = controller.shared_values[controller.shared_selected]["key"]
                    confirmation = _input(screen, f"delete {key_text}? type yes> ")
                    if confirmation == "yes":
                        controller.delete_selected_shared()
            elif key == ord("f") and controller.view == "agents":
                if controller.connections:
                    controller.connections.focus_selected()
                    controller.status = controller.connections.message
            elif key == ord("r"):
                controller.refresh(sync=True)
                if controller.view == "agents" and controller.connections:
                    controller.connections.refresh()
                refreshed_at = time.monotonic()
            if time.monotonic() - refreshed_at >= 1:
                controller.refresh()
                refreshed_at = time.monotonic()

    curses.wrapper(session)
