from fastapi.testclient import TestClient

from fungis_server.app import create_app


def join(client, workspace_id, agent_id, *, assigned_by="pm", lead=False):
    """그 방에 역할 하나를 만들어 앉힌다.

    방에 글을 남기려면 그 방 소속이어야 한다. 소속은 별도 명부가 아니라
    활성 배정에서 파생되므로, 테스트도 실제와 같은 방법으로 들어간다.
    """
    role = client.post(
        f"/v1/workspaces/{workspace_id}/roles", json={"name": f"{agent_id}-role"}
    ).json()
    if lead:
        client.put(f"/v1/roles/{role['id']}/lead", json={"is_lead": True})
    client.put(
        f"/v1/roles/{role['id']}/assignment",
        json={
            "agent_id": agent_id, "assigned_by": assigned_by,
            "send_onboarding": False,
        },
    )
    return role


def test_message_flow(tmp_path):
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        pm = client.post(
            "/v1/principals", json={"kind": "human", "display_name": "pm"}
        ).json()
        agent = client.post(
            "/v1/principals", json={"kind": "agent", "display_name": "agent1"}
        ).json()
        with client.websocket_connect(f"/v1/events/{agent['id']}?after=0") as websocket:
            message = client.post(
                "/v1/messages",
                json={
                    "workspace_id": "poc",
                    "sender_id": pm["id"],
                    "recipient_ids": [agent["id"]],
                    "body": "hello",
                },
            ).json()
            event = websocket.receive_json()
            assert event["kind"] == "inbox_available"
            assert event["through_seq"] == message["seq"]

        messages = client.get(
            "/v1/messages", params={"recipient": agent["id"], "after": 0}
        ).json()
        assert [item["body"] for item in messages] == ["hello"]
        state = client.post(
            "/v1/inbox/ack-processed",
            json={"recipient_id": agent["id"], "through_seq": message["seq"]},
        ).json()
        assert state["pending_count"] == 0


def test_project_message_bookmarks_are_ordered_and_deletable(tmp_path):
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        for principal_id, kind in (("pm", "human"), ("agent", "agent")):
            client.put(
                f"/v1/principals/{principal_id}",
                json={"id": principal_id, "kind": kind, "display_name": principal_id},
            )
        messages = [
            client.post(
                "/v1/messages",
                json={
                    "workspace_id": "local", "sender_id": "pm",
                    "recipient_ids": ["agent"], "body": body,
                },
            ).json()
            for body in ("wave one", "wave two")
        ]
        later = client.post(
            f"/v1/workspaces/local/messages/{messages[1]['seq']}/bookmarks",
            json={"label": "디자인 웨이브2 완료", "created_by": "pm"},
        ).json()
        client.post(
            f"/v1/workspaces/local/messages/{messages[0]['seq']}/bookmarks",
            json={"label": "디자인 웨이브1 완료", "created_by": "pm"},
        )

        bookmarks = client.get("/v1/workspaces/local/bookmarks").json()
        assert [item["message_seq"] for item in bookmarks] == [
            messages[0]["seq"], messages[1]["seq"]
        ]
        assert bookmarks[0]["created_by_name"] == "pm"
        assert client.delete(
            f"/v1/workspaces/local/bookmarks/{later['id']}"
        ).status_code == 204
        assert [item["label"] for item in client.get(
            "/v1/workspaces/local/bookmarks"
        ).json()] == ["디자인 웨이브1 완료"]


def test_timeline_pins_mark_message_gaps_separately_from_bookmarks(tmp_path):
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        for principal_id, kind in (("pm", "human"), ("agent", "agent")):
            client.put(
                f"/v1/principals/{principal_id}",
                json={"id": principal_id, "kind": kind, "display_name": principal_id},
            )
        messages = [
            client.post(
                "/v1/messages",
                json={
                    "workspace_id": "local", "sender_id": "pm",
                    "recipient_ids": ["agent"], "body": f"message-{index}",
                },
            ).json()
            for index in range(3)
        ]
        second = client.post(
            f"/v1/workspaces/local/messages/{messages[1]['seq']}/timeline-pins",
            json={"label": "디자인 웨이브2 완료", "created_by": "pm"},
        ).json()
        client.post(
            f"/v1/workspaces/local/messages/{messages[0]['seq']}/timeline-pins",
            json={"label": "디자인 웨이브1 완료", "created_by": "pm"},
        )

        pins = client.get("/v1/workspaces/local/timeline-pins").json()
        assert [pin["after_message_seq"] for pin in pins] == [
            messages[0]["seq"], messages[1]["seq"]
        ]
        assert client.post(
            f"/v1/workspaces/local/messages/{messages[1]['seq']}/timeline-pins",
            json={"label": "duplicate gap", "created_by": "pm"},
        ).status_code == 409
        assert client.delete(
            f"/v1/workspaces/local/timeline-pins/{second['id']}"
        ).status_code == 204
        assert [pin["label"] for pin in client.get(
            "/v1/workspaces/local/timeline-pins"
        ).json()] == ["디자인 웨이브1 완료"]


