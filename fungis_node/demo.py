from __future__ import annotations

import os
import signal
import subprocess
import sys
import shutil
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .cmux import CmuxAdapter
from .pm import PMClient
from .pm_tui import run_pm_tui
from .registry import LocalRegistry
from .supervisor import NodeSupervisor
from .web import run_web


def _healthy(server_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{server_url.rstrip('/')}/health", timeout=1) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError):
        return False


@dataclass
class DemoLauncher:
    registry_path: Path
    server_db_path: Path
    server_url: str = "http://127.0.0.1:8787"
    # 안 보내는 쪽이 말해야 한다. cli.add_wake_flags 참고.
    send_wakes: bool = True
    pm_name: str = "PM"

    def _start_server(self) -> subprocess.Popen | None:
        if _healthy(self.server_url):
            return None
        if self.server_url.rstrip("/") != "http://127.0.0.1:8787":
            raise RuntimeError("custom server URL must already be running")
        environment = os.environ.copy()
        environment["FUNGIS_DB"] = str(self.server_db_path)
        process = subprocess.Popen(
            [sys.executable, "-m", "fungis_server.main"],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if _healthy(self.server_url):
                return process
            if process.poll() is not None:
                raise RuntimeError("Fungis server exited during startup")
            time.sleep(0.1)
        process.terminate()
        process.wait(timeout=3)
        raise RuntimeError("Fungis server did not become healthy")

    def run(self) -> None:
        registry = LocalRegistry(self.registry_path)
        if not registry.list():
            registry.close()
            raise RuntimeError("no connected agents; run fungis-node ui first")
        registry.close()
        owned_server = self._start_server()
        stop_event = threading.Event()
        supervisor = NodeSupervisor(
            registry_path=self.registry_path,
            server_url=self.server_url,
            cmux=CmuxAdapter(),
            send_wakes=self.send_wakes,
        )
        supervisor_thread = threading.Thread(
            target=supervisor.run_forever,
            args=(stop_event,),
            name="fungis-supervisor",
            daemon=True,
        )
        supervisor_thread.start()
        chat_registry = LocalRegistry(self.registry_path)
        try:
            run_pm_tui(
                PMClient(
                    self.server_url,
                    chat_registry,
                    pm_name=self.pm_name,
                ),
                CmuxAdapter(),
            )
        finally:
            chat_registry.close()
            stop_event.set()
            supervisor_thread.join(timeout=5)
            if owned_server is not None and owned_server.poll() is None:
                owned_server.terminate()
                try:
                    owned_server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    owned_server.kill()
                    owned_server.wait(timeout=3)


@dataclass
class StackLauncher(DemoLauncher):
    def run(self) -> None:
        registry = LocalRegistry(self.registry_path)
        if not registry.list():
            registry.close()
            raise RuntimeError("no connected agents; run fungis-node ui first")
        registry.close()
        owned_server = self._start_server()
        stop_event = threading.Event()
        supervisor = NodeSupervisor(
            registry_path=self.registry_path,
            server_url=self.server_url,
            cmux=CmuxAdapter(),
            send_wakes=self.send_wakes,
        )
        try:
            print(
                "Fungis stack is running. Open chat in another terminal; "
                "Ctrl-C stops this stack.",
                flush=True,
            )
            supervisor.run_forever(stop_event)
        finally:
            stop_event.set()
            if owned_server is not None and owned_server.poll() is None:
                owned_server.terminate()
                try:
                    owned_server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    owned_server.kill()
                    owned_server.wait(timeout=3)


@dataclass
class DaemonLauncher(DemoLauncher):
    control_host: str = "127.0.0.1"
    control_port: int = 8790

    def run(self) -> None:
        # cmux가 없으면 뜨자마자 죽는다. 예전에는 그냥 떴다 — health는 200을
        # 주는데 화면 상태를 만들 때마다 409가 나고 웹소켓이 조용히 끊겼다.
        # 초록불인데 아무것도 안 되는 상태가 제일 나쁘다. 여기서 막는다.
        # 찾는 것은 어댑터가 한다 — PATH 다음에 앱 번들 자리까지 본다.
        if shutil.which(CmuxAdapter().executable) is None:
            raise RuntimeError(
                "cmux를 못 찾았다. PATH 에도 없고 아는 앱 번들 자리에도 없다."
                " 이 daemon 은 앱이 띄우는 것이고, 손으로 띄우려면 cmux 가"
                " PATH 에 있어야 한다."
            )
        # 연결된 에이전트가 없어도 뜬다. 앱이 이 daemon을 띄우고, 에이전트를
        # 연결하는 길은 그 앱뿐이라, 여기서 막으면 처음 켜는 사람은 영영
        # 아무것도 못 한다. supervisor는 빈 레지스트리를 견디고 새로 붙는
        # 것을 2초마다 알아서 집는다.
        owned = [self._start_server()]
        stop_event = threading.Event()
        supervisor = NodeSupervisor(
            registry_path=self.registry_path,
            server_url=self.server_url,
            cmux=CmuxAdapter(),
            send_wakes=self.send_wakes,
        )
        supervisor_thread = threading.Thread(
            target=supervisor.run_forever,
            args=(stop_event,),
            name="fungis-supervisor",
            daemon=True,
        )
        supervisor_thread.start()

        def watch_server() -> None:
            """서버가 사라지면 다시 띄운다.

            앱이 daemon을 다시 띄울 때, 아직 종료 중인 옛 서버를 살아 있다고
            보고 자기 서버를 안 띄우는 창이 있다. 그다음 옛 서버가 죽으면
            아무것도 안 남는다. 8/18에 두 번 겪었고 그때마다 PM이 기다리는
            중이었다. 무너지면 사람이 실패해봐야 아는 상태를 없앤다.
            """
            while not stop_event.wait(5):
                if _healthy(self.server_url):
                    continue
                try:
                    owned[0] = self._start_server()
                    print("Fungis server was gone; restarted.", flush=True)
                except Exception as error:
                    print(f"server restart failed: {error}", flush=True)

        threading.Thread(
            target=watch_server, name="fungis-server-watch", daemon=True
        ).start()
        # SIGTERM은 기본 처리에서 finally를 안 돌린다. 그러면 우리가 띄운 서버가
        # 고아로 남고, 다음 daemon은 8787이 살아 있으니 자기 서버를 안 띄운 채
        # 옛 서버를 그대로 쓴다. 재시작했다고 믿는데 옛 코드가 도는 상태가 된다.
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        try:
            print(
                f"Fungis daemon is running at http://{self.control_host}:"
                f"{self.control_port}; Ctrl-C stops it.",
                flush=True,
            )
            run_web(
                self.registry_path,
                self.server_url,
                self.control_host,
                self.control_port,
                sends_wakes=self.send_wakes,
            )
        finally:
            stop_event.set()
            supervisor_thread.join(timeout=5)
            server = owned[0]
            if server is not None and server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=3)
