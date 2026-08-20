import json

from fastapi.testclient import TestClient

from fungis_server.app import create_app


def test_permission_request_round_trip(tmp_path):
    with TestClient(create_app(tmp_path / "perm.db")) as client:
        created = client.post(
            "/v1/permission-requests",
            json={
                "workspace_id": "local",
                "session_id": "sess-1",
                "tool_name": "Write",
                "tool_input": json.dumps({"file_path": "/tmp/x", "content": "hi"}),
                "suggestions": json.dumps([{"type": "setMode", "mode": "acceptEdits"}]),
            },
        ).json()
        assert created["status"] == "pending"

        pending = client.get("/v1/workspaces/local/permission-requests").json()
        assert [item["id"] for item in pending] == [created["id"]]

        resolved = client.patch(
            f"/v1/permission-requests/{created['id']}", json={"status": "allowed"}
        ).json()
        assert resolved["status"] == "allowed"
        assert resolved["resolved_at"]

        assert client.get("/v1/workspaces/local/permission-requests").json() == []


def test_first_answer_wins(tmp_path):
    with TestClient(create_app(tmp_path / "perm.db")) as client:
        created = client.post(
            "/v1/permission-requests",
            json={
                "workspace_id": "local",
                "session_id": "sess-2",
                "tool_name": "Bash",
                "tool_input": json.dumps({"command": "rm -rf /"}),
                "suggestions": None,
            },
        ).json()

        client.patch(f"/v1/permission-requests/{created['id']}", json={"status": "denied"})
        # 사람이 누른 답과 시간 초과가 겹쳐도 먼저 온 쪽을 지킨다.
        again = client.patch(
            f"/v1/permission-requests/{created['id']}", json={"status": "expired"}
        ).json()
        assert again["status"] == "denied"


def test_unknown_request_is_not_found(tmp_path):
    with TestClient(create_app(tmp_path / "perm.db")) as client:
        assert client.get("/v1/permission-requests/nope").status_code == 404


def test_stale_pending_requests_stop_showing_as_cards(tmp_path, monkeypatch):
    """게이트가 죽으면 pending 행이 영원히 남는다.

    묻는 쪽은 정해진 시간만 기다리다 비켜선다. 그 뒤에도 pending인 것은 답을
    받아갈 프로세스가 없다는 뜻이라, PM 화면에 눌러도 아무 일 없는 카드가
    쌓인다. 8/16 실측에서 서버 재시작과 겹쳐 그대로 남았다.
    """
    from fungis_server import db as db_module

    with TestClient(create_app(tmp_path / "perm.db")) as client:
        created = client.post(
            "/v1/permission-requests",
            json={
                "workspace_id": "local",
                "session_id": "sess-stale",
                "tool_name": "Bash",
                "tool_input": json.dumps({"command": "ls"}),
                "suggestions": None,
            },
        ).json()
        assert [item["id"] for item in client.get(
            "/v1/workspaces/local/permission-requests"
        ).json()] == [created["id"]]

        # 대기 한도를 지나면 카드에서 빠지고 만료로 남는다.
        monkeypatch.setattr(db_module, "PERMISSION_REQUEST_TTL_SECONDS", 0)
        assert client.get("/v1/workspaces/local/permission-requests").json() == []
        assert client.get(
            f"/v1/permission-requests/{created['id']}"
        ).json()["status"] == "expired"


def test_hosted_request_waits_without_terminal_ttl_and_records_scope(tmp_path, monkeypatch):
    from fungis_server import db as db_module

    with TestClient(create_app(tmp_path / "hosted-perm.db")) as client:
        client.post(
            "/v1/principals",
            json={"id": "agent-hosted-1", "kind": "agent", "display_name": "Hosted 1"},
        )
        created = client.post(
            "/v1/permission-requests",
            json={
                "workspace_id": "local",
                "session_id": "thread-1",
                "agent_id": "agent-hosted-1",
                "tool_name": "command",
                "tool_input": json.dumps({"command": ["curl", "example.com"]}),
                "source": "hosted_appserver",
                "request_kind": "command",
                "provider_request_id": "42",
                "thread_id": "thread-1",
                "turn_id": "turn-1",
                "available_decisions": "accept,acceptForSession,decline",
            },
        ).json()

        monkeypatch.setattr(db_module, "PERMISSION_REQUEST_TTL_SECONDS", 0)
        pending = client.get("/v1/workspaces/local/permission-requests").json()
        assert [item["id"] for item in pending] == [created["id"]]

        resolved = client.patch(
            f"/v1/permission-requests/{created['id']}",
            json={
                "status": "allowed",
                "decision": "allowSession",
                "decision_scope": "session",
            },
        ).json()
        assert resolved["decision"] == "allowSession"
        assert resolved["decision_scope"] == "session"


