from fungis_node.cmux import CmuxAgentCandidate
from fungis_node.completion import CompletionReconciler
from fungis_node.inbox import InboxWatcher
from fungis_node.registry import LocalRegistry


def candidate():
    return CmuxAgentCandidate(
        provider="claude",
        agent_session_id="session-1",
        surface_id="surface-uuid",
        surface_ref="surface:7",
        workspace_ref="workspace:1",
        title="Agent",
        tty="ttys007",
        cwd="/project",
        lifecycle="idle",
        binding_verified=True,
        verification_reason="agent_tty_matches_surface",
    )


class FakeInbox(InboxWatcher):
    def __init__(self, registry, messages=None):
        super().__init__("http://fungis.test", "agent-1", registry)
        self.messages = messages or []
        self.acks = []

    def _http_get(self, path, query):
        if path.startswith("/v1/inbox/state"):
            return {"processed_seq": 0}
        return self.messages

    def _http_post(self, path, payload):
        self.acks.append((path, payload))
        return {"processed_seq": payload["through_seq"], "pending_count": 0}


def stop_event(session_id="claude-session-1", phase="completed"):
    return {
        "category": "agent",
        "source": "claude",
        "name": "agent.hook.Stop",
        "payload": {
            "hook_event_name": "Stop",
            "phase": phase,
            "session_id": session_id,
        },
    }


def test_read_requires_bound_surface_and_claims_only_new_messages(tmp_path):
    registry = LocalRegistry(tmp_path / "node.db")
    registry.attach("agent-1", candidate())
    principal_id = registry.binding("agent-1")["principal_id"]
    inbox = FakeInbox(registry, [{"seq": 2, "body": "a"}, {"seq": 4, "body": "b"}])

    try:
        inbox.read_messages("some-other-surface")
    except PermissionError:
        pass
    else:
        raise AssertionError("a PM surface was allowed to claim the agent inbox")

    assert [item["body"] for item in inbox.read_messages("surface-uuid")] == ["a", "b"]
    assert registry.claim("agent-1")["through_seq"] == 4
    assert registry.binding_for_surface("surface-uuid")["local_name"] == "agent-1"
    assert registry.binding_for_surface("some-other-surface") is None


def test_completed_stop_acks_matching_claim_once(tmp_path, monkeypatch):
    registry = LocalRegistry(tmp_path / "node.db")
    registry.attach("agent-1", candidate())
    principal_id = registry.binding("agent-1")["principal_id"]
    registry.claim_inbox("agent-1", 4, "session-1")
    calls = []

    def fake_ack(self, through_seq):
        calls.append((self.recipient_id, through_seq))
        self.registry.clear_claim(self.recipient_id, through_seq)
        return {"processed_seq": through_seq}

    monkeypatch.setattr(InboxWatcher, "ack_processed", fake_ack)
    reconciler = CompletionReconciler("http://fungis.test", registry)
    assert reconciler.handle_event(stop_event())["through_seq"] == 4
    assert reconciler.handle_event(stop_event()) is None
    assert calls == [(principal_id, 4)]


def test_a_turn_confirms_a_sent_schedule_but_not_a_freshly_booked_one(tmp_path):
    """예약은 claim 을 안 만든다 — 턴이 도는 것으로 확인한다.

    claim 이 있을 때만 정리하면 예약은 확인될 길이 없어 간격마다 계속 나간다.
    반대로 아무 턴에나 지우면, `wake --in 20m` 을 걸고 턴을 끝내는 정상 흐름에서
    방금 건 예약이 그 자리에서 사라진다.
    """
    registry = LocalRegistry(tmp_path / "node.db")
    registry.attach("agent-1", candidate())
    reconciler = CompletionReconciler("http://fungis.test", registry)

    # 걸어만 둔 예약. 턴이 끝나도 살아 있어야 한다.
    registry.schedule_wake("agent-1", "2030-01-01T00:00:00.000Z")
    reconciler.handle_event(stop_event())
    assert registry.wake_schedule("agent-1") is not None

    # 보낸 뒤라면 그 턴이 곧 확인이다.
    registry.mark_schedule_sent("agent-1", "2030-01-01T00:00:00.000Z")
    reconciler.handle_event(stop_event())
    assert registry.wake_schedule("agent-1") is None
    registry.close()


def test_received_wrong_session_and_subagent_stop_do_not_ack(tmp_path, monkeypatch):
    registry = LocalRegistry(tmp_path / "node.db")
    registry.attach("agent-1", candidate())
    registry.claim_inbox("agent-1", 4, "session-1")
    calls = []
    monkeypatch.setattr(
        InboxWatcher, "ack_processed", lambda self, seq: calls.append(seq)
    )
    reconciler = CompletionReconciler("http://server", registry)
    assert reconciler.handle_event(stop_event(phase="received")) is None
    assert reconciler.handle_event(stop_event("claude-session-2")) is None
    subagent = stop_event()
    subagent["payload"]["hook_event_name"] = "SubagentStop"
    assert reconciler.handle_event(subagent) is None
    assert calls == []


def test_reconcile_clears_stale_local_pending_from_server_cursor(tmp_path):
    registry = LocalRegistry(tmp_path / "node.db")
    registry.record_event(
        {
            "event_id": "event-1",
            "event_seq": 1,
            "recipient_id": "agent-1",
            "through_seq": 4,
            "kind": "inbox_available",
        }
    )
    inbox = FakeInbox(registry)
    inbox._http_get = lambda path, query: {"processed_seq": 4}
    state = inbox.reconcile_state()
    assert state["processed_seq"] == 4
    assert registry.pending("agent-1") == []


def test_replayed_event_already_processed_does_not_remain_pending(tmp_path):
    registry = LocalRegistry(tmp_path / "node.db")
    inbox = FakeInbox(registry)
    event = {
        "event_id": "event-4",
        "event_seq": 4,
        "recipient_id": "agent-1",
        "through_seq": 4,
        "kind": "inbox_available",
    }
    assert inbox.handle_event(event) is True
    assert registry.pending("agent-1") == []
