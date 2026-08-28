"""방 라우트 — 목록·만들기·이름 고치기·보관, 그리고 저장소 경로.

여섯이 이어져 있다. 다섯은 `client` 와 `fail` 만 쓰고, 저장소 경로를 지우는
하나가 `registry_path` 를 더 본다 — 그 값은 노드 쪽 장부라 서버를 거치지 않는다.

`/api/projects/{id}/file` 과 `/history` 는 여기 없다. 파일 읽기는 저장소를
직접 열고, 이력은 문맥 검사를 끼고 있어 다루는 것이 다르다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import HTTPException
from pydantic import BaseModel, Field

from .git_context import inspect_git_context
from .registry import LocalRegistry


class ProjectPayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class RepositoryPayload(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


def register_project_routes(
    app: Any,
    client: Callable[..., Any],
    fail: Callable[[Exception], Exception],
    registry_path: Path,
) -> None:
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
