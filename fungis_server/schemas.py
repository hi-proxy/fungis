from typing import Literal

from pydantic import BaseModel, Field, model_validator


class PrincipalCreate(BaseModel):
    id: str | None = None
    kind: Literal["human", "agent"]
    display_name: str = Field(min_length=1, max_length=80)


class ProjectCreate(BaseModel):
    id: str | None = Field(default=None, min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=80)


class ProjectUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class PMProfileUpdate(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)


class NodeUpsert(BaseModel):
    id: str
    display_name: str = Field(min_length=1, max_length=80)


class BindingUpsert(BaseModel):
    agent_id: str
    node_id: str
    agent_provider: str
    agent_session_id: str
    terminal_provider: Literal["cmux"] = "cmux"
    terminal_session_id: str
    lifecycle: Literal["running", "idle", "needs_input", "unknown"]


class MessageCreate(BaseModel):
    id: str | None = None
    workspace_id: str
    sender_id: str
    recipient_ids: list[str] = Field(default_factory=list)
    role_ids: list[str] = Field(default_factory=list)
    reference_ids: list[str] = Field(default_factory=list)
    body: str = Field(min_length=1, max_length=20000)
    kind: Literal["message", "pm_request"] = "message"
    reply_level: Literal["r1", "r2", "r3"] = "r1"
    in_reply_to: int | None = Field(default=None, gt=0)
    # 에이전트는 방별 표시 번호로 답장 지점을 말한다. 서버가 전역 seq로 바꾼다.
    in_reply_to_project_seq: int | None = Field(default=None, gt=0)
    track: str | None = Field(default=None, max_length=120)
    tags: list[str] | None = None
    inherit_context: bool = True

    @model_validator(mode="after")
    def has_recipient(self):
        if not self.recipient_ids and not self.role_ids:
            raise ValueError("at least one recipient or role is required")
        return self


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    onboarding_prompt: str = Field(default="", max_length=20000)


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    onboarding_prompt: str | None = Field(default=None, max_length=20000)


class RoleAssignmentUpsert(BaseModel):
    agent_id: str
    assigned_by: str
    send_onboarding: bool = False


class AckRequest(BaseModel):
    recipient_id: str
    through_seq: int = Field(gt=0)


class SharedValueUpsert(BaseModel):
    value: str = Field(min_length=1, max_length=20000)


class BookmarkCreate(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    created_by: str = Field(min_length=1)


class TimelinePinCreate(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    created_by: str = Field(min_length=1)


class WorkStart(BaseModel):
    workspace_id: str
    agent_id: str
    title: str = Field(min_length=1, max_length=500)


class WorkUpdate(BaseModel):
    report: str = Field(min_length=1, max_length=20000)


class PermissionRequestCreate(BaseModel):
    workspace_id: str
    session_id: str = Field(min_length=1)
    agent_id: str | None = None
    tool_name: str = Field(min_length=1, max_length=120)
    tool_input: str = Field(max_length=20000)
    suggestions: str | None = Field(default=None, max_length=20000)


class PermissionResolve(BaseModel):
    status: str = Field(pattern="^(allowed|denied|expired)$")
    resolved_by: str | None = None


class BoardLink(BaseModel):
    hq_id: str = Field(min_length=1)


class RoleLead(BaseModel):
    is_lead: bool


class BoardNodeCreate(BaseModel):
    project_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=200)
    created_by: str = Field(min_length=1)
    status: str = Field(default="todo", pattern="^(todo|active|done)$")


class BoardNodeUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    status: str | None = Field(default=None, pattern="^(todo|active|done)$")


class BoardEdge(BaseModel):
    node_id: str = Field(min_length=1)
    waits_for: str = Field(min_length=1)
    created_by: str = Field(min_length=1)