def test_idempotent_principal_sync_and_pm_timeline_status(tmp_path):
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        for _ in range(2):
            assert client.put(
                "/v1/principals/pm-local",
                json={"id": "pm-local", "kind": "human", "display_name": "PM"},
            ).status_code == 200
            assert client.put(
                "/v1/principals/agent-1",
                json={"id": "agent-1", "kind": "agent", "display_name": "agent-1"},
            ).status_code == 200
        message = client.post(
            "/v1/messages",
            json={
                "workspace_id": "local",
                "sender_id": "pm-local",
                "recipient_ids": ["agent-1"],
                "body": "check status",
            },
        ).json()
        before = client.get("/v1/timeline/pm-local").json()
        assert before[0]["body"] == "check status"
        assert before[0]["recipients"][0]["processed_at"] is None
        client.post(
            "/v1/inbox/ack-processed",
            json={"recipient_id": "agent-1", "through_seq": message["seq"]},
        )
        after = client.get("/v1/timeline/pm-local").json()
        assert after[0]["recipients"][0]["processed_at"] is not None


def test_pm_attention_is_prioritized_and_resolved_by_linked_reply(tmp_path):
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        for principal_id, kind in (("pm", "human"), ("agent", "agent")):
            client.put(
                f"/v1/principals/{principal_id}",
                json={"id": principal_id, "kind": kind, "display_name": principal_id},
            )
        join(client, "local", "agent")
        low = client.post(
            "/v1/messages",
            json={
                "workspace_id": "local",
                "sender_id": "agent",
                "recipient_ids": ["pm"],
                "body": "review later",
                "kind": "pm_request",
                "reply_level": "r2",
            },
        ).json()
        urgent = client.post(
            "/v1/messages",
            json={
                "workspace_id": "local",
                "sender_id": "agent",
                "recipient_ids": ["pm"],
                "body": "approve destructive step",
                "kind": "pm_request",
                "reply_level": "r3",
            },
        ).json()
        attention = client.get("/v1/attention/pm").json()
        assert [item["seq"] for item in attention] == [urgent["seq"], low["seq"]]
        client.post(
            "/v1/messages",
            json={
                "workspace_id": "local",
                "sender_id": "pm",
                "recipient_ids": ["agent"],
                "body": "approved",
                "in_reply_to": urgent["seq"],
            },
        )
        assert [item["seq"] for item in client.get("/v1/attention/pm").json()] == [
            low["seq"]
        ]


def test_message_context_is_indexed_and_inherited_by_replies(tmp_path):
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        for principal_id, kind in (("pm", "human"), ("agent", "agent")):
            client.put(
                f"/v1/principals/{principal_id}",
                json={"id": principal_id, "kind": kind, "display_name": principal_id},
            )
        join(client, "local", "agent")
        parent = client.post(
            "/v1/messages",
            json={
                "workspace_id": "local",
                "sender_id": "pm",
                "recipient_ids": ["agent"],
                "body": "work on this branch",
                "track": "branch/feature-a",
                "tags": ["ticket/ARC-42", "review", "review"],
            },
        ).json()
        assert parent["track"] == "branch/feature-a"
        assert parent["tags"] == ["ticket/ARC-42", "review"]

        inherited = client.post(
            "/v1/messages",
            json={
                "workspace_id": "local",
                "sender_id": "agent",
                "recipient_ids": ["pm"],
                "body": "done",
                "in_reply_to": parent["seq"],
            },
        ).json()
        assert inherited["track"] == parent["track"]
        assert inherited["tags"] == parent["tags"]

        detached = client.post(
            "/v1/messages",
            json={
                "workspace_id": "local",
                "sender_id": "agent",
                "recipient_ids": ["pm"],
                "body": "separate note",
                "in_reply_to": parent["seq"],
                "inherit_context": False,
            },
        ).json()
        assert detached["track"] is None
        assert detached["tags"] == []

        timeline = client.get("/v1/workspaces/local/timeline", params={"caller": "pm"}).json()
        assert timeline[0]["tags"] == ["ticket/ARC-42", "review"]
        assert timeline[1]["track"] == "branch/feature-a"


def test_shared_values_are_versioned_selectable_and_deletable(tmp_path):
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        client.put(
            "/v1/principals/pm",
            json={"id": "pm", "kind": "human", "display_name": "pm"},
        )
        first = client.put(
            "/v1/shared/local/repository",
            params={"updated_by": "pm"},
            json={"value": "https://example.test/repo"},
        ).json()
        assert first["version"] == 1
        second = client.put(
            "/v1/shared/local/repository",
            params={"updated_by": "pm"},
            json={"value": "ssh://git@example.test/repo"},
        ).json()
        assert second["version"] == 2
        client.put(
            "/v1/shared/local/review-rule",
            params={"updated_by": "pm"},
            json={"value": "r3 before deletion"},
        )
        selected = client.get(
            "/v1/shared/local", params=[("keys", "review-rule")]
        ).json()
        assert [(item["key"], item["value"]) for item in selected] == [
            ("review-rule", "r3 before deletion")
        ]
        assert client.delete("/v1/shared/local/repository").status_code == 204
        assert [item["key"] for item in client.get("/v1/shared/local").json()] == [
            "review-rule"
        ]


def test_work_start_report_done_tracks_elapsed_without_fake_tokens(tmp_path):
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        client.put(
            "/v1/principals/agent",
            json={"id": "agent", "kind": "agent", "display_name": "agent"},
        )
        started = client.post(
            "/v1/work",
            json={"workspace_id": "local", "agent_id": "agent", "title": "build"},
        ).json()
        assert started["status"] == "active"
        assert started["token_usage"] is None
        duplicate = client.post(
            "/v1/work",
            json={"workspace_id": "local", "agent_id": "agent", "title": "other"},
        )
        assert duplicate.status_code == 409
        reported = client.post(
            "/v1/work/agent/report", json={"report": "halfway"}
        ).json()
        assert reported["last_report"] == "halfway"
        done = client.post(
            "/v1/work/agent/done", json={"report": "verified"}
        ).json()
        assert done["status"] == "done"
        assert done["ended_at"] is not None
        assert done["elapsed_seconds"] >= 0
        listed = client.get("/v1/work/local").json()
        assert [(item["title"], item["last_report"]) for item in listed] == [
            ("build", "verified")
        ]


