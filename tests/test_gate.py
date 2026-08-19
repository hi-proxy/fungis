import sqlite3

from fungis_node.cmux import CmuxAdapter, CmuxAgentCandidate
from fungis_node.gate import IdleGate
from fungis_node.registry import LocalRegistry


class GateCmux(CmuxAdapter):
    def __init__(self, candidate, prompt_ready=False):
        self.candidate = candidate
        self.wakes = []
        self._prompt_ready = prompt_ready

    def discover_agents(self):
        return [self.candidate]

    def resolve_binding_candidate(self, **binding):
        if (
            self.candidate.provider == binding["provider"]
            and self.candidate.agent_session_id == binding["agent_session_id"]
            and self.candidate.surface_id == binding["surface_id"]
        ):
            return self.candidate
        return None

    def wake(self, surface_id, text="[fungis] inbox"):
        self.wakes.append((surface_id, text))

    def prompt_ready(self, surface_id):
        return self._prompt_ready


def candidate(lifecycle="idle"):
    return CmuxAgentCandidate(
        provider="codex",
        agent_session_id="session-1",
        surface_id="surface-uuid",
        surface_ref="surface:7",
        workspace_ref="workspace:1",
        title="Agent",
        tty="ttys007",
        cwd="/project",
        lifecycle=lifecycle,
        binding_verified=True,
        verification_reason="agent_tty_matches_surface",
    )


def record_pending(registry):
    registry.record_event(
        {
            "event_id": "event-1",
            "event_seq": 1,
            "recipient_id": "agent-1",
            "through_seq": 4,
            "kind": "inbox_available",
        }
    )


def test_running_never_wakes(tmp_path):
    registry = LocalRegistry(tmp_path / "node.db")
    current = candidate("running")
    registry.attach("agent-1", current)
    record_pending(registry)
    cmux = GateCmux(current)
    decision = IdleGate(registry, cmux, settle_seconds=0).run(
        "agent-1", send=True
    )
    assert decision.reason == "lifecycle_running"
    assert cmux.wakes == []


def test_needs_input_wakes_only_at_bare_prompt(tmp_path):
    registry = LocalRegistry(tmp_path / "node.db")
    waiting = candidate("needs_input")
    registry.attach("agent-1", waiting)
    record_pending(registry)
    blocked = GateCmux(waiting, prompt_ready=False)
    assert IdleGate(registry, blocked, settle_seconds=0).run(
        "agent-1", send=True
    ).reason == "lifecycle_needs_input"
    assert blocked.wakes == []

    ready = GateCmux(waiting, prompt_ready=True)
    decision = IdleGate(registry, ready, settle_seconds=0).run(
        "agent-1", send=True
    )
    assert decision.eligible is True
    assert ready.wakes == [("surface-uuid", "[fungis] inbox")]


def test_fresh_session_wakes_at_bare_prompt(tmp_path):
    """한 턴도 안 돈 세션의 lifecycle은 믿을 수 없다.

    8/16 실측: 같은 조건에서 tester1은 unknown, tester2는 running이었고 둘 다
    화면은 빈 프롬프트였다. lifecycle만 보면 배정 직후 첫 메시지가 영원히
    도착하지 않고, 사람이 터미널을 건드려 줘야만 풀린다. 화면이 판단한다.
    """
    for lifecycle in ("unknown", "running"):
        registry = LocalRegistry(tmp_path / f"node-{lifecycle}.db")
        fresh = candidate(lifecycle)
        registry.attach("agent-1", fresh)
        record_pending(registry)

        busy = GateCmux(fresh, prompt_ready=False)
        assert IdleGate(registry, busy, settle_seconds=0).run(
            "agent-1", send=True
        ).reason == f"lifecycle_{lifecycle}"
        assert busy.wakes == []

        ready = GateCmux(fresh, prompt_ready=True)
        decision = IdleGate(registry, ready, settle_seconds=0).run(
            "agent-1", send=True
        )
        assert decision.eligible is True
        assert ready.wakes == [("surface-uuid", "[fungis] inbox")]
        registry.close()


