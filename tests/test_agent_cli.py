import json

import pytest

from fungis_node.agent_cli import (
    active_project, compact_board, compact_history, emit_inbox, format_bootstrap,
    load_config,
    parser, stored_echo,
    write_error_message,
)
from fungis_node.install import install_agent_cli
from fungis_node.pm import PMClient
from fungis_node.registry import LocalRegistry


def test_install_agent_cli_writes_short_launcher_and_private_config(tmp_path):
    executable = tmp_path / "bin" / "fungis"
    config = tmp_path / "config" / "agent.json"
    result = install_agent_cli(
        registry_path=tmp_path / "node.db",
        server_url="http://127.0.0.1:8787",
        executable_path=executable,
        config_path=config,
    )
    assert result == {"executable": str(executable), "config": str(config)}
    assert executable.read_text().startswith("#!/bin/sh\nexec ")
    assert "fungis_node.agent_cli" in executable.read_text()
    assert executable.stat().st_mode & 0o111
    assert config.stat().st_mode & 0o077 == 0
    assert load_config(config) == {
        "registry": str((tmp_path / "node.db").resolve()),
        "server": "http://127.0.0.1:8787",
    }


def test_agent_config_rejects_missing_fields(tmp_path):
    config = tmp_path / "agent.json"
    config.write_text(json.dumps({"registry": "/node.db"}))
    try:
        load_config(config)
    except RuntimeError as error:
        assert "invalid" in str(error)
    else:
        raise AssertionError("incomplete agent config was accepted")


def test_install_rejects_placeholder_server_before_writing(tmp_path):
    executable = tmp_path / "bin" / "fungis"
    config = tmp_path / "config" / "agent.json"

    with pytest.raises(ValueError, match="placeholder host"):
        install_agent_cli(
            registry_path=tmp_path / "node.db",
            server_url="http://SERVER:8787",
            executable_path=executable,
            config_path=config,
        )

    assert not executable.exists()
    assert not config.exists()


def test_agent_config_rejects_placeholder_server(tmp_path):
    config = tmp_path / "agent.json"
    config.write_text(
        json.dumps({"registry": "/node.db", "server": "http://SERVER:8787"})
    )

    with pytest.raises(RuntimeError, match="placeholder host"):
        load_config(config)


def test_format_bootstrap_is_compact_and_marks_own_role():
    output = format_bootstrap(
        {
            "revision": "abc123",
            "project": {"name": "Relay"},
            "own_role": {"name": "dev-lead"},
            "pm": {"display_name": "PM"},
            "roles": [
                {"name": "dev-lead", "self": True, "agent_name": "Agent A"},
                {"name": "reviewer", "self": False, "agent_name": "Agent B"},
                {"name": "front", "self": False, "agent_name": None},
            ],
            "usage": {
                "inbox": "fungis inbox",
                "history": "fungis history 20",
                "reply_pm": 'fungis reply "..."',
                "message_role": 'fungis reply --role ROLE "..."',
                "copy_role": 'fungis reply --ref ROLE "..."',
                "request_review": 'fungis request --level r2 "..."',
                "request_approval": 'fungis request --level r3 "..."',
                "work_start": 'fungis work start "..."',
                "work_report": 'fungis work report "..."',
                "work_done": 'fungis work done "..."',
                "recovery": "if inbox output was lost, fungis history 20",
            },
        }
    )
    assert "you: @dev-lead" in output
    assert "roles: @dev-lead=you, @reviewer=Agent B, @front=unassigned" in output
    assert "fungis request --level r3" in output
    assert "restore context: fungis history 20" in output
    assert "recovery:" in output
    assert "for_me=false means you were copied" in output
    assert "language PM uses" in output
    assert "Do not restate it in the terminal" in output


def test_active_project_defaults_and_persists_per_agent(tmp_path):
    registry = LocalRegistry(tmp_path / "node.db")
    assert active_project(registry, "agent-a") == "local"
    registry.set_state("active_project:agent-a", "project-2")
    assert active_project(registry, "agent-a") == "project-2"
    assert active_project(registry, "agent-b") == "local"
    registry.close()


