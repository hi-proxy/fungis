import json

import pytest

from fungis_node.agent_cli import (
    active_project, addressing, compact_history, default_recipients, emit_inbox,
    warn_if_nobody_received,
    format_bootstrap, legacy_hint, load_config,
    parser, read_state, render_board, render_members, render_state,
    reply_reference, resolve_project, resolve_room, stored_echo,
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
            "chain": 0, "in_reply_to": None, "body": "approve",
            "track": None, "tags": [],
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
    assert "recipient by role" in help_text
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


def test_a_blocked_ticket_shows_which_room_to_ask_by_its_prefix():
    """막힌 쪽은 무엇을 기다리는지와 누구에게 물을지를 같이 받아야 한다.

    예전에는 줄마다 `fungis ask <uuid>` 명령을 실어 보냈다. 이제는 티켓 이름이
    방을 들고 다니므로 ARCH-1을 읽으면 물어볼 방이 ARCH라는 것이 바로 나온다.
    ask가 그 프리픽스를 그대로 받는다 — 한 번 더 대조하게 만들면 거기서 착오가 난다.
    """
    board = [
        {
            "project_id": "p-archivia", "project_name": "archivia",
            "ticket_prefix": "ARCH",
            "nodes": [{
                "id": "n1", "number": 1, "title": "선행작업", "status": "active",
                "state": "active", "blocked_by": [], "blocks": ["n2"],
            }],
        },
        {
            "project_id": "p-fungis", "project_name": "fungis",
            "ticket_prefix": "FUNG",
            "nodes": [{
                "id": "n2", "number": 2, "title": "2단계", "status": "todo",
                "state": "waiting", "blocked_by": ["n1"], "blocks": [],
            }],
        },
    ]
    line = next(
        row for row in render_board(board).split("\n") if row.startswith("FUNG-2 ")
    )
    assert "blockedBy ARCH-1" in line

    assert resolve_room(board, "ARCH") == "p-archivia"
    assert resolve_room(board, "arch") == "p-archivia"
    assert resolve_room(board, "archivia") == "p-archivia"
    assert resolve_room(board, "p-archivia") == "p-archivia"
    try:
        resolve_room(board, "nowhere")
    except RuntimeError as error:
        assert "ARCH" in str(error) and "FUNG" in str(error)
    else:
        raise AssertionError("없는 방은 거절해야 한다")


class FakeClient:
    """서버 대신 서는 최소한의 답변자. 명령 표면만 시험한다."""

    def __init__(
        self, *, workspace_id="local", roles=(), hq=None, members=None,
        memberships=(), board=(), projects=(),
    ):
        self.workspace_id = workspace_id
        self.pm_id = "pm"
        self._roles = list(roles)
        self._hq = hq
        self._members = members or {}
        self._memberships = list(memberships)
        self._board = list(board)
        self._projects = list(projects)
        self.asked = []

    def roles(self):
        return self._roles

    def hq(self):
        return self._hq

    def board(self):
        return self._board

    def projects(self):
        return self._projects

    def members(self, workspace_id=None):
        room = workspace_id or self.workspace_id
        self.asked.append(room)
        return self._members[room]

    def agent_role_memberships(self):
        return self._memberships


class Options:
    def __init__(self, to=(), to_id=(), cc=(), cc_id=()):
        self.to = list(to)
        self.to_id = list(to_id)
        self.cc = list(cc)
        self.cc_id = list(cc_id)


def test_state_lists_every_room_with_my_role_and_who_leads_it():
    """한 세션이 여러 방의 역할을 가질 수 있다. 인자 없이 부르면 전부 나온다.

    lead 칸의 you 와 - 는 다른 사실이다. 나가 이끄는 방과 아무도 이끌지 않는
    방을 같은 글자로 적으면 소집을 누가 받는지 알 수 없다.
    """
    output = render_state(
        "claude-c00cddca",
        "agent-me",
        [
            {
                "project_name": "fungis", "my_roles": ["dispatch.dev"],
                "lead": {"name": "archivia.dev", "agent_id": "agent-other"},
            },
            {
                "project_name": "ARCHIVIA bookclub", "my_roles": ["reviewer"],
                "lead": {"name": "reviewer", "agent_id": "agent-me"},
            },
        ],
    )
    assert output.split("\n") == [
        "you       claude-c00cddca",
        'project   "fungis"             @dispatch.dev  @archivia.dev',
        'project   "ARCHIVIA bookclub"  @reviewer      you',
    ]


def test_state_marks_an_unled_room_with_a_dash():
    output = render_state(
        "claude-1", "agent-me",
        [{"project_name": "mei", "my_roles": ["front1"], "lead": None}],
    )
    assert output.split("\n")[1] == 'project   "mei"  @front1  -'


def test_state_detail_separates_an_empty_seat_from_not_being_lead():
    """NONE 은 값이 비었다는 뜻, - 는 해당 없음이라는 뜻이다."""
    output = render_members(
        {
            "project_name": "fungis",
            "roles": [
                {
                    "name": "archivia.dev", "agent_id": "x",
                    "agent_name": "claude-archivia-cto", "is_lead": True,
                },
                {
                    "name": "dispatch.dev", "agent_id": "agent-me",
                    "agent_name": "claude-me", "is_lead": False,
                },
                {"name": "front1", "agent_id": None, "is_lead": False},
            ],
        },
        "agent-me",
    )
    assert output.split("\n") == [
        'project   "fungis"',
        "member    @archivia.dev  claude-archivia-cto  lead",
        "member    @dispatch.dev  you                  -",
        "member    @front1        NONE                 -",
    ]
    # 목록에 이미 있는 것을 상세에 또 적지 않는다.
    assert "role " not in output


def test_state_reads_without_touching_the_active_project():
    client = FakeClient(
        memberships=[
            {
                "agent_id": "agent-me", "project_id": "p-fungis",
                "project_name": "fungis", "role_name": "dispatch.dev",
            },
            {
                "agent_id": "someone-else", "project_id": "p-mei",
                "project_name": "mei", "role_name": "front1",
            },
        ],
        members={
            "p-fungis": {
                "project_name": "fungis",
                "lead": {"name": "dispatch.dev", "agent_id": "agent-me"},
                "roles": [],
            }
        },
    )
    output = read_state(
        client, {"principal_id": "agent-me", "local_name": "claude-1"}, None
    )
    assert output.split("\n") == [
        "you       claude-1",
        'project   "fungis"  @dispatch.dev  you',
    ]


def test_to_narrows_the_default_recipient_instead_of_adding_to_it():
    """--to 를 주면 기본 수신자는 사라진다. 결과를 봐도 알 수 없는 규칙이다."""
    client = FakeClient(roles=[{"id": "role-1", "name": "reviewer"}])

    role_ids, direct, cc, cc_ids = addressing(client, Options(to=["reviewer"]))
    assert (role_ids, direct) == (["reviewer"], [])
    # 지목했으므로 PM 은 붙지 않는다.
    assert (role_ids or direct) != []

    role_ids, direct, cc, cc_ids = addressing(client, Options())
    assert (role_ids, direct) == ([], [])
    assert default_recipients(client, "reply") == ["pm"]
    assert default_recipients(client, "request") == ["pm"]
    # send 는 주소 없이 자리에 붙이는 것이라 아무도 받지 않는다.
    assert default_recipients(client, "send") == []


def test_hq_with_no_recipient_means_every_convened_lead():
    client = FakeClient(workspace_id="hq-1", hq={"id": "hq-1", "name": "HQ"})
    for command in ("send", "reply", "request"):
        assert default_recipients(client, command) == []


def test_an_unknown_to_value_goes_out_as_an_address_for_the_server_to_resolve():
    """HQ 에는 역할이 없다. 방 이름을 그대로 넘겨 서버가 그 방 lead 로 푼다."""
    client = FakeClient(workspace_id="hq-1", roles=[])
    role_ids, direct, _, _ = addressing(client, Options(to=["ARCH"]))
    assert (role_ids, direct) == ([], ["ARCH"])


def test_sending_to_nobody_says_so_instead_of_looking_like_success(capsys):
    """recipient_ids 가 빈 것은 성공 출력 안에서 실패처럼 안 보인다.

    한 리드가 이 자리에서 세 번 연속으로 갔다고 믿었다. 방에는 남으니
    실패는 아니지만, 아무도 안 깨웠다는 것은 말해 줘야 안다.
    """
    warn_if_nobody_received({"recipient_ids": []}, [])
    assert "아무도 받지 않았다" in capsys.readouterr().err

    # 역할로 갔으면 간 것이다.
    warn_if_nobody_received({"recipient_ids": []}, ["reviewer"])
    # HQ 는 서버가 lead 전원으로 풀어 돌려준다.
    warn_if_nobody_received({"recipient_ids": ["agent-1"]}, [])
    captured = capsys.readouterr()
    assert captured.err == ""
    # 어느 경우에도 stdout 은 건드리지 않는다. 거기는 JSON 한 덩어리다.
    assert captured.out == ""


def test_a_role_can_be_typed_the_way_the_screen_shows_it():
    """state 는 역할을 @이름으로 보여준다. 읽은 그대로 쳐야 간다.

    안 받으면 @붙인 값이 수신자 자리로 새고, 서버는 그런 id 가 없다며
    외래키 오류를 뱉는다. 화면에서 베껴 친 사람은 이유를 알 수 없다.
    """
    client = FakeClient(roles=[{"id": "role-1", "name": "mei.dev"}])
    for typed in ("mei.dev", "@mei.dev"):
        role_ids, direct, _, _ = addressing(client, Options(to=[typed]))
        assert (role_ids, direct) == (["mei.dev"], []), typed

    # cc 도 같은 화면에서 베낀다.
    _, _, cc, _ = addressing(client, Options(cc=["@mei.dev"]))
    assert cc == ["mei.dev"]


def test_absolute_ids_stay_absolute():
    client = FakeClient(roles=[{"id": "role-1", "name": "reviewer"}])
    role_ids, direct, cc, cc_ids = addressing(
        client, Options(to=["reviewer"], to_id=["agent-x"], cc=["front1"],
                        cc_id=["agent-y"])
    )
    assert role_ids == ["reviewer"]
    assert direct == ["agent-x"]
    assert cc == ["front1"]
    assert cc_ids == ["agent-y"]


def test_old_syntax_fails_by_naming_what_replaced_it():
    """argparse 기본 오류는 무엇이 없는지만 말하고 새 이름은 말하지 않는다."""
    assert "--to" in legacy_hint(["reply", "--role", "reviewer", "hi"])
    assert "--cc" in legacy_hint(["reply", "--reference", "pm", "hi"])
    assert "--reply" in legacy_hint(["send", "--in-reply-to", "42", "hi"])
    assert "fungis send --project HQ --to" in legacy_hint(["ask", "ARCH", "hi"])
    hint = legacy_hint(["reply", "--project", "mei", "hi"])
    assert "fungis send --project" in hint and "--reply N" in hint
    # 새 문법은 그대로 지나간다.
    assert legacy_hint(["send", "--project", "HQ", "--to", "ARCH", "hi"]) is None
    assert legacy_hint(["history", "20", "--project", "HQ"]) is None
    assert legacy_hint(["state", "--project", "fungis"]) is None


def test_the_parser_itself_stops_on_old_syntax():
    with pytest.raises(SystemExit) as stopped:
        parser().parse_args(["reply", "--role", "reviewer", "hi"])
    assert "--to" in str(stopped.value)


def test_reply_takes_the_reference_as_its_first_argument():
    args = parser().parse_args(["reply", "42", "verified"])
    assert reply_reference(args.ref) == 42
    assert args.body == ["verified"]
    plain = parser().parse_args(["reply", "done"])
    assert reply_reference(plain.ref) is None
    assert plain.body == ["done"]
    # reply 에는 --project 가 없다.
    assert not hasattr(plain, "project")


def test_an_unquoted_body_is_refused_instead_of_losing_the_reference():
    with pytest.raises(RuntimeError) as error:
        reply_reference("done")
    assert "따옴표" in str(error.value)


def test_send_and_request_carry_the_reference_as_a_flag():
    args = parser().parse_args(["send", "--reply", "42", "-p", "HQ", "note"])
    assert (args.reply, args.project, args.body) == (42, "HQ", ["note"])
    asked = parser().parse_args(["request", "--level", "r3", "approve"])
    assert (asked.level, asked.reply) == ("r3", None)


def test_only_three_short_flags_exist(capsys):
    with pytest.raises(SystemExit):
        parser().parse_args(["send", "--help"])
    help_text = capsys.readouterr().out
    assert "-p PROJECT" in help_text
    assert "-t TO" in help_text
    assert "-c CC" in help_text
    for absent in ("-r ", "-l ", "-T "):
        assert absent not in help_text


def test_history_names_one_message_by_its_number_and_picks_a_room():
    args = parser().parse_args(["history", "--ref", "42"])
    assert args.ref == 42
    room = parser().parse_args(["history", "20", "--project", "HQ"])
    assert (room.count, room.project) == (20, "HQ")


def test_a_room_is_named_by_prefix_name_or_id_and_hq_by_its_own_name():
    board = [{
        "project_id": "p-archivia", "project_name": "archivia",
        "ticket_prefix": "ARCH", "nodes": [],
    }]
    client = FakeClient(hq={"id": "hq-1", "name": "HQ"}, board=board)
    assert resolve_project(client, None, "p-mine") == "p-mine"
    assert resolve_project(client, "HQ", "p-mine") == "hq-1"
    assert resolve_project(client, "hq", "p-mine") == "hq-1"
    assert resolve_project(client, "ARCH", "p-mine") == "p-archivia"
    assert resolve_project(client, "archivia", "p-mine") == "p-archivia"

    # 보드에 안 붙은 방은 프로젝트 목록에서 찾는다.
    client._projects = [{"id": "local", "name": "Local"}]
    assert resolve_project(client, "Local", "p-mine") == "local"
    # 아무 데도 없으면 아는 방을 보여주고 멈춘다.
    with pytest.raises(RuntimeError) as error:
        resolve_project(client, "nowhere", "p-mine")
    assert "ARCH" in str(error.value)


def test_send_as_takes_many_recipients_and_leaves_absolute_references_alone(
    monkeypatch,
):
    """HQ 에는 명부가 없다. 절대 id 로 준 참조를 풀려고 하면 거기서 막힌다."""
    class FakeRegistry:
        def pm_principal_id(self): return "pm-node"
        def node_id(self): return "node-test"
        def binding(self, identity): return None

    captured = {}

    def request(self, method, path, payload=None):
        if path.endswith("/roles"):
            return []
        captured.update(payload or {})
        return {"seq": 1}

    monkeypatch.setattr(PMClient, "_request", request)
    client = PMClient("http://127.0.0.1:8787", FakeRegistry())
    client.send_as(
        "claude-local", None, "보고",
        recipient_ids=["ARCH", "agent-x"],
        absolute_reference_ids=["agent-y"],
    )
    assert captured["recipient_ids"] == ["ARCH", "agent-x"]
    assert captured["reference_ids"] == ["agent-y"]


def test_init_teaches_the_new_syntax_and_the_whole_board(tmp_path):
    """새 세션은 init 에서 문법을 배운다. 여기가 옛 이름이면 계속 옛 이름을 친다."""
    from fungis_server.db import FungisDB

    database = FungisDB(tmp_path / "init.db")
    database.upsert_principal(principal_id="pm", kind="human", display_name="PM")
    database.upsert_principal(
        principal_id="agent", kind="agent", display_name="Agent"
    )
    usage = database.project_bootstrap(
        project_id="local", agent_id="agent", pm_id="pm"
    )["usage"]
    assert usage["message_role"] == 'fungis reply --to ROLE "..."'
    assert usage["copy_role"] == 'fungis reply --cc ROLE "..."'
    assert usage["state"] == "fungis state"
    assert "--role" not in json.dumps(usage)
    assert "--reference" not in json.dumps(usage)

    output = format_bootstrap(
        database.project_bootstrap(
            project_id="local", agent_id="agent", pm_id="pm"
        )
    )
    assert 'fungis reply --to ROLE "..."' in output
    assert 'fungis reply --cc ROLE "..."' in output
    assert "fungis state" in output
    # 기다린다는 것을 못 적으면 막힌 일이 보드에서 안 막힌 것으로 보인다.
    assert "fungis board wait TICKET BLOCKER_TICKET" in output
    assert "fungis board unwait TICKET BLOCKER_TICKET" in output


def test_a_reference_split_from_its_body_says_so(capsys):
    """argparse 는 자리 인자가 두 토막 나면 "모르는 인자"라고만 한다."""
    with pytest.raises(SystemExit):
        parser().parse_args(["reply", "42", "--track", "x", "body"])
    assert "붙여서 쓴다" in capsys.readouterr().err


def test_inbox_does_not_follow_a_broadcast_into_hq(tmp_path, monkeypatch):
    """HQ 방송을 받아도 활성 방은 자기 방에 남는다.

    따라가면 방송 한 번에 모든 lead 의 활성 방이 HQ 로 뒤집히고, 그 뒤의 맨
    reply 는 자기 방 대신 HQ 에 붙는다. 실제로 첫 HQ 방송에서 lead 셋의
    문맥이 전부 hq 로 넘어갔고 board add 가 403 을 받았다.
    """
    from fungis_node.registry import LocalRegistry
    from fungis_node.agent_cli import active_project

    registry = LocalRegistry(tmp_path / "node.db")
    registry.set_state("active_project:agent-1", "my-room")

    # inbox 분기의 방 따라가기 규칙만 떼어 재현한다.
    def follow(messages):
        rooms = {
            m.get("workspace_id") for m in messages if m.get("workspace_id")
        }
        if len(rooms) == 1 and (room := rooms.pop()) != "hq":
            registry.set_state("active_project:agent-1", room)

    follow([{"workspace_id": "hq"}])
    assert active_project(registry, "agent-1") == "my-room", "HQ 로 이사가면 안 된다"

    follow([{"workspace_id": "other-room"}])
    assert active_project(registry, "agent-1") == "other-room", "일반 방은 따라간다"
    registry.close()
