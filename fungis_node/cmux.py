from __future__ import annotations

import glob
import json
import os
import selectors
import shutil
import subprocess
from .terminal import AgentCandidate
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Event
from typing import Any, Iterator


LIFECYCLE_MAP = {
    "running": "running",
    "idle": "idle",
    "needsinput": "needs_input",
    "needs_input": "needs_input",
}


# 후보 타입은 어댑터 경계에 있다. 예전 이름을 별칭으로 남겨 부르던 곳을
# 건드리지 않는다.
CmuxAgentCandidate = AgentCandidate


# cmux 실행 파일은 앱 번들 안에 있다. 셸은 프로필이 PATH에 넣어 줘서 보이지만,
# Finder나 로그인 항목으로 뜬 GUI 앱은 최소 PATH만 물려받아 못 찾는다. 이
# daemon을 띄우는 것이 그 앱이라, PATH만 믿으면 재부팅 한 번에 조용히 못 뜬다.
CMUX_BUNDLE_PATHS = (
    Path("/Applications/cmux.app/Contents/Resources/bin/cmux"),
    Path.home() / "Applications/cmux.app/Contents/Resources/bin/cmux",
)


def tty_exists(name: str | None) -> bool:
    """그 tty 장치가 아직 시스템에 있나.

    cmux 가 복원한 표면은 재부팅 전 tty 이름을 들고 있다. 그 이름이 /dev 에
    없으면 낡은 메타데이터지 어긋난 짝이 아니다.
    """
    return bool(name) and Path("/dev", name).exists()


def resolve_cmux(name: str = "cmux") -> str:
    """PATH를 먼저 보고, 없으면 아는 번들 자리를 본다.

    못 찾으면 이름을 그대로 돌려준다 — 그래야 daemon의 시작 검사가 지금처럼
    걸려서 "PATH에서 못 찾았다"로 죽는다. 여기서 조용히 성공시키지 않는다.
    """
    found = shutil.which(name)
    if found is not None:
        return found
    for path in CMUX_BUNDLE_PATHS:
        if os.access(path, os.X_OK):
            return str(path)
    return name


class CmuxError(RuntimeError):
    pass