def test_send_as_resolves_local_sender_name_to_server_principal(monkeypatch):
    class FakeRegistry:
        def pm_principal_id(self): return "pm-node"
        def node_id(self): return "node-test"
        def binding(self, identity):
            if identity == "claude-local":
                return {"principal_id": "agent-node-claude-local"}
            return None

    captured = {}

    def request(self, method, path, payload=None):
        captured.update(payload or {})
        return {"seq": 1}

    monkeypatch.setattr(PMClient, "_request", request)
    client = PMClient("http://127.0.0.1:8787", FakeRegistry())
    client.send_as("claude-local", "pm-node", "done")
    assert captured["sender_id"] == "agent-node-claude-local"
    assert captured["recipient_ids"] == ["pm-node"]


def test_history_is_shared_compact_context_and_send_echoes_stored_body():
    message = {
        "seq": 17, "created_at": "2026-08-14T00:00:00Z",
        "sender_name": "CTO", "sender_id": "cto", "body": "four conditions",
        "kind": "message", "reply_level": "r1", "in_reply_to": None,
        "track": "merge", "tags": ["approval"],
        "role_recipients": [
            {"name": "dev-lead", "delivered_agent_id": "agent-a"}
        ],
        "recipients": [
            {"recipient_id": "agent-a", "display_name": "Agent A"},
            {"recipient_id": "pm", "display_name": "PM"},
        ],
    }
    history = compact_history("project-1", [message])
    assert history["messages"][0]["to"] == ["@dev-lead", "PM"]
    assert history["messages"][0]["body"] == "four conditions"
    echo = stored_echo(
        {
            "seq": 17, "workspace_id": "project-1", "sender_id": "agent-a",
            "recipient_ids": ["pm"], "body": "exact stored text",
            "kind": "message", "reply_level": "r1", "in_reply_to": None,
            "track": "design/ui-atlas", "tags": ["ticket/fungis"],
        },
        roles=[],
    )
    assert echo["stored"]["body"] == "exact stored text"
    assert echo["stored"]["body_chars"] == 17
    assert echo["stored"]["track"] == "design/ui-atlas"
    assert echo["stored"]["tags"] == ["ticket/fungis"]


def test_inbox_stdout_is_one_pure_json_document_and_guidance_is_stderr(capsys):
    emit_inbox(
        [{
            "seq": 25, "workspace_id": "local", "sender_id": "cto",
            "sender_name": "CTO", "body": "approve", "track": None, "tags": [],
        }]
    )
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "messages": [{
            "seq": 25, "project": "local", "from": "CTO", "for_me": True,
            "chain": 0, "body": "approve", "track": None, "tags": [],
        }]
    }
    assert captured.out.count("\n") == 1
    assert "Reply with:" not in captured.out
    assert "Reply with:" in captured.err
    assert "fungis history 20" in captured.err
    assert "copied" not in captured.err
    assert "agent turns" not in captured.err


def test_inbox_reports_chain_length_without_blocking(capsys):
    emit_inbox(
        [{
            "seq": 40, "workspace_id": "local", "sender_id": "cto",
            "sender_name": "CTO", "body": "재확인", "track": None, "tags": [],
            "agent_chain": 7,
        }]
    )
    captured = capsys.readouterr()
    assert json.loads(captured.out)["messages"][0]["chain"] == 7
    assert "7 agent turns" in captured.err
    assert "fact to add" in captured.err


def test_inbox_marks_reference_messages_as_listen_only(capsys):
    emit_inbox(
        [{
            "seq": 26, "workspace_id": "local", "sender_id": "cto",
            "sender_name": "CTO", "body": "PM께 보고", "track": None, "tags": [],
            "is_reference": 1,
        }]
    )
    captured = capsys.readouterr()
    assert json.loads(captured.out)["messages"][0]["for_me"] is False
    assert "do not reply" in captured.err


def test_message_help_documents_tracks_tags_roles_and_inheritance(capsys):
    with pytest.raises(SystemExit) as stopped:
        parser().parse_args(["reply", "--help"])
    assert stopped.value.code == 0
    help_text = capsys.readouterr().out
    assert "--track feature/login --tag commit/abc123" in help_text
    assert "stable role recipient" in help_text
    assert "inherits the parent message's track and tags" in help_text
    assert "exact body stored by the server" in help_text


