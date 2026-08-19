import json
import sqlite3
import subprocess

from fungis_node.cmux import CmuxAdapter, CmuxAgentCandidate
from fungis_node.registry import LocalRegistry
from fungis_node.agent_cli import current_binding


class FakeCmux(CmuxAdapter):
    def __init__(self, **kwargs):
        # 실행 파일을 어디서 찾는지는 이 파일의 관심이 아니다. 고정하지 않으면
        # 어댑터가 이 기계의 cmux 절대 경로를 들고 와서 호출 인자 비교가 깨진다.
        super().__init__(executable="cmux", **kwargs)

    def _run_json(self, *args):
        return {
            "windows": [
                {
                    "workspaces": [
                        {
                            "ref": "workspace:1",
                            "title": "Project",
                            "panes": [
                                {
                                    "surfaces": [
                                        {
                                            "ref": "surface:7",
                                            "id": "surface-uuid",
                                            "title": "Agent terminal",
                                            "tty": "ttys007",
                                        }
                                    ]
                                }
                            ],
                        }
                    ]
                }
            ]
        }

    @staticmethod
    def _process_tty(pid):
        return "ttys007"


def test_current_surface_id_accepts_identify_both_shape():
    adapter = FakeCmux()
    adapter._run_json = lambda *args: {
        "caller": {"surface": {"ref": "surface:7", "id": "surface-uuid"}}
    }
    assert adapter.current_surface_id() == "surface-uuid"


def test_current_surface_id_accepts_real_flat_caller_shape():
    adapter = FakeCmux()
    adapter._run_json = lambda *args: {
        "caller": {"surface_id": "surface-uuid", "surface_ref": "surface:7"},
        "focused": {"surface_id": "some-other-surface"},
    }
    assert adapter.current_surface_id() == "surface-uuid"


def test_agent_cli_prefers_direct_bound_surface_over_stale_internal_mapping(tmp_path):
    registry = LocalRegistry(tmp_path / "node.db")
    bound = CmuxAgentCandidate(
        "codex", "session-1", "surface-current", "surface:9", "workspace:1",
        "Current agent", "ttys009", "/project", "idle", True,
        "codex_process_tty_surface",
    )
    registry.attach("agent-1", bound)
    adapter = FakeCmux()
    adapter.current_surface_id = lambda: "surface-current"
    adapter.canonical_surface_for_context = lambda _: "surface-stale"
    assert current_binding(registry, adapter)["local_name"] == "agent-1"


def test_prompt_ready_requires_bare_latest_prompt(monkeypatch):
    adapter = FakeCmux()

    def result(output):
        return subprocess.CompletedProcess([], 0, stdout=output, stderr="")

    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: result("answer\n❯\u00a0\nstatus\n")
    )
    assert adapter.prompt_ready("surface-uuid") is True
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: result("answer\n❯ user is typing\nstatus\n"),
    )
    assert adapter.prompt_ready("surface-uuid") is False


