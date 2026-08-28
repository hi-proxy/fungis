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


def test_a_due_schedule_wakes_even_when_the_inbox_was_already_handed_over(tmp_path):
    """예약은 인박스와 다른 이유로 깨운다.

    넘겨준 인박스 구간이 예약까지 덮으면, 본인이 이 시각에 깨워 달라고 한 것이
    조용히 사라진다. 상주 에이전트는 그 예약으로 산다.
    """
    registry = LocalRegistry(tmp_path / "node.db")
    current = candidate("idle")
    registry.attach("agent-1", current)
    record_pending(registry)
    registry.claim_inbox("agent-1", 4, "session-1")
    registry.schedule_wake("agent-1", "2000-01-01T00:00:00.000Z")

    cmux = GateCmux(current, prompt_ready=True)
    gate = IdleGate(registry, cmux, settle_seconds=0)
    assert gate.run("agent-1", send=True).eligible is True
    registry.close()


def test_a_long_turn_is_not_woken_again_for_what_it_already_took(tmp_path):
    """`pending_events` 는 확인이 와야 지워지고, 확인은 턴이 끝나야 온다.

    그 사이 게이트가 그것만 보면 같은 메시지로 한 턴 내내 깨운다 — 긴 턴일수록
    심하다. 2026-08-28 밤에 터미널이 그렇게 도배됐다.
    """
    registry = LocalRegistry(tmp_path / "node.db")
    current = candidate("idle")
    registry.attach("agent-1", current)
    record_pending(registry)

    cmux = GateCmux(current, prompt_ready=True)
    gate = IdleGate(registry, cmux, settle_seconds=0)
    assert gate.run("agent-1", send=True).eligible is True

    # 읽어 갔다. 확인은 아직 안 왔고 pending 도 그대로다.
    registry.claim_inbox("agent-1", 4, "session-1")
    assert gate.run("agent-1", send=True).reason == "claimed"
    assert len(cmux.wakes) == 1

    # 새로 온 것은 넘겨준 구간 밖이라 다시 깨운다.
    registry.record_event({
        "event_id": "event-2", "event_seq": 2, "recipient_id": "agent-1",
        "through_seq": 9, "kind": "inbox_available",
    })
    assert gate.run("agent-1", send=True).eligible is True
    assert len(cmux.wakes) == 2
    registry.close()


def test_an_idle_screen_is_woken_even_with_a_wake_unconfirmed(tmp_path):
    """찌를 자리가 비어 있으면 두 번 찌르는 것이 아니다.

    앞의 깨우기를 못 봤거나 보고도 인박스를 안 돈 창은 지금 놀고 있다. 그런
    창을 한도가 다 갈 때까지 재워 두면, 가만히 있는데 수신만 끊긴다.
    """
    registry = LocalRegistry(tmp_path / "node.db")
    current = candidate("idle")
    registry.attach("agent-1", current)
    record_pending(registry)

    cmux = GateCmux(current, prompt_ready=True)
    gate = IdleGate(registry, cmux, settle_seconds=0)
    assert gate.run("agent-1", send=True).eligible is True
    # 확인이 안 왔지만 화면이 비었다. 한도를 기다릴 이유가 없다.
    assert gate.run("agent-1", send=True).eligible is True
    assert len(cmux.wakes) == 2
    registry.close()


def test_a_stalled_wake_is_counted_so_it_can_be_told_later(tmp_path, monkeypatch):
    """깨우기 줄은 다음 것이 덮어쓴다. 세어 두지 않으면 몇 번 갇혔는지 남지 않는다."""
    from fungis_node import registry as registry_module

    registry = LocalRegistry(tmp_path / "node.db")
    registry.attach("agent-1", candidate("idle"))
    principal = registry.binding("agent-1")["principal_id"]
    registry.record_wake(principal, 7)

    monkeypatch.setattr(registry_module, "WAKE_CONFIRM_TTL_SECONDS", 0)
    assert registry.outstanding_wake(principal) is None
    row = registry.connection.execute(
        "SELECT stalled_count FROM wake_attempts WHERE recipient_id = ?",
        (principal,),
    ).fetchone()
    assert row["stalled_count"] == 1
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


