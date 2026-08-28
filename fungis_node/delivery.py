"""배달 — 무엇이 왔고, 무엇을 넘겼고, 언제 깨웠나.

표 다섯이 한 흐름을 나눠 든다.

    pending_events   서버가 알려 온 것 중 아직 안 읽은 것
    inbox_claims     에이전트에게 여기까지 넘겼다
    wake_attempts    지금 기다리는 깨우기 하나
    wake_schedule    본인이 스스로 잡은 다음 걸음
    wake_log         지나간 깨우기 전부. 세는 데만 쓴다

`LocalRegistry` 가 이것을 상속한다. 연결과 `recipient_key` 는 그쪽이 준다 —
배달은 세션이 어느 창에 붙었는지까지는 몰라도 된다.
"""

from __future__ import annotations

import sqlite3
from typing import Any


# 깨우기 확인이 이 시간 안에 안 오면 없던 것으로 본다. 다시 깨우는 쪽이
# 안전하다 — 호출문 한 줄이고 게이트가 빈 프롬프트를 확인한 뒤에만 넣는다.
#
# 이 값이 하는 일은 **깨우고 나서 에이전트가 반응하기까지의 짧은 틈을 덮는
# 것**이다. 반응하면 화면이 차서 2층이 막고, 읽으면 claim 이 생겨 1층이 막는다.
# 그러니 그 틈만 덮으면 된다.
#
# 600 초였을 때는 '확인이 올 때까지' 와 다름없었고, 확인이 한 번 유실되면
# 그동안 새 메시지까지 전부 갇혔다. 2026-08-28 아침의 수신 끊김이 그것이다.
WAKE_CONFIRM_TTL_SECONDS = 45

# 인박스에는 쌓이되 턴을 열지는 않는 배달. 보내는 쪽이 `--later` 로 정한다.
# 서버가 이 값을 이벤트 kind 로 실어 보내므로 노드는 새 칸을 안 만든다.
QUIET_EVENT_KIND = "inbox_later"


