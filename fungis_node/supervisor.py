from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .cmux import CmuxAdapter
from .completion import CompletionReconciler
from .gate import IdleGate
from .inbox import InboxWatcher
from .registry import LocalRegistry


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
                wake_text="[fungis] inbox — run: fungis inbox",
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
        try:
            while not stop_event.is_set():
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