def test_idle_collapses_pending_and_sends_once(tmp_path):
    registry = LocalRegistry(tmp_path / "node.db")
    current = candidate("idle")
    registry.attach("agent-1", current)
    record_pending(registry)
    registry.record_event(
        {
            "event_id": "event-2",
            "event_seq": 2,
            "recipient_id": "agent-1",
            "through_seq": 5,
            "kind": "inbox_available",
        }
    )
    cmux = GateCmux(current)
    gate = IdleGate(registry, cmux, settle_seconds=0)
    first = gate.run("agent-1", send=True)
    second = gate.run("agent-1", send=True)
    assert first.eligible is True
    assert first.pending_count == 2
    assert first.through_seq == 5
    assert second.reason == "wake_unconfirmed"
    assert cmux.wakes == [("surface-uuid", "[fungis] inbox")]


def test_dry_run_never_records_or_sends(tmp_path):
    registry = LocalRegistry(tmp_path / "node.db")
    current = candidate("idle")
    registry.attach("agent-1", current)
    record_pending(registry)
    cmux = GateCmux(current)
    decision = IdleGate(registry, cmux, settle_seconds=0).run(
        "agent-1", send=False
    )
    assert decision.eligible is True
    assert cmux.wakes == []
    assert registry.outstanding_wake("agent-1") is None


def test_unverified_binding_is_rejected(tmp_path):
    registry = LocalRegistry(tmp_path / "node.db")
    unverified = CmuxAgentCandidate(
        **{
            **candidate("idle").__dict__,
            "binding_verified": False,
            "verification_reason": "agent_tty_surface_tty_mismatch",
        }
    )
    registry.attach("agent-1", unverified)
    record_pending(registry)
    gate = IdleGate(registry, GateCmux(unverified), settle_seconds=0)
    try:
        gate.run("agent-1", send=True)
    except LookupError as error:
        assert "PID/TTY verification" in str(error)
    else:
        raise AssertionError("unverified binding was allowed")