def test_two_pms_share_workspace_timeline_and_either_can_resolve_attention(tmp_path):
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        for principal_id, kind in (
            ("pm-a", "human"), ("pm-b", "human"), ("agent-a", "agent")
        ):
            client.put(
                f"/v1/principals/{principal_id}",
                json={"id": principal_id, "kind": kind, "display_name": principal_id},
            )
        client.post("/v1/projects", json={"id": "shared-room", "name": "shared room"})
        join(client, "shared-room", "agent-a", assigned_by="pm-a")
        client.post(
            "/v1/messages",
            json={
                "workspace_id": "shared-room",
                "sender_id": "pm-a",
                "recipient_ids": ["agent-a"],
                "body": "from first PM",
            },
        )
        request = client.post(
            "/v1/messages",
            json={
                "workspace_id": "shared-room",
                "sender_id": "agent-a",
                "recipient_ids": ["pm-a"],
                "body": "need approval",
                "kind": "pm_request",
                "reply_level": "r3",
            },
        ).json()
        timeline = client.get(
            "/v1/workspaces/shared-room/timeline", params={"caller": "pm-a"}
        ).json()
        assert [item["body"] for item in timeline] == [
            "from first PM", "need approval"
        ]
        assert client.get(
            "/v1/workspaces/shared-room/attention"
        ).json()[0]["seq"] == request["seq"]
        client.post(
            "/v1/messages",
            json={
                "workspace_id": "shared-room",
                "sender_id": "pm-b",
                "recipient_ids": ["agent-a"],
                "body": "approved by second PM",
                "in_reply_to": request["seq"],
            },
        )
        assert client.get(
            "/v1/workspaces/shared-room/attention"
        ).json() == []


def test_multiple_recipients_and_pm_reference_are_distinct(tmp_path):
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        for principal_id, kind in (
            ("pm", "human"), ("agent-a", "agent"), ("agent-b", "agent")
        ):
            client.put(
                f"/v1/principals/{principal_id}",
                json={"id": principal_id, "kind": kind, "display_name": principal_id},
            )
        join(client, "local", "agent-a")
        message = client.post(
            "/v1/messages",
            json={
                "workspace_id": "local",
                "sender_id": "agent-a",
                "recipient_ids": ["agent-a", "agent-b", "agent-b"],
                "reference_ids": ["pm", "agent-b"],
                "body": "coordinate",
            },
        ).json()
        assert message["recipient_ids"] == ["agent-a", "agent-b"]
        assert message["reference_ids"] == ["pm"]
        timeline = client.get("/v1/workspaces/local/timeline", params={"caller": "pm"}).json()
        assert [item["recipient_id"] for item in timeline[0]["recipients"]] == [
            "agent-a", "agent-b"
        ]
        assert timeline[0]["references"] == [
            {"principal_id": "pm", "display_name": "pm"}
        ]


def test_reference_is_delivered_but_marked_as_listen_only(tmp_path):
    """참조도 받아 봐야 맥락이 되지만, 수신자 자리에 서면 안 된다.

    배달하지 않으면 보내는 쪽이 참조 대신 수신자로 넣게 되고, 받는 쪽은
    그것을 지시로 읽어 서로 답장을 물고 늘어진다.
    """
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        for principal_id, kind in (
            ("pm", "human"), ("manager", "agent"), ("builder", "agent")
        ):
            client.put(
                f"/v1/principals/{principal_id}",
                json={"id": principal_id, "kind": kind, "display_name": principal_id},
            )
        join(client, "local", "manager")
        client.post(
            "/v1/messages",
            json={
                "workspace_id": "local",
                "sender_id": "manager",
                "recipient_ids": ["pm"],
                "reference_ids": ["builder"],
                "body": "보고",
            },
        )
        delivered = client.get(
            "/v1/messages", params={"recipient": "builder", "after": 0}
        ).json()
        assert [item["body"] for item in delivered] == ["보고"]
        assert delivered[0]["is_reference"] == 1

        to_pm = client.get(
            "/v1/messages", params={"recipient": "pm", "after": 0}
        ).json()
        assert to_pm[0]["is_reference"] == 0

        timeline = client.get("/v1/workspaces/local/timeline", params={"caller": "pm"}).json()
        assert [item["recipient_id"] for item in timeline[0]["recipients"]] == ["pm"]


def test_agent_chain_counts_only_since_the_last_human_message(tmp_path):
    """길어진 것을 알려만 준다. 막지는 않는다."""
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        for principal_id, kind in (
            ("pm", "human"), ("a", "agent"), ("b", "agent")
        ):
            client.put(
                f"/v1/principals/{principal_id}",
                json={"id": principal_id, "kind": kind, "display_name": principal_id},
            )

        for agent_id in ("a", "b"):
            join(client, "local", agent_id)

        def post(sender: str, recipients: list[str], body: str) -> None:
            client.post(
                "/v1/messages",
                json={
                    "workspace_id": "local", "sender_id": sender,
                    "recipient_ids": recipients, "body": body,
                },
            )

        post("pm", ["a", "b"], "둘 다 본다")
        post("a", ["pm", "b"], "1")
        post("b", ["pm", "a"], "2")
        post("a", ["pm", "b"], "3")
        chains = {
            item["body"]: item["agent_chain"]
            for item in client.get(
                "/v1/messages", params={"recipient": "b", "after": 0}
            ).json()
        }
        assert chains == {"둘 다 본다": 0, "1": 1, "3": 3}

        # 사람이 다시 말하면 0부터 센다.
        post("pm", ["a", "b"], "정리하자")
        post("b", ["pm", "a"], "4")
        after = {
            item["body"]: item["agent_chain"]
            for item in client.get(
                "/v1/messages", params={"recipient": "a", "after": 0}
            ).json()
        }
        assert after["정리하자"] == 0
        assert after["4"] == 1


