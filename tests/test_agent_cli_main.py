"""`main()` 의 명령 분기를 짚는다.

렌더 함수와 헬퍼는 테스트가 있었지만 **분기 안에서 무엇을 조립해 부르는지는
아무것도 안 지키고 있었다.** 12 개 분기가 전부 그랬다. 여기서 고정하는 것은
'무엇을 불렀나' 다 — 출력 모양은 각 render 테스트가 따로 본다.
"""

import json

import pytest

from fungis_node import agent_cli
from fungis_node.registry import LocalRegistry


class FakeClient:
    """PMClient 자리. 부른 것을 그대로 적어 둔다."""

    def __init__(self, *args, **kwargs):
        FakeClient.calls.append(kwargs)
        self.workspace_id = kwargs.get("workspace_id", "room-1")
        self.pm_id = "pm-local"

    calls: list = []
    sent: list = []
    asked_as: list = []

    def roles(self, workspace_id=None):
        return [{"id": "r1", "name": "lead", "agent_id": "agent-2"}]

    def overview(self, caller_id):
        FakeClient.asked_as.append(caller_id)
        return [{"name": "fungis", "quiet_days": 0, "todo": 1,
                 "active": 0, "done": 2, "unanswered": 0}]

    def send_as(self, local_name, reference, body, **kwargs):
        return self.send_message(body=body, **kwargs)

    def send_message(self, **kwargs):
        FakeClient.sent.append(kwargs)
        return {"seq": 7, "project_seq": 3, "sender_id": "agent-1",
                "recipient_ids": kwargs.get("recipient_ids", []),
                "roles": [], "body": kwargs.get("body", ""), "kind": "message",
                "reply_level": "r1", "in_reply_to": None, "track": None, "tags": [],
                "workspace_id": "room-1"}

    def projects(self):
        return [{"id": "room-1", "name": "fungis", "ticket_prefix": "FUNG"}]

    def hq(self):
        return None

    def members(self, *args, **kwargs):
        return {"roles": [], "people": []}

    def targets(self):
        return []


@pytest.fixture
def cli(tmp_path, monkeypatch, capsys):
    registry = LocalRegistry(tmp_path / "node.db")
    registry.connection.execute(
        "INSERT INTO bindings(local_name, principal_id, provider, agent_session_id,"
        " surface_id, lifecycle, data_json) VALUES"
        " ('me','agent-1','claude','session-1','surface-1','idle','{}')"
    )
    registry.connection.commit()
    registry.set_state("active_project:agent-1", "room-1")

    FakeClient.calls, FakeClient.sent, FakeClient.asked_as = [], [], []
    monkeypatch.setattr(agent_cli, "load_config", lambda *a, **k: {
        "server": "http://fungis.test", "registry": str(tmp_path / "node.db")})
    monkeypatch.setattr(agent_cli, "open_terminal_adapter", lambda: object())
    monkeypatch.setattr(agent_cli, "current_binding", lambda *a: {
        "principal_id": "agent-1", "surface_id": "surface-1", "local_name": "me"})
    monkeypatch.setattr(agent_cli, "PMClient", FakeClient)
    yield capsys
    registry.close()


def test_overview_asks_as_itself(cli):
    """전사 시야는 caller 를 실어 물어야 한다. 안 실으면 서버가 403 을 준다."""
    agent_cli.main(["overview"])
    assert FakeClient.asked_as == ["agent-1"]
    assert "fungis" in cli.readouterr().out


def test_reply_addresses_the_room_and_reports_what_was_sent(cli):
    """수신자를 안 적으면 그 방 사람에게 간다. 오늘 이 자리를 두 번 고쳤다."""
    agent_cli.main(["reply", "확인했다"])
    assert FakeClient.sent and FakeClient.sent[0]["body"] == "확인했다"
    # 화면에 보이는 번호는 방 번호다. 전역 seq 를 보여 주면 답글을 걸 수 없다.
    assert json.loads(cli.readouterr().out)["stored"]["seq"] == 3


def test_wake_books_the_next_step(cli, tmp_path):
    """예약은 상주 에이전트가 사는 사슬이다."""
    agent_cli.main(["wake", "--in", "20m"])
    registry = LocalRegistry(tmp_path / "node.db")
    try:
        assert registry.wake_schedule("agent-1") is not None
    finally:
        registry.close()


def test_a_bad_delay_is_said_in_one_line(cli):
    """잘못 친 값에 역추적을 보여 주면 읽는 쪽이 제 실수 대신 파이썬 속을 읽는다."""
    with pytest.raises(SystemExit) as exit_info:
        agent_cli.main(["wake", "--in", "나중에"])
    assert "20m" in str(exit_info.value)