def test_registry_migrates_pre_gate_binding_table(tmp_path):
    path = tmp_path / "old-node.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE bindings (
          local_name TEXT PRIMARY KEY, provider TEXT NOT NULL,
          agent_session_id TEXT NOT NULL, surface_id TEXT NOT NULL,
          lifecycle TEXT NOT NULL, attached INTEGER NOT NULL,
          data_json TEXT NOT NULL, updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO bindings VALUES (
          'agent-1', 'codex', 'session-1', 'surface-1',
          'idle', 1, '{}', '2026-08-14T00:00:00.000Z'
        )
        """
    )
    connection.commit()
    connection.close()
    registry = LocalRegistry(path)
    assert registry.binding("agent-1")["lifecycle_changed_at"] == (
        "2026-08-14T00:00:00.000Z"
    )


def test_gate_migrates_one_internal_codex_binding_to_canonical_session(tmp_path):
    registry = LocalRegistry(tmp_path / "node.db")
    internal = CmuxAgentCandidate(
        **{
            **candidate("idle").__dict__,
            "agent_session_id": "internal-session",
            "surface_id": "surface-internal",
        }
    )
    canonical = candidate("idle")
    registry.attach("agent-1", internal)

    class MigratingCmux(GateCmux):
        def resolve_binding_candidate(self, **binding):
            assert binding["surface_id"] == "surface-internal"
            return canonical

    decision = IdleGate(
        registry, MigratingCmux(canonical), settle_seconds=0
    ).run("agent-1", send=False)
    assert decision.reason == "no_pending"
    repaired = registry.binding("agent-1")
    assert repaired["agent_session_id"] == "session-1"
    assert repaired["surface_id"] == "surface-uuid"


def test_surface_move_supersedes_wake_sent_to_old_terminal(tmp_path):
    registry = LocalRegistry(tmp_path / "node.db")
    old = CmuxAgentCandidate(
        **{**candidate("idle").__dict__, "surface_id": "surface-old"}
    )
    moved = CmuxAgentCandidate(
        **{**candidate("idle").__dict__, "surface_id": "surface-new"}
    )
    registry.attach("agent-1", old)
    registry.record_wake("agent-1", 7)
    assert registry.outstanding_wake("agent-1") is not None
    registry.refresh_candidate("agent-1", moved)
    assert registry.outstanding_wake("agent-1") is None
    row = registry.connection.execute(
        "SELECT status FROM wake_attempts WHERE recipient_id = ?",
        (registry.binding("agent-1")["principal_id"],),
    ).fetchone()
    assert row["status"] == "superseded"


def test_reusing_a_terminal_window_supersedes_the_old_binding(tmp_path):
    """같은 창에 새 세션을 띄우면 옛 binding은 갈 곳이 없다.

    놔두면 서버의 창 단위 유일 제약(node_id, terminal_provider,
    terminal_session_id)에 걸려 sync가 통째로 409를 내고 배정도 연결도 막힌다.
    8/16 실측에서 PM이 터미널 창을 재사용하자 그대로 걸렸다.
    """
    registry = LocalRegistry(tmp_path / "node.db")
    old = candidate("idle")
    registry.attach("agent-old", old)

    fresh = CmuxAgentCandidate(
        **{**old.__dict__, "agent_session_id": "session-2"}
    )
    registry.attach("agent-new", fresh)

    live = registry.list()
    assert [row["local_name"] for row in live] == ["agent-new"]
    assert {row["surface_id"] for row in live} == {old.surface_id}
    assert registry.binding("agent-old") is None
    registry.close()


def test_unconfirmed_wake_does_not_deafen_forever(tmp_path, monkeypatch):
    """확인이 유실되면 게이트가 이후 모든 깨우기를 영구히 거부한다.

    그러면 그 에이전트는 다시는 안 깨어난다 — 메시지는 쌓이는데 아무도 모른다.
    8/18 실사용에서 그대로 걸렸다: 8/17 21:28에 보낸 것이 하루 넘게 남아
    메시지 5건을 막고 있었다. 다시 깨우는 쪽은 안전하다 — 호출문 한 줄이고
    게이트가 빈 프롬프트를 확인한 뒤에만 넣는다.
    """
    from fungis_node import registry as registry_module

    registry = LocalRegistry(tmp_path / "node.db")
    current = candidate("idle")
    registry.attach("agent-1", current)
    record_pending(registry)

    cmux = GateCmux(current)
    gate = IdleGate(registry, cmux, settle_seconds=0)
    assert gate.run("agent-1", send=True).eligible is True
    assert gate.run("agent-1", send=True).reason == "wake_unconfirmed"

    # 확인이 끝내 오지 않아도 한도가 지나면 다시 깨울 수 있어야 한다.
    monkeypatch.setattr(registry_module, "WAKE_CONFIRM_TTL_SECONDS", 0)
    assert gate.run("agent-1", send=True).eligible is True
    assert len(cmux.wakes) == 2
    registry.close()


def test_an_empty_read_clears_the_wake_it_answered(tmp_path, monkeypatch):
    """볼 것이 없어도 깨우기는 소진된 것이다.

    안 지우면 게이트가 wake_unconfirmed 로 이후 깨우기를 TTL 10분 동안 전부
    거부한다. Stop 훅을 쓰는 저장소에서는 이게 상례다 — 훅이 턴 끝마다 인박스를
    비우므로 그 직후 도착한 깨우기는 읽을 것이 없는 채로 뜬다. 2026-08-19 에
    PM 이 그 창에 두 건을 보냈고 둘 다 안 갔다.
    """
    from fungis_node.cmux import CmuxAgentCandidate
    from fungis_node.inbox import InboxWatcher
    from fungis_node.registry import LocalRegistry

    registry = LocalRegistry(tmp_path / "node.db")
    registry.attach("agent-1", CmuxAgentCandidate(
        provider="claude", agent_session_id="session-1", surface_id="surface-1",
        surface_ref="surface:1", workspace_ref="workspace:1", title="Agent",
        tty="ttys001", cwd="/project", lifecycle="idle",
        binding_verified=True, verification_reason="agent_tty_matches_surface",
    ))
    registry.record_wake("agent-1", 7)
    assert registry.outstanding_wake("agent-1") is not None

    watcher = InboxWatcher("http://127.0.0.1:8787", "agent-1", registry)
    monkeypatch.setattr(
        InboxWatcher, "_http_get",
        lambda self, path, params: {"processed_seq": 7} if "state" in path else [],
    )
    assert watcher.read_messages("surface-1") == []
    assert registry.outstanding_wake("agent-1") is None

    # 읽을 것이 있으면 종전대로 claim 을 잡는다. 지우는 쪽으로만 바뀐 게 아니다.
    registry.record_wake("agent-1", 9)
    monkeypatch.setattr(
        InboxWatcher, "_http_get",
        lambda self, path, params: (
            {"processed_seq": 7} if "state" in path else [{"seq": 9, "body": "x"}]
        ),
    )
    assert len(watcher.read_messages("surface-1")) == 1
    assert registry.claim("agent-1")["through_seq"] == 9
    registry.close()