def test_role_address_queues_until_assignment_and_preserves_history(tmp_path):
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        for principal_id, kind in (
            ("pm", "human"), ("agent-a", "agent"), ("agent-b", "agent")
        ):
            client.put(
                f"/v1/principals/{principal_id}",
                json={"id": principal_id, "kind": kind, "display_name": principal_id},
            )
        role = client.post(
            "/v1/workspaces/local/roles",
            json={"name": "front1", "onboarding_prompt": "You own front1."},
        ).json()
        queued = client.post(
            "/v1/messages",
            json={
                "workspace_id": "local", "sender_id": "pm",
                "role_ids": [role["id"]], "body": "queued task",
            },
        ).json()
        assert queued["recipient_ids"] == []
        assert client.get("/v1/messages", params={"recipient": "agent-a", "after": 0}).json() == []
        assert client.delete(f"/v1/roles/{role['id']}").status_code == 409

        assigned = client.put(
            f"/v1/roles/{role['id']}/assignment",
            json={
                "agent_id": "agent-a", "assigned_by": "pm",
                "send_onboarding": True,
            },
        ).json()
        assert assigned["agent_id"] == "agent-a"
        delivered = client.get(
            "/v1/messages", params={"recipient": "agent-a", "after": 0}
        ).json()
        assert [item["body"] for item in delivered][0] == "queued task"
        onboarding = delivered[1]["body"]
        assert "fungis init --project local" in onboarding
        assert onboarding.endswith("You own front1.")
        assert delivered[0]["role_recipients"][0]["name"] == "front1"

        client.delete(f"/v1/roles/{role['id']}/assignment")
        client.put(
            f"/v1/roles/{role['id']}/assignment",
            json={"agent_id": "agent-b", "assigned_by": "pm"},
        )
        history = client.get(f"/v1/roles/{role['id']}/assignments").json()
        assert [item["agent_id"] for item in history] == ["agent-b", "agent-a"]
        assert history[0]["ended_at"] is None
        assert history[1]["ended_at"] is not None
        assert client.get(
            "/v1/messages", params={"recipient": "agent-b", "after": 0}
        ).json() == []
        assert client.delete(f"/v1/roles/{role['id']}").status_code == 204
        recreated = client.post(
            "/v1/workspaces/local/roles", json={"name": "front1"}
        )
        assert recreated.status_code == 201


def test_role_avatar_is_stored_separately_from_role_json(tmp_path):
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        role = client.post(
            "/v1/workspaces/local/roles", json={"name": "design-lead"}
        ).json()
        assert role["has_avatar"] is False
        image = b"\x89PNG\r\n\x1a\nminimal-test-image"
        uploaded = client.put(
            f"/v1/roles/{role['id']}/avatar",
            content=image,
            headers={"content-type": "image/png"},
        )
        assert uploaded.status_code == 200
        assert uploaded.json()["has_avatar"] is True
        assert "avatar" not in uploaded.json()
        fetched = client.get(f"/v1/roles/{role['id']}/avatar")
        assert fetched.content == image
        assert fetched.headers["content-type"] == "image/png"
        client.delete(f"/v1/roles/{role['id']}/avatar")
        assert client.get(f"/v1/roles/{role['id']}/avatar").status_code == 404


def test_projects_allow_same_agent_one_role_per_project(tmp_path):
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        for principal_id, kind in (("pm", "human"), ("agent", "agent")):
            client.put(
                f"/v1/principals/{principal_id}",
                json={"id": principal_id, "kind": kind, "display_name": principal_id},
            )
        second = client.post("/v1/projects", json={"name": "Second"}).json()
        local_role = client.post(
            "/v1/workspaces/local/roles", json={"name": "devlead"}
        ).json()
        second_role = client.post(
            f"/v1/workspaces/{second['id']}/roles", json={"name": "reviewer"}
        ).json()
        for role in (local_role, second_role):
            response = client.put(
                f"/v1/roles/{role['id']}/assignment",
                json={"agent_id": "agent", "assigned_by": "pm"},
            )
            assert response.status_code == 200
        memberships = client.get("/v1/agent-role-memberships").json()
        assert {(item["project_name"], item["role_name"]) for item in memberships} == {
            ("Local", "devlead"), ("Second", "reviewer")
        }


def test_pm_profile_and_avatar_are_global(tmp_path):
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        client.put(
            "/v1/principals/pm",
            json={"id": "pm", "kind": "human", "display_name": "PM"},
        )
        updated = client.patch(
            "/v1/pm-profiles/pm", json={"display_name": "Product Lead"}
        ).json()
        assert updated["display_name"] == "Product Lead"
        client.put(
            "/v1/principals/pm",
            json={"id": "pm", "kind": "human", "display_name": "PM"},
        )
        assert client.get("/v1/pm-profiles/pm").json()["display_name"] == "Product Lead"
        image = b"\x89PNG\r\n\x1a\npm-profile"
        assert client.put(
            "/v1/pm-profiles/pm/avatar", content=image,
            headers={"content-type": "image/png"},
        ).status_code == 200
        assert client.get("/v1/pm-profiles/pm").json()["has_avatar"] is True
        assert client.get("/v1/pm-profiles/pm/avatar").content == image


