from __future__ import annotations

import json
import urllib.error
import urllib.request
import urllib.parse
from dataclasses import dataclass
from threading import Event

from websockets.sync.client import connect

from .registry import LocalRegistry
from .server_url import validate_server_url


class ServerError(RuntimeError):
    pass


@dataclass
class InboxWatcher:
    server_url: str
    recipient_id: str
    registry: LocalRegistry

    def __post_init__(self) -> None:
        self.server_url = validate_server_url(self.server_url)

    def _http_post(self, path: str, payload: dict) -> dict:
        request = urllib.request.Request(
            f"{self.server_url.rstrip('/')}{path}",
            data=json.dumps(payload).encode(),
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.load(response)
        except (urllib.error.URLError, json.JSONDecodeError) as error:
            raise ServerError(str(error)) from error

    def _http_get(self, path: str, query: dict) -> dict | list:
        url = f"{self.server_url.rstrip('/')}{path}?{urllib.parse.urlencode(query)}"
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                return json.load(response)
        except (urllib.error.URLError, json.JSONDecodeError) as error:
            raise ServerError(str(error)) from error

    def read_messages(self, current_surface_id: str | None) -> list[dict]:
        binding = self.registry.binding(self.recipient_id) or (
            self.registry.binding_for_principal(self.recipient_id)
        )
        if binding is None:
            raise LookupError(f"active binding not found: {self.recipient_id}")
        if not current_surface_id or current_surface_id != binding["surface_id"]:
            raise PermissionError("read must run inside the bound cmux surface")
        state = self._http_get(f"/v1/inbox/state/{self.recipient_id}", {})
        assert isinstance(state, dict)
        existing_claim = self.registry.claim(self.recipient_id)
        after = int(state["processed_seq"])
        if existing_claim:
            after = max(after, int(existing_claim["through_seq"]))
        messages = self._http_get(
            "/v1/messages", {"recipient": self.recipient_id, "after": after}
        )
        assert isinstance(messages, list)
        if messages:
            self.registry.claim_inbox(
                self.recipient_id,
                max(int(message["seq"]) for message in messages),
                binding["agent_session_id"],
            )
            return messages
        # 볼 것이 없으면 그 깨우기는 소진된 것이다. 안 지우면 게이트가
        # wake_unconfirmed 로 이후 깨우기를 TTL 10분 동안 전부 거부한다.
        #
        # Stop 훅을 쓰는 저장소에서는 이게 예외가 아니라 상례다. 훅이 턴 끝마다
        # 인박스를 비우므로, 그 직후 도착한 깨우기는 읽을 것이 없는 채로 뜬다.
        # 그러면 깨우기 한 번마다 10분씩 눈이 먼다. 2026-08-19 에 PM 이 그 창에
        # 두 건을 보냈고 둘 다 안 갔다.
        outstanding = self.registry.outstanding_wake(self.recipient_id)
        if outstanding is not None:
            self.registry.mark_wake_processed(
                self.recipient_id, int(outstanding["through_seq"])
            )
        return messages

    def read_current(self, current_surface_id: str | None) -> tuple[dict, list[dict]]:
        if not current_surface_id:
            raise PermissionError("current cmux surface is unavailable")
        binding = self.registry.binding_for_surface(current_surface_id)
        if binding is None:
            raise PermissionError("current cmux context is not bound to Fungis")
        if binding["local_name"] != self.recipient_id:
            raise PermissionError("current cmux context recipient mismatch")
        return binding, self.read_messages(current_surface_id)

    def ack_processed(self, through_seq: int) -> dict:
        state = self._http_post(
            "/v1/inbox/ack-processed",
            {"recipient_id": self.recipient_id, "through_seq": through_seq},
        )
        self.registry.clear_processed(self.recipient_id, through_seq)
        self.registry.mark_wake_processed(self.recipient_id, through_seq)
        self.registry.clear_claim(self.recipient_id, through_seq)
        return state

    def reconcile_state(self) -> dict:
        state = self._http_get(f"/v1/inbox/state/{self.recipient_id}", {})
        assert isinstance(state, dict)
        processed_seq = int(state["processed_seq"])
        if processed_seq > 0:
            self.registry.clear_processed(self.recipient_id, processed_seq)
            self.registry.mark_wake_processed(self.recipient_id, processed_seq)
            self.registry.clear_claim(self.recipient_id, processed_seq)
        return state

    def websocket_url(self) -> str:
        base = self.server_url.rstrip("/")
        if base.startswith("https://"):
            base = "wss://" + base.removeprefix("https://")
        elif base.startswith("http://"):
            base = "ws://" + base.removeprefix("http://")
        else:
            raise ValueError("server_url must begin with http:// or https://")
        return (
            f"{base}/v1/events/{self.recipient_id}"
            f"?after={self.registry.event_cursor(self.recipient_id)}"
        )

    def handle_event(self, event: dict) -> bool:
        if event.get("recipient_id") != self.recipient_id:
            raise ValueError("event recipient mismatch")
        inserted = self.registry.record_event(event)
        # The local commit happens before this ACK. A crash between them is safe:
        # the server replays the event, and record_event is idempotent.
        state = self._http_post(
            "/v1/inbox/ack-received",
            {
                "recipient_id": self.recipient_id,
                "through_seq": event["through_seq"],
            },
        )
        if int(state.get("processed_seq", 0)) >= int(event["through_seq"]):
            self.registry.clear_processed(
                self.recipient_id, int(state["processed_seq"])
            )
        return inserted

    def run_forever(self, stop_event: Event | None = None) -> None:
        stop_event = stop_event or Event()
        while not stop_event.is_set():
            try:
                self.reconcile_state()
                with connect(self.websocket_url(), ping_interval=20, ping_timeout=20) as socket:
                    while not stop_event.is_set():
                        try:
                            raw_event = socket.recv(timeout=1)
                        except TimeoutError:
                            continue
                        if raw_event is None:
                            break
                        self.handle_event(json.loads(raw_event))
            except KeyboardInterrupt:
                return
            except Exception as error:
                if stop_event.is_set():
                    return
                print(f"watch disconnected: {error}; retrying", flush=True)
                stop_event.wait(2)
