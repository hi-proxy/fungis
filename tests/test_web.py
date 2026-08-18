from fastapi.testclient import TestClient

from fungis_node.cmux import CmuxAgentCandidate
from fungis_node.registry import LocalRegistry
from fungis_node.web import create_web_app, pm_relation
from fungis_server.app import create_app


class FakeCmux:
    def __init__(self):
        self.focused = []
        self.candidate = CmuxAgentCandidate(
            "claude",
            "session-1",
            "surface-1",
            "surface:1",
            "workspace:1",
            "Agent one",
            "ttys001",
            "/project",
            "idle",
            True,
            "agent_tty_matches_surface",
        )

    def discover_agents(self):
        return [self.candidate]

    def focus(self, candidate):
        self.focused.append(candidate.surface_id)


def test_pm_relation_distinguishes_direct_reference_and_ambient():
    base = {"sender_id": "agent", "kind": "message", "reply_level": "r1"}
    assert pm_relation(
        {**base, "recipients": [{"recipient_id": "pm"}], "references": []}, "pm"
    ) == "direct"
    assert pm_relation(
        {**base, "recipients": [], "references": [{"principal_id": "pm"}]}, "pm"
    ) == "reference"
    assert pm_relation({**base, "recipients": [], "references": []}, "pm") == "ambient"
    assert pm_relation(
        {
            **base,
            "kind": "pm_request",
            "reply_level": "r3",
            "recipients": [{"recipient_id": "pm"}],
            "references": [],
        },
        "pm",
    ) == "confirm"


def test_web_index_is_served(tmp_path):
    app = create_web_app(tmp_path / "node.db", cmux=FakeCmux())
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "Fungis" in response.text


def test_control_health_does_not_require_server_or_cmux(tmp_path):
    app = create_web_app(tmp_path / "node.db", cmux=FakeCmux())
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok", "sends_wakes": True}


def test_health_admits_when_it_is_not_sending_wakes(tmp_path):
    """200만 주는 health가 dry-run daemon을 정상으로 보이게 했다.

    앱은 8790이 200이면 그 daemon을 자기 것으로 삼고 제대로 된 것을 띄우지
    않는다. 그래서 앱을 껐다 켜도 아무것도 안 오는 상태가 유지됐다. 무엇을
    보증하지 않는지 health가 말해야 앱이 구별할 수 있다.
    """
    app = create_web_app(tmp_path / "node.db", cmux=FakeCmux(), sends_wakes=False)
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok", "sends_wakes": False}


def test_web_agent_toggle_and_focus_use_local_cmux(tmp_path):
    cmux = FakeCmux()
    app = create_web_app(tmp_path / "node.db", cmux=cmux)
    with TestClient(app) as client:
        connected = client.post(
            "/api/agents/action",
            json={"surface_id": "surface-1", "action": "toggle"},
        )
        assert connected.status_code == 200
        registry = LocalRegistry(tmp_path / "node.db")
        assert registry.list()[0]["agent_session_id"] == "session-1"
        registry.close()

        focused = client.post(
            "/api/agents/action",
            json={"surface_id": "surface-1", "action": "focus"},
        )
        assert focused.json()["status"] == "focused"
        assert cmux.focused == ["surface-1"]

        disconnected = client.post(
            "/api/agents/action",
            json={"surface_id": "surface-1", "action": "toggle"},
        )
        assert disconnected.status_code == 200
        registry = LocalRegistry(tmp_path / "node.db")
        assert registry.list() == []
        registry.close()


def test_web_state_and_send_wrap_existing_server_contract(tmp_path, monkeypatch):
    server_app = create_app(tmp_path / "server.db")
    server = TestClient(server_app)
    server.__enter__()

    def request(self, method, path, payload=None):
        response = server.request(method, path, json=payload)
        response.raise_for_status()
        return {} if response.status_code == 204 else response.json()

    monkeypatch.setattr("fungis_node.pm.PMClient._request", request)
    cmux = FakeCmux()
    registry = LocalRegistry(tmp_path / "node.db")
    registry.attach("agent-one", cmux.candidate)
    registry.close()
    app = create_web_app(tmp_path / "node.db", cmux=cmux)
    try:
        with TestClient(app) as client:
            state = client.get("/api/state").json()
            assert state["targets"][0]["local_name"] == "agent-one"
            assert state["agents"][0]["connected"] is True
            role = client.post(
                "/api/roles",
                json={"name": "front1", "onboarding_prompt": "Own the frontend."},
            ).json()
            assigned = client.put(
                f"/api/roles/{role['id']}/assignment",
                json={"agent_id": "agent-one", "send_onboarding": False},
            )
            assert assigned.status_code == 200
            role_state = client.get("/api/state").json()["roles"][0]
            assert role_state["agent_id"] == state["targets"][0]["principal_id"]
            assert role_state["session_connected"] is True
            with client.websocket_connect("/api/events") as websocket:
                streamed = websocket.receive_json()
                assert streamed["targets"][0]["local_name"] == "agent-one"
                assert streamed["agents"][0]["connected"] is True
                sent = client.post(
                    "/api/messages",
                    json={"recipient_ids": ["agent-one"], "body": "hello from web"},
                )
                assert sent.status_code == 201
                changed = websocket.receive_json()
                assert changed["timeline"][-1]["body"] == "hello from web"
            refreshed = client.get("/api/state").json()
            assert refreshed["timeline"][-1]["body"] == "hello from web"
            bookmark = client.post(
                f"/api/projects/local/messages/{sent.json()['seq']}/bookmarks",
                json={"label": "빌드2 배포"},
            )
            assert bookmark.status_code == 201
            assert client.get("/api/state").json()["bookmarks"][0]["label"] == "빌드2 배포"
            assert client.delete(
                f"/api/projects/local/bookmarks/{bookmark.json()['id']}"
            ).status_code == 204
            assert client.get("/api/state").json()["bookmarks"] == []
            pin = client.post(
                f"/api/projects/local/messages/{sent.json()['seq']}/timeline-pins",
                json={"label": "디자인 웨이브 완료"},
            )
            assert pin.status_code == 201
            assert client.get("/api/state").json()["timeline_pins"][0]["label"] == "디자인 웨이브 완료"
            assert client.delete(
                f"/api/projects/local/timeline-pins/{pin.json()['id']}"
            ).status_code == 204
    finally:
        server.__exit__(None, None, None)


