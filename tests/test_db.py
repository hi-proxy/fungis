from fungis_server.db import FungisDB


def setup_db(tmp_path):
    db = FungisDB(tmp_path / "fungis.db")
    pm = db.create_principal(kind="human", display_name="pm")
    agent = db.create_principal(kind="agent", display_name="agent1")
    return db, pm, agent


def test_incremental_messages_and_ack(tmp_path):
    db, pm, agent = setup_db(tmp_path)
    first, _ = db.send_message(
        workspace_id="poc",
        sender_id=pm["id"],
        recipient_ids=[agent["id"]],
        body="first",
    )
    second, _ = db.send_message(
        workspace_id="poc",
        sender_id=pm["id"],
        recipient_ids=[agent["id"]],
        body="second",
    )

    assert [message["body"] for message in db.messages_after(
        recipient_id=agent["id"], after=first["seq"]
    )] == ["second"]
    assert db.inbox_state(agent["id"]) == {
        "received_seq": 0,
        "processed_seq": 0,
        "pending_count": 2,
    }
    assert db.ack(
        recipient_id=agent["id"], through_seq=second["seq"], processed=False
    )["received_seq"] == second["seq"]
    state = db.ack(
        recipient_id=agent["id"], through_seq=second["seq"], processed=True
    )
    assert state == {
        "received_seq": second["seq"],
        "processed_seq": second["seq"],
        "pending_count": 0,
    }


def test_duplicate_recipient_is_idempotent(tmp_path):
    db, pm, agent = setup_db(tmp_path)
    message, events = db.send_message(
        workspace_id="poc",
        sender_id=pm["id"],
        recipient_ids=[agent["id"], agent["id"]],
        body="once",
    )
    assert message["recipient_ids"] == [agent["id"]]
    assert len(events) == 1
    assert db.inbox_state(agent["id"])["pending_count"] == 1


def test_events_replay_after_cursor(tmp_path):
    db, pm, agent = setup_db(tmp_path)
    _, first_events = db.send_message(
        workspace_id="poc",
        sender_id=pm["id"],
        recipient_ids=[agent["id"]],
        body="first",
    )
    _, second_events = db.send_message(
        workspace_id="poc",
        sender_id=pm["id"],
        recipient_ids=[agent["id"]],
        body="second",
    )
    replay = db.delivery_events_after(
        recipient_id=agent["id"], after=first_events[0]["event_seq"]
    )
    assert replay == second_events


def test_bootstrap_revision_ignores_other_peoples_assignments(tmp_path):
    """리비전은 "다시 읽어야 하나"를 묻는 값이다.

    결과 전체를 해시하면 남이 들고 날 때마다 값이 달라져서, 매번 전체를
    다시 보내게 된다. 이 에이전트가 알아야 할 것만 담아야 한다.
    """
    db, pm, agent = setup_db(tmp_path)
    db.create_project(project_id="room", name="room")
    mine = db.create_role(workspace_id="room", name="mine")
    db.assign_role(role_id=mine["id"], agent_id=agent["id"], assigned_by=pm["id"])

    def revision():
        return db.project_bootstrap(
            project_id="room", agent_id=agent["id"], pm_id=pm["id"]
        )["revision"]

    theirs = db.create_role(workspace_id="room", name="zzz-theirs")
    after_new_role = revision()

    first = db.create_principal(kind="agent", display_name="peer1")
    db.assign_role(role_id=theirs["id"], agent_id=first["id"], assigned_by=pm["id"])
    assert revision() == after_new_role, "남의 배정이 리비전을 흔들면 안 된다"

    second = db.create_principal(kind="agent", display_name="peer2")
    db.assign_role(role_id=theirs["id"], agent_id=second["id"], assigned_by=pm["id"])
    assert revision() == after_new_role, "담당자 교체도 마찬가지다"

    db.create_role(workspace_id="room", name="zzz-another")
    assert revision() != after_new_role, "부를 수 있는 역할이 늘면 알아야 한다"


