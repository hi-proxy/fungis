"""보드 라우트.

`create_web_app` 안에 라우트 쉰둘이 한 덩이로 있었다. 보드 여덟은 `client` 와
`fail` 둘만 쓰므로 그대로 떼어 낼 수 있다 — 나머지 클로저(cmux · 잠금 · 지문
따위)는 이쪽에서 안 본다.

요청 본문 타입도 같이 왔다. 두면 web 을 다시 import 해야 하고 그건 순환이다.

경로는 그대로다. 옮기다 하나라도 빠지면 `test_every_route_is_still_there` 가
그 자리에서 잡는다.
"""

from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel, Field


class TrackLinkPayload(BaseModel):
    hq_id: str = Field(min_length=1)


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


def register_board_routes(
    app: Any, client: Callable[..., Any], fail: Callable[[Exception], Exception]
) -> None:
    """앱에 직접 등록한다.

    `APIRouter` 로 만들어 `include_router` 로 붙였더니 여덟 중 하나만 옮겨졌다.
    왜 그런지 파는 것보다, 옮기기 전과 **같은 방식으로 등록하는 것**이 이 작업의
    목적에 맞는다 — 지금 하는 일은 파일을 나누는 것이지 등록 방식을 바꾸는 게
    아니다.
    """

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

