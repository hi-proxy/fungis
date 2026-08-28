from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable

from .cmux import CmuxAdapter
from .registry import LocalRegistry


@dataclass(frozen=True)
class GateDecision:
    recipient_id: str
    eligible: bool
    reason: str
    lifecycle: str
    pending_count: int
    through_seq: int
    settle_remaining_seconds: float
    would_send: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


class IdleGate:
    def __init__(
        self,
        registry: LocalRegistry,
        adapter: CmuxAdapter,
        *,
        settle_seconds: float = 5.0,
        now: Callable[[], datetime] | None = None,
        wake_text: str = "[fungis] inbox",
        due_text: str = "[fungis] 예약한 시각이다 — 하던 걸음을 이어간다",
    ) -> None:
        self.registry = registry
        self.adapter = adapter
        self.settle_seconds = settle_seconds
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.wake_text = wake_text
        # 인박스 깨우기와 문구를 나눈다. 받는 쪽이 왜 깨어났는지 알아야
        # 인박스를 읽을지 하던 일을 이어갈지 정한다.
        self.due_text = due_text

    def _schedule_due(self, recipient_id: str) -> dict | None:
        """지금이 예약 시각을 지났나."""
        booked = self.registry.wake_schedule(recipient_id)
        if booked is None:
            return None
        try:
            due = datetime.fromisoformat(str(booked["due_at"]).replace("Z", "+00:00"))
        except ValueError:
            # 못 읽는 예약은 없는 것으로 본다. 여기서 죽으면 게이트가 통째로
            # 멈추고, 그게 깨우기를 영영 막는다.
            return None
        return booked if self.now() >= due else None

    def refresh(self, recipient_id: str) -> dict:
        binding = self.registry.binding(recipient_id)
        if binding is None:
            raise LookupError(f"active binding not found: {recipient_id}")
        candidate = self.adapter.resolve_binding_candidate(
            provider=binding["provider"],
            agent_session_id=binding["agent_session_id"],
            surface_id=binding["surface_id"],
        )
        if candidate is None:
            raise LookupError(
                f"binding target is not uniquely discoverable: {recipient_id}"
            )
        if not candidate.binding_verified:
            raise LookupError(
                "binding target failed PID/TTY verification: "
                f"{candidate.verification_reason}"
            )
        return self.registry.refresh_candidate(recipient_id, candidate)

    def evaluate(self, recipient_id: str, *, refresh: bool = True) -> GateDecision:
        if refresh:
            self.refresh(recipient_id)
        binding = self.registry.binding(recipient_id)
        if binding is None:
            raise LookupError(f"active binding not found: {recipient_id}")
        pending = self.registry.pending_summary(recipient_id)
        lifecycle = binding["lifecycle"]
        elapsed = self._elapsed_seconds(binding["lifecycle_changed_at"])
        remaining = max(0.0, self.settle_seconds - elapsed)
        common = dict(
            recipient_id=recipient_id,
            lifecycle=lifecycle,
            pending_count=pending["pending_count"],
            through_seq=pending["through_seq"],
            settle_remaining_seconds=round(remaining, 3),
        )
        # 보낼 말은 없지만 본인이 이 시각에 깨워 달라고 했을 수 있다. 그게
        # 없으면 착수만 선언하고 턴이 끝난 worker 는 아무도 말을 걸 때까지 선다.
        booked = self._schedule_due(recipient_id)
        # 쌓인 것이 아니라 깨울 이유가 있는 것을 센다. later 로 온 것은
        # 인박스에 있되 턴을 열지 않는다.
        waking = pending["waking_count"]
        if waking == 0 and booked is None:
            return GateDecision(eligible=False, reason="no_pending", **common)
        # 이미 넘겨준 것을 또 깨우지 않는다.
        #
        # `pending_events` 는 확인이 와야 지워진다. 확인은 턴이 끝나야 오므로,
        # 읽고 나서 일하는 동안 그 줄이 그대로 남는다. 게이트가 그것만 보면
        # **한 턴 내내 같은 메시지로 계속 깨운다** — 긴 턴일수록 심하다.
        #
        # claim 은 '여기까지 넘겨줬다' 는 표시라 그 구간을 정확히 가린다. 새
        # 메시지가 오면 pending 이 그보다 커져서 다시 깨운다.
        claim = self.registry.claim(recipient_id)
        if (
            booked is None
            and claim is not None
            and int(claim["through_seq"]) >= pending["through_seq"]
        ):
            return GateDecision(eligible=False, reason="claimed", **common)
        # idle만 믿고 나머지는 전부 화면이 판단한다. 한 턴도 안 돈 새 세션의
        # lifecycle은 믿을 수 없다 — cmux가 unknown으로 적기도 하고 running에
        # 머물기도 한다(8/16 tester1은 unknown, tester2는 running이었고 둘 다
        # 화면은 빈 프롬프트였다). lifecycle만 보면 갓 배정한 에이전트는 첫
        # 메시지를 영원히 못 받고, 사람이 터미널을 건드려 줘야만 풀린다.
        #
        # 화면으로 내려도 안전하다. 진짜로 일하는 중이면 빈 프롬프트가 없어서
        # 어차피 못 깨운다. 여기까지 왔다는 건 보낼 것이 있다는 뜻이라
        # read-screen 호출도 대기 중일 때만 일어난다.
        if lifecycle != "idle" and not self.adapter.prompt_ready(
            binding["surface_id"]
        ):
            return GateDecision(
                eligible=False, reason=f"lifecycle_{lifecycle}", **common
            )
        if remaining > 0:
            return GateDecision(eligible=False, reason="settling", **common)
        # 예약은 보낼 말이 없어도 깨우는 것이라 확인 대기와 무관하다. 인박스
        # 쪽만 그 검사를 탄다 — 예약을 여기서 막으면 앞선 인박스 깨우기 하나가
        # 그 뒤 모든 예약을 세워 버린다.
        if waking == 0:
            return GateDecision(
                eligible=True, reason="scheduled",
                would_send=self.due_text, **common,
            )
        # 같은 자리를 두 번 찌르지 않으려는 규칙이다. 그런데 **찌를 자리가
        # 비어 있으면 두 번이 아니다** — 앞의 깨우기를 못 봤거나 보고도 인박스를
        # 안 돈 창은 지금 놀고 있고, 그런 창은 한도가 다 갈 때까지 아무 말도
        # 못 듣는다. 가만히 있는데 수신이 끊기는 것이 그래서였다.
        #
        # 화면을 여기서 직접 본다. 위쪽 검사는 lifecycle 이 idle 이면 화면을
        # 건너뛰는데, 그 값은 cmux 가 적는 것이라 믿고 넘길 수 없다.
        if self.registry.outstanding_wake(
            recipient_id
        ) is not None and not self.adapter.prompt_ready(binding["surface_id"]):
            return GateDecision(
                eligible=False, reason="wake_unconfirmed", **common
            )
        return GateDecision(
            eligible=True,
            reason="eligible",
            would_send=self.wake_text,
            **common,
        )

    def run(
        self, recipient_id: str, *, send: bool = False, refresh: bool = True
    ) -> GateDecision:
        decision = self.evaluate(recipient_id, refresh=refresh)
        if send and decision.eligible:
            binding = self.registry.binding(recipient_id)
            assert binding is not None
            self.adapter.wake(binding["surface_id"], decision.would_send or "")
            # 예약으로 깨운 것은 확인을 기다릴 것이 없다. 읽을 인박스가 없으니
            # ACK 할 대상도 없고, wake_attempts 에 남기면 그 뒤 인박스 깨우기가
            # 확인 안 됨으로 막힌다.
            if decision.reason != "scheduled":
                self.registry.record_wake(recipient_id, decision.through_seq)
            # 어떤 이유로 깨웠든 예약은 소진된다. 인박스로 이미 턴이 열렸는데
            # 예약이 남아 있으면 곧바로 한 번 더 찌른다.
            self.registry.clear_wake_schedule(recipient_id)
        return decision

    def _elapsed_seconds(self, timestamp: str) -> float:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return max(0.0, (self.now() - parsed).total_seconds())