def test_detaching_hosted_agent_cancels_its_pending_requests(tmp_path):
    with TestClient(create_app(tmp_path / "hosted-detach.db")) as client:
        client.post(
            "/v1/principals",
            json={"id": "agent-hosted-2", "kind": "agent", "display_name": "Hosted 2"},
        )
        client.put(
            "/v1/nodes/fungis-app",
            json={"id": "fungis-app", "display_name": "Fungis App"},
        )
        client.put(
            "/v1/bindings/agent-hosted-2",
            json={
                "agent_id": "agent-hosted-2", "node_id": "fungis-app",
                "agent_provider": "codex", "agent_session_id": "thread-2",
                "terminal_provider": "fungis-app", "terminal_session_id": "thread-2",
                "lifecycle": "running",
            },
        )
        created = client.post(
            "/v1/permission-requests",
            json={
                "workspace_id": "local", "session_id": "thread-2",
                "agent_id": "agent-hosted-2", "tool_name": "fileChange",
                "tool_input": "{}", "source": "hosted_appserver",
                "request_kind": "fileChange",
            },
        ).json()

        assert client.delete("/v1/bindings/agent-hosted-2").status_code == 204
        closed = client.get(f"/v1/permission-requests/{created['id']}").json()
        assert closed["status"] == "expired"
        assert closed["decision"] == "cancel"


def test_gate_never_holds_the_terminal(tmp_path, monkeypatch):
    """이 hook은 권한 화면이 뜨기 전에 돈다. 여기서 기다리면 질문조차 안 뜬다.

    예전에는 답을 기다리느라 최대 110초를 붙잡았다. 결정을 돌려줄 수도 없는데
    (permissionDecision은 PreToolUse 전용) 터미널만 얼렸다. 8/16 실측에서 PM이
    "걍 블로킹"으로 겪었다.
    """
    import io
    from fungis_node import agent_cli

    polled = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def create_permission_request(self, **kwargs):
            return {"id": "req-1"}

        def permission_request(self, request_id):
            polled.append(request_id)
            return {"status": "pending"}

    class FakeRegistry:
        def state(self, key):
            return "local"

    monkeypatch.setattr(agent_cli, "PMClient", FakeClient)
    monkeypatch.setattr(
        agent_cli.sys, "stdin",
        io.StringIO(json.dumps({"session_id": "s-1", "tool_name": "Bash"})),
    )

    result = agent_cli.permission_gate(
        {"server": "http://127.0.0.1:8787"}, FakeRegistry(),
        {"principal_id": "agent-a"}, wait_seconds=110,
    )
    assert result == {}
    assert polled == []


def test_clear_removes_only_this_session_notice(tmp_path, monkeypatch):
    """도구가 실제로 돌았다는 것은 사람이 답을 끝냈다는 뜻이다.

    서버는 터미널의 답을 알 수 없어 시간으로만 걷었고, 그동안 PM 화면의
    입력창이 괜히 막혀 있었다. 같은 방의 다른 세션 것까지 걷으면 안 된다.
    """
    import io
    from fungis_node import agent_cli

    resolved = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def pending_permission_requests(self):
            return [
                {"id": "mine", "session_id": "s-1"},
                {"id": "someone-else", "session_id": "s-2"},
            ]

        def resolve_permission_request(self, request_id, status):
            resolved.append((request_id, status))

    class FakeRegistry:
        def state(self, key):
            return "local"

    monkeypatch.setattr(agent_cli, "PMClient", FakeClient)
    monkeypatch.setattr(
        agent_cli.sys, "stdin", io.StringIO(json.dumps({"session_id": "s-1"}))
    )

    result = agent_cli.permission_clear(
        {"server": "http://127.0.0.1:8787"}, FakeRegistry(),
        {"principal_id": "agent-a"},
    )
    assert result == {"cleared": ["mine"]}
    assert resolved == [("mine", "expired")]
