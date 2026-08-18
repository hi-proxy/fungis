from __future__ import annotations

import asyncio
import os
import tempfile
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect

from .db import FungisDB
from .schemas import (
    AckRequest, BindingUpsert, MessageCreate, NodeUpsert, PrincipalCreate,
    BookmarkCreate,
    PMProfileUpdate, ProjectCreate, ProjectUpdate,
    RoleAssignmentUpsert, RoleCreate, RoleUpdate,
    SharedValueUpsert,
    TimelinePinCreate,
    WorkStart, WorkUpdate,
    PermissionRequestCreate,
    PermissionResolve,
    BoardLink, RoleLead,
    BoardNodeCreate, BoardNodeUpdate, BoardEdge,
)


class EventHub:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, recipient_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[recipient_id].add(websocket)

    async def disconnect(self, recipient_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections[recipient_id].discard(websocket)
            if not self._connections[recipient_id]:
                self._connections.pop(recipient_id, None)

    async def publish(self, event: dict) -> None:
        async with self._lock:
            targets = list(self._connections.get(event["recipient_id"], set()))
        stale: list[WebSocket] = []
        for websocket in targets:
            try:
                await websocket.send_json(event)
            except Exception:
                stale.append(websocket)
        for websocket in stale:
            await self.disconnect(event["recipient_id"], websocket)


def create_app(database_path: str | Path | None = None) -> FastAPI:
    if database_path is None:
        database_path = os.environ.get(
            "FUNGIS_DB", str(Path(tempfile.gettempdir()) / "fungis.db")
        )
    db = FungisDB(database_path)
    hub = EventHub()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        db.close()

    app = FastAPI(title="Fungis", version="0.1.0", lifespan=lifespan)
    app.state.db = db
    app.state.hub = hub

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/principals", status_code=201)
    def create_principal(payload: PrincipalCreate) -> dict:
        try:
            return db.create_principal(
                kind=payload.kind,
                display_name=payload.display_name,
                principal_id=payload.id,
            )
        except Exception as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.put("/v1/principals/{principal_id}")
    def upsert_principal(principal_id: str, payload: PrincipalCreate) -> dict:
        if payload.id is not None and payload.id != principal_id:
            raise HTTPException(status_code=400, detail="principal id mismatch")
        try:
            return db.upsert_principal(
                principal_id=principal_id,
                kind=payload.kind,
                display_name=payload.display_name,
            )
        except Exception as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/v1/projects")
    def projects() -> list[dict]:
        return db.projects()

    @app.post("/v1/projects", status_code=201)
    def create_project(payload: ProjectCreate) -> dict:
        try:
            return db.create_project(name=payload.name, project_id=payload.id)
        except Exception as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.patch("/v1/projects/{project_id}")
    def update_project(project_id: str, payload: ProjectUpdate) -> dict:
        try:
            return db.update_project(project_id=project_id, name=payload.name)
        except Exception as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.delete("/v1/projects/{project_id}")
    def archive_project(project_id: str) -> dict:
        try:
            return db.archive_project(project_id=project_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/v1/pm-profiles/{principal_id}")
    def pm_profile(principal_id: str) -> dict:
        try:
            return db.pm_profile(principal_id)
        except Exception as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.patch("/v1/pm-profiles/{principal_id}")
    def update_pm_profile(principal_id: str, payload: PMProfileUpdate) -> dict:
        try:
            return db.update_pm_profile(
                principal_id=principal_id, display_name=payload.display_name
            )
        except Exception as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.put("/v1/pm-profiles/{principal_id}/avatar")
    async def set_pm_avatar(principal_id: str, request: Request) -> dict:
        media_type = request.headers.get("content-type", "")
        if media_type not in {"image/jpeg", "image/png", "image/gif"}:
            raise HTTPException(status_code=415, detail="unsupported avatar image type")
        data = await request.body()
        if not data or len(data) > 2_000_000:
            raise HTTPException(status_code=413, detail="avatar must be 1 byte to 2 MB")
        try:
            return db.set_pm_avatar(
                principal_id=principal_id, data=data, media_type=media_type
            )
        except Exception as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/v1/pm-profiles/{principal_id}/avatar")
    def pm_avatar(principal_id: str) -> Response:
        value = db.pm_avatar(principal_id)
        if value is None:
            raise HTTPException(status_code=404, detail="PM avatar not found")
        data, media_type = value
        return Response(content=data, media_type=media_type)

    @app.delete("/v1/pm-profiles/{principal_id}/avatar", status_code=204)
    def delete_pm_avatar(principal_id: str) -> None:
        try:
            db.set_pm_avatar(principal_id=principal_id, data=None, media_type=None)
        except Exception as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.put("/v1/nodes/{node_id}")
    def upsert_node(node_id: str, payload: NodeUpsert) -> dict:
        if node_id != payload.id:
            raise HTTPException(status_code=400, detail="node id mismatch")
        return db.upsert_node(node_id=node_id, display_name=payload.display_name)

    @app.put("/v1/bindings/{agent_id}")
    def upsert_binding(agent_id: str, payload: BindingUpsert) -> dict:
        if agent_id != payload.agent_id:
            raise HTTPException(status_code=400, detail="agent id mismatch")
        try:
            return db.upsert_binding(payload.model_dump())
        except Exception as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.delete("/v1/bindings/{agent_id}", status_code=204)
    def detach_binding(agent_id: str) -> None:
        if not db.detach_binding(agent_id):
            raise HTTPException(status_code=404, detail="active binding not found")

    @app.post("/v1/permission-requests", status_code=201)
    def create_permission_request(payload: PermissionRequestCreate) -> dict:
        return db.create_permission_request(
            workspace_id=payload.workspace_id,
            session_id=payload.session_id,
            agent_id=payload.agent_id,
            tool_name=payload.tool_name,
            tool_input=payload.tool_input,
            suggestions=payload.suggestions,
        )

    @app.get("/v1/permission-requests/{request_id}")
    def read_permission_request(request_id: str) -> dict:
        found = db.permission_request(request_id)
        if found is None:
            raise HTTPException(status_code=404, detail="permission request not found")
        return found

    @app.patch("/v1/permission-requests/{request_id}")
    def resolve_permission_request(request_id: str, payload: PermissionResolve) -> dict:
        found = db.resolve_permission_request(
            request_id=request_id,
            status=payload.status,
            resolved_by=payload.resolved_by,
        )
        if found is None:
            raise HTTPException(status_code=404, detail="permission request not found")
        return found

    @app.get("/v1/workspaces/{workspace_id}/permission-requests")
    def pending_permission_requests(workspace_id: str) -> list[dict]:
        return db.pending_permission_requests(workspace_id)

    @app.post("/v1/messages", status_code=201)
    async def send_message(payload: MessageCreate) -> dict:
        # 남의 방에 글을 남길 수는 없다. 읽기 경계와 같은 판정을 쓴다 — HQ는
        # 소속이 아니라 lead 여부로 열리고, 그 규칙이 이미 여기 들어 있다.
        if not db.workspace_participant(
            workspace_id=payload.workspace_id, principal_id=payload.sender_id
        ):
            raise HTTPException(
                status_code=403,
                detail=db.participation_denied(
                    workspace_id=payload.workspace_id,
                    principal_id=payload.sender_id,
                ),
            )
        in_reply_to = payload.in_reply_to
        if payload.in_reply_to_project_seq is not None:
            if in_reply_to is not None:
                raise HTTPException(
                    status_code=422,
                    detail="in_reply_to and in_reply_to_project_seq are mutually exclusive",
                )
            in_reply_to = db.global_seq(
                workspace_id=payload.workspace_id,
                project_seq=payload.in_reply_to_project_seq,
            )
            if in_reply_to is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"message {payload.in_reply_to_project_seq} not found in this project",
                )
        try:
            message, events = db.send_message(
                workspace_id=payload.workspace_id,
                sender_id=payload.sender_id,
                recipient_ids=payload.recipient_ids,
                role_ids=payload.role_ids,
                reference_ids=payload.reference_ids,
                body=payload.body,
                message_id=payload.id,
                kind=payload.kind,
                reply_level=payload.reply_level,
                in_reply_to=in_reply_to,
                track=payload.track,
                tags=payload.tags,
                inherit_context=payload.inherit_context,
            )
        except Exception as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        for event in events:
            await hub.publish(event)
        return message

    @app.get("/v1/workspaces/{workspace_id}/roles")
    def roles(workspace_id: str) -> list[dict]:
        return db.roles(workspace_id)

    @app.post("/v1/workspaces/{workspace_id}/roles", status_code=201)
    def create_role(workspace_id: str, payload: RoleCreate) -> dict:
        try:
            return db.create_role(
                workspace_id=workspace_id,
                name=payload.name,
                onboarding_prompt=payload.onboarding_prompt,
            )
        except Exception as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.patch("/v1/roles/{role_id}")
    def update_role(role_id: str, payload: RoleUpdate) -> dict:
        try:
            return db.update_role(
                role_id=role_id,
                name=payload.name,
                onboarding_prompt=payload.onboarding_prompt,
            )
        except Exception as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.delete("/v1/roles/{role_id}", status_code=204)
    def delete_role(role_id: str) -> None:
        try:
            if not db.delete_role(role_id):
                raise HTTPException(status_code=404, detail="role not found")
        except HTTPException:
            raise
        except Exception as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.put("/v1/roles/{role_id}/avatar")
    async def set_role_avatar(role_id: str, request: Request) -> dict:
        media_type = request.headers.get("content-type", "")
        if media_type not in {"image/jpeg", "image/png", "image/gif"}:
            raise HTTPException(status_code=415, detail="unsupported avatar image type")
        data = await request.body()
        if not data or len(data) > 2_000_000:
            raise HTTPException(status_code=413, detail="avatar must be 1 byte to 2 MB")
        try:
            return db.set_role_avatar(role_id=role_id, data=data, media_type=media_type)
        except Exception as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/v1/roles/{role_id}/avatar")
    def role_avatar(role_id: str) -> Response:
        value = db.role_avatar(role_id)
        if value is None:
            raise HTTPException(status_code=404, detail="role avatar not found")
        data, media_type = value
        return Response(content=data, media_type=media_type)

    @app.delete("/v1/roles/{role_id}/avatar", status_code=204)
    def delete_role_avatar(role_id: str) -> None:
        try:
            db.set_role_avatar(role_id=role_id, data=None, media_type=None)
        except Exception as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.put("/v1/roles/{role_id}/assignment")
    async def assign_role(role_id: str, payload: RoleAssignmentUpsert) -> dict:
        try:
            current = db.role(role_id)
            prompt = current["onboarding_prompt"]
            is_new_assignment = current.get("agent_id") != payload.agent_id
            role, events = db.assign_role(
                role_id=role_id,
                agent_id=payload.agent_id,
                assigned_by=payload.assigned_by,
                onboarding_sent=payload.send_onboarding and is_new_assignment,
            )
            if payload.send_onboarding and is_new_assignment:
                # 역할 설명이 비어 있어도 보낸다. 안 보내면 에이전트는 자기가
                # 배정된 줄도 모르고, PM은 앱에서 보냈다고 믿는다.
                #
                # 호출문에 프로젝트 ID를 늘 싣는다. 이게 없으면 에이전트는
                # 배정된 건 아는데 자기 방 번호를 몰라 fungis init을 못 하고
                # PM에게 되묻는다.
                lines = [
                    "[fungis:init] 사용법과 현재 역할 구성을 불러오세요: "
                    f"fungis init --project {role['workspace_id']}"
                ]
                if prompt:
                    lines.append(prompt)
                _, onboarding_events = db.send_message(
                    workspace_id=role["workspace_id"],
                    sender_id=payload.assigned_by,
                    recipient_ids=[payload.agent_id],
                    role_ids=[],
                    body="\n\n".join(lines),
                    tags=["onboarding"],
                )
                events.extend(onboarding_events)
            for event in events:
                await hub.publish(event)
            return role
        except Exception as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.delete("/v1/roles/{role_id}/assignment", status_code=204)
    def unassign_role(role_id: str) -> None:
        if not db.unassign_role(role_id):
            raise HTTPException(status_code=404, detail="active assignment not found")

    @app.get("/v1/roles/{role_id}/assignments")
    def assignment_history(role_id: str) -> list[dict]:
        return db.assignment_history(role_id)

    @app.get("/v1/agent-role-memberships")
    def active_agent_roles() -> list[dict]:
        return db.active_agent_roles()

    # ---- HQ와 상황보드 ------------------------------------------------------

    @app.get("/v1/hq")
    def read_hq() -> dict:
        hq = db.hq()
        if hq is None:
            raise HTTPException(status_code=404, detail="hq not set up")
        return hq

    @app.put("/v1/projects/{project_id}/board-link")
    def connect_project(project_id: str, payload: BoardLink) -> dict:
        try:
            return db.connect_project(project_id=project_id, hq_id=payload.hq_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.delete("/v1/projects/{project_id}/board-link", status_code=204)
    def disconnect_project(project_id: str) -> None:
        if not db.disconnect_project(project_id=project_id):
            raise HTTPException(status_code=404, detail="project is not on the board")

    @app.get("/v1/projects/{project_id}/lead")
    def project_lead(project_id: str) -> dict:
        lead = db.lead_of(project_id)
        if lead is None:
            raise HTTPException(status_code=404, detail="no lead for this project")
        return lead

    @app.put("/v1/roles/{role_id}/lead")
    def set_role_lead(role_id: str, payload: RoleLead) -> dict:
        try:
            return db.set_role_lead(role_id=role_id, is_lead=payload.is_lead)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/v1/board/candidates")
    def board_candidates(caller: str = Query(...)) -> list[dict]:
        # 보드와 소집은 접근 레벨이 다르다. 노드는 방끼리 공유하는 것이 맞지만
        # 소집은 아니다. 이 응답에는 전 프로젝트의 이름과 역할과 담당 에이전트가
        # 들어 있다. 부를 사람을 고르는 자리라 부르는 쪽만 본다.
        if db.principal_kind(caller) != "human":
            raise HTTPException(
                status_code=403, detail="only the PM can read the convene list"
            )
        return db.board_candidates()

    @app.get("/v1/board")
    def read_board() -> list[dict]:
        # 보드는 누구나 읽는다. 대화와 달리 상태는 가릴 것이 아니다.
        return db.board()

    def guard_board_write(project_id: str, actor_id: str) -> None:
        """보드 쓰기는 그 방 lead 와 PM 의 몫이다. 읽기는 그대로 열려 있다."""
        denied = db.board_write_denied(project_id=project_id, actor_id=actor_id)
        if denied:
            raise HTTPException(status_code=403, detail=denied)

    def guard_board_node_write(node_id: str, actor_id: str) -> None:
        project_id = db.board_node_project(node_id)
        if project_id is None:
            raise HTTPException(status_code=404, detail="node not found")
        guard_board_write(project_id, actor_id)

    @app.post("/v1/board/nodes", status_code=201)
    def create_board_node(payload: BoardNodeCreate) -> dict:
        guard_board_write(payload.project_id, payload.created_by)
        try:
            return db.create_board_node(
                project_id=payload.project_id, title=payload.title,
                created_by=payload.created_by, status=payload.status,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.patch("/v1/board/nodes/{node_id}")
    def update_board_node(node_id: str, payload: BoardNodeUpdate) -> dict:
        guard_board_node_write(node_id, payload.actor)
        try:
            return db.update_board_node(
                node_id, title=payload.title, status=payload.status
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.delete("/v1/board/nodes/{node_id}", status_code=204)
    def delete_board_node(node_id: str, actor: str = Query(min_length=1)) -> None:
        guard_board_node_write(node_id, actor)
        if not db.delete_board_node(node_id):
            raise HTTPException(status_code=404, detail="node not found")

    @app.post("/v1/board/edges", status_code=201)
    def link_board_nodes(payload: BoardEdge) -> dict:
        # 기다리는 쪽의 방을 본다. 선행을 거는 것은 자기 일의 순서를 정하는
        # 것이라 그 판단은 기다리는 방의 몫이다.
        guard_board_node_write(payload.node_id, payload.created_by)
        try:
            db.link_board_nodes(
                node_id=payload.node_id, waits_for=payload.waits_for,
                created_by=payload.created_by,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"node_id": payload.node_id, "waits_for": payload.waits_for}

    @app.delete("/v1/board/edges", status_code=204)
    def unlink_board_nodes(
        node_id: str = Query(...), waits_for: str = Query(...),
        actor: str = Query(min_length=1),
    ) -> None:
        guard_board_node_write(node_id, actor)
        if not db.unlink_board_nodes(node_id=node_id, waits_for=waits_for):
            raise HTTPException(status_code=404, detail="link not found")

    @app.get("/v1/projects/{project_id}/bootstrap")
    def project_bootstrap(
        project_id: str,
        agent_id: str = Query(min_length=1),
        pm_id: str = Query(min_length=1),
    ) -> dict:
        try:
            return db.project_bootstrap(
                project_id=project_id, agent_id=agent_id, pm_id=pm_id
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/v1/messages")
    def messages(
        recipient: str = Query(min_length=1), after: int = Query(default=0, ge=0)
    ) -> list[dict]:
        return db.messages_after(recipient_id=recipient, after=after)

    @app.get("/v1/timeline/{principal_id}")
    def timeline(
        principal_id: str, limit: int = Query(default=100, ge=1, le=500)
    ) -> list[dict]:
        return db.timeline(principal_id, limit)

    @app.get("/v1/attention/{principal_id}")
    def attention(principal_id: str) -> list[dict]:
        return db.attention(principal_id)

    @app.get("/v1/workspaces/{workspace_id}/timeline")
    def workspace_timeline(
        workspace_id: str,
        caller: str = Query(...),
        limit: int = Query(default=100, ge=1, le=500),
        after: int | None = Query(default=None, ge=0),
        after_project_seq: int | None = Query(default=None, ge=0),
        before: int | None = Query(default=None, gt=0),
    ) -> list[dict]:
        # caller를 선택으로 두면 안 싣는 쪽이 곧 우회로가 된다. 필수로 받는다.
        if not db.workspace_participant(
            workspace_id=workspace_id, principal_id=caller
        ):
            raise HTTPException(
                status_code=403,
                detail=db.participation_denied(
                    workspace_id=workspace_id, principal_id=caller
                ),
            )
        if after is not None and before is not None:
            raise HTTPException(status_code=422, detail="after and before are mutually exclusive")
        if after_project_seq is not None:
            # 에이전트는 방별 표시 번호로 복구 지점을 말한다.
            if after is not None or before is not None:
                raise HTTPException(
                    status_code=422,
                    detail="after_project_seq cannot be combined with after or before",
                )
            after = db.global_seq(
                workspace_id=workspace_id, project_seq=after_project_seq
            )
            if after is None:
                after = 0
        return db.workspace_timeline(workspace_id, limit, after, before)

    @app.get("/v1/workspaces/{workspace_id}/messages/{project_seq}")
    def workspace_message(
        workspace_id: str, project_seq: int, caller: str = Query(...)
    ) -> dict:
        # 글 하나도 열람 경계를 지난다. 한 개짜리 창구를 열어 두면 그것이 곧
        # 우회로가 된다.
        if not db.workspace_participant(
            workspace_id=workspace_id, principal_id=caller
        ):
            raise HTTPException(
                status_code=403,
                detail=db.participation_denied(
                    workspace_id=workspace_id, principal_id=caller
                ),
            )
        message = db.workspace_message(
            workspace_id=workspace_id, project_seq=project_seq
        )
        if message is None:
            raise HTTPException(
                status_code=404,
                detail=f"{project_seq} 번 글이 이 방에 없다. fungis history 로 번호를 확인하라.",
            )
        return message

    @app.get("/v1/workspaces/{workspace_id}/members")
    def workspace_members(workspace_id: str, caller: str = Query(...)) -> dict:
        # 자기 방 명단은 누구나 본다. 남의 방 명단은 lead 만 본다 — 방을 건너
        # 일을 거는 것이 lead 의 일이고, 그 일에는 상대 이름이 필요하다.
        if not db.workspace_participant(
            workspace_id=workspace_id, principal_id=caller
        ) and not db.is_any_lead(caller):
            raise HTTPException(
                status_code=403,
                detail=(
                    f'"{db.project_name(workspace_id)}" 명단은 그 방 사람이나 '
                    "lead 만 본다. 네 방 lead 를 통하거나 PM 에게 요청하라."
                ),
            )
        return db.members(workspace_id)

    @app.get("/v1/workspaces/{workspace_id}/attention")
    def workspace_attention(workspace_id: str) -> list[dict]:
        return db.workspace_attention(workspace_id)

    @app.get("/v1/workspaces/{workspace_id}/bookmarks")
    def bookmarks(workspace_id: str) -> list[dict]:
        return db.bookmarks(workspace_id)

    @app.post(
        "/v1/workspaces/{workspace_id}/messages/{message_seq}/bookmarks",
        status_code=201,
    )
    def create_bookmark(
        workspace_id: str, message_seq: int, payload: BookmarkCreate
    ) -> dict:
        try:
            return db.create_bookmark(
                workspace_id=workspace_id, message_seq=message_seq,
                label=payload.label, created_by=payload.created_by,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.delete(
        "/v1/workspaces/{workspace_id}/bookmarks/{bookmark_id}", status_code=204
    )
    def delete_bookmark(workspace_id: str, bookmark_id: str) -> None:
        if not db.delete_bookmark(
            workspace_id=workspace_id, bookmark_id=bookmark_id
        ):
            raise HTTPException(status_code=404, detail="bookmark not found")

    @app.get("/v1/workspaces/{workspace_id}/timeline-pins")
    def timeline_pins(workspace_id: str) -> list[dict]:
        return db.timeline_pins(workspace_id)

    @app.post(
        "/v1/workspaces/{workspace_id}/messages/{message_seq}/timeline-pins",
        status_code=201,
    )
    def create_timeline_pin(
        workspace_id: str, message_seq: int, payload: TimelinePinCreate
    ) -> dict:
        try:
            return db.create_timeline_pin(
                workspace_id=workspace_id, after_message_seq=message_seq,
                label=payload.label, created_by=payload.created_by,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.delete(
        "/v1/workspaces/{workspace_id}/timeline-pins/{pin_id}", status_code=204
    )
    def delete_timeline_pin(workspace_id: str, pin_id: str) -> None:
        if not db.delete_timeline_pin(workspace_id=workspace_id, pin_id=pin_id):
            raise HTTPException(status_code=404, detail="timeline pin not found")

    @app.get("/v1/shared/{workspace_id}")
    def shared_values(
        workspace_id: str, keys: list[str] | None = Query(default=None)
    ) -> list[dict]:
        return db.shared_values(workspace_id=workspace_id, keys=keys)

    @app.put("/v1/shared/{workspace_id}/{key}")
    def upsert_shared_value(
        workspace_id: str,
        key: str,
        payload: SharedValueUpsert,
        updated_by: str = Query(min_length=1),
    ) -> dict:
        try:
            return db.upsert_shared_value(
                workspace_id=workspace_id,
                key=key,
                value=payload.value,
                updated_by=updated_by,
            )
        except Exception as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.delete("/v1/shared/{workspace_id}/{key}", status_code=204)
    def delete_shared_value(workspace_id: str, key: str) -> None:
        if not db.delete_shared_value(workspace_id=workspace_id, key=key):
            raise HTTPException(status_code=404, detail="shared key not found")

    @app.post("/v1/work", status_code=201)
    def start_work(payload: WorkStart) -> dict:
        try:
            return db.start_work(
                workspace_id=payload.workspace_id,
                agent_id=payload.agent_id,
                title=payload.title,
            )
        except Exception as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/v1/work/{agent_id}/report")
    def report_work(agent_id: str, payload: WorkUpdate) -> dict:
        try:
            return db.update_active_work(
                agent_id=agent_id, report=payload.report, done=False
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/v1/work/{agent_id}/done")
    def finish_work(agent_id: str, payload: WorkUpdate) -> dict:
        try:
            return db.update_active_work(
                agent_id=agent_id, report=payload.report, done=True
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/v1/work/{workspace_id}")
    def work_items(
        workspace_id: str, limit: int = Query(default=100, ge=1, le=500)
    ) -> list[dict]:
        return db.work_items(workspace_id=workspace_id, limit=limit)

    @app.post("/v1/inbox/ack-received")
    def ack_received(payload: AckRequest) -> dict:
        try:
            return db.ack(
                recipient_id=payload.recipient_id,
                through_seq=payload.through_seq,
                processed=False,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/v1/inbox/ack-processed")
    def ack_processed(payload: AckRequest) -> dict:
        try:
            return db.ack(
                recipient_id=payload.recipient_id,
                through_seq=payload.through_seq,
                processed=True,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/v1/inbox/state/{recipient_id}")
    def inbox_state(recipient_id: str) -> dict:
        return db.inbox_state(recipient_id)

    @app.websocket("/v1/events/{recipient_id}")
    async def events(websocket: WebSocket, recipient_id: str, after: int = 0) -> None:
        await hub.connect(recipient_id, websocket)
        try:
            for event in db.delivery_events_after(
                recipient_id=recipient_id, after=after
            ):
                await websocket.send_json(event)
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await hub.disconnect(recipient_id, websocket)

    return app


app = create_app()