def test_project_bootstrap_returns_agent_specific_role_directory(tmp_path):
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        for principal_id, kind, name in (
            ("pm", "human", "Product Lead"),
            ("agent-a", "agent", "Alice Session"),
            ("agent-b", "agent", "Bob Session"),
        ):
            client.put(
                f"/v1/principals/{principal_id}",
                json={"id": principal_id, "kind": kind, "display_name": name},
            )
        lead = client.post(
            "/v1/workspaces/local/roles", json={"name": "dev-lead"}
        ).json()
        client.post("/v1/workspaces/local/roles", json={"name": "reviewer"})
        client.put(
            f"/v1/roles/{lead['id']}/assignment",
            json={"agent_id": "agent-a", "assigned_by": "pm"},
        )
        bootstrap = client.get(
            "/v1/projects/local/bootstrap",
            params={"agent_id": "agent-a", "pm_id": "pm"},
        ).json()
        assert bootstrap["project"]["name"] == "Local"
        assert bootstrap["own_role"]["name"] == "dev-lead"
        assert bootstrap["roles"][0]["self"] is True
        assert bootstrap["roles"][1]["assigned"] is False
        assert bootstrap["usage"]["reply_pm"] == 'fungis reply "..."'
        assert bootstrap["usage"]["history"] == "fungis history 20"
        assert "fungis history 20" in bootstrap["usage"]["recovery"]
        assert len(bootstrap["revision"]) == 12


def test_project_history_supports_compaction_restore_after_sequence(tmp_path):
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        for principal_id, kind in (("pm", "human"), ("agent", "agent")):
            client.put(
                f"/v1/principals/{principal_id}",
                json={"id": principal_id, "kind": kind, "display_name": principal_id},
            )
        join(client, "local", "agent")
        sequences = []
        for body, sender, recipient in (
            ("first", "pm", "agent"),
            ("second", "agent", "pm"),
            ("third", "pm", "agent"),
        ):
            sequences.append(client.post(
                "/v1/messages",
                json={
                    "workspace_id": "local", "sender_id": sender,
                    "recipient_ids": [recipient], "body": body,
                },
            ).json()["seq"])
        latest = client.get(
            "/v1/workspaces/local/timeline", params={"caller": "pm", "limit": 2}
        ).json()
        assert [item["body"] for item in latest] == ["second", "third"]
        after = client.get(
            "/v1/workspaces/local/timeline",
            params={"caller": "pm", "limit": 20, "after": sequences[0]},
        ).json()
        assert [item["body"] for item in after] == ["second", "third"]
        before = client.get(
            "/v1/workspaces/local/timeline",
            params={"caller": "pm", "limit": 2, "before": sequences[-1]},
        ).json()
        assert [item["body"] for item in before] == ["first", "second"]
        assert client.get(
            "/v1/workspaces/local/timeline",
            params={"caller": "pm", "after": sequences[0], "before": sequences[-1]},
        ).status_code == 422
        assert client.post(
            "/v1/messages",
            json={
                "workspace_id": "local", "sender_id": "pm",
                "recipient_ids": ["agent"], "body": "x" * 20001,
            },
        ).status_code == 422


def test_assignment_always_carries_the_project_id(tmp_path):
    """역할 설명이 비어도 배정은 말을 걸어야 한다.

    안 보내면 에이전트는 자기가 배정된 줄도 모르고, PM은 앱에서 보냈다고
    믿는다. 프로젝트 ID가 없으면 배정된 건 알아도 자기 방 번호를 몰라
    fungis init을 못 하고 PM에게 되묻는다. 8/16 실측에서 그대로 겪었다.
    """
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        for principal_id, kind in (("pm", "human"), ("agent-a", "agent")):
            client.put(
                f"/v1/principals/{principal_id}",
                json={"id": principal_id, "kind": kind, "display_name": principal_id},
            )
        role = client.post(
            "/v1/workspaces/local/roles", json={"name": "tester", "onboarding_prompt": ""}
        ).json()
        assigned = client.put(
            f"/v1/roles/{role['id']}/assignment",
            json={"agent_id": "agent-a", "assigned_by": "pm", "send_onboarding": True},
        ).json()
        assert assigned["onboarding_sent"] is True

        delivered = client.get(
            "/v1/messages", params={"recipient": "agent-a", "after": 0}
        ).json()
        assert len(delivered) == 1
        assert "fungis init --project local" in delivered[0]["body"]


def test_new_session_in_the_same_terminal_replaces_the_old_binding(tmp_path):
    """창 하나에 에이전트 하나. 비켜 주지 않으면 sync 전체가 409로 막힌다."""
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        client.put(
            "/v1/nodes/node-1", json={"id": "node-1", "display_name": "Node"}
        )
        for agent_id in ("agent-old", "agent-new"):
            client.put(
                f"/v1/principals/{agent_id}",
                json={"id": agent_id, "kind": "agent", "display_name": agent_id},
            )

        def bind(agent_id: str, session: str):
            return client.put(
                f"/v1/bindings/{agent_id}",
                json={
                    "agent_id": agent_id, "node_id": "node-1",
                    "agent_provider": "claude", "agent_session_id": session,
                    "terminal_provider": "cmux", "terminal_session_id": "surface-1",
                    "lifecycle": "idle",
                },
            )

        assert bind("agent-old", "session-1").status_code in (200, 201)
        # 같은 창, 새 세션. 예전에는 여기서 UNIQUE 위반으로 409가 났다.
        assert bind("agent-new", "session-2").status_code in (200, 201)


