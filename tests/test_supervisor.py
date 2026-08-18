import threading
from pathlib import Path

from fungis_node.cmux import CmuxAgentCandidate
from fungis_node.registry import LocalRegistry
from fungis_node.supervisor import NodeSupervisor
from fungis_node.inbox import InboxWatcher


def candidate():
    return CmuxAgentCandidate(
        provider="codex",
        agent_session_id="session-1",
        surface_id="surface-1",
        surface_ref="surface:1",
        workspace_ref="workspace:1",
        title="Agent",
        tty="ttys001",
        cwd="/project",
        lifecycle="idle",
        binding_verified=True,
        verification_reason="agent_tty_matches_surface",
    )


class RecordingSupervisor(NodeSupervisor):
    def __post_init__(self):
        self.started = []
        self.gated = []

    def _inbox_worker(self, recipient_id, stop_event):
        self.started.append(recipient_id)
        stop_event.wait(2)

    def _completion_worker(self, stop_event):
        stop_event.wait(2)

    def _run_gate(self, registry, recipient_id):
        self.gated.append(recipient_id)
        self.test_stop.set()


class WakeCmux:
    def __init__(self, current):
        self.current = current
        self.wakes = []

    def resolve_binding_candidate(self, **binding):
        return self.current

    def wake(self, surface_id, text):
        self.wakes.append((surface_id, text))

    def prompt_ready(self, surface_id):
        return True


def test_supervisor_discovers_attached_binding_and_stops_cleanly(tmp_path):
    path = tmp_path / "node.db"
    registry = LocalRegistry(path)
    registry.attach("agent-1", candidate())
    registry.close()
    stop = threading.Event()
    supervisor = RecordingSupervisor(path, "http://server", cmux=object())
    supervisor.__post_init__()
    supervisor.test_stop = stop
    supervisor.run_forever(stop)
    assert supervisor.started == ["agent-1"]
    assert supervisor.gated == ["agent-1"]


def test_supervisor_recipient_filter(tmp_path):
    path = tmp_path / "node.db"
    registry = LocalRegistry(path)
    registry.attach("agent-1", candidate())
    registry.close()
    stop = threading.Event()
    supervisor = RecordingSupervisor(
        path,
        "http://server",
        cmux=object(),
        recipients={"agent-2"},
        gate_interval=0.01,
    )
    supervisor.__post_init__()
    supervisor.test_stop = stop
    timer = threading.Timer(0.05, stop.set)
    timer.start()
    supervisor.run_forever(stop)
    timer.cancel()
    assert supervisor.started == []
    assert supervisor.gated == []


def test_supervisor_wake_contains_only_short_stable_command(tmp_path):
    path = tmp_path / "node.db"
    registry = LocalRegistry(path)
    current = candidate()
    registry.attach("agent-1", current)
    registry.record_event(
        {
            "event_id": "event-1",
            "event_seq": 1,
            "recipient_id": "agent-1",
            "through_seq": 1,
            "kind": "inbox_available",
        }
    )
    cmux = WakeCmux(current)
    supervisor = NodeSupervisor(
        path,
        "http://server",
        cmux,
        settle_seconds=0,
        send_wakes=True,
    )
    supervisor._run_gate(registry, "agent-1")
    assert cmux.wakes == [
        ("surface-1", "[fungis] inbox — run: fungis inbox")
    ]
    registry.close()


def test_supervisor_recovers_claim_when_reading_turn_ended_while_down(
    tmp_path, monkeypatch
):
    path = tmp_path / "node.db"
    registry = LocalRegistry(path)
    current = candidate()
    registry.attach("agent-1", current)
    registry.record_event(
        {
            "event_id": "event-1",
            "event_seq": 1,
            "recipient_id": "agent-1",
            "through_seq": 1,
            "kind": "inbox_available",
        }
    )
    registry.claim_inbox("agent-1", 1, "session-1")
    registry.record_wake("agent-1", 1)
    calls = []

    def fake_ack(self, through_seq):
        calls.append(through_seq)
        self.registry.clear_processed(self.recipient_id, through_seq)
        self.registry.mark_wake_processed(self.recipient_id, through_seq)
        self.registry.clear_claim(self.recipient_id, through_seq)
        return {"processed_seq": through_seq}

    monkeypatch.setattr(InboxWatcher, "ack_processed", fake_ack)
    cmux = WakeCmux(current)
    NodeSupervisor(path, "http://fungis.test", cmux)._run_gate(registry, "agent-1")
    assert calls == [1]
    assert registry.claim("agent-1") is None
    assert registry.outstanding_wake("agent-1") is None
    registry.close()


def test_daemon_starts_with_no_connected_agents(tmp_path, monkeypatch):
    """앱이 이 daemon을 띄우고, 에이전트를 연결하는 길은 그 앱뿐이다.

    여기서 막으면 처음 켜는 사람은 daemon도 못 띄우고 에이전트도 못 붙인다.
    앱은 stderr를 버려서 화면에는 이유 없는 실패만 남는다.
    """
    from fungis_node import demo

    served = threading.Event()
    monkeypatch.setattr(demo.DaemonLauncher, "_start_server", lambda self: None)
    monkeypatch.setattr(demo.NodeSupervisor, "run_forever", lambda *a, **k: None)
    monkeypatch.setattr(demo, "run_web", lambda *a, **k: served.set())

    demo.DaemonLauncher(
        registry_path=tmp_path / "node.db",
        server_db_path=tmp_path / "server.db",
        server_url="http://127.0.0.1:8787",
    ).run()

    assert served.is_set()


def test_daemon_sends_wakes_unless_dry_run_is_asked_for():
    """조용히 아무것도 안 하는 것이 기본값이면 안 된다.

    예전에는 `--send`가 opt-in이라, 그 플래그 없이 손으로 띄운 daemon이 판정만
    하고 깨우기를 한 건도 보내지 않았다. health는 200이고 로그에는 eligible이
    찍혀서 앱도 사람도 정상으로 봤다. 사용자는 메시지가 안 온다는 것으로만
    알았다.
    """
    from fungis_node.cli import parser, sends_wakes

    for command in ("daemon", "demo", "stack"):
        args = parser().parse_args([command])
        assert sends_wakes(args), f"{command}는 기본으로 깨우기를 보내야 한다"

        asked = parser().parse_args([command, "--dry-run"])
        assert not sends_wakes(asked), f"{command} --dry-run은 보내지 않아야 한다"

        # 옛 앱 번들이 그대로 넘기는 플래그다. 받아주되 동작을 바꾸지 않는다.
        legacy = parser().parse_args([command, "--send"])
        assert sends_wakes(legacy), f"{command} --send는 계속 받아야 한다"


def test_supervisor_sends_wakes_when_the_caller_says_nothing():
    """호출부가 깜빡해도 조용히 먹통이 되지는 않아야 한다."""
    supervisor = NodeSupervisor(
        registry_path=Path("unused.db"),
        server_url="http://127.0.0.1:8787",
        cmux=None,
    )
    assert supervisor.send_wakes
