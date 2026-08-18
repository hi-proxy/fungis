"""메시지 한 통이 실제로 터미널을 깨우는가.

이 경로는 여태 두 동강으로만 테스트됐다. 서버가 이벤트를 내보내는 것은
test_api가 보고, pending이 있으면 gate가 깨우는 것은 test_gate가 본다.
그 둘이 같은 말을 하는지는 아무도 안 봤다.

그래서 test_gate는 이벤트 딕셔너리를 손으로 지어낸다. 서버가 칸 이름을 바꿔도
테스트는 전부 초록이고 깨우기만 조용히 죽는다. 사용자가 겪은 "메시지가 안 간다"가
두 번 다 이 자리에서 나왔다.

여기서는 손으로 짓지 않는다. 서버 소켓이 실제로 뱉은 것을 그대로 registry에 넣는다.
"""

from fastapi.testclient import TestClient
from test_gate import GateCmux, candidate

from fungis_node.gate import IdleGate
from fungis_node.registry import LocalRegistry
from fungis_server.app import create_app

def enroll(client, principal_id, kind="agent"):
    client.put(
        f"/v1/principals/{principal_id}",
        json={"id": principal_id, "kind": kind, "display_name": principal_id},
    )
    return principal_id


def node(tmp_path, recipient_id):
    registry = LocalRegistry(tmp_path / "node.db")
    registry.attach(recipient_id, candidate())
    return registry, GateCmux(candidate())


def test_a_sent_message_wakes_the_recipient_terminal(tmp_path):
    app = create_app(tmp_path / "api.db")
    registry, cmux = node(tmp_path, "agent-a")
    with TestClient(app) as client:
        enroll(client, "pm", kind="human")
        enroll(client, "agent-a")
        with client.websocket_connect("/v1/events/agent-a?after=0") as websocket:
            client.post(
                "/v1/messages",
                json={
                    "workspace_id": "local",
                    "sender_id": "pm",
                    "recipient_ids": ["agent-a"],
                    "body": "hello",
                },
            )
            # 서버가 실제로 보낸 것을 그대로 넣는다. 칸 이름이 어긋나면 여기서 깨진다.
            registry.record_event(websocket.receive_json())

    IdleGate(registry, cmux, settle_seconds=0).run("agent-a", send=True)
    # 문구는 부르는 쪽이 정한다. 여기서 지키는 것은 서버가 말한 이벤트로
    # 그 터미널이 깨워졌다는 사실이다.
    assert [surface for surface, _ in cmux.wakes] == ["surface-uuid"]


def test_the_gate_stays_quiet_when_nothing_was_sent(tmp_path):
    """깨울 것이 없으면 깨우지 않는다. 위 테스트가 늘 통과하지는 않음을 보인다."""
    registry, cmux = node(tmp_path, "agent-a")
    IdleGate(registry, cmux, settle_seconds=0).run("agent-a", send=True)
    assert cmux.wakes == []


def test_reassigning_a_role_speaks_to_every_new_agent(tmp_path):
    """재배정도 말을 거는가.

    is_new_assignment 분기는 여태 None에서 처음 배정되는 경로만 테스트됐다.
    갈아타기와 되돌아오기는 미검증이었다.
    """
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        enroll(client, "pm", kind="human")
        enroll(client, "agent-a")
        enroll(client, "agent-b")
        role = client.post(
            "/v1/workspaces/local/roles",
            json={"name": "tester", "onboarding_prompt": ""},
        ).json()

        def assign(agent_id):
            before = client.get(
                "/v1/messages", params={"recipient": agent_id, "after": 0}
            ).json()
            client.put(
                f"/v1/roles/{role['id']}/assignment",
                json={
                    "agent_id": agent_id,
                    "assigned_by": "pm",
                    "send_onboarding": True,
                },
            )
            after = client.get(
                "/v1/messages", params={"recipient": agent_id, "after": 0}
            ).json()
            return after[len(before) :]

        # 역할 설명이 비어 있어도 부름은 나가야 한다. 안 나가면 에이전트는 자기가
        # 배정된 줄도 모르고 PM은 보냈다고 믿는다.
        arrived = assign("agent-a")
        assert arrived, "첫 배정에 init이 나가야 한다"
        assert "fungis init --project local" in arrived[-1]["body"]

        arrived = assign("agent-b")
        assert arrived, "다른 에이전트로 갈아타면 그쪽에 init이 나가야 한다"
        assert "fungis init --project local" in arrived[-1]["body"]

        arrived = assign("agent-a")
        assert arrived, "앞서 맡았던 에이전트로 되돌려도 init이 나가야 한다"