def test_archiving_a_project_keeps_messages_and_ends_assignments(tmp_path):
    """방을 닫는 것과 오간 말을 없애는 것은 다른 일이다.

    배정이 남으면 에이전트가 갈 곳 없는 역할을 쥔 채로 남는다 — 치우려던 것이
    다른 모양으로 남는 셈이다.
    """
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        for principal_id, kind in (("pm", "human"), ("agent-a", "agent")):
            client.put(
                f"/v1/principals/{principal_id}",
                json={"id": principal_id, "kind": kind, "display_name": principal_id},
            )
        project = client.post("/v1/projects", json={"name": "닫을 방"}).json()
        role = client.post(
            f"/v1/workspaces/{project['id']}/roles", json={"name": "worker"}
        ).json()
        client.put(
            f"/v1/roles/{role['id']}/assignment",
            json={"agent_id": "agent-a", "assigned_by": "pm"},
        )
        client.post(
            "/v1/messages",
            json={
                "workspace_id": project["id"], "sender_id": "pm",
                "recipient_ids": ["agent-a"], "body": "남아야 한다",
            },
        )

        archived = client.delete(f"/v1/projects/{project['id']}").json()
        assert archived["archived_at"]
        assert archived["ended_assignments"] == 1

        # 목록에서 빠진다.
        assert project["id"] not in [p["id"] for p in client.get("/v1/projects").json()]
        # 메시지는 남는다.
        timeline = client.get(f"/v1/workspaces/{project['id']}/timeline", params={"caller": "pm"}).json()
        assert [item["body"] for item in timeline] == ["남아야 한다"]
        # 두 번 닫으면 404.
        assert client.delete(f"/v1/projects/{project['id']}").status_code == 404


def test_workspace_timeline_is_readable_only_by_participants(tmp_path):
    """대화는 그 방 사람만 읽는다. 명단은 아니다.

    지키려는 것이 대화라서 init(명단)은 막지 않는다. 막으면 들어가려는
    에이전트가 들어갈 수 없다.

    신원은 자기 신고라 작정하면 우회된다. 여기서 막는 것은 실수다.
    """
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        for principal_id, kind in (
            ("pm", "human"), ("inside", "agent"), ("outside", "agent")
        ):
            client.put(
                f"/v1/principals/{principal_id}",
                json={"id": principal_id, "kind": kind, "display_name": principal_id},
            )
        client.post("/v1/projects", json={"id": "hq", "name": "hq"})
        role = client.post(
            "/v1/workspaces/hq/roles", json={"name": "lead"}
        ).json()
        client.put(
            f"/v1/roles/{role['id']}/assignment",
            json={"agent_id": "inside", "assigned_by": "pm", "send_onboarding": False},
        )
        client.post(
            "/v1/messages",
            json={
                "workspace_id": "hq", "sender_id": "pm",
                "recipient_ids": ["inside"], "body": "roadmap",
            },
        )

        def read(caller):
            return client.get(
                "/v1/workspaces/hq/timeline", params={"caller": caller}
            )

        assert [m["body"] for m in read("inside").json()] == ["roadmap"]
        assert [m["body"] for m in read("pm").json()] == ["roadmap"]
        assert read("outside").status_code == 403
        assert read("nobody").status_code == 403
        # 호출자를 안 실으면 통과가 아니라 거절이다. 빠뜨림이 우회로가 되면 안 된다.
        assert client.get("/v1/workspaces/hq/timeline").status_code == 422

        # 명단은 그대로 열려 있다.
        assert client.get(
            "/v1/projects/hq/bootstrap",
            params={"agent_id": "outside", "pm_id": "pm"},
        ).status_code == 200


