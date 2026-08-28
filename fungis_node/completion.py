from __future__ import annotations

from dataclasses import dataclass

from .inbox import InboxWatcher
from .registry import LocalRegistry


def normalized_session_id(event: dict) -> str | None:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return None
    source = event.get("source")
    prefix = f"{source}-" if isinstance(source, str) else ""
    return session_id.removeprefix(prefix)


def is_completed_main_turn(event: dict) -> bool:
    payload = event.get("payload")
    return bool(
        event.get("category") == "agent"
        and isinstance(payload, dict)
        and payload.get("hook_event_name") == "Stop"
        and payload.get("phase") == "completed"
    )


@dataclass
class CompletionReconciler:
    server_url: str
    registry: LocalRegistry

    def handle_event(self, event: dict) -> dict | None:
        if not is_completed_main_turn(event):
            return None
        session_id = normalized_session_id(event)
        if session_id is None:
            return None
        # 예약은 claim 을 안 만든다. claim 이 있을 때만 정리하면 예약은 확인될
        # 길이 없어 간격마다 계속 나간다.
        self.registry.clear_schedule_for_session(session_id)
        claim = self.registry.claim_for_session(session_id)
        if claim is None:
            return None
        through_seq = int(claim["through_seq"])
        state = InboxWatcher(
            self.server_url, claim["recipient_id"], self.registry
        ).ack_processed(through_seq)
        return {
            "recipient_id": claim["recipient_id"],
            "through_seq": through_seq,
            "state": state,
        }