def test_the_convene_list_is_not_shared_with_agents(tmp_path):
    """보드는 방끼리 공유하지만 소집은 아니다.

    이 응답에는 전 프로젝트의 이름과 역할과 붙은 에이전트가 들어 있다.
    부를 사람을 고르는 자리라 부르는 쪽만 본다.
    """
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        enroll(client, "pm", kind="human")
        enroll(client, "agent-a")

        assert client.get("/v1/board/candidates", params={"caller": "pm"}).status_code == 200
        assert client.get(
            "/v1/board/candidates", params={"caller": "agent-a"}
        ).status_code == 403
        assert client.get(
            "/v1/board/candidates", params={"caller": "nobody"}
        ).status_code == 403
        # caller를 선택으로 두면 안 싣는 쪽이 곧 우회로가 된다.
        assert client.get("/v1/board/candidates").status_code == 422

        # 노드는 그대로 공유한다.
        assert client.get("/v1/board").status_code == 200


def convened_room(client, name, prefix, agent_id):
    """방 하나를 만들고 lead를 세워 보드에 붙인다."""
    project = client.post("/v1/projects", json={"name": name}).json()
    role = client.post(
        f"/v1/workspaces/{project['id']}/roles", json={"name": f"{name}-lead"}
    ).json()
    client.put(
        f"/v1/roles/{role['id']}/assignment",
        json={"agent_id": agent_id, "assigned_by": "pm", "send_onboarding": False},
    )
    client.put(f"/v1/roles/{role['id']}/lead", json={"is_lead": True})
    client.put(
        f"/v1/projects/{project['id']}/board-link", json={"hq_id": "hq"}
    )
    return project["id"]


def test_hq_reaches_every_convened_lead_without_picking_anyone(tmp_path):
    """HQ에는 고를 역할이 없다. 지정하지 않는 것이 곧 전원이다.

    예전에는 받을 사람을 하나는 골라야 보낼 수 있었고, HQ에는 고를 것이
    하나도 없어서 입력창이 영영 잠겨 있었다.
    """
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        enroll(client, "pm", kind="human")
        enroll(client, "lead-a")
        enroll(client, "lead-b")
        enroll(client, "not-a-lead")
        convened_room(client, "archivia", "ARCH", "lead-a")
        convened_room(client, "mei", "MEI", "lead-b")

        client.post(
            "/v1/messages",
            json={
                "workspace_id": "hq", "sender_id": "pm",
                "recipient_ids": [], "body": "로드맵",
            },
        )

        def inbox(agent_id):
            return client.get(
                "/v1/messages", params={"recipient": agent_id, "after": 0}
            ).json()

        assert [m["body"] for m in inbox("lead-a")] == ["로드맵"]
        assert [m["body"] for m in inbox("lead-b")] == ["로드맵"]
        # lead가 아닌 에이전트에게는 안 간다. 전원이란 소집된 방의 lead들이다.
        assert inbox("not-a-lead") == []


def test_a_lead_can_read_hq_even_though_it_holds_no_role_there(tmp_path):
    """소집은 방을 붙이는 것이지 HQ에 역할을 만드는 것이 아니다.

    역할 보유로만 참가를 보면 모든 에이전트가 HQ에서 403을 받는다.
    """
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        enroll(client, "pm", kind="human")
        enroll(client, "lead-a")
        enroll(client, "stranger")
        convened_room(client, "archivia", "ARCH", "lead-a")
        client.post(
            "/v1/messages",
            json={
                "workspace_id": "hq", "sender_id": "pm",
                "recipient_ids": [], "body": "로드맵",
            },
        )

        def read(caller):
            return client.get("/v1/workspaces/hq/timeline", params={"caller": caller})

        assert [m["body"] for m in read("lead-a").json()] == ["로드맵"]
        assert read("pm").status_code == 200
        # 소집되지 않은 에이전트는 여전히 막힌다. 경계는 그대로 있다.
        assert read("stranger").status_code == 403