def test_board_tracks_nodes_and_links_over_http(tmp_path):
    """보드 한 바퀴. 트랙에 노드를 올리고 이어서 대기를 읽는다."""
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        for principal_id, kind in (("pm", "human"), ("lead", "agent")):
            client.put(
                f"/v1/principals/{principal_id}",
                json={"id": principal_id, "kind": kind, "display_name": principal_id},
            )
        # HQ는 만드는 것이 아니라 처음부터 있다.
        hq = client.get("/v1/hq").json()

        for name in ("archivia", "fungis"):
            client.post("/v1/projects", json={"id": name, "name": name})
            role = client.post(
                f"/v1/workspaces/{name}/roles", json={"name": f"{name}-lead"}
            ).json()
            # lead 없이 붙으려 하면 거절한다.
            assert client.put(
                f"/v1/projects/{name}/board-link", json={"hq_id": hq["id"]}
            ).status_code == 409
            client.put(f"/v1/roles/{role['id']}/lead", json={"is_lead": True})
            client.put(
                f"/v1/roles/{role['id']}/assignment",
                json={
                    "agent_id": "lead", "assigned_by": "pm", "send_onboarding": False,
                },
            )
            assert client.put(
                f"/v1/projects/{name}/board-link", json={"hq_id": hq["id"]}
            ).status_code == 200

        assert client.get("/v1/projects/archivia/lead").json()["agent_id"] == "lead"

        first = client.post(
            "/v1/board/nodes",
            json={
                "project_id": "archivia", "title": "선행작업",
                "created_by": "pm", "status": "active",
            },
        ).json()
        second = client.post(
            "/v1/board/nodes",
            json={"project_id": "fungis", "title": "2단계", "created_by": "lead"},
        ).json()
        assert client.post(
            "/v1/board/edges",
            json={
                "node_id": second["id"], "waits_for": first["id"],
                "created_by": "lead",
            },
        ).status_code == 201
        # 되돌려 이으면 순환이라 거절한다.
        assert client.post(
            "/v1/board/edges",
            json={
                "node_id": first["id"], "waits_for": second["id"],
                "created_by": "lead",
            },
        ).status_code == 409

        def state_of(node_id):
            for track in client.get("/v1/board").json():
                for node in track["nodes"]:
                    if node["id"] == node_id:
                        return node["state"]
            raise AssertionError("노드가 보드에 없다")

        assert state_of(second["id"]) == "waiting"
        client.patch(
            f"/v1/board/nodes/{first['id']}",
            json={"status": "done", "actor": "pm"},
        )
        assert state_of(second["id"]) == "todo"

        assert client.delete(
            f"/v1/board/nodes/{first['id']}", params={"actor": "pm"}
        ).status_code == 204
        remaining = [
            node["id"] for track in client.get("/v1/board").json()
            for node in track["nodes"]
        ]
        assert remaining == [second["id"]]


def convene(client, name, prefix_agent="lead"):
    """방 하나를 만들고 lead 를 앉힌 뒤 HQ 에 붙인다."""
    hq = client.get("/v1/hq").json()
    client.post("/v1/projects", json={"id": name, "name": name})
    role = join(client, name, prefix_agent, lead=True)
    client.put(f"/v1/projects/{name}/board-link", json={"hq_id": hq["id"]})
    return hq, role


def test_a_message_with_no_recipient_is_kept_and_read_back_as_history(tmp_path):
    """수신자 0인 글도 방에 남는다. 배달만 없다."""
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        for principal_id, kind in (("pm", "human"), ("agent", "agent")):
            client.put(
                f"/v1/principals/{principal_id}",
                json={"id": principal_id, "kind": kind, "display_name": principal_id},
            )
        join(client, "local", "agent")
        created = client.post(
            "/v1/messages",
            json={
                "workspace_id": "local", "sender_id": "agent",
                "recipient_ids": [], "body": "주소 없는 글",
            },
        )
        assert created.status_code == 201
        assert created.json()["recipient_ids"] == []
        # 아무도 깨우지 않는다.
        assert client.get("/v1/inbox/state/agent").json() == {
            "received_seq": 0, "processed_seq": 0, "pending_count": 0
        }
        assert client.get(
            "/v1/messages", params={"recipient": "agent", "after": 0}
        ).json() == []
        # 그래도 history 로 읽힌다.
        timeline = client.get(
            "/v1/workspaces/local/timeline", params={"caller": "agent"}
        ).json()
        assert [item["body"] for item in timeline] == ["주소 없는 글"]
        assert timeline[0]["recipients"] == []
        # 웹소켓을 새로 붙여도 밀린 이벤트가 없다.
        with client.websocket_connect("/v1/events/agent?after=0") as websocket:
            websocket.send_text("ping")


def test_hq_addresses_a_room_by_name_and_reaches_its_lead(tmp_path):
    """HQ 에서 수신자는 방 이름으로 지목한다. 서버가 그 방 lead 로 푼다."""
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        for principal_id, kind in (("pm", "human"), ("lead", "agent")):
            client.put(
                f"/v1/principals/{principal_id}",
                json={"id": principal_id, "kind": kind, "display_name": principal_id},
            )
        hq, _ = convene(client, "archivia")
        message = client.post(
            "/v1/messages",
            json={
                "workspace_id": hq["id"], "sender_id": "pm",
                "recipient_ids": ["ARCH"], "body": "표지 언제 끝나나",
            },
        )
        assert message.status_code == 201
        assert message.json()["recipient_ids"] == ["lead"]


def test_one_message_can_be_pulled_out_by_its_room_number(tmp_path):
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        for principal_id, kind in (
            ("pm", "human"), ("agent", "agent"), ("outsider", "agent")
        ):
            client.put(
                f"/v1/principals/{principal_id}",
                json={"id": principal_id, "kind": kind, "display_name": principal_id},
            )
        join(client, "local", "agent")
        for body in ("첫 글", "둘째 글"):
            client.post(
                "/v1/messages",
                json={
                    "workspace_id": "local", "sender_id": "pm",
                    "recipient_ids": ["agent"], "body": body,
                },
            )
        found = client.get(
            "/v1/workspaces/local/messages/2", params={"caller": "agent"}
        )
        assert found.status_code == 200
        assert found.json()["body"] == "둘째 글"
        assert found.json()["project_seq"] == 2
        # 없는 번호는 다음 행동을 함께 준다.
        missing = client.get(
            "/v1/workspaces/local/messages/99", params={"caller": "agent"}
        )
        assert missing.status_code == 404
        assert "fungis history" in missing.json()["detail"]
        # 열람 경계를 반드시 지난다.
        denied = client.get(
            "/v1/workspaces/local/messages/2", params={"caller": "outsider"}
        )
        assert denied.status_code == 403