class CmuxAdapter:
    def __init__(
        self,
        *,
        executable: str | None = None,
        hook_store_dir: str | Path = Path.home() / ".cmuxterm",
    ) -> None:
        # 한 번 풀어서 들고 있는다. 부르는 자리가 여섯이라 여기서 풀지 않으면
        # 시작 검사만 통과하고 실제 호출에서 죽는다.
        self.executable = executable or resolve_cmux()
        self.hook_store_dir = Path(hook_store_dir)

    def _run_json(self, *args: str) -> dict[str, Any]:
        process = subprocess.run(
            [self.executable, "--json", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if process.returncode != 0:
            raise CmuxError(process.stderr.strip() or process.stdout.strip())
        try:
            return json.loads(process.stdout)
        except json.JSONDecodeError as error:
            raise CmuxError("cmux returned invalid JSON") from error

    def surface_index(self) -> dict[str, dict[str, Any]]:
        tree = self._run_json("--id-format", "both", "tree", "--all")
        index: dict[str, dict[str, Any]] = {}
        for window in tree.get("windows", []):
            for workspace in window.get("workspaces", []):
                for pane in workspace.get("panes", []):
                    for surface in pane.get("surfaces", []):
                        record = dict(surface)
                        record["workspace_ref"] = workspace.get("ref")
                        record["workspace_title"] = workspace.get("title")
                        index[surface["ref"]] = record
        return index

    def current_surface_id(self) -> str | None:
        identity = self._run_json("--id-format", "both", "identify")
        caller = identity.get("caller", identity)
        surface = caller.get("surface") or caller.get("surface_id")
        if isinstance(surface, dict):
            return surface.get("id") or surface.get("uuid") or surface.get("ref")
        return surface or os.environ.get("CMUX_SURFACE_ID")

    def canonical_surface_for_context(self, surface_id: str) -> str | None:
        sessions = self._hook_sessions()
        current = [
            (provider, session)
            for provider, session in sessions
            if session.get("surfaceId") == surface_id
        ]
        if len(current) != 1:
            return None
        provider, context = current[0]
        if provider != "codex" or context.get("transcriptPath"):
            return surface_id
        pid = context.get("pid")
        launch_cwd = (context.get("launchCommand") or {}).get("workingDirectory")
        canonical = [
            session
            for candidate_provider, session in sessions
            if candidate_provider == provider
            and session.get("pid") == pid
            and session.get("transcriptPath")
            and (session.get("launchCommand") or {}).get("workingDirectory") == launch_cwd
        ]
        if len(canonical) != 1:
            return None
        return canonical[0].get("surfaceId")

    def resolve_binding_candidate(
        self, *, provider: str, agent_session_id: str, surface_id: str
    ) -> CmuxAgentCandidate | None:
        candidates = self.discover_agents()
        exact = [
            candidate
            for candidate in candidates
            if candidate.provider == provider
            and candidate.agent_session_id == agent_session_id
            and candidate.surface_id == surface_id
        ]
        if len(exact) == 1:
            return exact[0]
        # A live agent session can move between cmux panes/workspaces. In that
        # case its stable provider/session identity remains the same while the
        # surface UUID changes. Rebind only when the new verified candidate is
        # unique; ambiguity must still stop delivery.
        moved = [
            candidate
            for candidate in candidates
            if candidate.provider == provider
            and candidate.agent_session_id == agent_session_id
            and candidate.binding_verified
        ]
        if len(moved) == 1:
            return moved[0]
        canonical_surface = self.canonical_surface_for_context(surface_id)
        if canonical_surface is None or canonical_surface == surface_id:
            return None
        migrated = [
            candidate
            for candidate in candidates
            if candidate.provider == provider
            and candidate.surface_id == canonical_surface
            and candidate.binding_verified
        ]
        return migrated[0] if len(migrated) == 1 else None

    def agent_events(self, stop_event: Event | None = None) -> Iterator[dict[str, Any]]:
        stop_event = stop_event or Event()
        process = subprocess.Popen(
            [
                self.executable,
                "--json",
                "events",
                "--category",
                "agent",
                "--reconnect",
                "--no-ack",
                "--no-heartbeat",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert process.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            while not stop_event.is_set() and process.poll() is None:
                if not selector.select(timeout=0.5):
                    continue
                line = process.stdout.readline()
                if not line:
                    break
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    yield event
        finally:
            selector.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)

    def _hook_sessions(self) -> list[tuple[str, dict[str, Any]]]:
        found: list[tuple[str, dict[str, Any]]] = []
        pattern = str(self.hook_store_dir / "*-hook-sessions.json")
        for path_text in sorted(glob.glob(pattern)):
            path = Path(path_text)
            provider = path.name.removesuffix("-hook-sessions.json")
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            sessions = data.get("sessions", {})
            if isinstance(sessions, dict):
                for session in sessions.values():
                    if isinstance(session, dict):
                        found.append((provider, session))
        return found

    @staticmethod
    def _process_tty(pid: Any) -> str | None:
        if not isinstance(pid, int) or pid <= 0:
            return None
        process = subprocess.run(
            ["ps", "-o", "tty=", "-p", str(pid)],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if process.returncode != 0:
            return None
        return process.stdout.strip().removeprefix("/dev/") or None

    def discover_agents(
        self, *, include_hidden: bool = False
    ) -> list[CmuxAgentCandidate]:
        surfaces = self.surface_index()
        candidates: list[CmuxAgentCandidate] = []
        for provider, session in self._hook_sessions():
            surface_id = session.get("surfaceId")
            session_id = session.get("sessionId")
            if not surface_id or not session_id:
                continue
            surface_ref, surface = self._find_surface(surfaces, surface_id)
            transcript_path = session.get("transcriptPath")
            process_tty = self._process_tty(session.get("pid"))
            codex_tty_matches = []
            if provider == "codex" and transcript_path and process_tty:
                codex_tty_matches = [
                    (ref, value)
                    for ref, value in surfaces.items()
                    if str(value.get("tty") or "").removeprefix("/dev/")
                    == process_tty
                ]
                if len(codex_tty_matches) == 1:
                    surface_ref, surface = codex_tty_matches[0]
                    surface_id = str(surface.get("id") or surface_id)
            if surface is None:
                continue
            raw_lifecycle = (
                "running"
                if int(session.get("activePromptDepth") or 0) > 0
                else str(session.get("agentLifecycle", "unknown")).lower()
            )
            lifecycle = LIFECYCLE_MAP.get(raw_lifecycle, "unknown")
            surface_tty = str(surface.get("tty") or "").removeprefix("/dev/") or None
            hidden_reason = None
            if provider == "codex" and not transcript_path:
                # Codex may publish short-lived memory/maintenance hook sessions on
                # the same process as the user conversation. They do not own a
                # canonical rollout transcript and are not attachable terminals.
                hidden_reason = "internal_session_without_transcript"
            if process_tty is None:
                binding_verified = False
                verification_reason = "agent_pid_not_running"
                hidden_reason = hidden_reason or "stale_agent_pid"
            elif provider == "codex" and transcript_path:
                if len(codex_tty_matches) == 1:
                    # cmux may leave a stale hook surface after a terminal is
                    # moved. The live Codex process TTY is authoritative when it
                    # maps to exactly one surface in the current tree.
                    surface_ref, surface = codex_tty_matches[0]
                    surface_id = str(surface.get("id") or surface_id)
                    surface_tty = process_tty
                    binding_verified = True
                    verification_reason = "codex_process_tty_surface"
                elif not codex_tty_matches and surface_tty is None:
                    # cmux의 agent-session surface는 살아 있어도 tty를 null로
                    # 내놓을 수 있다. Codex 프로세스에는 실제 tty가 남아 있지만
                    # 트리 안에 그 tty가 하나도 없으므로, 예전에는 연결할 길이
                    # 없었다.
                    #
                    # 이때만 canonical transcript를 가진 살아 있는 PID와 그
                    # 세션의 hook가 직접 가리킨, 현재 트리에 실재하는 surface를
                    # 한 쌍으로 본다. tty가 다른 살아 있는 값이면 아래 거절을
                    # 그대로 타고, 같은 tty surface가 여럿인 경우도 허용하지
                    # 않는다. 검사를 없애는 것이 아니라 cmux가 증거 한 칸을
                    # 비워 둔 경우의 다른 증거를 쓴다.
                    surface_tty = process_tty
                    binding_verified = True
                    verification_reason = "codex_live_hook_surface_without_tty"
                else:
                    binding_verified = False
                    verification_reason = (
                        "codex_process_tty_not_unique"
                        if codex_tty_matches else "codex_process_tty_not_found"
                    )
            elif surface_tty is None:
                binding_verified = False
                verification_reason = "surface_tty_missing"
            elif process_tty != surface_tty and not tty_exists(surface_tty):
                # cmux 가 복원한 표면은 재부팅 전 tty 이름을 그대로 들고 있다.
                # 그 장치는 이제 없다. 없는 이름과 살아 있는 프로세스를 견주면
                # 영원히 안 맞고, 그러면 깨우기가 통째로 멈춘다(8/19).
                #
                # 표면 id 는 멀쩡하다 — 그 id 로 read-screen 하면 지금 화면이
                # 그대로 온다. 그리고 이 id 는 에이전트 자신의 훅이 적은 것이다.
                # 죽은 이름표보다 그쪽이 정본이다.
                binding_verified = True
                verification_reason = "surface_tty_gone_hook_surface_trusted"
            elif process_tty != surface_tty:
                binding_verified = False
                verification_reason = "agent_tty_surface_tty_mismatch"
            else:
                binding_verified = True
                verification_reason = "agent_tty_matches_surface"
            candidate = CmuxAgentCandidate(
                provider=provider,
                agent_session_id=session_id,
                surface_id=surface_id,
                surface_ref=surface_ref,
                workspace_ref=surface.get("workspace_ref"),
                title=surface.get("title") or surface.get("workspace_title") or provider,
                tty=surface_tty,
                cwd=session.get("cwd"),
                lifecycle=lifecycle,
                binding_verified=binding_verified,
                verification_reason=verification_reason,
                hidden_reason=hidden_reason,
            )
            if include_hidden or hidden_reason is None:
                candidates.append(candidate)
        return sorted(candidates, key=lambda item: (item.provider, item.title))

    @staticmethod
    def _find_surface(
        surfaces: dict[str, dict[str, Any]], surface_id: str
    ) -> tuple[str | None, dict[str, Any] | None]:
        for ref, surface in surfaces.items():
            if ref == surface_id or surface.get("id") == surface_id:
                return ref, surface
        return None, None

    def focus(self, candidate: CmuxAgentCandidate) -> None:
        process = subprocess.run(
            [
                self.executable,
                "rpc",
                "surface.focus",
                json.dumps({"surface_id": candidate.surface_id}),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if process.returncode != 0:
            raise CmuxError(process.stderr.strip() or process.stdout.strip())

    def wake(self, surface_id: str, text: str = "[fungis] inbox") -> None:
        send = subprocess.run(
            [self.executable, "send", "--surface", surface_id, text],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if send.returncode != 0:
            raise CmuxError(send.stderr.strip() or send.stdout.strip())
        enter = subprocess.run(
            [self.executable, "send-key", "--surface", surface_id, "enter"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if enter.returncode != 0:
            raise CmuxError(enter.stderr.strip() or enter.stdout.strip())

    def prompt_ready(self, surface_id: str) -> bool:
        process = subprocess.run(
            [self.executable, "read-screen", "--surface", surface_id, "--lines", "30"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if process.returncode != 0:
            return False
        for line in reversed(process.stdout.splitlines()):
            normalized = line.replace("\u00a0", " ").strip()
            if normalized.startswith("❯"):
                # A bare Claude prompt is safe. Text after it means the user is
                # already typing; permission/select dialogs have no bare prompt.
                return normalized == "❯"
        return False
