"""호스티드 세션 라우트 — fungis 가 직접 띄운 에이전트.

여섯이 이어져 있다. 하나가 `hosted_claim_lock` 을 쓴다 — 같은 세션을 둘이
차지하지 않게 막는 자물쇠고, 그래서 이 묶음은 `client·fail` 만으로는 안 된다.

터미널 에이전트와 갈리는 자리다. 이쪽은 프로세스를 우리가 들고 있어서 표준
입력으로 바로 넣고, 앱이 내려가면 함께 내려간다.

세션이 붙거나 떨어지면 발견 캐시를 비운다. 안 비우면 방금 앉힌 것이 목록에
안 보이거나, 떠난 것이 남아 보인다.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.parse
from typing import Any, Callable

from fastapi import HTTPException
from pydantic import BaseModel, Field


class HostedPermissionPayload(BaseModel):
    project_id: str = "local"
    session_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1, max_length=120)
    tool_input: str = Field(max_length=20000)
    request_kind: str = Field(min_length=1, max_length=80)
    provider_request_id: str | None = Field(default=None, max_length=120)
    thread_id: str | None = Field(default=None, max_length=200)
    turn_id: str | None = Field(default=None, max_length=200)
    available_decisions: str | None = Field(default=None, max_length=20000)


class HostedSessionPayload(BaseModel):
    local_name: str = Field(min_length=1, max_length=80)
    principal_id: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=80)
    session_id: str = Field(min_length=1, max_length=200)
    host_pid: int = Field(gt=0)
    project_id: str = Field(min_length=1)
    cwd: str = Field(min_length=1)
    model: str | None = Field(default=None, min_length=1)
    reasoning_effort: str | None = Field(default=None, min_length=1)


class HostedReplyPayload(BaseModel):
    project_id: str = Field(min_length=1)
    recipient_id: str = Field(min_length=1)
    body: str = Field(min_length=1, max_length=20000)
    in_reply_to_project_seq: int = Field(gt=0)


class HostedAckPayload(BaseModel):
    through_seq: int = Field(gt=0)


def register_hosted_routes(
    app: Any,
    client: Callable[..., Any],
    fail: Callable[[Exception], Exception],
    hosted_claim_lock: threading.Lock,
    discovery_cache: dict,
) -> None:
    @app.get("/api/hosted-sessions")
    def recoverable_hosted_sessions() -> list[dict]:
        with client() as pm:
            return pm.registry.recoverable_hosted()

    @app.put("/api/hosted-sessions/{principal_id}")
    def connect_hosted_session(
        principal_id: str, payload: HostedSessionPayload
    ) -> dict:
        if principal_id != payload.principal_id:
            raise HTTPException(status_code=400, detail="principal id mismatch")
        try:
            with hosted_claim_lock, client() as pm:
                existing = pm.registry.binding_for_principal(payload.principal_id)
                if existing is not None:
                    data = json.loads(existing.get("data_json") or "{}")
                    owner_pid = data.get("host_pid") if data.get("hosted") else None
                    if isinstance(owner_pid, int) and owner_pid != payload.host_pid:
                        try:
                            os.kill(owner_pid, 0)
                        except ProcessLookupError:
                            pass
                        except PermissionError:
                            raise HTTPException(
                                status_code=409, detail="hosted session has another live owner"
                            )
                        else:
                            raise HTTPException(
                                status_code=409, detail="hosted session has another live owner"
                            )
                binding = pm.registry.attach_hosted(
                    payload.local_name, payload.principal_id,
                    payload.provider, payload.session_id, payload.host_pid,
                    payload.cwd, payload.project_id,
                    payload.model, payload.reasoning_effort,
                )
                pm.registry.set_state(
                    f"active_project:{payload.principal_id}", payload.project_id
                )
                pm.sync_connections()
                discovery_cache["expires_at"] = 0.0
                return {
                    "local_name": binding["local_name"],
                    "principal_id": binding["principal_id"],
                    "provider": binding["provider"],
                    "agent_session_id": binding["agent_session_id"],
                    "lifecycle": binding["lifecycle"],
                }
        except Exception as error:
            if isinstance(error, HTTPException):
                raise
            raise fail(error) from error

    @app.delete("/api/hosted-sessions/{principal_id}", status_code=204)
    def disconnect_hosted_session(principal_id: str, forget: bool = True) -> None:
        try:
            with client() as pm:
                binding = pm.registry.binding_for_principal(principal_id)
                if binding is not None:
                    pm.registry.detach(binding["local_name"])
                if forget:
                    pm.registry.forget_hosted(principal_id)
                try:
                    pm._request("DELETE", f"/v1/bindings/{urllib.parse.quote(principal_id)}")
                except Exception:
                    pass
                discovery_cache["expires_at"] = 0.0
        except Exception as error:
            raise fail(error) from error

    @app.get("/api/hosted-sessions/{principal_id}/inbox")
    def hosted_inbox(principal_id: str, after: int = 0) -> list[dict]:
        try:
            with client() as pm:
                state = pm._request(
                    "GET",
                    f"/v1/inbox/state/{urllib.parse.quote(principal_id)}",
                )
                assert isinstance(state, dict)
                # AppModel의 in-memory cursor는 앱 재시작 때 0으로 돌아간다.
                # 서버 ack cursor보다 뒤로 물러나면 처리 완료 prompt를 재실행해
                # 새 메시지를 영원히 막을 수 있으므로 durable cursor가 하한이다.
                cursor = max(after, int(state.get("processed_seq", 0)))
                result = pm._request(
                    "GET",
                    f"/v1/messages?recipient={urllib.parse.quote(principal_id)}"
                    f"&caller={urllib.parse.quote(principal_id)}&after={cursor}",
                )
                assert isinstance(result, list)
                for message in result:
                    reply_id = (
                        f"hosted-reply:{principal_id}:"
                        f"{int(message['project_seq'])}"
                    )
                    status = pm._request(
                        "GET",
                        f"/v1/messages/{urllib.parse.quote(reply_id, safe='')}/status"
                        f"?caller={urllib.parse.quote(principal_id)}",
                    )
                    assert isinstance(status, dict)
                    message["reply_exists"] = bool(status.get("exists"))
                return result
        except Exception as error:
            raise fail(error) from error

    @app.post("/api/hosted-sessions/{principal_id}/reply", status_code=201)
    def hosted_reply(principal_id: str, payload: HostedReplyPayload) -> dict:
        try:
            with client(payload.project_id) as pm:
                return pm.send_as(
                    principal_id, payload.recipient_id, payload.body,
                    in_reply_to_project_seq=payload.in_reply_to_project_seq,
                    message_id=(
                        f"hosted-reply:{principal_id}:"
                        f"{payload.in_reply_to_project_seq}"
                    ),
                )
        except Exception as error:
            raise fail(error) from error

    @app.post("/api/hosted-sessions/{principal_id}/ack")
    def hosted_ack(principal_id: str, payload: HostedAckPayload) -> dict:
        try:
            with client() as pm:
                result = pm._request(
                    "POST", "/v1/inbox/ack-processed",
                    {"recipient_id": principal_id, "through_seq": payload.through_seq},
                )
                assert isinstance(result, dict)
                return result
        except Exception as error:
            raise fail(error) from error
