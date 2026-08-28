"""터미널 어댑터 경계.

Node는 어느 터미널을 쓰는지 몰라야 한다. cmux가 지금 유일한 구현이지만
일반 터미널과 Windows Terminal이 뒤따르므로, 무엇이 어댑터의 책임인지 여기
한 곳에 적어 둔다.

한 provider의 화면이나 명령이 바뀌어도 다른 어댑터가 깨지지 않는 것이 이
경계의 목적이다. 파싱 규칙은 어댑터 안에 가둔다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from threading import Event
from typing import Any, Iterator, Protocol, runtime_checkable


@dataclass(frozen=True)
class AgentCandidate:
    """터미널에서 찾은 에이전트 세션 하나.

    surface_id는 그 터미널이 창이나 pane을 부르는 이름이다. cmux는 자기
    surface id를, 일반 터미널 어댑터는 tty를 쓴다. Node는 그 값의 뜻을
    해석하지 않고 어댑터에 되돌려 줄 뿐이다.
    """

    provider: str
    agent_session_id: str
    surface_id: str
    surface_ref: str | None
    workspace_ref: str | None
    title: str
    tty: str | None
    cwd: str | None
    lifecycle: str
    binding_verified: bool = False
    verification_reason: str = "not_checked"
    hidden_reason: str | None = None

    def public_dict(self, *, diagnostic: bool = False) -> dict[str, Any]:
        value = asdict(self)
        if not diagnostic:
            value.pop("surface_ref", None)
            value.pop("surface_id", None)
            value.pop("hidden_reason", None)
        return value


@runtime_checkable
class TerminalAdapter(Protocol):
    """어댑터가 지켜야 하는 것.

    lifecycle은 running·idle·needs_input·unknown 넷 중 하나다. 판정할 수
    없으면 unknown으로 둔다. 명세 3.4가 unknown에는 입력하지 말라고 정했으므로
    모르는 것을 아는 척하는 쪽이 더 위험하다.
    """

    def discover_agents(
        self, *, include_hidden: bool = False
    ) -> list[AgentCandidate]:
        """실행 중인 에이전트 세션을 찾는다."""

    def current_surface_id(self) -> str | None:
        """이 프로세스가 딸린 터미널 표면. 어느 세션에서 부른 것인지 알 때 쓴다."""

    def canonical_surface_for_context(self, surface_id: str) -> str | None:
        """같은 세션을 가리키는 표면이 여럿일 때 대표를 고른다."""

    def resolve_binding_candidate(
        self, *args: Any, **kwargs: Any
    ) -> Any:
        """binding 대상을 유일하게 특정한다. 유일하지 않으면 붙지 않는다."""

    def agent_events(self, stop_event: Event | None = None) -> Iterator[dict[str, Any]]:
        """세션 상태 변화를 흘려보낸다."""

    def focus(self, candidate: AgentCandidate) -> None:
        """그 터미널 창을 사람 앞에 띄운다."""

    def wake(self, surface_id: str, text: str) -> None:
        """터미널에 고정 호출 신호를 넣는다.

        에이전트가 세션 신호를 직접 주는 어댑터에서는 할 일이 없다. 그때는
        아무것도 하지 않는다.
        """

    def prompt_ready(self, surface_id: str) -> bool:
        """지금 입력해도 되는 빈 프롬프트인지.

        확신할 수 없으면 False를 준다. 사람의 타이핑이나 권한 확인과 겹치는
        것보다 한 번 더 기다리는 쪽이 낫다.
        """


def open_terminal_adapter() -> TerminalAdapter:
    """어느 터미널 관리자를 쓸지 **여기 한 곳에서** 정한다.

    고르는 자리가 흩어져 있으면 어댑터를 하나 더 만들어도 나머지가 옛것을 직접
    부른다. 그러면 경계가 있어도 갈아끼울 수가 없다.

    지금은 cmux 하나뿐이다. tmux 나 일반 터미널이 붙을 자리가 여기다.
    """
    from .cmux import CmuxAdapter

    return CmuxAdapter()
