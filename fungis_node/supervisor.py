from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .cmux import CmuxAdapter
from .completion import CompletionReconciler
from .gate import IdleGate
from .inbox import InboxWatcher
from .registry import LocalRegistry


# 게이트 루프가 마지막으로 한 바퀴 돈 시각. health 가 이걸 읽어 루프의 생사를
# 말한다. node_state 에 두므로 daemon 이 바뀌어도 자리는 그대로다.
GATE_TICK_KEY = "gate_tick_at"


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@dataclass
class NodeSupervisor:
    registry_path: Path
    server_url: str
    cmux: CmuxAdapter
    recipients: set[str] | None = None
    gate_interval: float = 2.0
    settle_seconds: float = 5.0
    # 기본이 "안 보냄"이면 깜빡한 호출부가 조용히 아무것도 안 한다. 안 보내는 쪽이
    # 말을 해야 한다.
    send_wakes: bool = True
    registry_factory: Callable[[Path], LocalRegistry] = LocalRegistry

    def _log(self, kind: str, **data: object) -> None:
        print(json.dumps({"kind": kind, **data}, ensure_ascii=False), flush=True)

    def _beat(self, registry: LocalRegistry) -> None:
        """한 바퀴 돌았다는 것만 남긴다.

        이 값이 없으면 밖에서 루프의 생사를 볼 방법이 없다. 로그는 앱이 버리고
        health 는 200 을 준다. 2026-08-19 에 이 루프가 죽은 채로 34분을 갔고
        아무도 몰랐다.
        """
        registry.set_state(GATE_TICK_KEY, iso_now())

    def _inbox_worker(self, recipient_id: str, stop_event: threading.Event) -> None:
        registry = self.registry_factory(self.registry_path)
        try:
            binding = registry.binding(recipient_id)
            if binding is None:
                return
            InboxWatcher(
                self.server_url, binding["principal_id"], registry
            ).run_forever(stop_event)
        finally:
            registry.close()

    def _completion_worker(self, stop_event: threading.Event) -> None:
        registry = self.registry_factory(self.registry_path)
        reconciler = CompletionReconciler(self.server_url, registry)
        try:
            while not stop_event.is_set():
                try:
                    for event in self.cmux.agent_events(stop_event):
                        result = reconciler.handle_event(event)
                        if result:
                            self._log("processed", **result)
                except Exception as error:
                    if not stop_event.is_set():
                        self._log("completion_error", error=str(error))
                        stop_event.wait(2)
        finally:
            registry.close()

    def _active_recipients(self, registry: LocalRegistry) -> set[str]:
        active = {row["local_name"] for row in registry.list()}
        return active if self.recipients is None else active & self.recipients

    def _run_gate(self, registry: LocalRegistry, recipient_id: str) -> None:
        try:
            gate = IdleGate(
                registry,
                self.cmux,
                settle_seconds=self.settle_seconds,
                wake_text=(
                    "[fungis] Run fungis inbox, then act on messages for you "
                    "before ending this turn."
                ),
            )
            gate.refresh(recipient_id)
            binding = registry.binding(recipient_id)
            claim = registry.claim(recipient_id)
            if binding and claim:
                turn_complete = binding["lifecycle"] == "idle" or (
                    binding["lifecycle"] == "needs_input"
                    and self.cmux.prompt_ready(binding["surface_id"])
                )
                if turn_complete:
                    through_seq = int(claim["through_seq"])
                    state = InboxWatcher(
                        self.server_url, binding["principal_id"], registry
                    ).ack_processed(through_seq)
                    self._log(
                        "processed_recovered",
                        recipient_id=recipient_id,
                        through_seq=through_seq,
                        state=state,
                    )
            decision = gate.run(recipient_id, send=self.send_wakes, refresh=False)
            if decision.eligible:
                self._log(
                    "wake_sent" if self.send_wakes else "wake_eligible",
                    **decision.as_dict(),
                )
        except Exception as error:
            self._log("gate_error", recipient_id=recipient_id, error=str(error))

    def run_forever(self, stop_event: threading.Event | None = None) -> None:
        stop_event = stop_event or threading.Event()
        completion = threading.Thread(
            target=self._completion_worker,
            args=(stop_event,),
            name="fungis-completions",
            daemon=True,
        )
        completion.start()
        registry = self.registry_factory(self.registry_path)
        watchers: dict[str, tuple[threading.Thread, threading.Event]] = {}
        # 뜨자마자 한 번 찍는다. 이 값이 낡은 채로 남아 있으면 앱이 아직 안 도는
        # 루프를 도는 것으로 볼 수 있다.
        self._beat(registry)
        try:
            while not stop_event.is_set():
                try:
                    self._tick(registry, watchers)
                except Exception as error:
                    # 한 바퀴가 실패했다고 루프를 끝내면 daemon 은 살아 있는데
                    # 깨우기만 영영 멈춘다. 화면도 health 도 멀쩡해 보인다.
                    # cmux 가 잠깐 막혔을 때 실제로 그렇게 됐다.
                    self._log("tick_error", error=str(error))
                stop_event.wait(self.gate_interval)
        except KeyboardInterrupt:
            stop_event.set()
        finally:
            stop_event.set()
            for thread, child_stop in watchers.values():
                child_stop.set()
                thread.join(timeout=3)
            completion.join(timeout=3)
            registry.close()

    def _tick(
        self,
        registry: LocalRegistry,
        watchers: dict[str, tuple[threading.Thread, threading.Event]],
    ) -> None:
        active = self._active_recipients(registry)
        for recipient_id in active - watchers.keys():
            child_stop = threading.Event()
            thread = threading.Thread(
                target=self._inbox_worker,
                args=(recipient_id, child_stop),
                name=f"fungis-inbox-{recipient_id}",
                daemon=True,
            )
            watchers[recipient_id] = (thread, child_stop)
            thread.start()
            self._log("watch_started", recipient_id=recipient_id)
        for recipient_id in watchers.keys() - active:
            thread, child_stop = watchers.pop(recipient_id)
            child_stop.set()
            thread.join(timeout=2)
            self._log("watch_stopped", recipient_id=recipient_id)
        for recipient_id in sorted(active):
            self._run_gate(registry, recipient_id)
        # 한 바퀴를 끝까지 돌았을 때만 찍는다. 중간에 죽으면 시각이 안 움직인다.
        self._beat(registry)
