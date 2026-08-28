"""역할 라우트 — 만들고, 고치고, 누구를 앉히나.

여덟 개가 이어져 있고 `client` 와 `fail` 만 쓴다. 아바타 셋은 파일 뒤쪽에 따로
떨어져 있어 여기 없다 — 그쪽은 이미지 응답이라 다루는 것이 다르다.

`board` 와 같은 이유로 앱에 직접 등록한다.
"""

from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel, Field


class RolePayload(BaseModel):
    project_id: str = "local"
    name: str = Field(min_length=1, max_length=80)
    onboarding_prompt: str = Field(default="", max_length=20000)


class RoleLeadPayload(BaseModel):
    is_lead: bool


class RoleAssignmentPayload(BaseModel):
    agent_id: str = Field(min_length=1)
    send_onboarding: bool = False


def register_role_routes(
    app: Any, client: Callable[..., Any], fail: Callable[[Exception], Exception]
) -> None:
    @app.put("/api/roles/{role_id}/lead")
    def set_role_lead(role_id: str, payload: RoleLeadPayload) -> dict:
        try:
            with client() as pm:
                return pm.set_role_lead(role_id, payload.is_lead)
        except Exception as error:
            raise fail(error) from error

    @app.post("/api/lead-announcements/flush")
    def flush_lead_announcements() -> dict:
        # 소집 모달이 닫힐 때 앱이 부른다. 무엇이 바뀌었는지는 서버가
        # 기억과의 차이로 계산하므로 여기에는 실을 것이 없다.
        try:
            with client() as pm:
                return pm.flush_lead_announcements()
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