def test_chat_state_is_ten_messages_and_history_pages_back_by_fifty(
    tmp_path, monkeypatch
):
    server_app = create_app(tmp_path / "server.db")
    server = TestClient(server_app)
    server.__enter__()

    def request(self, method, path, payload=None):
        response = server.request(method, path, json=payload)
        response.raise_for_status()
        return {} if response.status_code == 204 else response.json()

    monkeypatch.setattr("fungis_node.pm.PMClient._request", request)
    for principal_id, kind in (("pm-local", "human"), ("agent", "agent")):
        server.put(
            f"/v1/principals/{principal_id}",
            json={"id": principal_id, "kind": kind, "display_name": principal_id},
        )
    for index in range(65):
        response = server.post(
            "/v1/messages",
            json={
                "workspace_id": "local", "sender_id": "pm-local",
                "recipient_ids": ["agent"], "body": f"message-{index:02d}",
            },
        )
        assert response.status_code == 201

    app = create_web_app(tmp_path / "node.db", cmux=FakeCmux())
    try:
        with TestClient(app) as control:
            latest = control.get("/api/state").json()["timeline"]
            assert len(latest) == 10
            assert [item["body"] for item in latest] == [
                f"message-{index:02d}" for index in range(55, 65)
            ]

            background = control.get(
                "/api/projects/local/history",
                params={"before": latest[0]["seq"], "limit": 50},
            ).json()
            assert len(background) == 50
            assert [item["body"] for item in background] == [
                f"message-{index:02d}" for index in range(5, 55)
            ]

            oldest = control.get(
                "/api/projects/local/history",
                params={"before": background[0]["seq"], "limit": 50},
            ).json()
            assert [item["body"] for item in oldest] == [
                f"message-{index:02d}" for index in range(5)
            ]
    finally:
        server.__exit__(None, None, None)


def test_web_nickname_is_persisted_without_changing_routing_id(tmp_path, monkeypatch):
    server_app = create_app(tmp_path / "server.db")
    server = TestClient(server_app)
    server.__enter__()

    def request(self, method, path, payload=None):
        response = server.request(method, path, json=payload)
        response.raise_for_status()
        return {} if response.status_code == 204 else response.json()

    monkeypatch.setattr("fungis_node.pm.PMClient._request", request)
    cmux = FakeCmux()
    registry = LocalRegistry(tmp_path / "node.db")
    registry.attach("agent-one", cmux.candidate)
    principal_id = registry.binding("agent-one")["principal_id"]
    registry.close()
    app = create_web_app(tmp_path / "node.db", cmux=cmux)
    try:
        with TestClient(app) as client:
            response = client.patch(
                "/api/agents/agent-one/nickname", json={"nickname": "설계 담당"}
            )
            assert response.json()["nickname"] == "설계 담당"
            state = client.get("/api/state").json()
            assert state["targets"][0]["nickname"] == "설계 담당"
            assert state["agents"][0]["nickname"] == "설계 담당"
        registry = LocalRegistry(tmp_path / "node.db")
        assert registry.binding("agent-one")["principal_id"] == principal_id
        assert registry.binding("agent-one")["nickname"] == "설계 담당"
        registry.close()
    finally:
        server.__exit__(None, None, None)


def test_project_repository_is_local_verified_and_in_snapshot(tmp_path, monkeypatch):
    server_app = create_app(tmp_path / "server.db")
    server = TestClient(server_app)
    server.__enter__()

    def request(self, method, path, payload=None):
        response = server.request(method, path, json=payload)
        response.raise_for_status()
        return {} if response.status_code == 204 else response.json()

    monkeypatch.setattr("fungis_node.pm.PMClient._request", request)
    repository = tmp_path / "repo"
    repository.mkdir()
    import subprocess
    subprocess.run(["git", "init", "-b", "main", str(repository)], check=True, capture_output=True)
    app = create_web_app(tmp_path / "node.db", cmux=FakeCmux())
    try:
        with TestClient(app) as client:
            saved = client.put(
                "/api/projects/local/repository", json={"path": str(repository)}
            )
            assert saved.status_code == 200
            assert saved.json()["git"]["branch"] == "main"
            state = client.get("/api/state", params={"project_id": "local"}).json()
            assert state["project_repositories"][0]["path"] == str(repository)
            assert state["project_repositories"][0]["git"]["verified"] is True
            assert client.delete("/api/projects/local/repository").status_code == 204
    finally:
        server.__exit__(None, None, None)
