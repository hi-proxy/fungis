from __future__ import annotations

import json
import hashlib
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


# 카드가 떠 있는 동안 입력창이 막힌다. 터미널에서 사람이 답해도 서버는 그걸
# 모르므로, 사람이 답할 만한 시간이 지나면 스스로 걷는다. 너무 길면 이미 푼
# 방을 계속 막고, 너무 짧으면 아직 멈춰 있는데 열어 준다.
PERMISSION_REQUEST_TTL_SECONDS = 90


# HQ도 티켓을 부를 이름이 있어야 한다. 참조 표기가 방 이름을 쓰니 HQ만
# 예외로 두면 거기 티켓은 부를 말이 없다. 예약어라 다른 방은 못 가진다.
HQ_TICKET_PREFIX = "HQ"


SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS principals (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('human', 'agent')),
    display_name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS pm_profiles (
    principal_id TEXT PRIMARY KEY REFERENCES principals(id) ON DELETE CASCADE,
    display_name TEXT NOT NULL DEFAULT 'PM',
    avatar BLOB,
    avatar_media_type TEXT,
    avatar_updated_at TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS client_nodes (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    last_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS agent_bindings (
    agent_id TEXT PRIMARY KEY REFERENCES principals(id) ON DELETE CASCADE,
    node_id TEXT NOT NULL REFERENCES client_nodes(id) ON DELETE CASCADE,
    agent_provider TEXT NOT NULL,
    agent_session_id TEXT NOT NULL,
    terminal_provider TEXT NOT NULL,
    terminal_session_id TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (
        lifecycle IN ('running', 'idle', 'needs_input', 'unknown')
    ),
    attached INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(node_id, terminal_provider, terminal_session_id)
);

CREATE TABLE IF NOT EXISTS messages (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    workspace_id TEXT NOT NULL,
    sender_id TEXT NOT NULL REFERENCES principals(id),
    body TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'message' CHECK (kind IN ('message', 'pm_request')),
    reply_level TEXT NOT NULL DEFAULT 'r1' CHECK (reply_level IN ('r1', 'r2', 'r3')),
    in_reply_to INTEGER REFERENCES messages(seq),
    track TEXT,
    project_seq INTEGER,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS inbox (
    recipient_id TEXT NOT NULL REFERENCES principals(id),
    message_seq INTEGER NOT NULL REFERENCES messages(seq) ON DELETE CASCADE,
    received_at TEXT,
    processed_at TEXT,
    PRIMARY KEY (recipient_id, message_seq)
);

CREATE TABLE IF NOT EXISTS message_references (
    principal_id TEXT NOT NULL REFERENCES principals(id),
    message_seq INTEGER NOT NULL REFERENCES messages(seq) ON DELETE CASCADE,
    PRIMARY KEY (principal_id, message_seq)
);

CREATE TABLE IF NOT EXISTS permission_requests (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    agent_id TEXT REFERENCES principals(id),
    tool_name TEXT NOT NULL,
    tool_input TEXT NOT NULL,
    suggestions TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'allowed', 'denied', 'expired')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    resolved_at TEXT,
    resolved_by TEXT REFERENCES principals(id)
);

CREATE TABLE IF NOT EXISTS message_tags (
    message_seq INTEGER NOT NULL REFERENCES messages(seq) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (message_seq, tag)
);

CREATE INDEX IF NOT EXISTS message_tags_tag_seq
ON message_tags(tag, message_seq);

CREATE TABLE IF NOT EXISTS message_bookmarks (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    message_seq INTEGER NOT NULL REFERENCES messages(seq) ON DELETE CASCADE,
    label TEXT NOT NULL,
    created_by TEXT NOT NULL REFERENCES principals(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(workspace_id, message_seq, label)
);

CREATE INDEX IF NOT EXISTS message_bookmarks_workspace_seq
ON message_bookmarks(workspace_id, message_seq);

CREATE TABLE IF NOT EXISTS timeline_pins (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    after_message_seq INTEGER NOT NULL REFERENCES messages(seq) ON DELETE CASCADE,
    label TEXT NOT NULL,
    created_by TEXT NOT NULL REFERENCES principals(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(workspace_id, after_message_seq)
);

CREATE INDEX IF NOT EXISTS timeline_pins_workspace_seq
ON timeline_pins(workspace_id, after_message_seq);

CREATE TABLE IF NOT EXISTS workspace_roles (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    onboarding_prompt TEXT NOT NULL DEFAULT '',
    -- 그 방을 대표해 다른 방의 물음을 받는 자리. 방마다 하나다. 둘을
    -- 허용하면 "누구에게 물어야 하나"가 그대로 남는데, lead는 바로 그
    -- 물음을 없애려고 있다.
    is_lead INTEGER NOT NULL DEFAULT 0,
    avatar BLOB,
    avatar_media_type TEXT,
    avatar_updated_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS unique_active_role_name
ON workspace_roles(workspace_id, name) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS role_assignments (
    id TEXT PRIMARY KEY,
    role_id TEXT NOT NULL REFERENCES workspace_roles(id),
    workspace_id TEXT NOT NULL,
    agent_id TEXT NOT NULL REFERENCES principals(id),
    assigned_by TEXT NOT NULL REFERENCES principals(id),
    assigned_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ended_at TEXT,
    onboarding_sent INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS one_active_assignment_per_role
ON role_assignments(role_id) WHERE ended_at IS NULL;

CREATE TABLE IF NOT EXISTS message_role_recipients (
    role_id TEXT NOT NULL REFERENCES workspace_roles(id),
    message_seq INTEGER NOT NULL REFERENCES messages(seq) ON DELETE CASCADE,
    delivered_agent_id TEXT REFERENCES principals(id),
    delivered_at TEXT,
    PRIMARY KEY (role_id, message_seq)
);

CREATE INDEX IF NOT EXISTS inbox_recipient_seq
ON inbox(recipient_id, message_seq);

CREATE TABLE IF NOT EXISTS delivery_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    recipient_id TEXT NOT NULL REFERENCES principals(id),
    kind TEXT NOT NULL,
    through_message_seq INTEGER NOT NULL REFERENCES messages(seq),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS delivery_recipient_seq
ON delivery_events(recipient_id, seq);

CREATE TABLE IF NOT EXISTS shared_values (
    workspace_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    updated_by TEXT NOT NULL REFERENCES principals(id),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (workspace_id, key)
);

-- 상황보드. HQ 하나에 붙는다.
--
-- 노드는 각 프로젝트가 자기 트랙에 올린다. 간선은 노드 사이를 잇는다.
-- 올리는 것도 잇는 것도 PM과 lead 모두 할 수 있어서 승인 자리가 없다.
--
-- 보드가 하나뿐이라 노드에 보드 id를 두지 않는다. 둘째가 생기면 그때 판다.
CREATE TABLE IF NOT EXISTS board_nodes (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    title TEXT NOT NULL,
    -- 대기는 여기 없다. 선행이 안 끝났으면 대기로 읽는 것이지 사람이
    -- 정하는 값이 아니다. 저장하면 선행과 어긋난다.
    status TEXT NOT NULL DEFAULT 'todo'
        CHECK (status IN ('todo', 'active', 'done')),
    created_by TEXT NOT NULL REFERENCES principals(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS board_nodes_project ON board_nodes(project_id);

-- 선행은 기다리는 쪽의 것이다. 그래서 간선을 긋는 것은 남의 노드가 아니라
-- 자기 노드를 고치는 일이 된다. 간선에 주인을 따로 정할 필요가 없다.
CREATE TABLE IF NOT EXISTS board_edges (
    node_id TEXT NOT NULL REFERENCES board_nodes(id) ON DELETE CASCADE,
    waits_for TEXT NOT NULL REFERENCES board_nodes(id) ON DELETE CASCADE,
    created_by TEXT NOT NULL REFERENCES principals(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (node_id, waits_for),
    CHECK (node_id <> waits_for)
);

CREATE TABLE IF NOT EXISTS work_items (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    agent_id TEXT NOT NULL REFERENCES principals(id),
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'done')),
    last_report TEXT,
    started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ended_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS one_active_work_per_agent
ON work_items(agent_id) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS work_reports (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id TEXT NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('report', 'done')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""


class FungisDB:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.path, check_same_thread=False, isolation_level=None
        )
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.executescript(SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        columns = {
            row["name"]
            for row in self._connection.execute("PRAGMA table_info(messages)")
        }
        if "kind" not in columns:
            self._connection.execute(
                "ALTER TABLE messages ADD COLUMN kind TEXT NOT NULL DEFAULT 'message'"
            )
        if "reply_level" not in columns:
            self._connection.execute(
                "ALTER TABLE messages ADD COLUMN reply_level TEXT NOT NULL DEFAULT 'r1'"
            )
        if "in_reply_to" not in columns:
            self._connection.execute(
                "ALTER TABLE messages ADD COLUMN in_reply_to INTEGER"
            )
        if "track" not in columns:
            self._connection.execute("ALTER TABLE messages ADD COLUMN track TEXT")
        if "project_seq" not in columns:
            # seq는 전역 단조 번호라 한 방만 보면 띄엄띄엄해진다. 에이전트가
            # 그걸 누락으로 읽고 확인 작업을 하므로 방마다 1부터 세는 표시
            # 번호를 따로 둔다. 저장된 참조(핀·북마크·in_reply_to)는 전역
            # seq를 그대로 쓴다.
            self._connection.execute(
                "ALTER TABLE messages ADD COLUMN project_seq INTEGER"
            )
            self._connection.execute(
                """UPDATE messages SET project_seq = (
                       SELECT COUNT(*) FROM messages older
                       WHERE older.workspace_id = messages.workspace_id
                         AND older.seq <= messages.seq
                   ) WHERE project_seq IS NULL"""
            )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_project_seq"
            " ON messages(workspace_id, project_seq)"
        )
        role_columns = {
            row["name"]
            for row in self._connection.execute("PRAGMA table_info(workspace_roles)")
        }
        if "avatar" not in role_columns:
            self._connection.execute("ALTER TABLE workspace_roles ADD COLUMN avatar BLOB")
        if "avatar_media_type" not in role_columns:
            self._connection.execute(
                "ALTER TABLE workspace_roles ADD COLUMN avatar_media_type TEXT"
            )
        if "avatar_updated_at" not in role_columns:
            self._connection.execute(
                "ALTER TABLE workspace_roles ADD COLUMN avatar_updated_at TEXT"
            )
        assignment_columns = {
            row["name"]
            for row in self._connection.execute("PRAGMA table_info(role_assignments)")
        }
        if "workspace_id" not in assignment_columns:
            self._connection.execute(
                "ALTER TABLE role_assignments ADD COLUMN workspace_id TEXT"
            )
            self._connection.execute(
                """UPDATE role_assignments SET workspace_id = (
                       SELECT workspace_id FROM workspace_roles
                       WHERE workspace_roles.id = role_assignments.role_id
                   ) WHERE workspace_id IS NULL"""
            )
        self._connection.execute("DROP INDEX IF EXISTS one_active_role_per_agent")
        self._connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS one_active_role_per_agent_per_project
               ON role_assignments(workspace_id, agent_id) WHERE ended_at IS NULL"""
        )
        if "is_lead" not in role_columns:
            self._connection.execute(
                "ALTER TABLE workspace_roles ADD COLUMN is_lead INTEGER NOT NULL DEFAULT 0"
            )
        self._connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS one_lead_per_project
               ON workspace_roles(workspace_id)
               WHERE is_lead = 1 AND deleted_at IS NULL"""
        )
        project_columns = {
            row["name"]
            for row in self._connection.execute("PRAGMA table_info(projects)")
        }
        if "kind" not in project_columns:
            self._connection.execute(
                "ALTER TABLE projects ADD COLUMN kind TEXT NOT NULL DEFAULT 'project'"
            )
        if "parent_id" not in project_columns:
            # 보드에 연결된 프로젝트만 부모를 갖는다. 연결은 명시적인 일이라
            # 안 붙은 프로젝트는 NULL로 남는다.
            self._connection.execute("ALTER TABLE projects ADD COLUMN parent_id TEXT")
        # 닫힌 HQ가 자리를 붙들면 새 HQ를 영영 못 만든다. 이 코드의 다른
        # "하나만" 인덱스 중 삭제 술어를 가진 것은 unique_active_role_name
        # 하나뿐인데, 여기서는 그쪽을 본보기로 삼는다.
        if "ticket_prefix" not in project_columns:
            # 티켓 이름은 방마다 다르다. 프리픽스가 방을 들고 다니므로
            # ARCH-12 한 토큰이 어느 방 몇 번인지 다 말한다. 방 이름을 바꿔도
            # 프리픽스는 안 따라간다 — 이미 붙은 티켓 이름이 흔들리면 안 된다.
            self._connection.execute(
                "ALTER TABLE projects ADD COLUMN ticket_prefix TEXT"
            )
        self._connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS one_prefix_per_board
               ON projects(ticket_prefix) WHERE ticket_prefix IS NOT NULL"""
        )
        node_columns = {
            row["name"]
            for row in self._connection.execute("PRAGMA table_info(board_nodes)")
        }
        if "number" not in node_columns:
            # 방 안에서 1부터 센다. 전역으로 세면 방이 독립인데 번호만 HQ가
            # 나눠주는 꼴이 되고, 보드에서 뗀 방의 번호가 구멍으로 남는다.
            self._connection.execute("ALTER TABLE board_nodes ADD COLUMN number INTEGER")
            self._backfill_ticket_numbers()
        self._connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS one_number_per_project
               ON board_nodes(project_id, number) WHERE number IS NOT NULL"""
        )
        self._connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS one_live_hq
               ON projects(kind) WHERE kind = 'hq' AND archived_at IS NULL"""
        )
        self._connection.execute(
            "INSERT OR IGNORE INTO projects(id, name) VALUES ('local', 'Local')"
        )
        # HQ는 만드는 것이 아니라 있는 것이다. 만들게 하면 "아직 없음" 상태가
        # 생기고, 그 상태를 화면과 API가 각각 다뤄야 한다. 처음부터 두면
        # 그 갈래가 통째로 사라진다.
        self._connection.execute(
            "INSERT OR IGNORE INTO projects(id, name, kind) VALUES ('hq', 'HQ', 'hq')"
        )
        self._backfill_ticket_prefixes()
        for table in ("workspace_roles", "messages", "shared_values", "work_items"):
            rows = self._connection.execute(
                f"SELECT DISTINCT workspace_id FROM {table} WHERE workspace_id IS NOT NULL"
            ).fetchall()
            for row in rows:
                workspace_id = str(row["workspace_id"])
                self._connection.execute(
                    "INSERT OR IGNORE INTO projects(id, name) VALUES (?, ?)",
                    (workspace_id, workspace_id if workspace_id != "local" else "Local"),
                )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
            else:
                self._connection.execute("COMMIT")

    def create_principal(
        self, *, kind: str, display_name: str, principal_id: str | None = None
    ) -> dict[str, Any]:
        principal_id = principal_id or str(uuid.uuid4())
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO principals(id, kind, display_name) VALUES (?, ?, ?)",
                (principal_id, kind, display_name),
            )
            row = conn.execute(
                "SELECT * FROM principals WHERE id = ?", (principal_id,)
            ).fetchone()
        return dict(row)

    def upsert_principal(
        self, *, principal_id: str, kind: str, display_name: str
    ) -> dict[str, Any]:
        with self.transaction() as conn:
            if kind == "human":
                profile = conn.execute(
                    "SELECT display_name FROM pm_profiles WHERE principal_id = ?",
                    (principal_id,),
                ).fetchone()
                if profile is not None:
                    display_name = str(profile["display_name"])
            conn.execute(
                """
                INSERT INTO principals(id, kind, display_name) VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  kind = excluded.kind,
                  display_name = excluded.display_name
                """,
                (principal_id, kind, display_name),
            )
            row = conn.execute(
                "SELECT * FROM principals WHERE id = ?", (principal_id,)
            ).fetchone()
        return dict(row)

    def create_permission_request(
        self, *, workspace_id: str, session_id: str, agent_id: str | None,
        tool_name: str, tool_input: str, suggestions: str | None,
    ) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO permission_requests(
                       id, workspace_id, session_id, agent_id,
                       tool_name, tool_input, suggestions
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    request_id, workspace_id, session_id, agent_id,
                    tool_name, tool_input, suggestions,
                ),
            )
            row = conn.execute(
                "SELECT * FROM permission_requests WHERE id = ?", (request_id,)
            ).fetchone()
        return dict(row)

    def permission_request(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """SELECT r.*, p.display_name AS agent_name
                   FROM permission_requests r
                   LEFT JOIN principals p ON p.id = r.agent_id
                   WHERE r.id = ?""",
                (request_id,),
            ).fetchone()
        return dict(row) if row else None

    def pending_permission_requests(self, workspace_id: str) -> list[dict[str, Any]]:
        # 묻는 쪽은 정해진 시간만 기다리다 비켜선다. 그 뒤에도 pending으로 남은
        # 것은 답을 받아갈 프로세스가 없다는 뜻이다 — 게이트가 죽었거나 서버가
        # 다시 떴거나 터미널이 닫혔다. 그대로 두면 PM 화면에 눌러도 아무 일도
        # 없는 카드가 쌓인다. 읽을 때 걷어낸다. 따로 도는 것을 두지 않는다.
        with self.transaction() as conn:
            conn.execute(
                """UPDATE permission_requests SET status = 'expired',
                     resolved_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                   WHERE workspace_id = ? AND status = 'pending'
                     AND created_at < strftime(
                       '%Y-%m-%dT%H:%M:%fZ', 'now', ?
                     )""",
                (workspace_id, f"-{PERMISSION_REQUEST_TTL_SECONDS} seconds"),
            )
        with self._lock:
            rows = self._connection.execute(
                """SELECT r.*, p.display_name AS agent_name
                   FROM permission_requests r
                   LEFT JOIN principals p ON p.id = r.agent_id
                   WHERE r.workspace_id = ? AND r.status = 'pending'
                   ORDER BY r.created_at ASC""",
                (workspace_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def resolve_permission_request(
        self, *, request_id: str, status: str, resolved_by: str | None,
    ) -> dict[str, Any] | None:
        if status not in ("allowed", "denied", "expired"):
            raise ValueError(f"unknown status: {status}")
        with self.transaction() as conn:
            # 이미 답이 나온 요청은 덮어쓰지 않는다. 사람이 누른 답과 시간
            # 초과가 겹쳤을 때 먼저 온 쪽을 지킨다.
            conn.execute(
                """UPDATE permission_requests
                   SET status = ?,
                       resolved_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                       resolved_by = ?
                   WHERE id = ? AND status = 'pending'""",
                (status, resolved_by, request_id),
            )
            row = conn.execute(
                "SELECT * FROM permission_requests WHERE id = ?", (request_id,)
            ).fetchone()
        return dict(row) if row else None

    def principal_kind(self, principal_id: str) -> str | None:
        """없는 신원은 None. 판정하는 쪽이 없음과 에이전트를 구별해야 한다."""
        with self._lock:
            row = self._connection.execute(
                "SELECT kind FROM principals WHERE id = ?", (principal_id,)
            ).fetchone()
            return row["kind"] if row else None

    def is_hq(self, workspace_id: str) -> bool:
        with self._lock:
            return self._connection.execute(
                "SELECT 1 FROM projects WHERE id = ? AND kind = 'hq'", (workspace_id,)
            ).fetchone() is not None

    def convened_leads(self) -> list[str]:
        """소집된 방들의 lead 담당자. HQ의 명부다."""
        with self._lock:
            return [
                row["agent_id"]
                for row in self._connection.execute(
                    """SELECT DISTINCT a.agent_id FROM workspace_roles r
                       JOIN role_assignments a
                         ON a.role_id = r.id AND a.ended_at IS NULL
                       JOIN projects p ON p.id = r.workspace_id
                       WHERE r.is_lead = 1 AND r.deleted_at IS NULL
                         AND p.parent_id IS NOT NULL AND p.archived_at IS NULL
                       ORDER BY a.agent_id"""
                )
            ]

    def workspace_participant(self, *, workspace_id: str, principal_id: str) -> bool:
        """이 사람이 그 방의 대화를 읽어도 되나.

        지키려는 것은 대화지 명단이 아니다. 명단은 init으로 볼 수 있다 —
        들어가려면 이미 안에 있어야 하는 꼴이 되면 아무도 못 들어온다.

        참가 = 역할 보유로 본다. 지금 모델에서 방에 있다는 것을 말하는 다른
        수단이 없다. 사람은 통과시킨다. PM은 어느 방에도 역할로 적혀 있지
        않지만 모든 방을 본다.

        HQ만 규칙이 다르다. 거기 구성원은 역할이 아니라 소집된 방의 lead다.

        여기서 막는 것은 실수다. 신원은 자기 신고라 작정하면 우회된다.
        그건 서버가 이 기계를 벗어날 때 인증으로 풀 일이고, 그때 이 검사는
        뜯지 않고 그 위에 얹힌다.
        """
        with self._lock:
            row = self._connection.execute(
                "SELECT kind FROM principals WHERE id = ?", (principal_id,)
            ).fetchone()
            if row is None:
                return False
            if row["kind"] == "human":
                return True
            # HQ에는 역할이 없다. 소집은 방을 붙이는 것이지 HQ에 역할을 만드는
            # 것이 아니라, 역할 보유로만 보면 모든 에이전트가 막힌다. HQ의
            # 구성원은 소집된 방의 lead다.
            hq = self._connection.execute(
                "SELECT 1 FROM projects WHERE id = ? AND kind = 'hq'", (workspace_id,)
            ).fetchone()
            if hq is not None and self._connection.execute(
                    """SELECT 1 FROM workspace_roles r
                       JOIN role_assignments a
                         ON a.role_id = r.id AND a.ended_at IS NULL
                       JOIN projects p ON p.id = r.workspace_id
                       WHERE r.is_lead = 1 AND r.deleted_at IS NULL
                         AND p.parent_id IS NOT NULL AND p.archived_at IS NULL
                         AND a.agent_id = ?
                       LIMIT 1""",
                (principal_id,),
            ).fetchone() is not None:
                return True
            # HQ에 직접 역할을 가진 경우는 그대로 통과한다. lead 규칙은 그 위에
            # 더하는 것이지 대신하는 것이 아니다.
            return self._connection.execute(
                """SELECT 1 FROM role_assignments
                   WHERE workspace_id = ? AND agent_id = ? AND ended_at IS NULL
                   LIMIT 1""",
                (workspace_id, principal_id),
            ).fetchone() is not None

    def is_any_lead(self, principal_id: str) -> bool:
        """어느 방이든 lead 자리에 앉아 있나."""
        with self._lock:
            return self._connection.execute(
                """SELECT 1 FROM workspace_roles r
                   JOIN role_assignments a
                     ON a.role_id = r.id AND a.ended_at IS NULL
                   WHERE r.is_lead = 1 AND r.deleted_at IS NULL AND a.agent_id = ?
                   LIMIT 1""",
                (principal_id,),
            ).fetchone() is not None

    def project_name(self, project_id: str) -> str:
        """없으면 ID를 그대로 돌려준다. 거절 문구가 빈칸으로 나가면 안 된다."""
        with self._lock:
            row = self._connection.execute(
                "SELECT name FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        return row["name"] if row else project_id

    def principal_projects(self, principal_id: str) -> list[dict[str, Any]]:
        """이 에이전트가 지금 활성 배정을 가진 방들."""
        with self._lock:
            rows = self._connection.execute(
                """SELECT DISTINCT p.id, p.name FROM role_assignments a
                   JOIN projects p ON p.id = a.workspace_id
                   WHERE a.agent_id = ? AND a.ended_at IS NULL
                     AND p.archived_at IS NULL
                   ORDER BY p.name""",
                (principal_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def participation_denied(
        self, *, workspace_id: str, principal_id: str
    ) -> str:
        """거절할 때 왜 막혔는지와 다음에 무엇을 할지 같이 말한다.

        "not a participant" 만 돌려주면 받은 쪽은 자기가 어디 소속인지도,
        누구에게 물어야 하는지도 모른 채 같은 명령을 다시 친다.
        """
        room = self.project_name(workspace_id)
        if self.is_hq(workspace_id):
            return (
                f'"{room}" 는 소집된 방의 lead 만 쓴다. 너는 지금 어느 방의 '
                "lead 도 아니다. 네 방 lead 를 통하거나 PM 에게 요청하라."
            )
        mine = [item["name"] for item in self.principal_projects(principal_id)]
        if mine:
            joined = ", ".join(f'"{name}"' for name in mine)
            return f'너는 "{room}" 소속이 아니다. 속한 프로젝트는 {joined} 이다.'
        return (
            f'너는 "{room}" 소속이 아니다. 속한 프로젝트가 하나도 없다. '
            "PM 에게 배정을 요청하라."
        )

    def members(self, workspace_id: str) -> dict[str, Any]:
        """방 하나의 역할·담당자·lead. 명단은 대화와 달리 lead 가 건너서 본다."""
        roles = [
            {
                "role_id": role["id"],
                "name": role["name"],
                "is_lead": role["is_lead"],
                "agent_id": role.get("agent_id"),
                "agent_name": role.get("agent_name"),
            }
            for role in self.roles(workspace_id)
        ]
        return {
            "project_id": workspace_id,
            "project_name": self.project_name(workspace_id),
            "lead": next((role for role in roles if role["is_lead"]), None),
            "roles": roles,
        }

    # ---- 상황보드 ----------------------------------------------------------

    def board(self) -> list[dict[str, Any]]:
        """트랙 단위로 묶어 돌려준다. 연결된 프로젝트만 트랙이 된다.

        노드가 없는 트랙도 돌려준다. 연결은 했는데 아직 아무것도 안 올린
        상태가 보여야 PM이 비었다는 걸 안다.
        """
        with self._lock:
            tracks = self._connection.execute(
                """SELECT id, name, ticket_prefix FROM projects
                   WHERE parent_id IS NOT NULL AND archived_at IS NULL
                   ORDER BY name"""
            ).fetchall()
            nodes = self._connection.execute(
                "SELECT * FROM board_nodes ORDER BY created_at, id"
            ).fetchall()
            edges = self._connection.execute(
                "SELECT node_id, waits_for FROM board_edges"
            ).fetchall()
        done = {row["id"] for row in nodes if row["status"] == "done"}
        waits: dict[str, list[str]] = {}
        # 역방향도 같이 만든다. 선행 쪽이 자기가 누구를 막고 있는지 모르면
        # 끝내고 알릴 상대를 알 수 없다.
        blocks: dict[str, list[str]] = {}
        for edge in edges:
            waits.setdefault(edge["node_id"], []).append(edge["waits_for"])
            blocks.setdefault(edge["waits_for"], []).append(edge["node_id"])
        by_track: dict[str, list[dict[str, Any]]] = {}
        for row in nodes:
            node = dict(row)
            node["waits_for"] = sorted(waits.get(node["id"], []))
            blocked = [item for item in node["waits_for"] if item not in done]
            # 대기는 저장하지 않고 여기서 읽는다. 안 시작했는데 선행이 남아
            # 있으면 못 하는 것이지 안 하는 것이 아니다.
            node["blocked_by"] = sorted(blocked)
            node["blocks"] = sorted(blocks.get(node["id"], []))
            node["state"] = (
                "waiting" if node["status"] == "todo" and blocked else node["status"]
            )
            by_track.setdefault(node["project_id"], []).append(node)
        return [
            {
                "project_id": track["id"],
                "project_name": track["name"],
                "ticket_prefix": track["ticket_prefix"],
                "nodes": by_track.get(track["id"], []),
            }
            for track in tracks
        ]

    def board_candidates(self) -> list[dict[str, Any]]:
        """소집 화면이 한 번에 필요한 것. 목록·연결 여부·lead·배정된 역할.

        PM은 "누구를 부를까"를 고르는 자리에서 "부를 수 있나"까지 같이 봐야
        한다. 두 번 부르게 하면 화면이 두 상태를 조립해야 하고, 그 사이에
        어긋난다.
        """
        with self._lock:
            projects = self._connection.execute(
                """SELECT id, name, parent_id FROM projects
                   WHERE archived_at IS NULL AND kind != 'hq'
                   ORDER BY created_at, name"""
            ).fetchall()
            roles = self._connection.execute(
                """SELECT r.workspace_id, r.id, r.name, r.is_lead,
                          a.agent_id, p.display_name AS agent_name
                   FROM workspace_roles r
                   LEFT JOIN role_assignments a
                     ON a.role_id = r.id AND a.ended_at IS NULL
                   LEFT JOIN principals p ON p.id = a.agent_id
                   WHERE r.deleted_at IS NULL
                   ORDER BY r.name"""
            ).fetchall()
        by_project: dict[str, list[dict[str, Any]]] = {}
        for row in roles:
            item = dict(row)
            item["is_lead"] = bool(item["is_lead"])
            by_project.setdefault(item["workspace_id"], []).append(item)
        result = []
        for project in projects:
            items = by_project.get(project["id"], [])
            lead = next((item for item in items if item["is_lead"]), None)
            result.append({
                "id": project["id"],
                "name": project["name"],
                "connected": project["parent_id"] is not None,
                "lead": lead,
                # 배정된 역할만 준다. 사람이 없는 역할을 lead로 세우면
                # 조회가 빈손으로 돌아온다.
                "roles": [item for item in items if item["agent_id"]],
            })
        return result

    def create_board_node(
        self, *, project_id: str, title: str, created_by: str,
        status: str = "todo", node_id: str | None = None,
    ) -> dict[str, Any]:
        node_id = node_id or str(uuid.uuid4())
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT parent_id FROM projects WHERE id = ? AND archived_at IS NULL",
                (project_id,),
            ).fetchone()
            if row is None or row["parent_id"] is None:
                raise LookupError("project is not on the board")
            next_number = conn.execute(
                "SELECT COALESCE(MAX(number), 0) + 1 FROM board_nodes WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0]
            conn.execute(
                """INSERT INTO board_nodes(id, project_id, title, status, created_by, number)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (node_id, project_id, title, status, created_by, next_number),
            )
            return dict(
                conn.execute(
                    "SELECT * FROM board_nodes WHERE id = ?", (node_id,)
                ).fetchone()
            )

    def update_board_node(
        self, node_id: str, *, title: str | None = None, status: str | None = None
    ) -> dict[str, Any]:
        with self.transaction() as conn:
            current = conn.execute(
                "SELECT * FROM board_nodes WHERE id = ?", (node_id,)
            ).fetchone()
            if current is None:
                raise LookupError("node not found")
            conn.execute(
                """UPDATE board_nodes SET title = ?, status = ?,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                   WHERE id = ?""",
                (
                    current["title"] if title is None else title,
                    current["status"] if status is None else status,
                    node_id,
                ),
            )
            return dict(
                conn.execute(
                    "SELECT * FROM board_nodes WHERE id = ?", (node_id,)
                ).fetchone()
            )

    def delete_board_node(self, node_id: str) -> bool:
        with self.transaction() as conn:
            # 간선은 따라서 지워진다(ON DELETE CASCADE). 내리는 것은 수동이고
            # 사람이 판단하는 일이라 여기서 막지 않는다.
            return conn.execute(
                "DELETE FROM board_nodes WHERE id = ?", (node_id,)
            ).rowcount > 0

    def link_board_nodes(
        self, *, node_id: str, waits_for: str, created_by: str
    ) -> None:
        with self.transaction() as conn:
            if node_id == waits_for:
                raise ValueError("a node cannot wait for itself")
            found = conn.execute(
                "SELECT id FROM board_nodes WHERE id IN (?, ?)", (node_id, waits_for)
            ).fetchall()
            if len(found) != 2:
                raise LookupError("node not found")
            # 순환은 눈으로 못 잡는다. 이어 붙이기 전에 훑는다.
            edges: dict[str, list[str]] = {}
            for row in conn.execute("SELECT node_id, waits_for FROM board_edges"):
                edges.setdefault(row["node_id"], []).append(row["waits_for"])
            stack, seen = [waits_for], set()
            while stack:
                current = stack.pop()
                if current == node_id:
                    raise ValueError("that link would make a cycle")
                if current in seen:
                    continue
                seen.add(current)
                stack.extend(edges.get(current, []))
            conn.execute(
                """INSERT OR IGNORE INTO board_edges(node_id, waits_for, created_by)
                   VALUES (?, ?, ?)""",
                (node_id, waits_for, created_by),
            )

    def unlink_board_nodes(self, *, node_id: str, waits_for: str) -> bool:
        with self.transaction() as conn:
            return conn.execute(
                "DELETE FROM board_edges WHERE node_id = ? AND waits_for = ?",
                (node_id, waits_for),
            ).rowcount > 0

    def board_node_project(self, node_id: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT project_id FROM board_nodes WHERE id = ?", (node_id,)
            ).fetchone()
        return row["project_id"] if row else None

    def board_write_denied(self, *, project_id: str, actor_id: str) -> str | None:
        """보드에 써도 되면 None, 아니면 거절 문구.

        읽기는 모두에게 열려 있다. 쓰기만 그 방 lead 와 사람(PM)의 몫이다.
        아무나 쓰면 보드는 누가 무엇을 책임지는지 말하지 않는 목록이 된다.
        """
        if self.principal_kind(actor_id) == "human":
            return None
        lead = self.lead_of(project_id)
        if lead is not None and lead.get("agent_id") == actor_id:
            return None
        room = self.project_name(project_id)
        head = f'"{room}" 보드는 그 방 lead 나 PM 만 쓴다.'
        if lead is None or not lead.get("agent_id"):
            return f'{head} "{room}" 에는 지금 lead 가 없다. PM 에게 요청하라.'
        return f'{head} "{room}" lead 는 {lead["agent_name"]} 다. 그에게 부탁하라.'

    def resolve_room_id(self, given: str) -> str | None:
        """방 이름·티켓 프리픽스·ID 를 방 ID 로 푼다. 아니면 None.

        보드에서 읽은 `ARCH` 를 그대로 다시 쓸 수 있어야 한다. 한 번 더
        대조하게 만들면 그 대조에서 착오가 난다.
        """
        wanted = given.strip()
        if not wanted:
            return None
        with self._lock:
            rows = self._connection.execute(
                "SELECT id, name, ticket_prefix FROM projects WHERE archived_at IS NULL"
            ).fetchall()
        for row in rows:
            if row["id"] == wanted:
                return row["id"]
        for row in rows:
            if (row["ticket_prefix"] or "").upper() == wanted.upper():
                return row["id"]
        for row in rows:
            if row["name"].casefold() == wanted.casefold():
                return row["id"]
        return None

    def global_seq(self, *, workspace_id: str, project_seq: int) -> int | None:
        """방별 표시 번호를 전역 seq로 되돌린다. 경계에서만 쓴다."""
        with self._lock:
            row = self._connection.execute(
                "SELECT seq FROM messages WHERE workspace_id = ? AND project_seq = ?",
                (workspace_id, project_seq),
            ).fetchone()
        return int(row["seq"]) if row else None

    def projects(self) -> list[dict[str, Any]]:
        # last_message_seq는 방마다 어디까지 왔는지 알리는 파생값이다. 읽음
        # 여부는 클라이언트가 자기 커서와 대조해 판단한다.
        with self._lock:
            rows = self._connection.execute(
                """SELECT p.*,
                          (SELECT MAX(seq) FROM messages m WHERE m.workspace_id = p.id)
                              AS last_message_seq
                   FROM projects p
                   WHERE p.archived_at IS NULL
                   ORDER BY p.created_at, p.name"""
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def ticket_prefix_for(name: str, taken: set[str]) -> str:
        """방 이름에서 프리픽스를 만든다. 겹치면 숫자를 붙인다.

        사람이 고쳐도 되는 값이라 여기서는 첫 제안만 만든다. 라틴 문자가
        없으면 이름 앞을 그대로 쓴다 — 한글 방 이름도 있다.
        """
        # HQ는 HQ 방의 몫이라 다른 방에 내주지 않는다. 이름이 "HQ"로 시작하는
        # 방이 있어도 여기서 걸러 다음 후보로 넘어간다.
        taken = set(taken) | {HQ_TICKET_PREFIX}
        letters = [c for c in name.upper() if c.isalnum()]
        latin = [c for c in letters if c.isascii()]
        base = "".join((latin or letters)[:4]) or "T"
        if base not in taken:
            return base
        for suffix in range(2, 100):
            candidate = f"{base}{suffix}"
            if candidate not in taken:
                return candidate
        raise ValueError("no free ticket prefix")

    def _taken_ticket_prefixes(self) -> set[str]:
        """지금 쓰이고 있는 프리픽스 전부. 잠금을 잡은 쪽에서 부른다."""
        return {
            row["ticket_prefix"]
            for row in self._connection.execute(
                "SELECT ticket_prefix FROM projects WHERE ticket_prefix IS NOT NULL"
            )
        }

    def _backfill_ticket_numbers(self) -> None:
        """이미 있던 것에 만든 순서대로 번호를 붙인다. 지우고 다시 만들지 않는다."""
        rows = self._connection.execute(
            "SELECT id, project_id FROM board_nodes ORDER BY project_id, created_at, id"
        ).fetchall()
        counters: dict[str, int] = {}
        for row in rows:
            counters[row["project_id"]] = counters.get(row["project_id"], 0) + 1
            self._connection.execute(
                "UPDATE board_nodes SET number = ? WHERE id = ?",
                (counters[row["project_id"]], row["id"]),
            )

    def _backfill_ticket_prefixes(self) -> None:
        """프리픽스가 빈 방을 채운다. 새 방은 create_project 가 붙이므로
        여기 걸리는 것은 이 코드보다 먼저 만들어진 방과 'local' 뿐이다."""
        # 예약어를 이미 들고 있는 방이 있으면 먼저 비켜 세운다. HQ 규칙이
        # 생기기 전 backfill 이 나눠 줬을 수 있다.
        for row in self._connection.execute(
            "SELECT id FROM projects WHERE ticket_prefix = ? AND kind != 'hq'",
            (HQ_TICKET_PREFIX,),
        ).fetchall():
            self._connection.execute(
                "UPDATE projects SET ticket_prefix = NULL WHERE id = ?", (row["id"],)
            )
        self._connection.execute(
            "UPDATE projects SET ticket_prefix = ?"
            " WHERE kind = 'hq' AND ticket_prefix IS NULL",
            (HQ_TICKET_PREFIX,),
        )
        taken = self._taken_ticket_prefixes()
        for row in self._connection.execute(
            """SELECT id, name FROM projects
               WHERE ticket_prefix IS NULL AND kind != 'hq' ORDER BY created_at, id"""
        ).fetchall():
            prefix = self.ticket_prefix_for(row["name"], taken)
            taken.add(prefix)
            self._connection.execute(
                "UPDATE projects SET ticket_prefix = ? WHERE id = ?", (prefix, row["id"])
            )

    def create_project(self, *, name: str, project_id: str | None = None) -> dict[str, Any]:
        project_id = project_id or str(uuid.uuid4())
        with self.transaction() as conn:
            # 프리픽스는 방을 만들 때 붙는다. 나중에 붙이면 그 사이에 만든
            # 티켓이 부를 이름 없이 보드에 올라간다.
            conn.execute(
                "INSERT INTO projects(id, name, ticket_prefix) VALUES (?, ?, ?)",
                (
                    project_id,
                    name.strip(),
                    self.ticket_prefix_for(name.strip(), self._taken_ticket_prefixes()),
                ),
            )
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return dict(row)

    def update_project(self, *, project_id: str, name: str) -> dict[str, Any]:
        with self.transaction() as conn:
            cursor = conn.execute(
                "UPDATE projects SET name = ? WHERE id = ? AND archived_at IS NULL",
                (name.strip(), project_id),
            )
            if cursor.rowcount != 1:
                raise LookupError("project not found")
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return dict(row)

    def set_role_lead(self, *, role_id: str, is_lead: bool) -> dict[str, Any]:
        """lead 자리를 옮긴다. 방마다 하나라 앞 자리는 저절로 내려온다.

        인덱스에 맡기면 두 번째 지정이 오류로 튕긴다. PM이 원하는 것은
        "옮기기"지 "실패"가 아니라서 여기서 먼저 내린다.
        """
        with self.transaction() as conn:
            role = conn.execute(
                "SELECT workspace_id FROM workspace_roles"
                " WHERE id = ? AND deleted_at IS NULL",
                (role_id,),
            ).fetchone()
            if role is None:
                raise LookupError("role not found")
            if is_lead:
                conn.execute(
                    "UPDATE workspace_roles SET is_lead = 0"
                    " WHERE workspace_id = ? AND deleted_at IS NULL",
                    (role["workspace_id"],),
                )
            conn.execute(
                "UPDATE workspace_roles SET is_lead = ? WHERE id = ?",
                (1 if is_lead else 0, role_id),
            )
            return self._role_by_id(conn, role_id)

    def lead_of(self, project_id: str) -> dict[str, Any] | None:
        """그 방에 물으려면 누구에게 하나. 답은 늘 하나거나 없다."""
        with self._lock:
            row = self._connection.execute(
                """SELECT r.id AS role_id, r.name AS role_name, r.workspace_id,
                          a.agent_id, p.display_name AS agent_name
                   FROM workspace_roles r
                   LEFT JOIN role_assignments a
                     ON a.role_id = r.id AND a.ended_at IS NULL
                   LEFT JOIN principals p ON p.id = a.agent_id
                   WHERE r.workspace_id = ? AND r.deleted_at IS NULL AND r.is_lead = 1""",
                (project_id,),
            ).fetchone()
        return dict(row) if row else None

    def hq(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM projects WHERE kind = 'hq' AND archived_at IS NULL"
            ).fetchone()
        return dict(row) if row else None

    def connect_project(self, *, project_id: str, hq_id: str) -> dict[str, Any]:
        """프로젝트를 보드에 붙인다. lead가 있어야 붙는다.

        연결 시점에만 검사한다. 그 뒤에 lead가 빠질 수 있는데, 그때는 조회가
        빈손으로 돌아오고 PM이 HQ에서 그걸 본다. 폴백이 PM이라 따로 막지
        않는다.
        """
        with self.transaction() as conn:
            hq = conn.execute(
                "SELECT id FROM projects WHERE id = ? AND kind = 'hq'"
                " AND archived_at IS NULL",
                (hq_id,),
            ).fetchone()
            if hq is None:
                raise LookupError("hq not found")
            if project_id == hq_id:
                raise ValueError("hq cannot be a track of itself")
            lead = conn.execute(
                """SELECT 1 FROM workspace_roles r
                   JOIN role_assignments a ON a.role_id = r.id AND a.ended_at IS NULL
                   WHERE r.workspace_id = ? AND r.deleted_at IS NULL AND r.is_lead = 1
                   LIMIT 1""",
                (project_id,),
            ).fetchone()
            if lead is None:
                raise ValueError("the project needs a lead before it joins the board")
            cursor = conn.execute(
                "UPDATE projects SET parent_id = ? WHERE id = ? AND archived_at IS NULL",
                (hq_id, project_id),
            )
            if cursor.rowcount != 1:
                raise LookupError("project not found")
            return dict(
                conn.execute(
                    "SELECT * FROM projects WHERE id = ?", (project_id,)
                ).fetchone()
            )

    def disconnect_project(self, *, project_id: str) -> bool:
        with self.transaction() as conn:
            # 노드는 남긴다. 뗐다가 다시 붙이는 일이 흔하고, 지우면 그것을
            # 기다리던 남의 노드가 무엇을 기다렸는지 잃는다.
            return conn.execute(
                "UPDATE projects SET parent_id = NULL WHERE id = ? AND parent_id IS NOT NULL",
                (project_id,),
            ).rowcount > 0

    def archive_project(self, *, project_id: str) -> dict[str, Any]:
        """방을 목록에서 치운다. 메시지는 지우지 않는다.

        하드 삭제하지 않는 이유는 대화가 기록이기 때문이다. 방을 닫는 것과
        오간 말을 없애는 것은 다른 일이고, 뒤쪽은 되돌릴 수 없다.

        배정이 남아 있으면 함께 끝낸다. 안 그러면 에이전트가 갈 곳 없는 역할을
        쥔 채로 남는다 — 치우려던 것이 다른 모양으로 남는 셈이다.
        """
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE id = ? AND archived_at IS NULL",
                (project_id,),
            ).fetchone()
            if row is None:
                raise LookupError("project not found")
            ended = conn.execute(
                """UPDATE role_assignments
                   SET ended_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                   WHERE ended_at IS NULL AND role_id IN (
                     SELECT id FROM workspace_roles WHERE workspace_id = ?
                   )""",
                (project_id,),
            ).rowcount
            conn.execute(
                """UPDATE projects
                   SET archived_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                   WHERE id = ?""",
                (project_id,),
            )
            archived = conn.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        result = dict(archived)
        result["ended_assignments"] = ended
        return result

    def ensure_project(self, project_id: str) -> None:
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM projects WHERE id = ? AND archived_at IS NULL", (project_id,)
            ).fetchone()
        if row is None:
            raise LookupError("project not found")

    def pm_profile(self, principal_id: str) -> dict[str, Any]:
        with self.transaction() as conn:
            principal = conn.execute(
                "SELECT display_name FROM principals WHERE id = ? AND kind = 'human'",
                (principal_id,),
            ).fetchone()
            if principal is None:
                raise LookupError("PM principal not found")
            conn.execute(
                "INSERT OR IGNORE INTO pm_profiles(principal_id, display_name) VALUES (?, ?)",
                (principal_id, principal["display_name"]),
            )
            row = conn.execute(
                """SELECT principal_id, display_name,
                          CASE WHEN avatar IS NULL THEN 0 ELSE 1 END AS has_avatar,
                          avatar_updated_at, updated_at
                   FROM pm_profiles WHERE principal_id = ?""",
                (principal_id,),
            ).fetchone()
        value = dict(row)
        value["has_avatar"] = bool(value["has_avatar"])
        return value

    def update_pm_profile(self, *, principal_id: str, display_name: str) -> dict[str, Any]:
        self.pm_profile(principal_id)
        with self.transaction() as conn:
            conn.execute(
                """UPDATE pm_profiles SET display_name = ?,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                   WHERE principal_id = ?""",
                (display_name.strip(), principal_id),
            )
            conn.execute(
                "UPDATE principals SET display_name = ? WHERE id = ?",
                (display_name.strip(), principal_id),
            )
        return self.pm_profile(principal_id)

    def set_pm_avatar(
        self, *, principal_id: str, data: bytes | None, media_type: str | None
    ) -> dict[str, Any]:
        self.pm_profile(principal_id)
        with self.transaction() as conn:
            conn.execute(
                """UPDATE pm_profiles SET avatar = ?, avatar_media_type = ?,
                   avatar_updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                   updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                   WHERE principal_id = ?""",
                (data, media_type, principal_id),
            )
        return self.pm_profile(principal_id)

    def pm_avatar(self, principal_id: str) -> tuple[bytes, str] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT avatar, avatar_media_type FROM pm_profiles WHERE principal_id = ?",
                (principal_id,),
            ).fetchone()
        if row is None or row["avatar"] is None:
            return None
        return bytes(row["avatar"]), str(row["avatar_media_type"])

    def upsert_node(self, *, node_id: str, display_name: str) -> dict[str, Any]:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO client_nodes(id, display_name) VALUES (?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  display_name = excluded.display_name,
                  last_seen_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                (node_id, display_name),
            )
            row = conn.execute(
                "SELECT * FROM client_nodes WHERE id = ?", (node_id,)
            ).fetchone()
        return dict(row)

    def upsert_binding(self, binding: dict[str, Any]) -> dict[str, Any]:
        with self.transaction() as conn:
            # 창 하나에 에이전트 하나다. 같은 창에 새 세션이 들어오면 앞의
            # 주인은 이미 없다. 비켜 주지 않으면 UNIQUE에 걸려 sync가 통째로
            # 409를 내고, 그 노드의 배정과 연결이 전부 막힌다.
            conn.execute(
                """
                DELETE FROM agent_bindings
                WHERE node_id = ? AND terminal_provider = ?
                  AND terminal_session_id = ? AND agent_id != ?
                """,
                (
                    binding["node_id"],
                    binding["terminal_provider"],
                    binding["terminal_session_id"],
                    binding["agent_id"],
                ),
            )
            conn.execute(
                """
                INSERT INTO agent_bindings(
                  agent_id, node_id, agent_provider, agent_session_id,
                  terminal_provider, terminal_session_id, lifecycle, attached
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(agent_id) DO UPDATE SET
                  node_id = excluded.node_id,
                  agent_provider = excluded.agent_provider,
                  agent_session_id = excluded.agent_session_id,
                  terminal_provider = excluded.terminal_provider,
                  terminal_session_id = excluded.terminal_session_id,
                  lifecycle = excluded.lifecycle,
                  attached = 1,
                  updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                (
                    binding["agent_id"],
                    binding["node_id"],
                    binding["agent_provider"],
                    binding["agent_session_id"],
                    binding["terminal_provider"],
                    binding["terminal_session_id"],
                    binding["lifecycle"],
                ),
            )
            row = conn.execute(
                "SELECT * FROM agent_bindings WHERE agent_id = ?",
                (binding["agent_id"],),
            ).fetchone()
        result = dict(row)
        result["attached"] = bool(result["attached"])
        return result

    def detach_binding(self, agent_id: str) -> bool:
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE agent_bindings
                SET attached = 0,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE agent_id = ? AND attached = 1
                """,
                (agent_id,),
            )
        return cursor.rowcount == 1

    def roles(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT r.id, r.workspace_id, r.name, r.onboarding_prompt, r.is_lead,
                       r.created_at, r.deleted_at,
                       CASE WHEN r.avatar IS NULL THEN 0 ELSE 1 END AS has_avatar,
                       r.avatar_updated_at,
                       a.id AS assignment_id, a.agent_id, a.assigned_at,
                       a.onboarding_sent, p.display_name AS agent_name
                FROM workspace_roles r
                LEFT JOIN role_assignments a ON a.role_id = r.id AND a.ended_at IS NULL
                LEFT JOIN principals p ON p.id = a.agent_id
                WHERE r.workspace_id = ? AND r.deleted_at IS NULL
                ORDER BY r.name
                """,
                (workspace_id,),
            ).fetchall()
        return [self._role_dict(row) for row in rows]

    def role(self, role_id: str) -> dict[str, Any]:
        with self._lock:
            return self._role_by_id(self._connection, role_id)

    def create_role(
        self, *, workspace_id: str, name: str, onboarding_prompt: str = ""
    ) -> dict[str, Any]:
        self.ensure_project(workspace_id)
        role_id = str(uuid.uuid4())
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO workspace_roles(id, workspace_id, name, onboarding_prompt)
                VALUES (?, ?, ?, ?)
                """,
                (role_id, workspace_id, name.strip(), onboarding_prompt),
            )
        return next(role for role in self.roles(workspace_id) if role["id"] == role_id)

    def update_role(
        self, *, role_id: str, name: str | None, onboarding_prompt: str | None
    ) -> dict[str, Any]:
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM workspace_roles WHERE id = ? AND deleted_at IS NULL",
                (role_id,),
            ).fetchone()
            if row is None:
                raise LookupError("role not found")
            conn.execute(
                """
                UPDATE workspace_roles SET name = ?, onboarding_prompt = ? WHERE id = ?
                """,
                (
                    name.strip() if name is not None else row["name"],
                    onboarding_prompt if onboarding_prompt is not None else row["onboarding_prompt"],
                    role_id,
                ),
            )
            workspace_id = row["workspace_id"]
        return next(role for role in self.roles(workspace_id) if role["id"] == role_id)

    def set_role_avatar(
        self, *, role_id: str, data: bytes | None, media_type: str | None
    ) -> dict[str, Any]:
        with self.transaction() as conn:
            cursor = conn.execute(
                """UPDATE workspace_roles SET avatar = ?, avatar_media_type = ?,
                   avatar_updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                   WHERE id = ? AND deleted_at IS NULL""",
                (data, media_type, role_id),
            )
            if cursor.rowcount != 1:
                raise LookupError("role not found")
        return self.role(role_id)

    def role_avatar(self, role_id: str) -> tuple[bytes, str] | None:
        with self._lock:
            row = self._connection.execute(
                """SELECT avatar, avatar_media_type FROM workspace_roles
                   WHERE id = ? AND deleted_at IS NULL""",
                (role_id,),
            ).fetchone()
        if row is None or row["avatar"] is None:
            return None
        return bytes(row["avatar"]), str(row["avatar_media_type"])

    def delete_role(self, role_id: str) -> bool:
        with self.transaction() as conn:
            pending = conn.execute(
                """SELECT COUNT(*) AS count FROM message_role_recipients
                   WHERE role_id = ? AND delivered_agent_id IS NULL""",
                (role_id,),
            ).fetchone()["count"]
            if pending:
                raise ValueError("role has undelivered messages; assign it before deletion")
            conn.execute(
                """
                UPDATE role_assignments
                SET ended_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE role_id = ? AND ended_at IS NULL
                """,
                (role_id,),
            )
            cursor = conn.execute(
                """
                UPDATE workspace_roles
                SET deleted_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND deleted_at IS NULL
                """,
                (role_id,),
            )
        return cursor.rowcount == 1

    def assign_role(
        self, *, role_id: str, agent_id: str, assigned_by: str,
        onboarding_sent: bool = False,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        events: list[dict[str, Any]] = []
        with self.transaction() as conn:
            role = conn.execute(
                "SELECT * FROM workspace_roles WHERE id = ? AND deleted_at IS NULL",
                (role_id,),
            ).fetchone()
            if role is None:
                raise LookupError("role not found")
            agent = conn.execute(
                "SELECT 1 FROM principals WHERE id = ? AND kind = 'agent'", (agent_id,)
            ).fetchone()
            if agent is None:
                raise LookupError("agent not found")
            current = conn.execute(
                "SELECT * FROM role_assignments WHERE role_id = ? AND ended_at IS NULL",
                (role_id,),
            ).fetchone()
            if current is not None and current["agent_id"] == agent_id:
                return self._role_by_id(conn, role_id), events
            conn.execute(
                """UPDATE role_assignments SET ended_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                   WHERE ended_at IS NULL AND
                     (role_id = ? OR (agent_id = ? AND workspace_id = ?))""",
                (role_id, agent_id, role["workspace_id"]),
            )
            assignment_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO role_assignments(
                       id, role_id, workspace_id, agent_id, assigned_by, onboarding_sent
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (assignment_id, role_id, role["workspace_id"], agent_id,
                 assigned_by, onboarding_sent),
            )
            pending = conn.execute(
                """SELECT message_seq FROM message_role_recipients
                   WHERE role_id = ? AND delivered_agent_id IS NULL ORDER BY message_seq""",
                (role_id,),
            ).fetchall()
            for pending_row in pending:
                message_seq = int(pending_row["message_seq"])
                inserted = conn.execute(
                    "INSERT OR IGNORE INTO inbox(recipient_id, message_seq) VALUES (?, ?)",
                    (agent_id, message_seq),
                ).rowcount
                conn.execute(
                    """UPDATE message_role_recipients SET delivered_agent_id = ?,
                       delivered_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                       WHERE role_id = ? AND message_seq = ?""",
                    (agent_id, role_id, message_seq),
                )
                if inserted:
                    events.append(self._create_delivery_event(conn, agent_id, message_seq))
            result = self._role_by_id(conn, role_id)
        return result, events

    def unassign_role(self, role_id: str) -> bool:
        with self.transaction() as conn:
            cursor = conn.execute(
                """UPDATE role_assignments
                   SET ended_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                   WHERE role_id = ? AND ended_at IS NULL""",
                (role_id,),
            )
        return cursor.rowcount == 1

    def assignment_history(self, role_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT a.*, p.display_name AS agent_name
                   FROM role_assignments a JOIN principals p ON p.id = a.agent_id
                   WHERE a.role_id = ? ORDER BY a.assigned_at DESC""",
                (role_id,),
            ).fetchall()
        result = [dict(row) for row in rows]
        for item in result:
            item["onboarding_sent"] = bool(item["onboarding_sent"])
        return result

    def active_agent_roles(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT a.agent_id, r.id AS role_id, r.name AS role_name,
                          r.workspace_id AS project_id, p.name AS project_name,
                          a.assigned_at
                   FROM role_assignments a
                   JOIN workspace_roles r ON r.id = a.role_id
                   JOIN projects p ON p.id = r.workspace_id
                   WHERE a.ended_at IS NULL AND r.deleted_at IS NULL
                   ORDER BY p.name, r.name"""
            ).fetchall()
        return [dict(row) for row in rows]

    def project_bootstrap(
        self, *, project_id: str, agent_id: str, pm_id: str
    ) -> dict[str, Any]:
        self.ensure_project(project_id)
        with self._lock:
            project = self._connection.execute(
                "SELECT id, name FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            agent = self._connection.execute(
                "SELECT id, display_name FROM principals WHERE id = ? AND kind = 'agent'",
                (agent_id,),
            ).fetchone()
            if agent is None:
                raise LookupError("agent not found")
            pm = self._connection.execute(
                """SELECT p.id, COALESCE(profile.display_name, p.display_name) AS display_name
                   FROM principals p
                   LEFT JOIN pm_profiles profile ON profile.principal_id = p.id
                   WHERE p.id = ? AND p.kind = 'human'""",
                (pm_id,),
            ).fetchone()
            if pm is None:
                raise LookupError("PM not found")
            rows = self._connection.execute(
                """SELECT r.id, r.name, a.agent_id, principal.display_name AS agent_name,
                          a.assigned_at
                   FROM workspace_roles r
                   LEFT JOIN role_assignments a
                     ON a.role_id = r.id AND a.ended_at IS NULL
                   LEFT JOIN principals principal ON principal.id = a.agent_id
                   WHERE r.workspace_id = ? AND r.deleted_at IS NULL
                   ORDER BY r.name""",
                (project_id,),
            ).fetchall()
        roles = []
        own_role = None
        for row in rows:
            role = dict(row)
            role["assigned"] = role["agent_id"] is not None
            role["self"] = role["agent_id"] == agent_id
            roles.append(role)
            if role["self"]:
                own_role = {"id": role["id"], "name": role["name"]}
        result = {
            "project": dict(project),
            "agent": {"id": agent["id"], "display_name": agent["display_name"]},
            "own_role": own_role,
            "pm": dict(pm),
            "roles": roles,
            "usage": {
                "inbox": "fungis inbox",
                "history": "fungis history 20",
                # 새 세션은 여기서 문법을 배운다. 옛 이름을 남겨 두면 에이전트가
                # 계속 그것을 치고, 고쳐야 할 곳이 CLI 가 아니라 여기가 된다.
                "state": "fungis state",
                "reply_pm": 'fungis reply "..."',
                "message_role": 'fungis reply --to ROLE "..."',
                "copy_role": 'fungis reply --cc ROLE "..."',
                "send": 'fungis send "..."',
                "request_review": 'fungis request --level r2 "..."',
                "request_approval": 'fungis request --level r3 "..."',
                "work_start": 'fungis work start "..."',
                "work_report": 'fungis work report "..."',
                "work_done": 'fungis work done "..."',
                "recovery": "if inbox output was lost, fungis history 20",
            },
        }
        # 결과 전체를 해시하면 남의 배정이 바뀔 때마다 값이 달라진다. 그러면
        # "바뀐 것만 다시 보낸다"가 "누가 들고 나기만 해도 다시 보낸다"가 된다.
        # 이 에이전트가 알아야 할 것만 넣는다 — 자기 역할, 부를 수 있는 역할
        # 이름, 사용법. 담당자가 누구인지는 필요할 때 조회하면 된다.
        stable = {
            "project": result["project"],
            "agent": result["agent"],
            "own_role": result["own_role"],
            "pm": result["pm"],
            "role_names": sorted(role["name"] for role in roles),
            "usage": result["usage"],
        }
        encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True).encode()
        result["revision"] = hashlib.sha256(encoded).hexdigest()[:12]
        return result

    @staticmethod
    def _role_dict(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["assigned"] = value.get("assignment_id") is not None
        value["onboarding_sent"] = bool(value.get("onboarding_sent", 0))
        value["has_avatar"] = bool(value.get("has_avatar", 0))
        value["is_lead"] = bool(value.get("is_lead", 0))
        return value

    def _role_by_id(self, conn: sqlite3.Connection, role_id: str) -> dict[str, Any]:
        row = conn.execute(
            """SELECT r.id, r.workspace_id, r.name, r.onboarding_prompt, r.is_lead,
                      r.created_at, r.deleted_at,
                      CASE WHEN r.avatar IS NULL THEN 0 ELSE 1 END AS has_avatar,
                      r.avatar_updated_at,
                      a.id AS assignment_id, a.agent_id, a.assigned_at,
                      a.onboarding_sent, p.display_name AS agent_name
               FROM workspace_roles r
               LEFT JOIN role_assignments a ON a.role_id = r.id AND a.ended_at IS NULL
               LEFT JOIN principals p ON p.id = a.agent_id
               WHERE r.id = ?""",
            (role_id,),
        ).fetchone()
        if row is None:
            raise LookupError("role not found")
        return self._role_dict(row)

    @staticmethod
    def _create_delivery_event(
        conn: sqlite3.Connection, recipient_id: str, message_seq: int
    ) -> dict[str, Any]:
        event_id = str(uuid.uuid4())
        cursor = conn.execute(
            """INSERT INTO delivery_events(id, recipient_id, kind, through_message_seq)
               VALUES (?, ?, 'inbox_available', ?)""",
            (event_id, recipient_id, message_seq),
        )
        return {
            "event_id": event_id, "event_seq": int(cursor.lastrowid),
            "kind": "inbox_available", "recipient_id": recipient_id,
            "through_seq": message_seq,
        }

    def _recipient_or_room_lead(self, given: str) -> str:
        """아는 신원이면 그대로, 방 이름이면 그 방 lead 로 바꾼다."""
        if self.principal_kind(given) is not None:
            return given
        room_id = self.resolve_room_id(given)
        if room_id is None:
            return given
        lead = self.lead_of(room_id)
        if lead is None or not lead.get("agent_id"):
            room = self.project_name(room_id)
            raise ValueError(
                f'"{room}" 에는 지금 lead 가 없다. PM 에게 물어라.'
            )
        return str(lead["agent_id"])

    def send_message(
        self,
        *,
        workspace_id: str,
        sender_id: str,
        recipient_ids: list[str],
        role_ids: list[str] | None = None,
        reference_ids: list[str] | None = None,
        body: str,
        message_id: str | None = None,
        kind: str = "message",
        reply_level: str = "r1",
        in_reply_to: int | None = None,
        track: str | None = None,
        tags: list[str] | None = None,
        inherit_context: bool = True,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        message_id = message_id or str(uuid.uuid4())
        # HQ에는 역할이 없어서 받을 사람을 고를 목록 자체가 없다. 거기서 받는
        # 사람은 소집된 방의 lead 전원이다. 고르게 하면 매번 전원을 고르게 되고
        # 한 번 빠뜨리면 그 방만 못 본다.
        if not recipient_ids and not role_ids and self.is_hq(workspace_id):
            recipient_ids = self.convened_leads()
            if not recipient_ids:
                raise ValueError("no room has been convened yet")
        # 일반 방에서는 수신자 0인 글을 그대로 받는다. 아무에게도 배달되지 않고
        # 아무도 깨우지 않지만 타임라인에는 남아 history 로 읽힌다 — 주소 없는
        # 글은 게시판에 붙인 쪽지지 부재중 전화가 아니다.
        unique_recipients = list(dict.fromkeys(recipient_ids))
        # 수신자 자리에 방 이름이 올 수 있다. HQ 에서 "그 방에 묻는다"는 곧 그
        # 방 lead 에게 묻는 것이라, 부르는 쪽이 사람 이름을 따로 찾지 않게 여기서
        # 푼다. 이미 아는 신원이면 건드리지 않는다.
        unique_recipients = [
            self._recipient_or_room_lead(value) for value in unique_recipients
        ]
        unique_recipients = list(dict.fromkeys(unique_recipients))
        unique_roles = list(dict.fromkeys(role_ids or []))
        unique_references = [
            value
            for value in dict.fromkeys(reference_ids or [])
            if value not in unique_recipients
        ]
        normalized_track = track.strip() if track and track.strip() else None
        normalized_tags = self._normalize_tags(tags) if tags is not None else None
        with self.transaction() as conn:
            resolved_roles: list[tuple[str, str | None]] = []
            for role_id in unique_roles:
                role = conn.execute(
                    """SELECT r.workspace_id, a.agent_id FROM workspace_roles r
                       LEFT JOIN role_assignments a ON a.role_id = r.id AND a.ended_at IS NULL
                       WHERE r.id = ? AND r.deleted_at IS NULL""",
                    (role_id,),
                ).fetchone()
                if role is None or role["workspace_id"] != workspace_id:
                    raise LookupError(f"role {role_id} not found in workspace")
                resolved_roles.append((role_id, role["agent_id"]))
                if role["agent_id"] and role["agent_id"] not in unique_recipients:
                    unique_recipients.append(role["agent_id"])
            if in_reply_to is not None and inherit_context:
                parent = conn.execute(
                    "SELECT track FROM messages WHERE seq = ?", (in_reply_to,)
                ).fetchone()
                if parent is None:
                    raise ValueError(f"parent message {in_reply_to} not found")
                if normalized_track is None:
                    normalized_track = parent["track"]
                if normalized_tags is None:
                    normalized_tags = self._message_tags(in_reply_to)
            normalized_tags = normalized_tags or []
            normalized_tags = [tag for tag in normalized_tags if tag != normalized_track]
            next_project_seq = int(
                conn.execute(
                    "SELECT COALESCE(MAX(project_seq), 0) + 1 FROM messages"
                    " WHERE workspace_id = ?",
                    (workspace_id,),
                ).fetchone()[0]
            )
            cursor = conn.execute(
                """
                INSERT INTO messages(
                  id, workspace_id, sender_id, body, kind, reply_level, in_reply_to,
                  track, project_seq
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id, workspace_id, sender_id, body,
                    kind, reply_level, in_reply_to, normalized_track,
                    next_project_seq,
                ),
            )
            message_seq = int(cursor.lastrowid)
            for role_id, agent_id in resolved_roles:
                conn.execute(
                    """INSERT INTO message_role_recipients(
                           role_id, message_seq, delivered_agent_id, delivered_at
                       ) VALUES (?, ?, ?, CASE WHEN ? IS NULL THEN NULL
                           ELSE strftime('%Y-%m-%dT%H:%M:%fZ', 'now') END)""",
                    (role_id, message_seq, agent_id, agent_id),
                )
            for tag in normalized_tags:
                conn.execute(
                    "INSERT INTO message_tags(message_seq, tag) VALUES (?, ?)",
                    (message_seq, tag),
                )
            events: list[dict[str, Any]] = []
            for principal_id in unique_references:
                conn.execute(
                    "INSERT INTO message_references(principal_id, message_seq) VALUES (?, ?)",
                    (principal_id, message_seq),
                )
                # 참조도 배달한다. 배달하지 않으면 보는 사람이 참조 대신 수신자
                # 자리에 넣게 되고, 받는 쪽은 그것을 지시로 읽는다. 읽을 수는
                # 있되 답할 자리는 아니라는 구분이 필요해서 자리를 나눠 둔다.
                conn.execute(
                    "INSERT INTO inbox(recipient_id, message_seq) VALUES (?, ?)",
                    (principal_id, message_seq),
                )
                events.append(
                    self._create_delivery_event(conn, principal_id, message_seq)
                )
            for recipient_id in unique_recipients:
                conn.execute(
                    "INSERT INTO inbox(recipient_id, message_seq) VALUES (?, ?)",
                    (recipient_id, message_seq),
                )
                events.append(self._create_delivery_event(conn, recipient_id, message_seq))
            row = conn.execute(
                "SELECT * FROM messages WHERE seq = ?", (message_seq,)
            ).fetchone()
        message = dict(row)
        message["recipient_ids"] = unique_recipients
        message["reference_ids"] = unique_references
        message["role_ids"] = unique_roles
        message["tags"] = normalized_tags
        return message, events

    def messages_after(self, *, recipient_id: str, after: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT m.*, p.display_name AS sender_name,
                              parent.project_seq AS in_reply_to_project_seq,
                              EXISTS (
                                SELECT 1 FROM message_references r
                                WHERE r.message_seq = m.seq
                                  AND r.principal_id = i.recipient_id
                              ) AS is_reference,
                              -- 사람이 마지막으로 말한 뒤 에이전트끼리 몇 번
                              -- 오갔는지. 막지 않고 알려만 준다. 길어진 것을
                              -- 알면 새로 보탤 것이 없을 때 멈출 수 있다.
                              (SELECT COUNT(*) FROM messages c
                               WHERE c.workspace_id = m.workspace_id
                                 AND c.seq <= m.seq
                                 AND c.seq > COALESCE((
                                   SELECT MAX(h.seq) FROM messages h
                                   JOIN principals hp ON hp.id = h.sender_id
                                   WHERE h.workspace_id = m.workspace_id
                                     AND h.seq <= m.seq AND hp.kind = 'human'
                                 ), 0)
                              ) AS agent_chain
                FROM messages m
                JOIN inbox i ON i.message_seq = m.seq
                JOIN principals p ON p.id = m.sender_id
                LEFT JOIN messages parent ON parent.seq = m.in_reply_to
                WHERE i.recipient_id = ? AND m.seq > ?
                ORDER BY m.seq ASC
                """,
                (recipient_id, after),
            ).fetchall()
        result = []
        for row in rows:
            message = dict(row)
            message["tags"] = self._message_tags(message["seq"])
            message["role_recipients"] = self._message_roles(message["seq"])
            result.append(message)
        return result

    def timeline(self, principal_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT DISTINCT m.*, p.display_name AS sender_name
                FROM messages m
                JOIN principals p ON p.id = m.sender_id
                LEFT JOIN messages parent ON parent.seq = m.in_reply_to
                LEFT JOIN inbox own ON own.message_seq = m.seq
                  AND own.recipient_id = ?
                WHERE m.sender_id = ? OR own.recipient_id IS NOT NULL
                ORDER BY m.seq DESC LIMIT ?
                """,
                (principal_id, principal_id, limit),
            ).fetchall()
            result = []
            for row in reversed(rows):
                message = dict(row)
                message["recipients"] = self._message_recipients(message["seq"])
                message["references"] = self._message_references(message["seq"])
                message["tags"] = self._message_tags(message["seq"])
                message["role_recipients"] = self._message_roles(message["seq"])
                result.append(message)
        return result

    def workspace_timeline(
        self, workspace_id: str, limit: int = 100,
        after: int | None = None, before: int | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            if before is not None:
                rows = self._connection.execute(
                    """SELECT m.*, p.display_name AS sender_name,
                              parent.project_seq AS in_reply_to_project_seq
                       FROM messages m JOIN principals p ON p.id = m.sender_id
                       LEFT JOIN messages parent ON parent.seq = m.in_reply_to
                       WHERE m.workspace_id = ? AND m.seq < ?
                       ORDER BY m.seq DESC LIMIT ?""",
                    (workspace_id, before, limit),
                ).fetchall()
                rows = list(reversed(rows))
            elif after is None:
                rows = self._connection.execute(
                    """SELECT m.*, p.display_name AS sender_name,
                              parent.project_seq AS in_reply_to_project_seq
                       FROM messages m JOIN principals p ON p.id = m.sender_id
                       LEFT JOIN messages parent ON parent.seq = m.in_reply_to
                       WHERE m.workspace_id = ?
                       ORDER BY m.seq DESC LIMIT ?""",
                    (workspace_id, limit),
                ).fetchall()
                rows = list(reversed(rows))
            else:
                rows = self._connection.execute(
                    """SELECT m.*, p.display_name AS sender_name,
                              parent.project_seq AS in_reply_to_project_seq
                       FROM messages m JOIN principals p ON p.id = m.sender_id
                       LEFT JOIN messages parent ON parent.seq = m.in_reply_to
                       WHERE m.workspace_id = ? AND m.seq > ?
                       ORDER BY m.seq ASC LIMIT ?""",
                    (workspace_id, after, limit),
                ).fetchall()
            result = []
            for row in rows:
                message = dict(row)
                message["recipients"] = self._message_recipients(message["seq"])
                message["references"] = self._message_references(message["seq"])
                message["tags"] = self._message_tags(message["seq"])
                message["role_recipients"] = self._message_roles(message["seq"])
                result.append(message)
        return result

    def workspace_message(
        self, *, workspace_id: str, project_seq: int
    ) -> dict[str, Any] | None:
        """방 안의 표시 번호로 글 하나만 꺼낸다.

        앞뒤 스무 개를 받아 눈으로 골라내는 것이 지금까지의 유일한 방법이었다.
        번호를 이미 알고 있을 때는 그 스무 개가 전부 낭비다.
        """
        with self._lock:
            row = self._connection.execute(
                """SELECT m.*, p.display_name AS sender_name,
                          parent.project_seq AS in_reply_to_project_seq
                   FROM messages m JOIN principals p ON p.id = m.sender_id
                   LEFT JOIN messages parent ON parent.seq = m.in_reply_to
                   WHERE m.workspace_id = ? AND m.project_seq = ?""",
                (workspace_id, project_seq),
            ).fetchone()
            if row is None:
                return None
            message = dict(row)
            message["recipients"] = self._message_recipients(message["seq"])
            message["references"] = self._message_references(message["seq"])
            message["tags"] = self._message_tags(message["seq"])
            message["role_recipients"] = self._message_roles(message["seq"])
        return message

    def bookmarks(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT b.*, p.display_name AS created_by_name,
                          m.project_seq AS message_project_seq
                   FROM message_bookmarks b
                   JOIN principals p ON p.id = b.created_by
                   LEFT JOIN messages m ON m.seq = b.message_seq
                   WHERE b.workspace_id = ?
                   ORDER BY b.message_seq ASC, b.created_at ASC""",
                (workspace_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_bookmark(
        self, *, workspace_id: str, message_seq: int,
        label: str, created_by: str,
    ) -> dict[str, Any]:
        bookmark_id = str(uuid.uuid4())
        with self.transaction() as conn:
            cursor = conn.execute(
                """INSERT INTO message_bookmarks(
                       id, workspace_id, message_seq, label, created_by
                   )
                   SELECT ?, ?, m.seq, ?, ? FROM messages m
                   WHERE m.seq = ? AND m.workspace_id = ?""",
                (
                    bookmark_id, workspace_id, label, created_by,
                    message_seq, workspace_id,
                ),
            )
            if cursor.rowcount != 1:
                raise LookupError("message not found in project")
            row = conn.execute(
                "SELECT * FROM message_bookmarks WHERE id = ?", (bookmark_id,)
            ).fetchone()
        return dict(row)

    def delete_bookmark(self, *, workspace_id: str, bookmark_id: str) -> bool:
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM message_bookmarks WHERE workspace_id = ? AND id = ?",
                (workspace_id, bookmark_id),
            )
        return cursor.rowcount == 1

    def timeline_pins(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT t.*, p.display_name AS created_by_name,
                          m.project_seq AS after_message_project_seq
                   FROM timeline_pins t
                   JOIN principals p ON p.id = t.created_by
                   LEFT JOIN messages m ON m.seq = t.after_message_seq
                   WHERE t.workspace_id = ?
                   ORDER BY t.after_message_seq ASC""",
                (workspace_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_timeline_pin(
        self, *, workspace_id: str, after_message_seq: int,
        label: str, created_by: str,
    ) -> dict[str, Any]:
        pin_id = str(uuid.uuid4())
        with self.transaction() as conn:
            cursor = conn.execute(
                """INSERT INTO timeline_pins(
                       id, workspace_id, after_message_seq, label, created_by
                   )
                   SELECT ?, ?, m.seq, ?, ? FROM messages m
                   WHERE m.seq = ? AND m.workspace_id = ?""",
                (
                    pin_id, workspace_id, label, created_by,
                    after_message_seq, workspace_id,
                ),
            )
            if cursor.rowcount != 1:
                raise LookupError("message not found in project")
            row = conn.execute(
                "SELECT * FROM timeline_pins WHERE id = ?", (pin_id,)
            ).fetchone()
        return dict(row)

    def delete_timeline_pin(self, *, workspace_id: str, pin_id: str) -> bool:
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM timeline_pins WHERE workspace_id = ? AND id = ?",
                (workspace_id, pin_id),
            )
        return cursor.rowcount == 1

    def _message_references(self, message_seq: int) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            """
            SELECT r.principal_id, p.display_name
            FROM message_references r
            JOIN principals p ON p.id = r.principal_id
            WHERE r.message_seq = ? ORDER BY p.display_name
            """,
            (message_seq,),
        ).fetchall()
        return [dict(row) for row in rows]

    def _message_tags(self, message_seq: int) -> list[str]:
        rows = self._connection.execute(
            "SELECT tag FROM message_tags WHERE message_seq = ? ORDER BY rowid",
            (message_seq,),
        ).fetchall()
        return [str(row["tag"]) for row in rows]

    def _message_roles(self, message_seq: int) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            """SELECT mr.role_id, r.name, mr.delivered_agent_id, mr.delivered_at
               FROM message_role_recipients mr
               JOIN workspace_roles r ON r.id = mr.role_id
               WHERE mr.message_seq = ? ORDER BY r.name""",
            (message_seq,),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _normalize_tags(tags: list[str]) -> list[str]:
        normalized = [tag.strip() for tag in tags if tag.strip()]
        if len(normalized) > 20:
            raise ValueError("a message may have at most 20 tags")
        if any(len(tag) > 120 for tag in normalized):
            raise ValueError("tags may be at most 120 characters")
        return list(dict.fromkeys(normalized))

    def attention(self, principal_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT m.*, p.display_name AS sender_name,
                              parent.project_seq AS in_reply_to_project_seq
                FROM messages m
                JOIN principals p ON p.id = m.sender_id
                LEFT JOIN messages parent ON parent.seq = m.in_reply_to
                JOIN inbox i ON i.message_seq = m.seq
                WHERE i.recipient_id = ? AND m.kind = 'pm_request'
                  AND NOT EXISTS (
                    SELECT 1 FROM messages answer
                    WHERE answer.in_reply_to = m.seq
                      AND answer.sender_id = ?
                  )
                ORDER BY CASE m.reply_level
                  WHEN 'r3' THEN 3 WHEN 'r2' THEN 2 ELSE 1 END DESC,
                  m.seq ASC
                """,
                (principal_id, principal_id),
            ).fetchall()
            result = []
            for row in rows:
                message = dict(row)
                message["recipients"] = self._message_recipients(message["seq"])
                message["references"] = self._message_references(message["seq"])
                message["tags"] = self._message_tags(message["seq"])
                message["role_recipients"] = self._message_roles(message["seq"])
                result.append(message)
        return result

    def workspace_attention(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT m.*, p.display_name AS sender_name,
                              parent.project_seq AS in_reply_to_project_seq
                FROM messages m JOIN principals p ON p.id = m.sender_id
                       LEFT JOIN messages parent ON parent.seq = m.in_reply_to
                WHERE m.workspace_id = ? AND m.kind = 'pm_request'
                  AND NOT EXISTS (
                    SELECT 1 FROM messages answer
                    JOIN principals answerer ON answerer.id = answer.sender_id
                    WHERE answer.in_reply_to = m.seq
                      AND answerer.kind = 'human'
                  )
                ORDER BY CASE m.reply_level
                  WHEN 'r3' THEN 3 WHEN 'r2' THEN 2 ELSE 1 END DESC,
                  m.seq ASC
                """,
                (workspace_id,),
            ).fetchall()
            result = []
            for row in rows:
                message = dict(row)
                message["recipients"] = self._message_recipients(message["seq"])
                message["references"] = self._message_references(message["seq"])
                message["tags"] = self._message_tags(message["seq"])
                message["role_recipients"] = self._message_roles(message["seq"])
                result.append(message)
        return result

    def _message_recipients(self, message_seq: int) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            """
            SELECT i.recipient_id, p.display_name,
                   i.received_at, i.processed_at
            FROM inbox i JOIN principals p ON p.id = i.recipient_id
            WHERE i.message_seq = ?
              AND NOT EXISTS (
                SELECT 1 FROM message_references r
                WHERE r.message_seq = i.message_seq
                  AND r.principal_id = i.recipient_id
              )
            ORDER BY p.display_name
            """,
            (message_seq,),
        ).fetchall()
        return [dict(row) for row in rows]

    def shared_values(
        self, *, workspace_id: str, keys: list[str] | None = None
    ) -> list[dict[str, Any]]:
        with self._lock:
            if keys:
                placeholders = ",".join("?" for _ in keys)
                rows = self._connection.execute(
                    f"""
                    SELECT * FROM shared_values
                    WHERE workspace_id = ? AND key IN ({placeholders})
                    ORDER BY key
                    """,
                    (workspace_id, *keys),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    """
                    SELECT * FROM shared_values
                    WHERE workspace_id = ? ORDER BY key
                    """,
                    (workspace_id,),
                ).fetchall()
        return [dict(row) for row in rows]

    def upsert_shared_value(
        self, *, workspace_id: str, key: str, value: str, updated_by: str
    ) -> dict[str, Any]:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO shared_values(workspace_id, key, value, updated_by)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(workspace_id, key) DO UPDATE SET
                  value = excluded.value,
                  version = shared_values.version + 1,
                  updated_by = excluded.updated_by,
                  updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                (workspace_id, key, value, updated_by),
            )
            row = conn.execute(
                """
                SELECT * FROM shared_values WHERE workspace_id = ? AND key = ?
                """,
                (workspace_id, key),
            ).fetchone()
        return dict(row)

    def delete_shared_value(self, *, workspace_id: str, key: str) -> bool:
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM shared_values WHERE workspace_id = ? AND key = ?",
                (workspace_id, key),
            )
        return cursor.rowcount == 1

    def start_work(
        self, *, workspace_id: str, agent_id: str, title: str
    ) -> dict[str, Any]:
        work_id = str(uuid.uuid4())
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO work_items(id, workspace_id, agent_id, title)
                VALUES (?, ?, ?, ?)
                """,
                (work_id, workspace_id, agent_id, title),
            )
            row = conn.execute(
                "SELECT * FROM work_items WHERE id = ?", (work_id,)
            ).fetchone()
        return self._work_dict(row)

    def update_active_work(
        self, *, agent_id: str, report: str, done: bool
    ) -> dict[str, Any]:
        with self.transaction() as conn:
            row = conn.execute(
                """
                SELECT * FROM work_items
                WHERE agent_id = ? AND status = 'active'
                """,
                (agent_id,),
            ).fetchone()
            if row is None:
                raise LookupError("active work not found")
            kind = "done" if done else "report"
            conn.execute(
                "INSERT INTO work_reports(work_id, body, kind) VALUES (?, ?, ?)",
                (row["id"], report, kind),
            )
            conn.execute(
                """
                UPDATE work_items SET last_report = ?,
                  status = CASE WHEN ? THEN 'done' ELSE status END,
                  ended_at = CASE WHEN ? THEN strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                                  ELSE ended_at END,
                  updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (report, done, done, row["id"]),
            )
            updated = conn.execute(
                "SELECT * FROM work_items WHERE id = ?", (row["id"],)
            ).fetchone()
        return self._work_dict(updated)

    def work_items(self, *, workspace_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT w.*, p.display_name AS agent_name
                FROM work_items w JOIN principals p ON p.id = w.agent_id
                WHERE w.workspace_id = ?
                ORDER BY CASE w.status WHEN 'active' THEN 0 ELSE 1 END,
                         w.started_at DESC LIMIT ?
                """,
                (workspace_id, limit),
            ).fetchall()
        return [self._work_dict(row) for row in rows]

    @staticmethod
    def _work_dict(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        with sqlite3.connect(":memory:") as conn:
            elapsed = conn.execute(
                """
                SELECT CAST((julianday(COALESCE(?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')))
                  - julianday(?)) * 86400 AS INTEGER)
                """,
                (value.get("ended_at"), value["started_at"]),
            ).fetchone()[0]
        value["elapsed_seconds"] = max(0, int(elapsed or 0))
        value["token_usage"] = None
        return value

    def delivery_events_after(
        self, *, recipient_id: str, after: int
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT id AS event_id, seq AS event_seq, kind, recipient_id,
                       through_message_seq AS through_seq
                FROM delivery_events
                WHERE recipient_id = ? AND seq > ?
                ORDER BY seq ASC
                """,
                (recipient_id, after),
            ).fetchall()
        return [dict(row) for row in rows]

    def ack(self, *, recipient_id: str, through_seq: int, processed: bool) -> dict[str, int]:
        column = "processed_at" if processed else "received_at"
        with self.transaction() as conn:
            exists = conn.execute(
                """
                SELECT 1 FROM inbox
                WHERE recipient_id = ? AND message_seq = ?
                """,
                (recipient_id, through_seq),
            ).fetchone()
            if exists is None:
                raise LookupError("through_seq is not in the recipient inbox")
            conn.execute(
                f"""
                UPDATE inbox
                SET {column} = COALESCE({column}, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                WHERE recipient_id = ? AND message_seq <= ?
                """,
                (recipient_id, through_seq),
            )
            if processed:
                conn.execute(
                    """
                    UPDATE inbox
                    SET received_at = COALESCE(received_at, processed_at)
                    WHERE recipient_id = ? AND message_seq <= ?
                    """,
                    (recipient_id, through_seq),
                )
            state = self._inbox_state(conn, recipient_id)
        return state

    def inbox_state(self, recipient_id: str) -> dict[str, int]:
        with self._lock:
            return self._inbox_state(self._connection, recipient_id)

    @staticmethod
    def _inbox_state(conn: sqlite3.Connection, recipient_id: str) -> dict[str, int]:
        row = conn.execute(
            """
            SELECT
              COALESCE(MAX(CASE WHEN received_at IS NOT NULL THEN message_seq END), 0)
                AS received_seq,
              COALESCE(MAX(CASE WHEN processed_at IS NOT NULL THEN message_seq END), 0)
                AS processed_seq,
              COUNT(CASE WHEN processed_at IS NULL THEN 1 END) AS pending_count
            FROM inbox WHERE recipient_id = ?
            """,
            (recipient_id,),
        ).fetchone()
        return {key: int(row[key]) for key in row.keys()}
