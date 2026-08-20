from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .registry import LocalRegistry
from .server_url import validate_server_url


class PMServerError(RuntimeError):
    pass


@dataclass
class PMClient:
    server_url: str
    registry: LocalRegistry
    pm_id: str | None = None
    pm_name: str = "PM"
    node_id: str | None = None
    workspace_id: str = "local"
    # 읽기 검사에 실어 보낼 사람. 앱은 PM이고 에이전트 CLI는 자기 자신이다.
    caller_id: str | None = None
    _targets: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.server_url = validate_server_url(self.server_url)
        self.pm_id = self.pm_id or self.registry.pm_principal_id()
        self.node_id = self.node_id or self.registry.node_id()
        self.caller_id = self.caller_id or self.pm_id

    def _request(
        self, method: str, path: str, payload: dict | None = None
    ) -> dict | list:
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            f"{self.server_url.rstrip('/')}{path}",
            data=data,
            headers={"content-type": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                if response.status == 204:
                    return {}
                return json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")
            raise PMServerError(f"server {error.code}: {detail}") from error
        except (urllib.error.URLError, json.JSONDecodeError) as error:
            raise PMServerError(str(error)) from error

    def _raw_request(
        self, method: str, path: str, data: bytes | None = None,
        content_type: str | None = None,
    ) -> tuple[bytes, str]:
        headers = {"content-type": content_type} if content_type else {}
        request = urllib.request.Request(
            f"{self.server_url.rstrip('/')}{path}", data=data,
            headers=headers, method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                return response.read(), response.headers.get_content_type()
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")
            raise PMServerError(f"server {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            raise PMServerError(str(error)) from error

    def sync_connections(self) -> list[dict[str, Any]]:
        self._request(
            "PUT",
            f"/v1/principals/{self.pm_id}",
            {
                "id": self.pm_id,
                "kind": "human",
                "display_name": self._display_name(self.pm_name),
            },
        )
        self._request(
            "PUT",
            f"/v1/nodes/{self.node_id}",
            {"id": self.node_id, "display_name": self._display_name("Node")},
        )
        targets = []
        for binding in self.registry.list():
            binding_data = json.loads(binding.get("data_json") or "{}")
            host_pid = binding_data.get("host_pid") if binding_data.get("hosted") else None
            if isinstance(host_pid, int):
                try:
                    os.kill(host_pid, 0)
                except ProcessLookupError:
                    self.registry.detach(binding["local_name"])
                    try:
                        self._request(
                            "DELETE", f"/v1/bindings/{binding['principal_id']}"
                        )
                    except PMServerError:
                        pass
                    continue
                except PermissionError:
                    pass
            targets.append(binding)
        for binding in targets:
            binding_data = json.loads(binding.get("data_json") or "{}")
            recipient_id = binding["principal_id"]
            self._request(
                "PUT",
                f"/v1/principals/{recipient_id}",
                {
                    "id": recipient_id,
                    "kind": "agent",
                    "display_name": self._display_name(
                        binding.get("nickname") or binding["local_name"]
                    ),
                },
            )
            self._request(
                "PUT",
                f"/v1/bindings/{recipient_id}",
                {
                    "agent_id": recipient_id,
                    "node_id": self.node_id,
                    "agent_provider": binding["provider"],
                    "agent_session_id": binding["agent_session_id"],
                    "terminal_provider": binding_data.get("terminal_provider", "cmux"),
                    "terminal_session_id": binding_data.get(
                        "terminal_session_id", binding["surface_id"]
                    ),
                    "lifecycle": binding["lifecycle"],
                },
            )
        self._targets = targets
        return targets

    def _display_name(self, name: str) -> str:
        suffix = str(self.node_id).removeprefix("node-")[:8]
        return name if self.node_id == "node-local" else f"{name}@{suffix}"

    def _recipient_id(self, identity: str) -> str:
        binding = self.registry.binding(identity)
        return binding["principal_id"] if binding else identity

    def _reference_id(self, identity: str) -> str:
        """참조 대상을 principal ID로 바꾼다.

        세션 로컬 이름과 역할 이름을 모두 받는다. 예전에는 못 찾으면 입력값을
        그대로 서버에 넘겨 FOREIGN KEY 오류로 떨어졌다. 에이전트에게는 왜
        거절됐는지 알 길이 없는 메시지라, 여기서 막고 이유를 말한다.
        """
        binding = self.registry.binding(identity)
        if binding:
            return str(binding["principal_id"])
        for role in self.roles():
            if role["id"] == identity or role["name"] == identity:
                agent_id = role.get("agent_id")
                if agent_id:
                    return str(agent_id)
                raise PMServerError(
                    f"role has no assignee to reference: {identity}"
                )
            # 앱의 CC 칩은 역할이 아니라 그 역할 담당자의 principal을 보낸다.
            # 이걸 못 알아보면 마지막 방어선까지 흘러가는데, 거기 쓰는
            # _targets는 sync_connections에서만 채워지고 앱의 발송 경로는
            # 그걸 부르지 않아 늘 비어 있다. 그래서 CC가 통째로 막혔다.
            if identity and role.get("agent_id") == identity:
                return str(identity)
        known = {str(target["principal_id"]) for target in self._targets}
        known.add(str(self.pm_id))
        if identity in known:
            return identity
        raise PMServerError(
            f"reference not found: {identity}. "
            "use a role name, a session local name, or a principal id"
        )

    def _role_id(self, identity: str) -> str:
        matches = [
            role for role in self.roles()
            if role["id"] == identity or role["name"] == identity
        ]
        if len(matches) != 1:
            raise PMServerError(f"role is not uniquely discoverable: {identity}")
        return str(matches[0]["id"])

    def targets(self) -> list[dict[str, Any]]:
        return list(self._targets)

    def projects(self) -> list[dict]:
        result = self._request("GET", "/v1/projects")
        assert isinstance(result, list)
        return result

    def create_project(self, name: str) -> dict:
        result = self._request("POST", "/v1/projects", {"name": name})
        assert isinstance(result, dict)
        return result

    def archive_project(self, project_id: str) -> dict:
        result = self._request(
            "DELETE", f"/v1/projects/{urllib.parse.quote(project_id)}"
        )
        assert isinstance(result, dict)
        return result

    def update_project(self, project_id: str, name: str) -> dict:
        result = self._request(
            "PATCH", f"/v1/projects/{urllib.parse.quote(project_id)}", {"name": name}
        )
        assert isinstance(result, dict)
        return result

    def pm_profile(self) -> dict:
        result = self._request("GET", f"/v1/pm-profiles/{self.pm_id}")
        assert isinstance(result, dict)
        return result

    def update_pm_profile(self, display_name: str) -> dict:
        result = self._request(
            "PATCH", f"/v1/pm-profiles/{self.pm_id}",
            {"display_name": display_name},
        )
        assert isinstance(result, dict)
        return result

    def put_pm_avatar(self, data: bytes, media_type: str) -> None:
        self._raw_request(
            "PUT", f"/v1/pm-profiles/{self.pm_id}/avatar", data, media_type
        )

    def pm_avatar(self) -> tuple[bytes, str]:
        return self._raw_request("GET", f"/v1/pm-profiles/{self.pm_id}/avatar")

    def delete_pm_avatar(self) -> None:
        self._raw_request("DELETE", f"/v1/pm-profiles/{self.pm_id}/avatar")

    def agent_role_memberships(self) -> list[dict]:
        result = self._request("GET", "/v1/agent-role-memberships")
        assert isinstance(result, list)
        return result

    def project_bootstrap(self, project_id: str, agent_id: str) -> dict:
        query = urllib.parse.urlencode(
            {"agent_id": agent_id, "pm_id": self.pm_id}
        )
        result = self._request(
            "GET", f"/v1/projects/{urllib.parse.quote(project_id)}/bootstrap?{query}"
        )
        assert isinstance(result, dict)
        return result

    def agent_statuses(self) -> list[dict[str, Any]]:
        statuses = []
        for binding in self.registry.list():
            state = self._request(
                "GET", f"/v1/inbox/state/{binding['principal_id']}"
            )
            assert isinstance(state, dict)
            pending = self.registry.pending_summary(binding["local_name"])
            statuses.append(
                {
                    "id": binding["local_name"],
                    "nickname": binding.get("nickname"),
                    "provider": binding["provider"],
                    "lifecycle": binding["lifecycle"],
                    "local_pending": pending["pending_count"],
                    **state,
                }
            )
        return statuses

    def send(
        self, recipient_id: str, body: str, *, in_reply_to: int | None = None
    ) -> dict:
        return self.send_many([recipient_id], body, in_reply_to=in_reply_to)

    def send_many(
        self,
        recipient_ids: list[str],
        body: str,
        *,
        in_reply_to: int | None = None,
        in_reply_to_project_seq: int | None = None,
        reference_ids: list[str] | None = None,
        role_ids: list[str] | None = None,
        track: str | None = None,
        tags: list[str] | None = None,
        inherit_context: bool = True,
        later: bool = False,
    ) -> dict:
        result = self._request(
            "POST",
            "/v1/messages",
            {
                "workspace_id": self.workspace_id,
                "sender_id": self.pm_id,
                "recipient_ids": [
                    self._recipient_id(recipient_id) for recipient_id in recipient_ids
                ],
                "reference_ids": [
                    self._reference_id(reference_id)
                    for reference_id in (reference_ids or [])
                ],
                "role_ids": [self._role_id(role_id) for role_id in (role_ids or [])],
                "body": body,
                "in_reply_to": in_reply_to,
                "in_reply_to_project_seq": in_reply_to_project_seq,
                "track": track,
                "tags": tags,
                "inherit_context": inherit_context,
                "later": later,
            },
        )
        assert isinstance(result, dict)
        return result

    def send_as(
        self,
        sender_id: str,
        recipient_id: str | None,
        body: str,
        *,
        recipient_ids: list[str] | None = None,
        absolute_reference_ids: list[str] | None = None,
        kind: str = "message",
        reply_level: str = "r1",
        reference_ids: list[str] | None = None,
        in_reply_to: int | None = None,
        in_reply_to_project_seq: int | None = None,
        track: str | None = None,
        tags: list[str] | None = None,
        inherit_context: bool = True,
        later: bool = False,
        role_ids: list[str] | None = None,
        message_id: str | None = None,
    ) -> dict:
        result = self._request(
            "POST",
            "/v1/messages",
            {
                "id": message_id,
                "workspace_id": self.workspace_id,
                "sender_id": self._recipient_id(sender_id),
                "recipient_ids": [
                    self._recipient_id(value)
                    for value in ([recipient_id] if recipient_id else [])
                    + list(recipient_ids or [])
                ],
                "role_ids": [self._role_id(role_id) for role_id in (role_ids or [])],
                "body": body,
                "kind": kind,
                "reply_level": reply_level,
                # 절대 id 로 준 참조는 풀지 않는다. 이 방 명부에 없는 사람도
                # 지목할 수 있어야 하고, HQ 에는 명부 자체가 없다.
                "reference_ids": [
                    self._reference_id(reference_id)
                    for reference_id in (reference_ids or [])
                ] + list(absolute_reference_ids or []),
                "in_reply_to": in_reply_to,
                "in_reply_to_project_seq": in_reply_to_project_seq,
                "track": track,
                "tags": tags,
                "inherit_context": inherit_context,
                "later": later,
            },
        )
        assert isinstance(result, dict)
        return result

    def attention(self) -> list[dict]:
        result = self._request(
            "GET",
            f"/v1/workspaces/{urllib.parse.quote(self.workspace_id)}/attention"
            f"?caller={urllib.parse.quote(self.caller_id or '')}",
        )
        assert isinstance(result, list)
        return result

    def roles(self) -> list[dict]:
        result = self._request(
            "GET", f"/v1/workspaces/{urllib.parse.quote(self.workspace_id)}/roles"
        )
        assert isinstance(result, list)
        return result

    def create_role(self, name: str, onboarding_prompt: str = "") -> dict:
        result = self._request(
            "POST", f"/v1/workspaces/{urllib.parse.quote(self.workspace_id)}/roles",
            {"name": name, "onboarding_prompt": onboarding_prompt},
        )
        assert isinstance(result, dict)
        return result

    def update_role(self, role_id: str, name: str, onboarding_prompt: str) -> dict:
        result = self._request(
            "PATCH", f"/v1/roles/{urllib.parse.quote(role_id)}",
            {"name": name, "onboarding_prompt": onboarding_prompt},
        )
        assert isinstance(result, dict)
        return result

    def delete_role(self, role_id: str) -> None:
        self._request("DELETE", f"/v1/roles/{urllib.parse.quote(role_id)}")

    def assign_role(self, role_id: str, agent_id: str, send_onboarding: bool) -> dict:
        result = self._request(
            "PUT", f"/v1/roles/{urllib.parse.quote(role_id)}/assignment",
            {
                "agent_id": self._recipient_id(agent_id),
                "assigned_by": self.pm_id,
                "send_onboarding": send_onboarding,
            },
        )
        assert isinstance(result, dict)
        return result

    def unassign_role(self, role_id: str) -> None:
        self._request("DELETE", f"/v1/roles/{urllib.parse.quote(role_id)}/assignment")

    def assignment_history(self, role_id: str) -> list[dict]:
        result = self._request(
            "GET", f"/v1/roles/{urllib.parse.quote(role_id)}/assignments"
        )
        assert isinstance(result, list)
        return result

    def put_role_avatar(self, role_id: str, data: bytes, media_type: str) -> None:
        self._raw_request(
            "PUT", f"/v1/roles/{urllib.parse.quote(role_id)}/avatar",
            data, media_type,
        )

    def role_avatar(self, role_id: str) -> tuple[bytes, str]:
        return self._raw_request(
            "GET", f"/v1/roles/{urllib.parse.quote(role_id)}/avatar"
        )

    def delete_role_avatar(self, role_id: str) -> None:
        self._raw_request(
            "DELETE", f"/v1/roles/{urllib.parse.quote(role_id)}/avatar"
        )

    # ---- HQ와 상황보드 ------------------------------------------------------

    def hq(self) -> dict | None:
        try:
            result = self._request("GET", "/v1/hq")
        except PMServerError as error:
            # 아직 안 만든 상태가 정상이다. 오류로 올리면 앱이 첫 화면부터
            # 빨개진다.
            if "server 404" in str(error):
                return None
            raise
        assert isinstance(result, dict)
        return result

    def connect_project(self, project_id: str, hq_id: str) -> dict:
        result = self._request(
            "PUT",
            f"/v1/projects/{urllib.parse.quote(project_id)}/board-link",
            {"hq_id": hq_id},
        )
        assert isinstance(result, dict)
        return result

    def disconnect_project(self, project_id: str) -> None:
        self._request(
            "DELETE", f"/v1/projects/{urllib.parse.quote(project_id)}/board-link"
        )

    def set_role_lead(self, role_id: str, is_lead: bool) -> dict:
        result = self._request(
            "PUT",
            f"/v1/roles/{urllib.parse.quote(role_id)}/lead",
            {"is_lead": is_lead},
        )
        assert isinstance(result, dict)
        return result

    def flush_lead_announcements(self) -> dict:
        """소집 모달이 닫힐 때 부른다. 서버가 기억하는 "마지막 안내"와 지금
        lead의 차이만 그 담당자에게 나간다."""
        result = self._request(
            "POST", "/v1/lead-announcements/flush", {"sender_id": self.pm_id}
        )
        assert isinstance(result, dict)
        return result

    def lead_of(self, project_id: str) -> dict | None:
        try:
            result = self._request(
                "GET", f"/v1/projects/{urllib.parse.quote(project_id)}/lead"
            )
        except PMServerError as error:
            if "server 404" in str(error):
                return None
            raise
        assert isinstance(result, dict)
        return result

    def board_candidates(self) -> list[dict]:
        query = urllib.parse.urlencode({"caller": self.caller_id or ""})
        result = self._request("GET", f"/v1/board/candidates?{query}")
        assert isinstance(result, list)
        return result

    def board(self) -> list[dict]:
        result = self._request("GET", "/v1/board")
        assert isinstance(result, list)
        return result

    def create_board_node(
        self, project_id: str, title: str, status: str = "todo",
        created_by: str | None = None,
    ) -> dict:
        result = self._request(
            "POST", "/v1/board/nodes",
            {
                "project_id": project_id, "title": title, "status": status,
                "created_by": created_by or self.caller_id,
            },
        )
        assert isinstance(result, dict)
        return result

    def update_board_node(
        self, node_id: str, title: str | None = None, status: str | None = None,
        actor: str | None = None,
    ) -> dict:
        payload: dict[str, Any] = {"actor": actor or self.caller_id}
        if title is not None:
            payload["title"] = title
        if status is not None:
            payload["status"] = status
        result = self._request(
            "PATCH", f"/v1/board/nodes/{urllib.parse.quote(node_id)}", payload
        )
        assert isinstance(result, dict)
        return result

    def delete_board_node(self, node_id: str, actor: str | None = None) -> None:
        query = urllib.parse.urlencode({"actor": actor or self.caller_id or ""})
        self._request(
            "DELETE", f"/v1/board/nodes/{urllib.parse.quote(node_id)}?{query}"
        )

    def link_board_nodes(
        self, node_id: str, waits_for: str, created_by: str | None = None
    ) -> dict:
        result = self._request(
            "POST", "/v1/board/edges",
            {
                "node_id": node_id, "waits_for": waits_for,
                "created_by": created_by or self.caller_id,
            },
        )
        assert isinstance(result, dict)
        return result

    def unlink_board_nodes(
        self, node_id: str, waits_for: str, actor: str | None = None
    ) -> None:
        query = urllib.parse.urlencode(
            {
                "node_id": node_id, "waits_for": waits_for,
                "actor": actor or self.caller_id or "",
            }
        )
        self._request("DELETE", f"/v1/board/edges?{query}")

    def shared(self, keys: list[str] | None = None) -> list[dict]:
        query = urllib.parse.urlencode(
            {"caller": self.caller_id or "", "keys": keys or []}, doseq=True
        )
        result = self._request(
            "GET", f"/v1/shared/{urllib.parse.quote(self.workspace_id)}?{query}"
        )
        assert isinstance(result, list)
        return result

    def put_shared(self, key: str, value: str) -> dict:
        result = self._request(
            "PUT",
            f"/v1/shared/{urllib.parse.quote(self.workspace_id)}/"
            f"{urllib.parse.quote(key, safe='')}?"
            f"{urllib.parse.urlencode({'updated_by': self.pm_id})}",
            {"value": value},
        )
        assert isinstance(result, dict)
        return result

    def delete_shared(self, key: str) -> None:
        self._request(
            "DELETE",
            f"/v1/shared/{urllib.parse.quote(self.workspace_id)}/"
            f"{urllib.parse.quote(key, safe='')}",
        )

    def bookmarks(self) -> list[dict]:
        result = self._request(
            "GET",
            f"/v1/workspaces/{urllib.parse.quote(self.workspace_id)}/bookmarks"
            f"?caller={urllib.parse.quote(self.caller_id or '')}",
        )
        assert isinstance(result, list)
        return result

    def create_bookmark(self, message_seq: int, label: str) -> dict:
        result = self._request(
            "POST",
            f"/v1/workspaces/{urllib.parse.quote(self.workspace_id)}/messages/"
            f"{message_seq}/bookmarks",
            {"label": label, "created_by": self.pm_id},
        )
        assert isinstance(result, dict)
        return result

    def delete_bookmark(self, bookmark_id: str) -> None:
        self._request(
            "DELETE",
            f"/v1/workspaces/{urllib.parse.quote(self.workspace_id)}/bookmarks/"
            f"{urllib.parse.quote(bookmark_id)}",
        )

    def create_permission_request(
        self, *, session_id: str, agent_id: str | None, tool_name: str,
        tool_input: str, suggestions: str | None, source: str = "terminal_hook",
        request_kind: str | None = None, provider_request_id: str | None = None,
        thread_id: str | None = None, turn_id: str | None = None,
        available_decisions: str | None = None,
    ) -> dict:
        result = self._request("POST", "/v1/permission-requests", {
            "workspace_id": self.workspace_id,
            "session_id": session_id,
            "agent_id": agent_id,
            "tool_name": tool_name,
            "tool_input": tool_input,
            "suggestions": suggestions,
            "source": source,
            "request_kind": request_kind,
            "provider_request_id": provider_request_id,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "available_decisions": available_decisions,
        })
        assert isinstance(result, dict)
        return result

    def permission_request(self, request_id: str) -> dict:
        result = self._request(
            "GET", f"/v1/permission-requests/{urllib.parse.quote(request_id)}"
        )
        assert isinstance(result, dict)
        return result

    def resolve_permission_request(
        self, request_id: str, status: str, resolved_by: str | None = None,
        decision: str | None = None, decision_scope: str | None = None,
    ) -> dict:
        result = self._request(
            "PATCH", f"/v1/permission-requests/{urllib.parse.quote(request_id)}",
            {
                "status": status, "resolved_by": resolved_by,
                "decision": decision, "decision_scope": decision_scope,
            },
        )
        assert isinstance(result, dict)
        return result

    def pending_permission_requests(self) -> list[dict]:
        result = self._request(
            "GET",
            f"/v1/workspaces/{urllib.parse.quote(self.workspace_id)}"
            "/permission-requests",
        )
        assert isinstance(result, list)
        return result

    def timeline_pins(self) -> list[dict]:
        result = self._request(
            "GET",
            f"/v1/workspaces/{urllib.parse.quote(self.workspace_id)}/timeline-pins"
            f"?caller={urllib.parse.quote(self.caller_id or '')}",
        )
        assert isinstance(result, list)
        return result

    def create_timeline_pin(self, after_message_seq: int, label: str) -> dict:
        result = self._request(
            "POST",
            f"/v1/workspaces/{urllib.parse.quote(self.workspace_id)}/messages/"
            f"{after_message_seq}/timeline-pins",
            {"label": label, "created_by": self.pm_id},
        )
        assert isinstance(result, dict)
        return result

    def delete_timeline_pin(self, pin_id: str) -> None:
        self._request(
            "DELETE",
            f"/v1/workspaces/{urllib.parse.quote(self.workspace_id)}/timeline-pins/"
            f"{urllib.parse.quote(pin_id)}",
        )

    def work_items(self) -> list[dict]:
        result = self._request(
            "GET",
            f"/v1/work/{urllib.parse.quote(self.workspace_id)}"
            f"?caller={urllib.parse.quote(self.caller_id or '')}",
        )
        assert isinstance(result, list)
        return result

    def start_work(self, agent_id: str, title: str) -> dict:
        result = self._request(
            "POST",
            "/v1/work",
            {
                "workspace_id": self.workspace_id,
                "agent_id": self._recipient_id(agent_id),
                "title": title,
            },
        )
        assert isinstance(result, dict)
        return result

    def update_work(self, agent_id: str, report: str, *, done: bool) -> dict:
        action = "done" if done else "report"
        agent_id = self._recipient_id(agent_id)
        result = self._request(
            "POST", f"/v1/work/{urllib.parse.quote(agent_id)}/{action}",
            {"report": report},
        )
        assert isinstance(result, dict)
        return result

    def timeline(
        self, limit: int = 100, after: int | None = None,
        before: int | None = None, after_project_seq: int | None = None,
    ) -> list[dict]:
        query: dict[str, Any] = {"limit": limit, "caller": self.caller_id or ""}
        if after is not None:
            query["after"] = after
        if before is not None:
            query["before"] = before
        if after_project_seq is not None:
            query["after_project_seq"] = after_project_seq
        result = self._request(
            "GET",
            f"/v1/workspaces/{urllib.parse.quote(self.workspace_id)}/timeline?"
            f"{urllib.parse.urlencode(query)}",
        )
        assert isinstance(result, list)
        return result

    def message(self, project_seq: int) -> dict:
        """방 안의 표시 번호로 글 하나. 번호를 알 때 앞뒤를 다 받지 않는다."""
        query = urllib.parse.urlencode({"caller": self.caller_id or ""})
        result = self._request(
            "GET",
            f"/v1/workspaces/{urllib.parse.quote(self.workspace_id)}/messages/"
            f"{int(project_seq)}?{query}",
        )
        assert isinstance(result, dict)
        return result

    def members(self, workspace_id: str | None = None) -> dict:
        """방 하나의 역할·담당자·lead."""
        room = workspace_id or self.workspace_id
        query = urllib.parse.urlencode({"caller": self.caller_id or ""})
        result = self._request(
            "GET", f"/v1/workspaces/{urllib.parse.quote(room)}/members?{query}"
        )
        assert isinstance(result, dict)
        return result


def delivery_status(message: dict) -> str:
    recipients = message.get("recipients") or []
    if not recipients:
        return "-"
    if all(recipient.get("processed_at") for recipient in recipients):
        return "processed"
    if all(recipient.get("received_at") for recipient in recipients):
        return "received"
    return "sent"