def test_writing_into_someone_elses_room_is_refused_with_a_next_step(tmp_path):
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        for principal_id, kind in (("pm", "human"), ("agent", "agent")):
            client.put(
                f"/v1/principals/{principal_id}",
                json={"id": principal_id, "kind": kind, "display_name": principal_id},
            )
        client.post("/v1/projects", json={"id": "mei", "name": "mei"})
        join(client, "local", "agent")
        denied = client.post(
            "/v1/messages",
            json={
                "workspace_id": "mei", "sender_id": "agent",
                "recipient_ids": ["pm"], "body": "남의 방",
            },
        )
        assert denied.status_code == 403
        detail = denied.json()["detail"]
        assert '"mei" 소속이 아니다' in detail
        # 다음 행동: 어디에 속해 있는지 말해 준다.
        assert "Local" in detail


def test_only_a_lead_reads_another_rooms_member_list(tmp_path):
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        for principal_id, kind in (
            ("pm", "human"), ("lead", "agent"), ("worker", "agent")
        ):
            client.put(
                f"/v1/principals/{principal_id}",
                json={"id": principal_id, "kind": kind, "display_name": principal_id},
            )
        convene(client, "archivia")
        join(client, "local", "worker")
        # 자기 방은 누구나 본다.
        mine = client.get(
            "/v1/workspaces/local/members", params={"caller": "worker"}
        )
        assert mine.status_code == 200
        assert [role["name"] for role in mine.json()["roles"]] == ["worker-role"]
        # 남의 방은 lead 만 본다.
        assert client.get(
            "/v1/workspaces/archivia/members", params={"caller": "lead"}
        ).json()["lead"]["agent_id"] == "lead"
        denied = client.get(
            "/v1/workspaces/archivia/members", params={"caller": "worker"}
        )
        assert denied.status_code == 403
        assert "PM" in denied.json()["detail"]
        assert client.get(
            "/v1/workspaces/archivia/members", params={"caller": "pm"}
        ).status_code == 200


def test_the_board_is_read_by_everyone_but_written_by_the_lead(tmp_path):
    """읽기는 열려 있고 쓰기만 그 방 lead 와 PM 의 몫이다."""
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        for principal_id, kind in (
            ("pm", "human"), ("lead", "agent"), ("worker", "agent")
        ):
            client.put(
                f"/v1/principals/{principal_id}",
                json={"id": principal_id, "kind": kind, "display_name": principal_id},
            )
        convene(client, "archivia")
        join(client, "archivia", "worker")

        # 읽기는 그대로 열려 있다.
        assert client.get("/v1/board").status_code == 200

        denied = client.post(
            "/v1/board/nodes",
            json={
                "project_id": "archivia", "title": "표지 정리",
                "created_by": "worker",
            },
        )
        assert denied.status_code == 403
        assert "lead 는 lead 다" in denied.json()["detail"]

        node = client.post(
            "/v1/board/nodes",
            json={
                "project_id": "archivia", "title": "표지 정리",
                "created_by": "lead",
            },
        ).json()
        assert client.patch(
            f"/v1/board/nodes/{node['id']}",
            json={"status": "active", "actor": "worker"},
        ).status_code == 403
        assert client.patch(
            f"/v1/board/nodes/{node['id']}",
            json={"status": "active", "actor": "lead"},
        ).status_code == 200
        assert client.delete(
            f"/v1/board/nodes/{node['id']}", params={"actor": "worker"}
        ).status_code == 403
        assert client.delete(
            f"/v1/board/nodes/{node['id']}", params={"actor": "pm"}
        ).status_code == 204


def test_the_board_tells_you_to_ask_the_pm_when_a_room_has_no_lead(tmp_path):
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        for principal_id, kind in (("pm", "human"), ("worker", "agent")):
            client.put(
                f"/v1/principals/{principal_id}",
                json={"id": principal_id, "kind": kind, "display_name": principal_id},
            )
        hq = client.get("/v1/hq").json()
        client.post("/v1/projects", json={"id": "archivia", "name": "archivia"})
        role = join(client, "archivia", "worker", lead=True)
        client.put("/v1/projects/archivia/board-link", json={"hq_id": hq["id"]})
        # lead 자리를 비운다.
        client.put(f"/v1/roles/{role['id']}/lead", json={"is_lead": False})
        denied = client.post(
            "/v1/board/nodes",
            json={
                "project_id": "archivia", "title": "표지 정리",
                "created_by": "worker",
            },
        )
        assert denied.status_code == 403
        assert "lead 가 없다" in denied.json()["detail"]
        assert "PM" in denied.json()["detail"]


def test_a_reply_carries_what_it_answers_to_the_reader(tmp_path):
    """받은 글이 무엇에 대한 답인지 읽는 쪽에 실린다."""
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        for principal_id, kind in (("pm", "human"), ("agent", "agent")):
            client.put(
                f"/v1/principals/{principal_id}",
                json={"id": principal_id, "kind": kind, "display_name": principal_id},
            )
        join(client, "local", "agent")
        asked = client.post(
            "/v1/messages",
            json={
                "workspace_id": "local", "sender_id": "agent",
                "recipient_ids": ["pm"], "body": "물어본다",
            },
        ).json()
        client.post(
            "/v1/messages",
            json={
                "workspace_id": "local", "sender_id": "pm",
                "recipient_ids": ["agent"], "body": "답한다",
                "in_reply_to_project_seq": asked["project_seq"],
            },
        )
        delivered = client.get(
            "/v1/messages", params={"recipient": "agent", "after": 0}
        ).json()
        assert delivered[-1]["in_reply_to_project_seq"] == asked["project_seq"]
