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
        # 예약 문구를 넣고 이만큼은 다시 안 넣는다. 창에 들어간 것과 에이전트가
        # 본 것은 다른 일이라 재시도가 필요한데, 간격이 없으면 2초마다 도배가 된다.
        resend_seconds: float = 120.0,
        now: Callable[[], datetime] | None = None,
        wake_text: str = "[fungis] inbox",
        due_text: str = "[fungis] 예약한 시각이다 — 하던 걸음을 이어간다",
    ) -> None:
        self.registry = registry
        self.adapter = adapter
        self.settle_seconds = settle_seconds
        self.resend_seconds = resend_seconds
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
        # 판정은 세 층이다. 층을 섞으면 무엇을 보고 무엇을 안 보는지가 흐려지고,
        # 2026-08-28 에 그렇게 두 번 깨졌다 — 한 번은 화면을 안 봐서 수신이
        # 끊겼고, 한 번은 claim 을 안 봐서 2초마다 도배했다.
        #
        #   1층  무엇을 보낼까      보낼 것이 없으면 여기서 끝난다
        #   2층  보낼 수 있나       받는 쪽 상태
        #   3층  지금 보내도 되나   보내는 쪽 절제
        errand, blocked = self._what_to_send(recipient_id, pending)
        if errand is None:
            return GateDecision(eligible=False, reason=blocked, **common)

        blocked = self._can_receive(binding, lifecycle, remaining)
        if blocked is not None:
            return GateDecision(eligible=False, reason=blocked, **common)

        blocked = self._may_send_now(recipient_id, errand, pending["through_seq"])
        if blocked is not None:
            return GateDecision(eligible=False, reason=blocked, **common)

        return GateDecision(
            eligible=True,
            reason=errand,
            would_send=(
                self.due_text if errand == "scheduled" else self.wake_text
            ),
            **common,
        )

    # 1층 — 무엇을 보낼까
    def _what_to_send(
        self, recipient_id: str, pending: dict
    ) -> tuple[str | None, str]:
        """보낼 것을 고른다. **여기서 보내지는 않는다.**

        고르는 것과 보내는 것을 한 자리에서 하면 아래 층을 건너뛰게 된다.
        예약이 그랬다 — 절제도 재시도도 못 받았다.
        """
        # 쌓인 것이 아니라 깨울 이유가 있는 것을 센다. later 로 온 것은
        # 인박스에 있되 턴을 열지 않는다.
        waking = pending["waking_count"]
        # 보낼 말은 없지만 본인이 이 시각에 깨워 달라고 했을 수 있다. 그게
        # 없으면 착수만 선언하고 턴이 끝난 worker 는 아무도 말을 걸 때까지 선다.
        booked = self._schedule_due(recipient_id)
        if waking == 0 and booked is None:
            return None, "no_pending"
        if waking == 0:
            return "scheduled", ""
        # 이미 넘겨준 것을 또 깨우지 않는다. `pending_events` 는 확인이 와야
        # 지워지고 확인은 턴이 끝나야 오므로, 읽고 일하는 동안 그 줄이 그대로
        # 남는다. claim 이 '여기까지 넘겨줬다' 를 말해 그 구간을 가린다.
        #
        # 예약이 함께 걸려 있으면 넘어간다. 그것은 인박스와 다른 이유로 깨우는
        # 것이라, 넘겨준 인박스 구간이 예약까지 덮으면 안 된다.
        claim = self.registry.claim(recipient_id)
        if (
            booked is None
            and claim is not None
            and int(claim["through_seq"]) >= pending["through_seq"]
        ):
            return None, "claimed"
        return "eligible", ""

    # 2층 — 보낼 수 있나
    def _can_receive(
        self, binding: dict, lifecycle: str, remaining: float
    ) -> str | None:
        """받는 쪽이 지금 받을 수 있는 상태인가.

        idle 만 믿고 나머지는 화면이 판단한다. 한 턴도 안 돈 새 세션의
        lifecycle 은 믿을 수 없다 — cmux 가 unknown 으로 적기도 하고 running 에
        머물기도 한다(8/16 tester1 은 unknown, tester2 는 running 이었고 둘 다
        화면은 빈 프롬프트였다). lifecycle 만 보면 갓 배정한 에이전트는 첫
        메시지를 영원히 못 받는다.
        """
        if lifecycle != "idle" and not self.adapter.prompt_ready(
            binding["surface_id"]
        ):
            return f"lifecycle_{lifecycle}"
        if remaining > 0:
            return "settling"
        return None

    # 3층 — 지금 보내도 되나
    def _may_send_now(
        self, recipient_id: str, errand: str, through_seq: int
    ) -> str | None:
        """같은 자리를 연달아 찌르지 않는다.

        **찌를 자리가 비어 있으면 두 번이 아니다** — 앞의 깨우기를 못 봤거나
        보고도 인박스를 안 돈 창은 지금 놀고 있고, 그런 창을 한도가 다 갈 때까지
        재워 두면 가만히 있는데 수신만 끊긴다.

        예약은 제 줄(`wake_schedule.sent_at`)로 잰다. `wake_attempts` 는 인박스
        확인 추적 전용이라, 예약을 거기 넣으면 영영 미확인으로 남아 인박스 쪽을
        막는다 — 그래서 예약이 이 층을 통째로 건너뛰고 있었다.
        """
        if errand == "scheduled":
            booked = self.registry.wake_schedule(recipient_id)
            sent_at = (booked or {}).get("sent_at")
            if sent_at and self._elapsed_seconds(str(sent_at)) < self.resend_seconds:
                return "schedule_sent"
            return None
        # 화면은 여기서 안 본다. **그것은 2층 질문이고 이미 지나왔다.**
        #
        # 섞어 두면 화면이 빈 동안 절제가 통째로 사라져서, 읽기 전까지 게이트
        # 주기마다 계속 나간다 — 실제로 24초 사이에 두 번 나갔다. 갇히는 것을
        # 막는 일은 짧아진 한도(45초)가 한다.
        # **같은 것**만 막는다. 새 메시지는 다른 용건이라 기다릴 이유가 없다 —
        # 여기서 한도를 걸면 새로 온 말이 그 시간만큼 늦는다.
        outstanding = self.registry.outstanding_wake(recipient_id)
        if outstanding is not None and int(outstanding["through_seq"]) >= through_seq:
            return "wake_unconfirmed"
        return None

    def run(
        self, recipient_id: str, *, send: bool = False, refresh: bool = True
    ) -> GateDecision:
        decision = self.evaluate(recipient_id, refresh=refresh)
        if send and decision.eligible:
            binding = self.registry.binding(recipient_id)
            assert binding is not None
            self.adapter.wake(binding["surface_id"], decision.would_send or "")
            now = self.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            # 세는 데만 쓴다. 판정은 위에서 이미 끝났다.
            self.registry.log_wake(
                recipient_id,
                decision.reason,
                None if decision.reason == "scheduled" else decision.through_seq,
                now,
            )
            # 예약으로 깨운 것은 확인을 기다릴 것이 없다. 읽을 인박스가 없으니
            # ACK 할 대상도 없고, wake_attempts 에 남기면 그 뒤 인박스 깨우기가
            # 확인 안 됨으로 막힌다.
            if decision.reason == "scheduled":
                # 보냈다고 지우지 않는다. 문구가 창에 들어간 것과 에이전트가
                # 그것을 본 것은 다른 일이라, 지우면 못 본 예약이 조용히
                # 사라진다. 턴이 돌면 그때 확인된 것으로 보고 지운다.
                self.registry.mark_schedule_sent(recipient_id, now)
            else:
                self.registry.record_wake(recipient_id, decision.through_seq)
                # 인박스로 턴이 열렸으면 예약도 함께 소진된다. 남겨 두면 열린
                # 턴을 곧바로 한 번 더 찌른다.
                self.registry.clear_wake_schedule(recipient_id)
        return decision

    def _elapsed_seconds(self, timestamp: str) -> float:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return max(0.0, (self.now() - parsed).total_seconds())