class DeliveryStore:
    """`LocalRegistry` 에 얹히는 배달 부분."""

    connection: sqlite3.Connection

    def recipient_key(self, identity: str) -> str:  # LocalRegistry 가 준다
        raise NotImplementedError

    def claim_inbox(
        self, recipient_id: str, through_seq: int, agent_session_id: str
    ) -> dict[str, Any]:
        recipient_id = self.recipient_key(recipient_id)
        self.connection.execute(
            """
            INSERT INTO inbox_claims(recipient_id, through_seq, agent_session_id)
            VALUES (?, ?, ?)
            ON CONFLICT(recipient_id) DO UPDATE SET
              through_seq = MAX(inbox_claims.through_seq, excluded.through_seq),
              agent_session_id = excluded.agent_session_id,
              claimed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (recipient_id, through_seq, agent_session_id),
        )
        self.connection.commit()
        return self.claim(recipient_id) or {}

    def claim(self, recipient_id: str) -> dict[str, Any] | None:
        recipient_id = self.recipient_key(recipient_id)
        row = self.connection.execute(
            "SELECT * FROM inbox_claims WHERE recipient_id = ?", (recipient_id,)
        ).fetchone()
        return dict(row) if row else None

    def claim_for_session(self, agent_session_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT inbox_claims.* FROM inbox_claims
            JOIN bindings ON bindings.principal_id = inbox_claims.recipient_id
            WHERE bindings.attached = 1
              AND bindings.agent_session_id = ?
              AND inbox_claims.agent_session_id = ?
            ORDER BY inbox_claims.claimed_at LIMIT 1
            """,
            (agent_session_id, agent_session_id),
        ).fetchone()
        return dict(row) if row else None

    def clear_schedule_for_session(self, agent_session_id: str) -> int:
        """턴이 돌았으면 그 세션에 보낸 예약은 닿은 것이다.

        예약 깨우기는 읽을 인박스가 없어 claim 을 만들지 않는다. 그래서 claim 이
        있을 때만 정리하는 길로는 예약이 영영 안 지워지고, 간격마다 다시 나간다.

        **아직 안 보낸 예약은 건드리지 않는다.** 에이전트가 `wake --in 20m` 을
        걸고 턴을 끝내는 것이 정상 흐름인데, 그 턴 종료가 방금 건 예약을 지우면
        예약이라는 기능이 성립하지 않는다.
        """
        cursor = self.connection.execute(
            """
            DELETE FROM wake_schedule
            WHERE sent_at IS NOT NULL AND recipient_id IN (
              SELECT principal_id FROM bindings
              WHERE attached = 1 AND agent_session_id = ?
            )
            """,
            (agent_session_id,),
        )
        self.connection.commit()
        return cursor.rowcount

    def clear_claim(self, recipient_id: str, through_seq: int) -> int:
        recipient_id = self.recipient_key(recipient_id)
        cursor = self.connection.execute(
            """
            DELETE FROM inbox_claims
            WHERE recipient_id = ? AND through_seq <= ?
            """,
            (recipient_id, through_seq),
        )
        self.connection.commit()
        return cursor.rowcount

    def record_event(self, event: dict[str, Any]) -> bool:
        recipient_id = self.recipient_key(event["recipient_id"])
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO pending_events(
              event_id, event_seq, recipient_id, through_seq, kind
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                event["event_id"],
                event["event_seq"],
                recipient_id,
                event["through_seq"],
                event["kind"],
            ),
        )
        self.connection.execute(
            """
            INSERT INTO node_state(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value =
              CAST(MAX(CAST(value AS INTEGER), CAST(excluded.value AS INTEGER)) AS TEXT)
            """,
            (
                f"server_event_cursor:{recipient_id}",
                str(event["event_seq"]),
            ),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def event_cursor(self, recipient_id: str) -> int:
        recipient_id = self.recipient_key(recipient_id)
        row = self.connection.execute(
            "SELECT value FROM node_state WHERE key = ?",
            (f"server_event_cursor:{recipient_id}",),
        ).fetchone()
        return int(row["value"]) if row else 0

    def pending(self, recipient_id: str | None = None) -> list[dict[str, Any]]:
        if recipient_id is None:
            rows = self.connection.execute(
                "SELECT * FROM pending_events ORDER BY event_seq"
            ).fetchall()
        else:
            recipient_id = self.recipient_key(recipient_id)
            rows = self.connection.execute(
                """
                SELECT * FROM pending_events
                WHERE recipient_id = ? ORDER BY event_seq
                """,
                (recipient_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def clear_processed(self, recipient_id: str, through_seq: int) -> int:
        recipient_id = self.recipient_key(recipient_id)
        cursor = self.connection.execute(
            """
            DELETE FROM pending_events
            WHERE recipient_id = ? AND through_seq <= ?
            """,
            (recipient_id, through_seq),
        )
        self.connection.commit()
        return cursor.rowcount

    def pending_summary(self, recipient_id: str) -> dict[str, int]:
        """무엇이 쌓였고, 그중 무엇이 깨울 이유인가.

        둘을 가른다. `later` 로 온 것은 인박스에 그대로 쌓이지만 그것 때문에
        턴을 열지는 않는다 — 한 걸음 도는 중에 끼면 그 걸음이 통째로 밀린다.

        through_seq 는 세는 것과 무관하게 전부에서 뽑는다. 커서는 읽은 자리를
        말하는 것이라 깨울 이유였는지와 상관이 없다.
        """
        recipient_id = self.recipient_key(recipient_id)
        row = self.connection.execute(
            """
            SELECT COUNT(*) AS pending_count,
                   SUM(CASE WHEN kind = ? THEN 0 ELSE 1 END) AS waking_count,
                   COALESCE(MAX(through_seq), 0) AS through_seq
            FROM pending_events WHERE recipient_id = ?
            """,
            (QUIET_EVENT_KIND, recipient_id),
        ).fetchone()
        return {
            "pending_count": int(row["pending_count"]),
            "waking_count": int(row["waking_count"] or 0),
            "through_seq": int(row["through_seq"]),
        }

    def outstanding_wake(self, recipient_id: str) -> dict[str, Any] | None:
        """확인을 기다리는 깨우기. 늙은 것은 없는 것으로 본다.

        확인이 한 번 유실되면 게이트가 이후 모든 깨우기를 영구히 거부한다.
        그러면 그 에이전트는 다시는 안 깨어난다 — 메시지는 쌓이는데 아무도
        모른다. 8/18에 실제로 걸렸다(8/17 21:28에 보낸 것이 하루 넘게 남아
        메시지 5건을 막고 있었다).

        다시 깨우는 쪽은 안전하다. 호출문 한 줄이고, 게이트가 빈 프롬프트를
        확인한 뒤에만 넣는다.
        """
        recipient_id = self.recipient_key(recipient_id)
        self.connection.execute(
            """
            UPDATE wake_attempts SET status = 'superseded',
              processed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
              stalled_count = stalled_count + 1
            WHERE recipient_id = ? AND status = 'sent'
              -- 한도가 0 이면 '지금 당장 만료' 라는 뜻이다. `<` 로 두면 같은
              -- 밀리초에 보낸 것이 안 잡혀서 그 뜻이 지켜지지 않는다.
              AND sent_at <= strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?)
            """,
            (recipient_id, f"-{WAKE_CONFIRM_TTL_SECONDS} seconds"),
        )
        self.connection.commit()
        row = self.connection.execute(
            """
            SELECT * FROM wake_attempts
            WHERE recipient_id = ? AND status = 'sent'
            """,
            (recipient_id,),
        ).fetchone()
        return dict(row) if row else None

    def schedule_wake(
        self, recipient_id: str, due_at: str, note: str | None = None
    ) -> dict[str, Any]:
        """다음 걸음을 스스로 예약한다.

        지금 깨우기는 인박스에 뭔가 있을 때만 나간다. 그래서 worker 가 착수를
        선언하고 턴을 끝내면, 아무도 말을 안 거는 한 턴이 다시 안 열린다 —
        2026-08-19 루프 2회차에서 그렇게 1시간이 갔다.

        이 예약은 보낼 말이 없어도 깨운다. 이유가 다르다.

        같은 사람이 다시 걸면 미룬 횟수를 센다. 진전 없이 반복해서 미루는 것은
        그 자체가 막힘 신호다.
        """
        recipient_id = self.recipient_key(recipient_id)
        self.connection.execute(
            """
            INSERT INTO wake_schedule(recipient_id, due_at, note)
            VALUES (?, ?, ?)
            ON CONFLICT(recipient_id) DO UPDATE SET
              due_at = excluded.due_at,
              note = excluded.note,
              deferrals = wake_schedule.deferrals + 1,
              updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (recipient_id, due_at, note),
        )
        self.connection.commit()
        return self.wake_schedule(recipient_id) or {}

    def wake_schedule(self, recipient_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM wake_schedule WHERE recipient_id = ?",
            (self.recipient_key(recipient_id),),
        ).fetchone()
        return dict(row) if row else None

    def clear_wake_schedule(self, recipient_id: str) -> None:
        """어떤 이유로든 깨어났으면 예약은 소진된 것이다.

        인박스 깨우기로 이미 턴이 열렸는데 예약이 남아 있으면 곧바로 한 번 더
        찌른다. 깨우는 것이 목적이지 그 이유가 무엇이었냐가 목적이 아니다.
        """
        self.connection.execute(
            "DELETE FROM wake_schedule WHERE recipient_id = ?",
            (self.recipient_key(recipient_id),),
        )
        self.connection.commit()

    def mark_schedule_sent(self, recipient_id: str, when: str) -> None:
        """예약 문구를 창에 넣었다. **지우지는 않는다.**

        지우면 재시도가 없어진다 — 문구가 들어간 것과 에이전트가 그것을 본 것은
        다른 일이다. 확인은 턴이 도는 것으로 하고, 그때 `clear_wake_schedule` 이
        지운다.
        """
        # 시각은 부르는 쪽이 준다. 게이트는 주입된 시계로 판정하는데 여기서
        # DB 의 `now` 를 쓰면 두 시계가 섞여, 재발송 간격이 그 차이만큼 어긋난다.
        self.connection.execute(
            "UPDATE wake_schedule SET sent_at = ? WHERE recipient_id = ?",
            (when, self.recipient_key(recipient_id)),
        )
        self.connection.commit()

    def log_wake(
        self, recipient_id: str, kind: str, through_seq: int | None, when: str
    ) -> None:
        """나간 깨우기를 한 줄 남긴다. 판정에는 안 쓴다 — 세는 데만 쓴다."""
        self.connection.execute(
            "INSERT INTO wake_log(recipient_id, kind, through_seq, sent_at)"
            " VALUES (?, ?, ?, ?)",
            (self.recipient_key(recipient_id), kind, through_seq, when),
        )
        self.connection.commit()

    def close_wake_log(self, recipient_id: str, column: str, when: str) -> None:
        """아직 안 닫힌 줄에 시각을 찍는다.

        `column` 은 `read_at` 또는 `settled_at` 이다. 부르는 자리가 고정이라
        문자열을 그대로 끼워 넣되, 그 둘만 받는다.
        """
        if column not in ("read_at", "settled_at"):
            raise ValueError(f"unknown column: {column}")
        self.connection.execute(
            f"UPDATE wake_log SET {column} = ?"
            f" WHERE recipient_id = ? AND {column} IS NULL",
            (when, self.recipient_key(recipient_id)),
        )
        self.connection.commit()

    def wake_stats(self, since: str) -> list[dict[str, Any]]:
        """깨우기가 얼마나 닿았나. `since` 이후로 센다.

        고친 뒤 나아졌는지를 말하려면 이 숫자가 있어야 한다. 2026-08-28 에는
        다섯 번 고치고도 전후를 못 댔다.
        """
        rows = self.connection.execute(
            """
            SELECT kind,
                   COUNT(*) AS sent,
                   SUM(read_at IS NOT NULL) AS read,
                   SUM(settled_at IS NOT NULL) AS settled,
                   -- CAST 만 쓰면 자른다. julianday 차이는 부동소수라 3 초가
                   -- 2.9999 로 나오고, 그대로 잘리면 지연을 늘 짧게 보고한다.
                   CAST(ROUND(AVG(
                     CASE WHEN read_at IS NOT NULL
                     THEN (julianday(read_at) - julianday(sent_at)) * 86400 END
                   )) AS INTEGER) AS avg_read_seconds
            FROM wake_log WHERE sent_at >= ?
            GROUP BY kind ORDER BY kind
            """,
            (since,),
        ).fetchall()
        return [dict(row) for row in rows]

    def record_wake(self, recipient_id: str, through_seq: int) -> None:
        recipient_id = self.recipient_key(recipient_id)
        self.connection.execute(
            """
            INSERT INTO wake_attempts(recipient_id, through_seq, status)
            VALUES (?, ?, 'sent')
            ON CONFLICT(recipient_id) DO UPDATE SET
              through_seq = excluded.through_seq,
              status = 'sent',
              sent_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
              processed_at = NULL
            """,
            (recipient_id, through_seq),
        )
        self.connection.commit()

    def mark_wake_processed(self, recipient_id: str, through_seq: int) -> None:
        recipient_id = self.recipient_key(recipient_id)
        self.connection.execute(
            """
            UPDATE wake_attempts SET
              status = 'processed',
              processed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE recipient_id = ? AND through_seq <= ?
            """,
            (recipient_id, through_seq),
        )
        self.connection.commit()
