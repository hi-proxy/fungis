from __future__ import annotations

import asyncio
import json
import os
import signal
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterable, Iterator, Literal

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .cmux import CmuxAdapter
from .context_detection import commit_candidates, detect_contexts
from .git_context import inspect_git_context, is_verified_commit
from .pm import PMClient
from .registry import LocalRegistry
from .tui import ConnectionController


ASSETS = Path(__file__).with_name("web_assets")


def default_source_roots() -> list[Path]:
    """daemon이 실행하는 파이썬 소스 디렉토리들.

    editable 설치에서는 레포의 `fungis_node/`와 `fungis_server/`다. import로
    찾지 않고 경로로 찾는다 — fungis_server를 여기서 import하면 지문 하나
    때문에 서버 의존이 통째로 딸려 온다.
    """
    here = Path(__file__).resolve().parent
    return [here, here.parent / "fungis_server"]


def source_fingerprint(roots: Iterable[Path]) -> tuple | None:
    """소스 트리의 지문. (경로, mtime, 크기) 목록이며 .py만 본다.

    git을 쓰지 않는 이유: 커밋 안 한 변경도 잡혀야 하고, 판정에 레포 상태가
    끼면 앱까지 git을 알아야 한다. 디렉토리를 하나도 못 찾으면 None을 준다 —
    패키징된 배포에서 못 재는 것을 낡았다고 하면 재시작 루프가 된다.
    """
    entries: list[tuple[str, int, int]] = []
    found = False
    for root in roots:
        if not root.is_dir():
            continue
        found = True
        for path in sorted(root.rglob("*.py")):
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append((str(path), stat.st_mtime_ns, stat.st_size))
    return tuple(entries) if found else None


def _exit_after_response() -> None:
    """/shutdown 응답이 소켓을 떠난 뒤 죽는다.

    SIGTERM을 자기에게 보내면 demo.DaemonLauncher가 걸어둔 handler가
    sys.exit(0)을 부르고, finally가 supervisor와 자식 서버를 같이 내린다.
    BackgroundTasks는 응답 후에 돌지만 커널 버퍼가 비울 틈을 잠깐 더 준다.
    """
    time.sleep(0.2)
    os.kill(os.getpid(), signal.SIGTERM)


class MessagePayload(BaseModel):
    project_id: str = "local"
    recipient_ids: list[str] = Field(default_factory=list)
    role_ids: list[str] = Field(default_factory=list)
    reference_ids: list[str] = Field(default_factory=list)
    body: str = Field(min_length=1, max_length=20000)
    in_reply_to: int | None = Field(default=None, gt=0)
    track: str | None = Field(default=None, max_length=120)
    tags: list[str] | None = None
    inherit_context: bool = True


class PermissionDecision(BaseModel):
    project_id: str = "local"
    status: str = Field(pattern="^(allowed|denied)$")


class RolePayload(BaseModel):
    project_id: str = "local"
    name: str = Field(min_length=1, max_length=80)
    onboarding_prompt: str = Field(default="", max_length=20000)


class TrackLinkPayload(BaseModel):
    hq_id: str = Field(min_length=1)


class RoleLeadPayload(BaseModel):
    is_lead: bool


class BoardNodePayload(BaseModel):
    project_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=200)
    status: str = Field(default="todo", pattern="^(todo|active|done)$")


class BoardNodeUpdatePayload(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    status: str | None = Field(default=None, pattern="^(todo|active|done)$")


class BoardEdgePayload(BaseModel):
    node_id: str = Field(min_length=1)
    waits_for: str = Field(min_length=1)


class RoleAssignmentPayload(BaseModel):
    agent_id: str = Field(min_length=1)
    send_onboarding: bool = False


class SharedPayload(BaseModel):
    project_id: str = "local"
    key: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=20000)


class AgentAction(BaseModel):
    surface_id: str = Field(min_length=1)
    action: Literal["toggle", "focus"]


class NicknamePayload(BaseModel):
    nickname: str = Field(max_length=80)


class ProjectPayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class PMProfilePayload(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)