def test_a_claim_with_nothing_to_read_is_acknowledged(tmp_path, monkeypatch):
    """확인 하나가 유실되면 깨우기가 멈추지 않는다.

    claim 이 `after` 를 밀어 올려서, 서버가 미처리로 들고 있는 그 메시지가
    여기서는 영영 안 나온다. 게이트는 서버 쪽만 보고 계속 깨우고, 깨어나서
    읽으면 또 빈 목록이다. 2026-08-28 에 그 창이 그렇게 돌았다.
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
    registry.claim_inbox("agent-1", 9, "session-1")
    registry.record_event({
        "event_id": "event-9", "event_seq": 9, "recipient_id": "agent-1",
        "through_seq": 9, "kind": "inbox_available",
    })

    acked = []
    watcher = InboxWatcher("http://127.0.0.1:8787", "agent-1", registry)
    monkeypatch.setattr(
        InboxWatcher, "_http_get",
        lambda self, path, params: {"processed_seq": 0} if "state" in path else [],
    )
    monkeypatch.setattr(
        InboxWatcher, "_http_post",
        lambda self, path, payload: acked.append(payload) or {"pending_count": 0},
    )
    assert watcher.read_messages("surface-1") == []

    assert acked == [{"recipient_id": "agent-1", "through_seq": 9}]
    assert registry.claim("agent-1") is None
    assert registry.pending_summary("agent-1")["pending_count"] == 0
    registry.close()


def _bound(tmp_path):
    from fungis_node.cmux import CmuxAgentCandidate
    from fungis_node.registry import LocalRegistry

    registry = LocalRegistry(tmp_path / "node.db")
    registry.attach("agent-1", CmuxAgentCandidate(
        provider="claude", agent_session_id="session-1", surface_id="surface-1",
        surface_ref="surface:1", workspace_ref="workspace:1", title="Agent",
        tty="ttys001", cwd="/project", lifecycle="idle",
        binding_verified=True, verification_reason="agent_tty_matches_surface",
    ))
    return registry


class _Screen:
    """화면은 늘 비어 있고, 붙잡은 것을 그대로 돌려준다."""

    def __init__(self, current):
        self.current = current
        self.wakes = []

    def resolve_binding_candidate(self, **binding):
        return self.current

    def wake(self, surface_id, text):
        self.wakes.append(text)

    def prompt_ready(self, surface_id):
        return True


def test_a_worker_can_book_its_own_next_step(tmp_path):
    """보낼 말이 없어도 예약 시각이면 깨운다.

    착수만 선언하고 턴이 끝나면 아무도 말을 걸 때까지 선다. 2026-08-19 루프
    2회차에서 그렇게 1시간이 갔다.
    """
    from datetime import datetime, timedelta, timezone

    from fungis_node.gate import IdleGate

    registry = _bound(tmp_path)
    screen = _Screen(registry.list()[0])
    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    gate = IdleGate(registry, screen, settle_seconds=0, now=lambda: now)

    # 예약이 없으면 보낼 것도 없으니 안 깨운다. 종전 그대로다.
    assert gate.evaluate("agent-1", refresh=False).reason == "no_pending"

    # 아직 시각이 안 됐다.
    later = (now + timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    registry.schedule_wake("agent-1", later)
    assert gate.evaluate("agent-1", refresh=False).reason == "no_pending"

    # 시각이 지나면 깨운다. 문구가 인박스와 다르다 — 받는 쪽이 왜 깨어났는지
    # 알아야 인박스를 읽을지 하던 일을 이어갈지 정한다.
    past = (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    registry.schedule_wake("agent-1", past)
    decision = gate.run("agent-1", send=True, refresh=False)
    assert decision.eligible and decision.reason == "scheduled"
    assert screen.wakes == [gate.due_text]

    # 소진된다. 한 번 예약으로 계속 깨우면 그게 소음이다.
    assert registry.wake_schedule("agent-1") is None
    registry.close()


def test_a_scheduled_wake_does_not_block_the_inbox_path(tmp_path):
    """예약 깨우기는 확인을 기다릴 것이 없다.

    wake_attempts 에 남기면 그 뒤 인박스 깨우기가 '확인 안 됨' 으로 막힌다 —
    어제 열 시간을 쓴 그 자리다.
    """
    from datetime import datetime, timedelta, timezone

    from fungis_node.gate import IdleGate

    registry = _bound(tmp_path)
    screen = _Screen(registry.list()[0])
    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    gate = IdleGate(registry, screen, settle_seconds=0, now=lambda: now)

    past = (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    registry.schedule_wake("agent-1", past)
    gate.run("agent-1", send=True, refresh=False)
    assert registry.outstanding_wake("agent-1") is None
    registry.close()


def test_waking_for_any_reason_spends_the_booking(tmp_path):
    """인박스로 이미 턴이 열렸는데 예약이 남으면 곧바로 한 번 더 찌른다."""
    from datetime import datetime, timedelta, timezone

    from fungis_node.gate import IdleGate

    registry = _bound(tmp_path)
    screen = _Screen(registry.list()[0])
    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    gate = IdleGate(registry, screen, settle_seconds=0, now=lambda: now)

    registry.record_event({
        "event_id": "e1", "event_seq": 1, "recipient_id": "agent-1",
        "through_seq": 1, "kind": "inbox_available",
    })
    later = (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    registry.schedule_wake("agent-1", later)

    decision = gate.run("agent-1", send=True, refresh=False)
    assert decision.reason == "eligible"      # 인박스 쪽이다
    assert registry.wake_schedule("agent-1") is None
    registry.close()


def test_a_delay_has_a_ceiling_and_deferrals_are_counted(tmp_path):
    """미루기만 하다 조용히 죽는 것이 가장 나쁘다."""
    import pytest

    from fungis_node.agent_cli import parse_delay

    assert parse_delay("20m") == 1200
    assert parse_delay("2h") == 7200
    assert parse_delay("30") == 1800          # 숫자만 주면 분
    with pytest.raises(ValueError, match="6시간"):
        parse_delay("9h")
    with pytest.raises(ValueError):
        parse_delay("abc")
    with pytest.raises(ValueError):
        parse_delay("0m")

    registry = _bound(tmp_path)
    for expected in (1, 2, 3):
        booked = registry.schedule_wake("agent-1", "2026-08-20T12:00:00.000Z")
        assert booked["deferrals"] == expected
    registry.close()


class _BusyScreen(_Screen):
    """일하는 중이라 프롬프트가 안 비어 있다."""

    def prompt_ready(self, surface_id):
        return False


def test_a_booking_does_not_interrupt_a_working_agent(tmp_path):
    """예약 시각이 됐어도 일하는 중이면 안 찌른다.

    처음 만들 때 예약 분기를 화면 검사보다 앞에 뒀다가 이 성질을 깼다.
    보낼 말이 없다는 것과 깨워도 된다는 것은 다른 얘기다.
    """
    from datetime import datetime, timedelta, timezone

    from fungis_node.gate import IdleGate

    from fungis_node.cmux import CmuxAgentCandidate

    registry = _bound(tmp_path)
    # cmux 가 running 이라고 말하고 화면도 안 비어 있다.
    working = CmuxAgentCandidate(
        provider="claude", agent_session_id="session-1", surface_id="surface-1",
        surface_ref="surface:1", workspace_ref="workspace:1", title="Agent",
        tty="ttys001", cwd="/project", lifecycle="running",
        binding_verified=True, verification_reason="agent_tty_matches_surface",
    )
    registry.refresh_candidate("agent-1", working)
    screen = _BusyScreen(working)
    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    gate = IdleGate(registry, screen, settle_seconds=0, now=lambda: now)

    past = (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    registry.schedule_wake("agent-1", past)

    decision = gate.run("agent-1", send=True, refresh=False)
    assert not decision.eligible
    assert decision.reason == "lifecycle_running"
    assert screen.wakes == []
    # 예약은 남는다. 화면이 비면 그때 깨운다.
    assert registry.wake_schedule("agent-1") is not None
    registry.close()


def test_only_the_last_booking_stands(tmp_path):
    """같은 사람이 여러 번 걸면 마지막 것 하나만 남는다.

    쌓아 두면 한 번 깨어난 뒤에도 지난 예약들이 줄줄이 따라와 찌른다.
    """
    registry = _bound(tmp_path)
    registry.schedule_wake("agent-1", "2026-08-20T12:00:00.000Z", "첫째")
    registry.schedule_wake("agent-1", "2026-08-20T13:00:00.000Z", "둘째")
    booked = registry.wake_schedule("agent-1")
    assert booked["due_at"] == "2026-08-20T13:00:00.000Z"
    assert booked["note"] == "둘째"
    assert booked["deferrals"] == 2
    registry.close()


def test_bookings_are_per_agent(tmp_path):
    """한 사람의 예약이 다른 사람을 깨우지 않는다."""
    from fungis_node.cmux import CmuxAgentCandidate

    registry = _bound(tmp_path)
    registry.attach("agent-2", CmuxAgentCandidate(
        provider="claude", agent_session_id="session-2", surface_id="surface-2",
        surface_ref="surface:2", workspace_ref="workspace:1", title="Other",
        tty="ttys002", cwd="/other", lifecycle="idle",
        binding_verified=True, verification_reason="agent_tty_matches_surface",
    ))
    registry.schedule_wake("agent-1", "2026-08-20T12:00:00.000Z")
    assert registry.wake_schedule("agent-1") is not None
    assert registry.wake_schedule("agent-2") is None
    registry.close()


def test_an_unreadable_booking_does_not_stop_the_gate(tmp_path):
    """못 읽는 예약 하나가 게이트를 통째로 멈추면 그게 더 나쁘다."""
    from datetime import datetime, timezone

    from fungis_node.gate import IdleGate

    registry = _bound(tmp_path)
    screen = _Screen(registry.list()[0])
    gate = IdleGate(
        registry, screen, settle_seconds=0,
        now=lambda: datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
    )
    registry.schedule_wake("agent-1", "쓰레기")
    assert gate.evaluate("agent-1", refresh=False).reason == "no_pending"
    registry.close()


def test_a_later_message_waits_in_the_inbox_without_opening_a_turn(tmp_path):
    """쌓이는 것과 깨울 이유가 있는 것은 다르다.

    한 걸음 도는 중에 끼면 그 걸음이 통째로 밀린다. 그래서 later 는 인박스에
    그대로 남되 턴을 열지 않는다.
    """
    from datetime import datetime, timezone

    from fungis_node.gate import IdleGate

    registry = _bound(tmp_path)
    screen = _Screen(registry.list()[0])
    gate = IdleGate(
        registry, screen, settle_seconds=0,
        now=lambda: datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
    )

    registry.record_event({
        "event_id": "quiet-1", "event_seq": 1, "recipient_id": "agent-1",
        "through_seq": 7, "kind": "inbox_later",
    })
    summary = registry.pending_summary("agent-1")
    assert summary["pending_count"] == 1     # 인박스에는 있다
    assert summary["waking_count"] == 0      # 깨울 이유는 아니다
    assert summary["through_seq"] == 7       # 커서는 전부에서 뽑는다

    assert gate.run("agent-1", send=True, refresh=False).reason == "no_pending"
    assert screen.wakes == []

    # 보통 메시지가 하나라도 오면 그때 깨운다. 밀려 있던 것도 함께 읽힌다.
    registry.record_event({
        "event_id": "loud-1", "event_seq": 2, "recipient_id": "agent-1",
        "through_seq": 8, "kind": "inbox_available",
    })
    assert registry.pending_summary("agent-1")["waking_count"] == 1
    assert gate.run("agent-1", send=True, refresh=False).reason == "eligible"
    assert screen.wakes == [gate.wake_text]
    registry.close()