def test_focus_uses_stable_surface_id_with_current_cmux_rpc_shape(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    candidate = CmuxAgentCandidate(
        "codex",
        "session-1",
        "surface-uuid",
        "surface:7",
        "workspace:1",
        "Agent",
        "ttys007",
        "/project",
        "idle",
        True,
        "verified",
    )

    FakeCmux().focus(candidate)

    assert calls == [
        ["cmux", "rpc", "surface.focus", '{"surface_id": "surface-uuid"}']
    ]


def test_discover_and_detach_do_not_send_input(tmp_path):
    hook_store = tmp_path / "hooks"
    hook_store.mkdir()
    (hook_store / "codex-hook-sessions.json").write_text(
        json.dumps(
            {
                "sessions": {
                    "agent-session": {
                        "sessionId": "agent-session",
                        "surfaceId": "surface-uuid",
                        "cwd": "/project",
                        "agentLifecycle": "running",
                        "pid": 123,
                        "transcriptPath": "/sessions/rollout-agent-session.jsonl",
                    }
                }
            }
        )
    )
    adapter = FakeCmux(hook_store_dir=hook_store)
    candidates = adapter.discover_agents()
    assert len(candidates) == 1
    assert candidates[0].lifecycle == "running"
    assert candidates[0].binding_verified is True
    assert "surface_ref" not in candidates[0].public_dict()

    registry = LocalRegistry(tmp_path / "node.db")
    registry.attach("agent1", candidates[0])
    assert len(registry.list()) == 1
    assert registry.detach("agent1") is True
    assert registry.list() == []


def test_active_prompt_depth_overrides_stale_idle_lifecycle(tmp_path):
    hook_store = tmp_path / "hooks"
    hook_store.mkdir()
    (hook_store / "codex-hook-sessions.json").write_text(json.dumps({
        "sessions": {"agent-session": {
            "sessionId": "agent-session", "surfaceId": "surface-uuid",
            "cwd": "/project", "agentLifecycle": "idle",
            "activePromptDepth": 1, "pid": 123,
            "transcriptPath": "/sessions/agent-session.jsonl",
        }}
    }))
    candidate = FakeCmux(hook_store_dir=hook_store).discover_agents()[0]
    assert candidate.lifecycle == "running"


def test_codex_internal_session_without_transcript_is_hidden(tmp_path):
    hook_store = tmp_path / "hooks"
    hook_store.mkdir()
    (hook_store / "codex-hook-sessions.json").write_text(
        json.dumps(
            {
                "sessions": {
                    "memory-session": {
                        "sessionId": "memory-session",
                        "surfaceId": "surface-uuid",
                        "cwd": "/Users/person/.codex/memories",
                        "agentLifecycle": "idle",
                        "pid": 123,
                    }
                }
            }
        )
    )
    adapter = FakeCmux(hook_store_dir=hook_store)
    assert adapter.discover_agents() == []
    diagnostic = adapter.discover_agents(include_hidden=True)
    assert len(diagnostic) == 1
    assert diagnostic[0].hidden_reason == "internal_session_without_transcript"


def test_codex_canonical_transcript_requires_process_tty_surface(tmp_path):
    hook_store = tmp_path / "hooks"
    hook_store.mkdir()
    (hook_store / "codex-hook-sessions.json").write_text(
        json.dumps(
            {
                "sessions": {
                    "main-session": {
                        "sessionId": "main-session",
                        "surfaceId": "surface-uuid",
                        "cwd": "/project",
                        "agentLifecycle": "idle",
                        "pid": 123,
                        "transcriptPath": "/sessions/rollout-main-session.jsonl",
                    }
                }
            }
        )
    )
    adapter = FakeCmux(hook_store_dir=hook_store)
    adapter._process_tty = lambda pid: "ttys007"
    discovered = adapter.discover_agents()
    assert len(discovered) == 1
    assert discovered[0].binding_verified is True
    assert discovered[0].verification_reason == "codex_process_tty_surface"


def test_codex_stale_hook_surface_is_corrected_by_unique_process_tty(tmp_path):
    hook_store = tmp_path / "hooks"
    hook_store.mkdir()
    (hook_store / "codex-hook-sessions.json").write_text(json.dumps({
        "sessions": {"session-1": {
            "sessionId": "session-1", "surfaceId": "surface-old",
            "cwd": "/project", "agentLifecycle": "idle", "pid": 123,
            "transcriptPath": "/sessions/session-1.jsonl",
        }}
    }))
    adapter = FakeCmux(hook_store_dir=hook_store)
    original_run_json = adapter._run_json
    def moved_tree(*args):
        value = original_run_json(*args)
        value["windows"][0]["workspaces"][0]["panes"][0]["surfaces"].append({
            "ref": "surface:new-ref", "id": "surface-new",
            "tty": "/dev/ttys009", "title": "Moved agent",
        })
        return value
    adapter._run_json = moved_tree
    adapter._process_tty = lambda pid: "ttys009"
    candidate = adapter.discover_agents()[0]
    assert candidate.surface_id == "surface-new"
    assert candidate.surface_ref == "surface:new-ref"
    assert candidate.verification_reason == "codex_process_tty_surface"


def test_binding_follows_unique_verified_session_after_surface_move(monkeypatch):
    adapter = CmuxAdapter()
    moved = CmuxAgentCandidate(
        provider="codex", agent_session_id="session-stable",
        surface_id="surface-new", surface_ref="surface:9",
        workspace_ref="workspace:2", title="Moved agent", tty="ttys009",
        cwd="/project", lifecycle="idle", binding_verified=True,
        verification_reason="codex_transcript_hook_surface",
    )
    monkeypatch.setattr(adapter, "discover_agents", lambda: [moved])
    assert adapter.resolve_binding_candidate(
        provider="codex", agent_session_id="session-stable",
        surface_id="surface-old",
    ) == moved


def test_binding_does_not_follow_ambiguous_or_unverified_session(monkeypatch):
    adapter = CmuxAdapter()
    base = dict(
        provider="codex", agent_session_id="session-stable",
        surface_ref=None, workspace_ref=None, title="Agent", tty=None,
        cwd="/project", lifecycle="idle",
        verification_reason="codex_transcript_hook_surface",
    )
    candidates = [
        CmuxAgentCandidate(surface_id="surface-a", binding_verified=True, **base),
        CmuxAgentCandidate(surface_id="surface-b", binding_verified=True, **base),
    ]
    monkeypatch.setattr(adapter, "discover_agents", lambda: candidates)
    monkeypatch.setattr(adapter, "canonical_surface_for_context", lambda _: None)
    assert adapter.resolve_binding_candidate(
        provider="codex", agent_session_id="session-stable",
        surface_id="surface-old",
    ) is None


def test_stale_agent_pid_is_hidden_from_default_discovery(tmp_path):
    hook_store = tmp_path / "hooks"
    hook_store.mkdir()
    (hook_store / "codex-hook-sessions.json").write_text(
        json.dumps(
            {
                "sessions": {
                    "stale-session": {
                        "sessionId": "stale-session",
                        "surfaceId": "surface-uuid",
                        "cwd": "/project",
                        "agentLifecycle": "idle",
                        "pid": 999,
                        "transcriptPath": "/sessions/stale.jsonl",
                    }
                }
            }
        )
    )
    adapter = FakeCmux(hook_store_dir=hook_store)
    adapter._process_tty = lambda pid: None
    assert adapter.discover_agents() == []
    diagnostic = adapter.discover_agents(include_hidden=True)
    assert diagnostic[0].hidden_reason == "stale_agent_pid"


def test_internal_codex_context_maps_to_one_canonical_surface(tmp_path):
    hook_store = tmp_path / "hooks"
    hook_store.mkdir()
    shared = {
        "pid": 123,
        "launchCommand": {"workingDirectory": "/project"},
    }
    (hook_store / "codex-hook-sessions.json").write_text(
        json.dumps(
            {
                "sessions": {
                    "main": {
                        **shared,
                        "sessionId": "main",
                        "surfaceId": "surface-main",
                        "transcriptPath": "/sessions/main.jsonl",
                    },
                    "internal": {
                        **shared,
                        "sessionId": "internal",
                        "surfaceId": "surface-internal",
                    },
                }
            }
        )
    )
    adapter = FakeCmux(hook_store_dir=hook_store)
    assert adapter.canonical_surface_for_context("surface-internal") == (
        "surface-main"
    )

    class CandidateCmux(FakeCmux):
        def discover_agents(self, *, include_hidden=False):
            return [
                CmuxAgentCandidate(
                    "codex",
                    "main",
                    "surface-main",
                    "surface:1",
                    "workspace:1",
                    "Main",
                    "ttys001",
                    "/project",
                    "idle",
                    True,
                    "codex_transcript_hook_surface",
                )
            ]

    resolver = CandidateCmux(hook_store_dir=hook_store)
    resolved = resolver.resolve_binding_candidate(
        provider="codex",
        agent_session_id="internal",
        surface_id="surface-internal",
    )
    assert resolved is not None
    assert resolved.agent_session_id == "main"


def test_pending_event_is_durable_and_idempotent(tmp_path):
    registry = LocalRegistry(tmp_path / "node.db")
    event = {
        "event_id": "event-1",
        "event_seq": 7,
        "recipient_id": "agent-1",
        "through_seq": 12,
        "kind": "inbox_available",
    }
    assert registry.record_event(event) is True
    assert registry.record_event(event) is False
    assert registry.event_cursor("agent-1") == 7
    assert registry.event_cursor("agent-2") == 0
    assert len(registry.pending("agent-1")) == 1
    assert registry.clear_processed("agent-1", 12) == 1
    assert registry.pending("agent-1") == []


def test_fresh_nodes_get_unique_namespaced_identities(tmp_path):
    first = LocalRegistry(tmp_path / "first.db")
    second = LocalRegistry(tmp_path / "second.db")
    assert first.node_id().startswith("node-")
    assert first.node_id() != second.node_id()
    assert first.pm_principal_id() != second.pm_principal_id()
    first.attach("agent1", CmuxAgentCandidate(
        "codex", "s1", "surface-1", "surface:1", "workspace:1",
        "Agent", "ttys001", "/project", "idle", True, "verified",
    ))
    principal = first.binding("agent1")["principal_id"]
    assert principal.startswith(f"agent-{first.node_id().removeprefix('node-')}-")
    assert first.binding_for_principal(principal)["local_name"] == "agent1"


def test_existing_binding_database_keeps_legacy_server_identity(tmp_path):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE bindings (
          local_name TEXT PRIMARY KEY, provider TEXT NOT NULL,
          agent_session_id TEXT NOT NULL, surface_id TEXT NOT NULL,
          lifecycle TEXT NOT NULL, attached INTEGER NOT NULL,
          data_json TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE node_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO bindings VALUES (
          'agent-old', 'codex', 'session-old', 'surface-old',
          'idle', 1, '{}', '2026-08-14T00:00:00.000Z'
        );
        """
    )
    connection.close()
    registry = LocalRegistry(path)
    assert registry.node_id() == "node-local"
    assert registry.pm_principal_id() == "pm-local"
    assert registry.binding("agent-old")["principal_id"] == "agent-old"


def test_a_surface_whose_tty_device_is_gone_is_still_the_agents_terminal(tmp_path):
    """cmux 가 복원한 표면은 재부팅 전 tty 이름을 그대로 들고 있다.

    그 장치는 이제 없다. 없는 이름과 살아 있는 프로세스를 견주면 영원히 안 맞고,
    2026-08-19 에 그것 때문에 깨우기가 통째로 멈췄다. 표면 id 는 멀쩡했다 —
    그 id 로 읽으면 지금 화면이 그대로 왔다. 죽은 이름표보다 그쪽이 정본이다.
    """
    hook_store = tmp_path / "hooks"
    hook_store.mkdir()
    (hook_store / "claude-hook-sessions.json").write_text(json.dumps({
        "sessions": {"agent-session": {
            "sessionId": "agent-session", "surfaceId": "surface-uuid",
            "cwd": "/project", "agentLifecycle": "idle", "pid": 123,
        }}
    }))

    class RestoredCmux(FakeCmux):
        """표면은 살아 있는데 tty 이름만 재부팅 전 것이다."""

        def _run_json(self, *args):
            tree = super()._run_json(*args)
            surface = tree["windows"][0]["workspaces"][0]["panes"][0]["surfaces"][0]
            surface["tty"] = "ttys99999"
            return tree

    candidate = RestoredCmux(hook_store_dir=hook_store).discover_agents()[0]
    assert candidate.binding_verified is True
    assert candidate.verification_reason == "surface_tty_gone_hook_surface_trusted"

    # 살아 있는 tty 가 안 맞는 것은 여전히 거부한다. 검사를 없앤 것이 아니다.
    # 어느 기계에나 있는 장치를 써야 이 판정이 기계마다 달라지지 않는다.
    class MovedCmux(FakeCmux):
        def _run_json(self, *args):
            tree = super()._run_json(*args)
            tree["windows"][0]["workspaces"][0]["panes"][0]["surfaces"][0]["tty"] = "null"
            return tree

        @staticmethod
        def _process_tty(pid):
            return "ttys008"

    moved = MovedCmux(hook_store_dir=hook_store).discover_agents()[0]
    assert moved.binding_verified is False
    assert moved.verification_reason == "agent_tty_surface_tty_mismatch"


def test_tty_exists_says_no_for_a_missing_device(tmp_path):
    from fungis_node.cmux import tty_exists

    assert not tty_exists(None)
    assert not tty_exists("")
    assert not tty_exists("ttys99999")
    # 어느 기계에나 있는 장치로 참을 확인한다.
    assert tty_exists("null")