class RepositoryPayload(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


class BookmarkPayload(BaseModel):
    label: str = Field(min_length=1, max_length=120)


class TimelinePinPayload(BaseModel):
    label: str = Field(min_length=1, max_length=120)


def create_web_app(
    registry_path: str | Path = ".fungis-node.db",
    server_url: str = "http://127.0.0.1:8787",
    *,
    cmux: CmuxAdapter | None = None,
    sends_wakes: bool = True,
    source_roots: Iterable[Path] | None = None,
    shutdown: Callable[[], None] | None = None,
) -> FastAPI:
    registry_path = Path(registry_path)
    cmux = cmux or CmuxAdapter()
    source_roots = list(source_roots) if source_roots is not None else default_source_roots()
    shutdown = shutdown or _exit_after_response
    # 기동 시점의 소스 지문. 그 뒤 디스크가 달라지면 이 프로세스는 옛 코드로
    # 돌고 있는 것이다.
    startup_fingerprint = source_fingerprint(source_roots)
    app = FastAPI(title="Fungis Control", version="0.1.0")
    discovery_cache: dict[str, object] = {
        "expires_at": 0.0,
        "targets": [],
        "agents": [],
    }
    discovery_lock = threading.Lock()

    @contextmanager
    def client(workspace_id: str = "local") -> Iterator[PMClient]:
        registry = LocalRegistry(registry_path)
        try:
            yield PMClient(server_url, registry, workspace_id=workspace_id)
        finally:
            registry.close()

    def fail(error: Exception) -> HTTPException:
        return HTTPException(status_code=409, detail=str(error))

    def strict_contexts(body: str, git_context: dict | None) -> list[dict]:
        if not git_context or not git_context.get("verified"):
            return []
        repo_root = git_context.get("repo_root")
        if not repo_root:
            return []
        verified_commits = {
            candidate
            for candidate in commit_candidates(body)
            if is_verified_commit(
                str(repo_root), git_context.get("head"), candidate
            )
        }
        return detect_contexts(
            body, [git_context], verified_commits=verified_commits
        )

    def build_state(project_id: str = "local") -> dict:
        with client(project_id) as pm:
            bindings = pm.registry.list()
            now = time.monotonic()
            with discovery_lock:
                if now >= float(discovery_cache["expires_at"]):
                    targets = pm.sync_connections()
                    connected = {
                        (item["agent_session_id"], item["surface_id"]): item
                        for item in bindings
                    }
                    agents = []
                    for candidate in cmux.discover_agents():
                        value = candidate.public_dict(diagnostic=True)
                        binding = connected.get(
                            (candidate.agent_session_id, candidate.surface_id)
                        )
                        value["connected"] = binding is not None
                        value["local_name"] = (
                            binding["local_name"] if binding else None
                        )
                        value["nickname"] = binding.get("nickname") if binding else None
                        value["principal_id"] = binding.get("principal_id") if binding else None
                        value["git"] = inspect_git_context(candidate.cwd)
                        # needs_input인데 빈 프롬프트가 아니면 권한 확인이나
                        # 선택 화면에서 멈춘 것이다. 여기서만 화면을 읽으므로
                        # read-screen 호출은 discovery 주기로 제한된다.
                        value["awaiting_input"] = bool(
                            value["connected"]
                            and candidate.lifecycle == "needs_input"
                            and not cmux.prompt_ready(candidate.surface_id)
                        )
                        agents.append(value)
                    discovery_cache.update(
                        expires_at=now + 15,
                        targets=targets,
                        agents=agents,
                    )
                targets = list(discovery_cache["targets"])
                agents = list(discovery_cache["agents"])
            project_repositories = []
            for item in pm.registry.project_repositories():
                value = dict(item)
                value["git"] = inspect_git_context(value["path"])
                project_repositories.append(value)
            selected_repository = next(
                (item for item in project_repositories if item["project_id"] == project_id),
                None,
            )
            timeline = pm.timeline(10)
            attention = pm.attention()
            git_context = (
                selected_repository.get("git") if selected_repository else None
            )
            for message in timeline:
                message["pm_relation"] = pm_relation(message, str(pm.pm_id))
                message["detected_contexts"] = strict_contexts(
                    message["body"], git_context
                )
            for request in attention:
                request["pm_relation"] = pm_relation(request, str(pm.pm_id))
                request["detected_contexts"] = strict_contexts(
                    request["body"], git_context
                )
            roles = pm.roles()
            memberships = pm.agent_role_memberships()
            by_agent: dict[str, list[dict]] = {}
            for membership in memberships:
                by_agent.setdefault(str(membership["agent_id"]), []).append(membership)
            for target in targets:
                target["memberships"] = by_agent.get(str(target["principal_id"]), [])
            for agent in agents:
                agent["memberships"] = by_agent.get(str(agent.get("principal_id")), [])
            connected_principals = {target["principal_id"] for target in targets}
            for role in roles:
                role["session_connected"] = (
                    role.get("agent_id") in connected_principals
                    if role.get("agent_id") else False
                )
            return {
                "project_id": project_id,
                "projects": pm.projects(),
                "project_repositories": project_repositories,
                "pm_id": pm.pm_id,
                "pm_profile": pm.pm_profile(),
                "targets": targets,
                "statuses": pm.agent_statuses(),
                "timeline": timeline,
                "attention": attention,
                "bookmarks": pm.bookmarks(),
                "timeline_pins": pm.timeline_pins(),
                "shared": pm.shared(),
                "work": pm.work_items(),
                "roles": roles,
                "agents": agents,
                "permission_requests": pm.pending_permission_requests(),
            }

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(ASSETS / "index.html")

    @app.get("/health")
    def health() -> dict[str, object]:
        # status만 주면 깨우기를 한 건도 안 보내는 daemon도 200을 준다. 앱은 그걸
        # 자기 것으로 삼고 제대로 된 daemon을 띄우지 않는다. 초록불인데 아무것도
        # 안 오는 상태가 그렇게 만들어졌다. 무엇을 보증하는지 같이 말한다.
        #
        # stale: 파이썬을 고치고 앱만 다시 열면 화면은 새것인데 서버는 옛 코드로
        # 답하던 문제. 기동 시점 지문과 지금 디스크를 대조해 앱이 이 daemon을
        # 갈아치울지 판단하게 한다. 지금 지문을 못 재면(디렉토리가 사라짐 등)
        # 낡았다고 하지 않는다 — 그쪽은 재시작해도 똑같아서 루프가 된다.
        current = source_fingerprint(source_roots)
        stale = (
            startup_fingerprint is not None
            and current is not None
            and current != startup_fingerprint
        )
        return {"status": "ok", "sends_wakes": sends_wakes, "stale": stale}

    @app.post("/shutdown")
    def shutdown_daemon(background: BackgroundTasks) -> dict[str, str]:
        # 앱이 낡은 daemon을 갈아치울 때 부른다. pid를 몰라도 되고, daemon이
        # 자식 서버까지 정리하고 내려간다. 응답을 돌려준 뒤에 죽는다.
        background.add_task(shutdown)
        return {"status": "shutting-down"}

    app.mount("/assets", StaticFiles(directory=ASSETS), name="assets")

    @app.get("/api/state")
    def state(project_id: str = "local") -> dict:
        try:
            return build_state(project_id)
        except Exception as error:
            raise fail(error) from error

    @app.get("/api/projects/{project_id}/history")
    def project_history(
        project_id: str,
        before: int = Query(gt=0),
        limit: int = Query(default=50, ge=1, le=50),
    ) -> list[dict]:
        try:
            with client(project_id) as pm:
                messages = pm.timeline(limit, before=before)
                repository = pm.registry.project_repository(project_id)
                git_context = (
                    inspect_git_context(repository["path"]) if repository else None
                )
                for message in messages:
                    message["pm_relation"] = pm_relation(message, str(pm.pm_id))
                    message["detected_contexts"] = strict_contexts(
                        message["body"], git_context
                    )
                return messages
        except Exception as error:
            raise fail(error) from error

    @app.websocket("/api/events")
    async def state_events(websocket: WebSocket) -> None:
        await websocket.accept()
        project_id = websocket.query_params.get("project_id", "local")
        previous = ""
        last_sent = 0.0
        try:
            while True:
                snapshot = await asyncio.to_thread(build_state, project_id)
                fingerprint = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
                now = time.monotonic()
                if fingerprint != previous or now - last_sent >= 15:
                    await websocket.send_json(snapshot)
                    previous = fingerprint
                    last_sent = now
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=2)
                except TimeoutError:
                    pass
        except (WebSocketDisconnect, RuntimeError):
            return

    @app.post("/api/messages", status_code=201)
    def send_message(payload: MessagePayload) -> dict:
        try:
            with client(payload.project_id) as pm:
                return pm.send_many(
                    payload.recipient_ids,
                    payload.body.strip(),
                    in_reply_to=payload.in_reply_to,
                    reference_ids=payload.reference_ids,
                    role_ids=payload.role_ids,
                    track=payload.track,
                    tags=payload.tags,
                    inherit_context=payload.inherit_context,
                )
        except Exception as error:
            raise fail(error) from error

    @app.post(
        "/api/projects/{project_id}/messages/{message_seq}/bookmarks",
        status_code=201,
    )
    def create_bookmark(
        project_id: str, message_seq: int, payload: BookmarkPayload
    ) -> dict:
        try:
            with client(project_id) as pm:
                return pm.create_bookmark(message_seq, payload.label.strip())
        except Exception as error:
            raise fail(error) from error

    @app.delete(
        "/api/projects/{project_id}/bookmarks/{bookmark_id}", status_code=204
    )
    def delete_bookmark(project_id: str, bookmark_id: str) -> None:
        try:
            with client(project_id) as pm:
                pm.delete_bookmark(bookmark_id)
        except Exception as error:
            raise fail(error) from error

    @app.post(
        "/api/projects/{project_id}/messages/{message_seq}/timeline-pins",
        status_code=201,
    )
    def create_timeline_pin(
        project_id: str, message_seq: int, payload: TimelinePinPayload
    ) -> dict:
        try:
            with client(project_id) as pm:
                return pm.create_timeline_pin(message_seq, payload.label.strip())
        except Exception as error:
            raise fail(error) from error

    @app.delete(
        "/api/projects/{project_id}/timeline-pins/{pin_id}", status_code=204
    )
    def delete_timeline_pin(project_id: str, pin_id: str) -> None:
        try:
            with client(project_id) as pm:
                pm.delete_timeline_pin(pin_id)
        except Exception as error:
            raise fail(error) from error

    # ---- HQ와 상황보드 ------------------------------------------------------
    #
    # 보드는 앱 스냅샷에 넣지 않는다. 스냅샷은 방 하나를 통째로 다시 만들고
    # 지문을 비교해 흘려보내는 구조라, 보드를 거기 넣으면 한 글자 바뀔 때마다
    # 열려 있는 모든 방 스트림이 전량 재전송된다.

    @app.get("/api/board")
    def read_board() -> dict:
        try:
            with client() as pm:
                hq = pm.hq()
                return {
                    "hq": hq,
                    "tracks": pm.board() if hq else [],
                    # 소집 화면이 한 번만 부르면 되게 같이 싣는다.
                    "candidates": pm.board_candidates(),
                }
        except Exception as error:
            raise fail(error) from error

    @app.put("/api/board/tracks/{project_id}")
    def connect_track(project_id: str, payload: TrackLinkPayload) -> dict:
        try:
            with client() as pm:
                return pm.connect_project(project_id, payload.hq_id)
        except Exception as error:
            raise fail(error) from error

    @app.delete("/api/board/tracks/{project_id}", status_code=204)
    def disconnect_track(project_id: str) -> None:
        try:
            with client() as pm:
                pm.disconnect_project(project_id)
        except Exception as error:
            raise fail(error) from error

    @app.post("/api/board/nodes", status_code=201)
    def create_board_node(payload: BoardNodePayload) -> dict:
        try:
            with client() as pm:
                return pm.create_board_node(
                    payload.project_id, payload.title.strip(), payload.status
                )
        except Exception as error:
            raise fail(error) from error

    @app.patch("/api/board/nodes/{node_id}")
    def update_board_node(node_id: str, payload: BoardNodeUpdatePayload) -> dict:
        try:
            with client() as pm:
                return pm.update_board_node(node_id, payload.title, payload.status)
        except Exception as error:
            raise fail(error) from error

    @app.delete("/api/board/nodes/{node_id}", status_code=204)
    def delete_board_node(node_id: str) -> None:
        try:
            with client() as pm:
                pm.delete_board_node(node_id)
        except Exception as error:
            raise fail(error) from error

    @app.post("/api/board/edges", status_code=201)
    def link_board_nodes(payload: BoardEdgePayload) -> dict:
        try:
            with client() as pm:
                return pm.link_board_nodes(payload.node_id, payload.waits_for)
        except Exception as error:
            raise fail(error) from error

    @app.delete("/api/board/edges", status_code=204)
    def unlink_board_nodes(node_id: str, waits_for: str) -> None:
        try:
            with client() as pm:
                pm.unlink_board_nodes(node_id, waits_for)
        except Exception as error:
            raise fail(error) from error

    @app.put("/api/roles/{role_id}/lead")
    def set_role_lead(role_id: str, payload: RoleLeadPayload) -> dict:
        try:
            with client() as pm:
                return pm.set_role_lead(role_id, payload.is_lead)
        except Exception as error:
            raise fail(error) from error

    @app.post("/api/roles", status_code=201)
    def create_role(payload: RolePayload) -> dict:
        try:
            with client(payload.project_id) as pm:
                return pm.create_role(payload.name.strip(), payload.onboarding_prompt)
        except Exception as error:
            raise fail(error) from error

    @app.patch("/api/roles/{role_id}")
    def update_role(role_id: str, payload: RolePayload) -> dict:
        try:
            with client() as pm:
                return pm.update_role(
                    role_id, payload.name.strip(), payload.onboarding_prompt
                )
        except Exception as error:
            raise fail(error) from error

    @app.delete("/api/roles/{role_id}", status_code=204)
    def delete_role(role_id: str) -> None:
        try:
            with client() as pm:
                pm.delete_role(role_id)
        except Exception as error:
            raise fail(error) from error

    @app.put("/api/roles/{role_id}/assignment")
    def assign_role(role_id: str, payload: RoleAssignmentPayload) -> dict:
        try:
            with client() as pm:
                pm.sync_connections()
                return pm.assign_role(
                    role_id, payload.agent_id, payload.send_onboarding
                )
        except Exception as error:
            raise fail(error) from error

    @app.delete("/api/roles/{role_id}/assignment", status_code=204)
    def unassign_role(role_id: str) -> None:
        try:
            with client() as pm:
                pm.unassign_role(role_id)
        except Exception as error:
            raise fail(error) from error

    @app.get("/api/roles/{role_id}/assignments")
    def assignment_history(role_id: str) -> list[dict]:
        try:
            with client() as pm:
                return pm.assignment_history(role_id)
        except Exception as error:
            raise fail(error) from error

    @app.put("/api/roles/{role_id}/avatar")
    async def set_role_avatar(role_id: str, request: Request) -> dict:
        data = await request.body()
        media_type = request.headers.get("content-type", "")
        try:
            with client() as pm:
                pm.put_role_avatar(role_id, data, media_type)
            return {"status": "updated"}
        except Exception as error:
            raise fail(error) from error

    @app.get("/api/roles/{role_id}/avatar")
    def role_avatar(role_id: str) -> Response:
        try:
            with client() as pm:
                data, media_type = pm.role_avatar(role_id)
            return Response(content=data, media_type=media_type)
        except Exception as error:
            raise fail(error) from error

    @app.delete("/api/roles/{role_id}/avatar", status_code=204)
    def delete_role_avatar(role_id: str) -> None:
        try:
            with client() as pm:
                pm.delete_role_avatar(role_id)
        except Exception as error:
            raise fail(error) from error

    @app.put("/api/shared/{key}")
    def save_shared(key: str, payload: SharedPayload) -> dict:
        if key != payload.key:
            raise HTTPException(status_code=400, detail="shared key mismatch")
        try:
            with client(payload.project_id) as pm:
                return pm.put_shared(key, payload.value.strip())
        except Exception as error:
            raise fail(error) from error

    @app.delete("/api/shared/{key}", status_code=204)
    def delete_shared(key: str, project_id: str = "local") -> None:
        try:
            with client(project_id) as pm:
                pm.delete_shared(key)
        except Exception as error:
            raise fail(error) from error

    @app.post("/api/permission-requests/{request_id}/resolve")
    def resolve_permission(request_id: str, payload: PermissionDecision) -> dict:
        with client(payload.project_id) as pm:
            return pm.resolve_permission_request(
                request_id, payload.status, resolved_by=str(pm.pm_id)
            )

    @app.get("/api/projects")
    def projects() -> list[dict]:
        try:
            with client() as pm:
                pm.sync_connections()
                return pm.projects()
        except Exception as error:
            raise fail(error) from error

    @app.post("/api/projects", status_code=201)
    def create_project(payload: ProjectPayload) -> dict:
        try:
            with client() as pm:
                return pm.create_project(payload.name.strip())
        except Exception as error:
            raise fail(error) from error

    @app.patch("/api/projects/{project_id}")
    def update_project(project_id: str, payload: ProjectPayload) -> dict:
        try:
            with client() as pm:
                return pm.update_project(project_id, payload.name.strip())
        except Exception as error:
            raise fail(error) from error

    @app.delete("/api/projects/{project_id}")
    def archive_project(project_id: str) -> dict:
        try:
            with client() as pm:
                return pm.archive_project(project_id)
        except Exception as error:
            raise fail(error) from error

    @app.put("/api/projects/{project_id}/repository")
    def set_project_repository(project_id: str, payload: RepositoryPayload) -> dict:
        try:
            with client() as pm:
                if project_id not in {str(item["id"]) for item in pm.projects()}:
                    raise LookupError("project not found")
                git = inspect_git_context(payload.path)
                if git is None:
                    raise ValueError("selected folder is not inside a Git worktree")
                stored = pm.registry.set_project_repository(project_id, git["repo_root"])
                return {**stored, "git": git}
        except Exception as error:
            raise fail(error) from error

    @app.delete("/api/projects/{project_id}/repository", status_code=204)
    def delete_project_repository(project_id: str) -> None:
        registry = LocalRegistry(registry_path)
        try:
            if not registry.delete_project_repository(project_id):
                raise HTTPException(status_code=404, detail="project repository not set")
        finally:
            registry.close()

    @app.patch("/api/pm-profile")
    def update_pm_profile(payload: PMProfilePayload) -> dict:
        try:
            with client() as pm:
                pm.sync_connections()
                return pm.update_pm_profile(payload.display_name.strip())
        except Exception as error:
            raise fail(error) from error

    @app.put("/api/pm-profile/avatar")
    async def set_pm_avatar(request: Request) -> dict:
        data = await request.body()
        media_type = request.headers.get("content-type", "")
        try:
            with client() as pm:
                pm.sync_connections()
                pm.put_pm_avatar(data, media_type)
            return {"status": "updated"}
        except Exception as error:
            raise fail(error) from error

    @app.get("/api/pm-profile/avatar")
    def pm_avatar() -> Response:
        try:
            with client() as pm:
                data, media_type = pm.pm_avatar()
            return Response(content=data, media_type=media_type)
        except Exception as error:
            raise fail(error) from error

    @app.delete("/api/pm-profile/avatar", status_code=204)
    def delete_pm_avatar() -> None:
        try:
            with client() as pm:
                pm.delete_pm_avatar()
        except Exception as error:
            raise fail(error) from error

    @app.post("/api/agents/action")
    def agent_action(payload: AgentAction) -> dict:
        registry = LocalRegistry(registry_path)
        try:
            candidates = cmux.discover_agents()
            matches = [item for item in candidates if item.surface_id == payload.surface_id]
            if len(matches) != 1:
                raise LookupError("agent terminal is not uniquely discoverable")
            candidate = matches[0]
            if payload.action == "focus":
                cmux.focus(candidate)
                return {"status": "focused", "surface_id": candidate.surface_id}
            controller = ConnectionController(registry, cmux, candidates=candidates)
            controller.selected = candidates.index(candidate)
            controller.toggle_selected()
            if controller.message.startswith("cannot"):
                raise RuntimeError(controller.message)
            with discovery_lock:
                discovery_cache["expires_at"] = 0.0
            return {"status": controller.message}
        except Exception as error:
            raise fail(error) from error
        finally:
            registry.close()

    @app.patch("/api/agents/{local_name}/nickname")
    def set_agent_nickname(local_name: str, payload: NicknamePayload) -> dict:
        registry = LocalRegistry(registry_path)
        try:
            result = registry.set_nickname(local_name, payload.nickname)
            with client() as pm:
                pm.sync_connections()
            with discovery_lock:
                discovery_cache["expires_at"] = 0.0
            return {
                "local_name": result["local_name"],
                "nickname": result.get("nickname"),
            }
        except Exception as error:
            raise fail(error) from error
        finally:
            registry.close()

    return app


def pm_relation(message: dict, pm_id: str) -> str:
    if message.get("sender_id") == pm_id:
        return "self"
    recipients = {
        item.get("recipient_id") for item in message.get("recipients", [])
    }
    references = {
        item.get("principal_id") for item in message.get("references", [])
    }
    if pm_id in recipients:
        if message.get("kind") == "pm_request" and message.get("reply_level") == "r3":
            return "confirm"
        return "direct"
    if pm_id in references:
        return "reference"
    return "ambient"


def run_web(
    registry_path: str | Path,
    server_url: str,
    host: str = "127.0.0.1",
    port: int = 8790,
    *,
    sends_wakes: bool = True,
) -> None:
    uvicorn.run(
        create_web_app(registry_path, server_url, sends_wakes=sends_wakes),
        host=host,
        port=port,
        reload=False,
    )