def test_write_error_points_to_init_and_history_recovery():
    message = write_error_message(RuntimeError("server 409: role unavailable"))
    assert "fungis init --project PROJECT_ID" in message
    assert "fungis history 20" in message


def test_reference_accepts_the_assignee_principal(monkeypatch):
    """앱의 CC 칩은 역할이 아니라 담당자 principal을 보낸다.

    이걸 못 알아보면 마지막 방어선(_targets)까지 흘러가는데, 그건
    sync_connections에서만 채워지고 앱의 발송 경로는 그걸 부르지 않아 늘
    비어 있다. 8/16 실측에서 CC가 통째로 'reference not found'로 막혔다.
    """
    class FakeRegistry:
        def pm_principal_id(self): return "pm-node"
        def node_id(self): return "node-test"
        def binding(self, identity): return None

    captured = {}

    def request(self, method, path, payload=None):
        if path.endswith("/roles"):
            return [{
                "id": "role-1", "name": "a1",
                "agent_id": "agent-local-claude-abc123",
            }]
        captured.update(payload or {})
        return {"seq": 1}

    monkeypatch.setattr(PMClient, "_request", request)
    client = PMClient("http://127.0.0.1:8787", FakeRegistry())
    client.send_many(
        ["pm-node"], "보고",
        reference_ids=["agent-local-claude-abc123"],
    )
    assert captured["reference_ids"] == ["agent-local-claude-abc123"]


def test_inbox_warns_when_messages_span_rooms(capsys):
    """여러 방에서 온 것을 한 번에 읽으면 답장 목적지가 흐려진다.

    기본 목적지를 마지막 것으로 뒤집으면, 다른 방 얘기를 하려던 답장이 방금
    읽은 방으로 간다. 8/18에 archivia CTO가 두 방에 앉으면서 실제로 그
    상태가 됐다.
    """
    emit_inbox([
        {"seq": 1, "workspace_id": "room-a", "sender_id": "pm",
         "sender_name": "PM", "body": "a", "track": None, "tags": []},
        {"seq": 2, "workspace_id": "room-b", "sender_id": "pm",
         "sender_name": "PM", "body": "b", "track": None, "tags": []},
    ])
    captured = capsys.readouterr()
    assert "--project" in captured.err
    assert {m["project"] for m in json.loads(captured.out)["messages"]} == {
        "room-a", "room-b"
    }


def test_inbox_is_quiet_when_one_room(capsys):
    emit_inbox([
        {"seq": 1, "workspace_id": "room-a", "sender_id": "pm",
         "sender_name": "PM", "body": "a", "track": None, "tags": []},
    ])
    assert "--project" not in capsys.readouterr().err


def test_compact_board_carries_the_command_to_ask_the_blocking_project():
    """막힌 노드는 무엇을 기다리는지와 물어보는 법을 같이 들고 온다.

    "archivia를 기다림"까지만 주면 에이전트가 누구에게 어떻게 물을지 한 번
    더 생각해야 한다. 명령이 실려 오면 생각할 것이 없다.
    """
    board = [
        {
            "project_id": "archivia", "project_name": "archivia",
            "nodes": [{
                "id": "n1", "title": "선행작업", "status": "active",
                "state": "active", "waits_for": [], "blocked_by": [],
            }],
        },
        {
            "project_id": "fungis", "project_name": "fungis",
            "nodes": [
                {
                    "id": "n2", "title": "2단계", "status": "todo",
                    "state": "waiting", "waits_for": ["n1"], "blocked_by": ["n1"],
                },
                {
                    "id": "n3", "title": "1단계 후속", "status": "active",
                    "state": "active", "waits_for": [], "blocked_by": [],
                },
            ],
        },
    ]
    compact = compact_board(board)["board"]
    fungis = next(track for track in compact if track["project"] == "fungis")
    blocked = next(node for node in fungis["nodes"] if node["id"] == "n2")
    assert blocked["state"] == "waiting"
    assert blocked["waiting_for"] == [
        {
            "project": "archivia", "title": "선행작업", "status": "active",
            "ask": 'fungis ask archivia "..."',
        }
    ]
    # 안 막힌 노드에는 붙이지 않는다. 없는 자리에 명령이 있으면 부른다.
    running = next(node for node in fungis["nodes"] if node["id"] == "n3")
    assert "waiting_for" not in running