def board_setup(tmp_path):
    db, pm, agent = setup_db(tmp_path)
    hq = db.hq()
    for name in ("archivia", "fungis"):
        db.create_project(project_id=name, name=name)
        role = db.create_role(workspace_id=name, name=f"{name}-lead")
        db.set_role_lead(role_id=role["id"], is_lead=True)
        db.assign_role(role_id=role["id"], agent_id=agent["id"], assigned_by=pm["id"])
        db.connect_project(project_id=name, hq_id=hq["id"])
    return db, pm, agent, hq


def test_board_needs_a_lead_before_a_project_joins(tmp_path):
    db, pm, agent = setup_db(tmp_path)
    hq = db.hq()
    db.create_project(project_id="mei", name="mei")

    try:
        db.connect_project(project_id="mei", hq_id=hq["id"])
    except ValueError:
        pass
    else:
        raise AssertionError("lead 없이 붙으면 안 된다")

    role = db.create_role(workspace_id="mei", name="mei-lead")
    db.set_role_lead(role_id=role["id"], is_lead=True)
    db.assign_role(role_id=role["id"], agent_id=agent["id"], assigned_by=pm["id"])
    assert db.connect_project(project_id="mei", hq_id=hq["id"])["parent_id"] == hq["id"]


def test_board_reads_waiting_from_the_links_not_from_a_stored_value(tmp_path):
    db, pm, agent, _ = board_setup(tmp_path)
    first = db.create_board_node(
        project_id="archivia", title="선행작업", created_by=pm["id"], status="active"
    )
    second = db.create_board_node(
        project_id="fungis", title="2단계", created_by=agent["id"]
    )
    db.link_board_nodes(
        node_id=second["id"], waits_for=first["id"], created_by=agent["id"]
    )

    def state_of(node_id):
        for track in db.board():
            for node in track["nodes"]:
                if node["id"] == node_id:
                    return node["state"]
        raise AssertionError("노드가 보드에 없다")

    assert state_of(second["id"]) == "waiting", "선행이 안 끝났으면 대기다"
    db.update_board_node(first["id"], status="done")
    assert state_of(second["id"]) == "todo", "선행이 끝나면 대기가 풀린다"


def test_board_refuses_links_that_would_loop(tmp_path):
    db, pm, _, _ = board_setup(tmp_path)
    a = db.create_board_node(project_id="archivia", title="a", created_by=pm["id"])
    b = db.create_board_node(project_id="fungis", title="b", created_by=pm["id"])
    c = db.create_board_node(project_id="fungis", title="c", created_by=pm["id"])
    db.link_board_nodes(node_id=b["id"], waits_for=a["id"], created_by=pm["id"])
    db.link_board_nodes(node_id=c["id"], waits_for=b["id"], created_by=pm["id"])

    for node_id, waits_for, why in (
        (a["id"], c["id"], "멀리 도는 순환"),
        (a["id"], a["id"], "자기 자신"),
    ):
        try:
            db.link_board_nodes(
                node_id=node_id, waits_for=waits_for, created_by=pm["id"]
            )
        except ValueError:
            continue
        raise AssertionError(f"{why}을 받으면 안 된다")


def test_disconnecting_a_project_keeps_its_nodes(tmp_path):
    db, pm, _, _ = board_setup(tmp_path)
    db.create_board_node(project_id="archivia", title="남아야 한다", created_by=pm["id"])
    assert db.disconnect_project(project_id="archivia") is True
    assert [track["project_id"] for track in db.board()] == ["fungis"]
    # 다시 붙이면 그대로 있다. 뗐다고 지우면 기다리던 쪽이 근거를 잃는다.
    hq = db.hq()
    db.connect_project(project_id="archivia", hq_id=hq["id"])
    nodes = [node["title"] for track in db.board() for node in track["nodes"]]
    assert nodes == ["남아야 한다"]
