from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .cmux import CmuxAgentCandidate


from .delivery import (  # noqa: F401  옛 이름으로 부르던 자리를 위해 남긴다
    QUIET_EVENT_KIND,
    WAKE_CONFIRM_TTL_SECONDS,
    DeliveryStore,
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS bindings (
    local_name TEXT PRIMARY KEY,
    principal_id TEXT UNIQUE,
    nickname TEXT,
    provider TEXT NOT NULL,
    agent_session_id TEXT NOT NULL,
    surface_id TEXT NOT NULL,
    lifecycle TEXT NOT NULL,
    attached INTEGER NOT NULL DEFAULT 1,
    data_json TEXT NOT NULL,
    lifecycle_changed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS node_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_events (
    event_id TEXT PRIMARY KEY,
    event_seq INTEGER NOT NULL,
    recipient_id TEXT NOT NULL,
    through_seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    received_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(recipient_id, event_seq)
);

CREATE TABLE IF NOT EXISTS wake_attempts (
    recipient_id TEXT PRIMARY KEY,
    through_seq INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('sent', 'processed', 'superseded')),
    sent_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    processed_at TEXT,
    -- 이 줄은 깨우기마다 덮어쓰인다. 한도가 다 가도록 확인이 안 온 횟수는
    -- 그렇게 지워지는데, 그것이 곧 그 창이 몇 번 갇혔나다. 세어 두지 않으면
    -- 다음에 같은 얘기가 나와도 몇 번인지 댈 수가 없다.
    stalled_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS inbox_claims (
    recipient_id TEXT PRIMARY KEY,
    through_seq INTEGER NOT NULL,
    agent_session_id TEXT NOT NULL,
    claimed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS wake_schedule (
    recipient_id TEXT PRIMARY KEY,
    due_at TEXT NOT NULL,
    note TEXT,
    -- 보낸 시각. 예전에는 보내는 순간 이 줄을 지웠고, 그래서 문구가 창에 들어간
    -- 뒤 에이전트가 그것을 못 보고 지나가면 예약이 조용히 사라졌다. 재시도가
    -- 없다는 뜻이고, 상주 에이전트는 이 예약 사슬로 산다.
    sent_at TEXT,
    -- 진전 없이 반복해서 미루는 것은 그 자체가 막힘 신호다. 세어 두지 않으면
    -- 미루기만 하다 조용히 죽는 것을 밖에서 볼 방법이 없다.
    deferrals INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- 깨우기 한 줄씩. `wake_attempts` 는 에이전트당 한 행이라 다음 깨우기가
-- 덮어쓰고, 그래서 '몇 번 깨웠고 몇 번 읽혔나' 가 안 남는다. 그 숫자 없이는
-- 게이트를 고쳐도 나아졌는지 말할 수가 없다 — 2026-08-28 에 다섯 번 고치고도
-- 전후를 못 댔다.
CREATE TABLE IF NOT EXISTS wake_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_id TEXT NOT NULL,
    -- inbox 인가 scheduled 인가. 둘은 다른 이유로 나가고 다른 식으로 확인된다.
    kind TEXT NOT NULL,
    through_seq INTEGER,
    sent_at TEXT NOT NULL,
    -- 읽어 간 시각. 확인된 시각과 다르다 — 확인은 정리로도 찍히지만 이것은
    -- 에이전트가 실제로 인박스를 연 순간이다.
    read_at TEXT,
    settled_at TEXT,
    settled_by TEXT
);

CREATE INDEX IF NOT EXISTS wake_log_recipient_sent
    ON wake_log(recipient_id, sent_at);

CREATE TABLE IF NOT EXISTS project_repositories (
    project_id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""


class LocalRegistry(DeliveryStore):
    def __init__(self, path: str | Path):
        self.connection = sqlite3.connect(str(path))
        self.connection.row_factory = sqlite3.Row
        # 이 둘은 스키마보다 **먼저** 온다. 뒤에 두면 스키마를 실행하는 동안
        # 대기 시간이 0 이라, 그 순간 누가 쓰고 있으면 기다리지 않고 그 자리에서
        # 깨진다. daemon 은 요청마다 이 생성자를 타므로 몰릴 때 제일 먼저
        # 무너지는 자리가 여기였다.
        #
        # WAL 이 아니면 쓰기 하나가 읽기 전부를 막는다. 앱은 daemon 을 통해
        # 화면을 그리고 에이전트도 같은 문으로 들어와서, 쓰기 한 번에 화면과
        # 수신이 함께 선다. 서버 DB 는 이미 WAL 이다.
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.executescript(SCHEMA)
        self._migrate()

    def close(self) -> None:
        self.connection.close()

    def _migrate(self) -> None:
        columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(bindings)").fetchall()
        }
        if "lifecycle_changed_at" not in columns:
            self.connection.execute(
                "ALTER TABLE bindings ADD COLUMN lifecycle_changed_at TEXT"
            )
            self.connection.execute(
                """
                UPDATE bindings
                SET lifecycle_changed_at = COALESCE(
                  updated_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                )
                """
            )
            self.connection.commit()
        if "principal_id" not in columns:
            self.connection.execute(
                "ALTER TABLE bindings ADD COLUMN principal_id TEXT"
            )
            self.connection.execute(
                "UPDATE bindings SET principal_id = local_name WHERE principal_id IS NULL"
            )
            self.connection.commit()
        if "nickname" not in columns:
            self.connection.execute("ALTER TABLE bindings ADD COLUMN nickname TEXT")
            self.connection.commit()
        wake_schema = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'wake_attempts'"
        ).fetchone()["sql"]
        if "superseded" not in wake_schema:
            self.connection.executescript(
                """
                ALTER TABLE wake_attempts RENAME TO wake_attempts_legacy;
                CREATE TABLE wake_attempts (
                    recipient_id TEXT PRIMARY KEY,
                    through_seq INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('sent', 'processed', 'superseded')
                    ),
                    sent_at TEXT NOT NULL DEFAULT (
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    ),
                    processed_at TEXT
                );
                INSERT INTO wake_attempts(
                    recipient_id, through_seq, status, sent_at, processed_at
                ) SELECT recipient_id, through_seq, status, sent_at, processed_at
                  FROM wake_attempts_legacy;
                DROP TABLE wake_attempts_legacy;
                """
            )
        wake_columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(wake_attempts)")
        }
        if "stalled_count" not in wake_columns:
            self.connection.execute(
                "ALTER TABLE wake_attempts"
                " ADD COLUMN stalled_count INTEGER NOT NULL DEFAULT 0"
            )
        schedule_columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(wake_schedule)")
        }
        if "sent_at" not in schedule_columns:
            self.connection.execute(
                "ALTER TABLE wake_schedule ADD COLUMN sent_at TEXT"
            )
        self._ensure_identity()

    def _ensure_identity(self) -> None:
        existing = self.connection.execute(
            "SELECT COUNT(*) AS count FROM bindings"
        ).fetchone()["count"]
        values = {
            row["key"]: row["value"]
            for row in self.connection.execute(
                "SELECT key, value FROM node_state WHERE key IN (?, ?)",
                ("node_id", "pm_principal_id"),
            )
        }
        if "node_id" not in values:
            node_id = "node-local" if existing else f"node-{uuid.uuid4()}"
            self.set_state("node_id", node_id)
        if "pm_principal_id" not in values:
            pm_id = "pm-local" if existing else f"pm-{self.node_id().removeprefix('node-')}"
            self.set_state("pm_principal_id", pm_id)

    def set_state(self, key: str, value: str) -> None:
        self.connection.execute(
            """
            INSERT INTO node_state(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        self.connection.commit()

    def state(self, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM node_state WHERE key = ?", (key,)
        ).fetchone()
        return str(row["value"]) if row else None

    def node_id(self) -> str:
        value = self.state("node_id")
        assert value is not None
        return value

    def pm_principal_id(self) -> str:
        value = self.state("pm_principal_id")
        assert value is not None
        return value

    def new_agent_principal_id(self, local_name: str) -> str:
        return f"agent-{self.node_id().removeprefix('node-')}-{local_name}"

    def set_project_repository(self, project_id: str, path: str) -> dict[str, Any]:
        self.connection.execute(
            """INSERT INTO project_repositories(project_id, path) VALUES (?, ?)
               ON CONFLICT(project_id) DO UPDATE SET
                 path = excluded.path,
                 updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')""",
            (project_id, path),
        )
        self.connection.commit()
        return self.project_repository(project_id) or {}

    def project_repository(self, project_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM project_repositories WHERE project_id = ?", (project_id,)
        ).fetchone()
        return dict(row) if row else None

    def project_repositories(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM project_repositories ORDER BY project_id"
        ).fetchall()
        return [dict(row) for row in rows]

    def delete_project_repository(self, project_id: str) -> bool:
        cursor = self.connection.execute(
            "DELETE FROM project_repositories WHERE project_id = ?", (project_id,)
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def attach(self, local_name: str, candidate: CmuxAgentCandidate) -> dict[str, Any]:
        data = asdict(candidate)
        previous = self.connection.execute(
            "SELECT principal_id, agent_session_id FROM bindings WHERE local_name = ?",
            (local_name,),
        ).fetchone()
        # 창 하나에 에이전트 하나다. 같은 창을 다시 써서 새 세션을 띄우면 옛
        # binding은 갈 곳이 없다. 놔두면 서버의 창 단위 유일 제약에 걸려
        # sync가 통째로 409를 내고, 배정도 연결도 전부 막힌다. 화면에는
        # SQLite 제약 문구가 그대로 뜬다.
        self.connection.execute(
            """
            UPDATE bindings SET attached = 0,
              updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE surface_id = ? AND local_name != ? AND attached = 1
            """,
            (candidate.surface_id, local_name),
        )
        self.connection.execute(
            """
            INSERT INTO bindings(
              local_name, principal_id, provider, agent_session_id, surface_id,
              lifecycle, attached, data_json, lifecycle_changed_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(local_name) DO UPDATE SET
              principal_id = COALESCE(bindings.principal_id, excluded.principal_id),
              provider = excluded.provider,
              agent_session_id = excluded.agent_session_id,
              surface_id = excluded.surface_id,
              lifecycle = excluded.lifecycle,
              attached = 1,
              data_json = excluded.data_json,
              lifecycle_changed_at = CASE
                WHEN bindings.lifecycle != excluded.lifecycle
                THEN strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                ELSE bindings.lifecycle_changed_at
              END,
              updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (
                local_name,
                self.new_agent_principal_id(local_name),
                candidate.provider,
                candidate.agent_session_id,
                candidate.surface_id,
                candidate.lifecycle,
                json.dumps(data),
            ),
        )
        # 세션이 갈렸으면 앞 세션에 보낸 깨우기는 확인될 리가 없다. 받을 상대가
        # 이미 없는데 게이트는 그것을 미확인으로 세고, 시한이 다 갈 때까지 다음
        # 깨우기를 내보내지 않는다 — 새로 붙인 에이전트가 붙자마자 10분을
        # 기다리는 이유가 이것이었다. 같은 세션을 다시 확인할 때는 건드리지
        # 않는다. 살아 있는 깨우기를 지우면 같은 자리를 두 번 찌른다.
        if (
            previous is not None
            and previous["principal_id"]
            and previous["agent_session_id"] != candidate.agent_session_id
        ):
            self.connection.execute(
                """
                UPDATE wake_attempts SET status = 'superseded',
                  processed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE recipient_id = ? AND status = 'sent'
                """,
                (str(previous["principal_id"]),),
            )
        self.connection.commit()
        return {"local_name": local_name, **candidate.public_dict()}

    def attach_hosted(
        self, local_name: str, principal_id: str, provider: str, session_id: str,
        host_pid: int, cwd: str, project_id: str,
        model: str | None = None, reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        existing_name = self.binding(local_name)
        if (
            existing_name is not None
            and existing_name.get("principal_id") != principal_id
        ):
            raise ValueError(
                f"hosted local name is already bound: {local_name}"
            )
        existing_principal = self.binding_for_principal(principal_id)
        if (
            existing_principal is not None
            and existing_principal.get("local_name") != local_name
        ):
            raise ValueError(
                f"hosted principal is already bound: {principal_id}"
            )
        data = {
            "terminal_provider": "fungis-app",
            "terminal_session_id": session_id,
            "hosted": True,
            "host_pid": host_pid,
            "cwd": cwd,
            "project_id": project_id,
            "model": model,
            "reasoning_effort": reasoning_effort,
        }
        self.connection.execute(
            """
            INSERT INTO bindings(
              local_name, principal_id, provider, agent_session_id, surface_id,
              lifecycle, attached, data_json, lifecycle_changed_at
            ) VALUES (?, ?, ?, ?, ?, 'idle', 1, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(local_name) DO UPDATE SET
              principal_id = excluded.principal_id,
              provider = excluded.provider,
              agent_session_id = excluded.agent_session_id,
              surface_id = excluded.surface_id,
              lifecycle = 'idle',
              attached = 1,
              data_json = excluded.data_json,
              updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (
                local_name, principal_id, provider, session_id,
                f"hosted:{session_id}", json.dumps(data),
            ),
        )
        self.connection.commit()
        return self.binding(local_name) or {}

    def recoverable_hosted(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM bindings ORDER BY updated_at, local_name"
        ).fetchall()
        result = []
        for row in rows:
            value = dict(row)
            try:
                data = json.loads(value.get("data_json") or "{}")
            except json.JSONDecodeError:
                continue
            if not data.get("hosted"):
                continue
            project_id = (
                data.get("project_id")
                or self.state(f"active_project:{value['principal_id']}")
            )
            repository = self.project_repository(project_id) if project_id else None
            result.append({
                "local_name": value["local_name"],
                "principal_id": value["principal_id"],
                "provider": value["provider"],
                "session_id": value["agent_session_id"],
                "cwd": data.get("cwd") or (repository or {}).get("path"),
                "project_id": project_id,
                "model": data.get("model"),
                "reasoning_effort": data.get("reasoning_effort"),
                "attached": bool(value["attached"]),
                "host_pid": data.get("host_pid"),
            })
        return result

    def forget_hosted(self, principal_id: str) -> bool:
        row = self.connection.execute(
            "SELECT data_json FROM bindings WHERE principal_id = ?", (principal_id,)
        ).fetchone()
        if row is None:
            return False
        try:
            hosted = bool(json.loads(row["data_json"] or "{}").get("hosted"))
        except json.JSONDecodeError:
            hosted = False
        if not hosted:
            return False
        cursor = self.connection.execute(
            "DELETE FROM bindings WHERE principal_id = ?", (principal_id,)
        )
        self.connection.execute(
            "DELETE FROM node_state WHERE key = ?", (f"active_project:{principal_id}",)
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def refresh_candidate(
        self, local_name: str, candidate: CmuxAgentCandidate
    ) -> dict[str, Any]:
        row = self.connection.execute(
            """SELECT principal_id, surface_id FROM bindings
               WHERE local_name = ? AND attached = 1""",
            (local_name,),
        ).fetchone()
        if row is None:
            raise LookupError(f"active binding not found: {local_name}")
        if row["surface_id"] != candidate.surface_id:
            self.connection.execute(
                """UPDATE wake_attempts SET status = 'superseded',
                   processed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                   WHERE recipient_id = ? AND status = 'sent'""",
                (row["principal_id"],),
            )
            self.connection.commit()
        return self.attach(local_name, candidate)

    def detach(self, local_name: str) -> bool:
        cursor = self.connection.execute(
            """
            UPDATE bindings SET attached = 0,
              updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE local_name = ? AND attached = 1
            """,
            (local_name,),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def set_nickname(self, local_name: str, nickname: str | None) -> dict[str, Any]:
        value = nickname.strip() if nickname else None
        cursor = self.connection.execute(
            """
            UPDATE bindings SET nickname = ?,
              updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE local_name = ? AND attached = 1
            """,
            (value or None, local_name),
        )
        self.connection.commit()
        if cursor.rowcount != 1:
            raise LookupError(f"active binding not found: {local_name}")
        return self.binding(local_name) or {}

    def list(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM bindings WHERE attached = 1 ORDER BY local_name"
        ).fetchall()
        return [dict(row) for row in rows]

    def binding(self, local_name: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT * FROM bindings
            WHERE local_name = ? AND attached = 1
            """,
            (local_name,),
        ).fetchone()
        return dict(row) if row else None

    def binding_for_surface(self, surface_id: str) -> dict[str, Any] | None:
        rows = self.connection.execute(
            "SELECT * FROM bindings WHERE surface_id = ? AND attached = 1",
            (surface_id,),
        ).fetchall()
        if len(rows) > 1:
            raise LookupError("multiple active bindings for current surface")
        return dict(rows[0]) if rows else None

    def binding_for_principal(self, principal_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT * FROM bindings
            WHERE principal_id = ? AND attached = 1
            """,
            (principal_id,),
        ).fetchone()
        return dict(row) if row else None

    def recipient_key(self, identity: str) -> str:
        binding = self.binding(identity)
        return str(binding["principal_id"]) if binding else identity

