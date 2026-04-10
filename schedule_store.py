import sqlite3
from pathlib import Path
from typing import Any


class ScheduleStore:
    def __init__(self, path: str | Path | None = None, max_seen_ids: int = 500):
        default_path = Path(__file__).resolve().parent / "schedule_state.db"
        self.path = Path(path) if path else default_path
        self.max_seen_ids = max_seen_ids
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()
        self.clear_active_runs()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS subscriptions (
                chat_id INTEGER NOT NULL,
                platform TEXT NOT NULL,
                PRIMARY KEY (chat_id, platform)
            );

            CREATE TABLE IF NOT EXISTS seen_job_ids (
                chat_id INTEGER NOT NULL,
                platform TEXT NOT NULL,
                job_id TEXT NOT NULL,
                seen_order INTEGER NOT NULL,
                PRIMARY KEY (chat_id, platform, job_id)
            );

            CREATE TABLE IF NOT EXISTS run_state (
                chat_id INTEGER NOT NULL,
                platform TEXT NOT NULL,
                last_run_at TEXT NOT NULL,
                last_result_count INTEGER NOT NULL,
                last_error TEXT,
                PRIMARY KEY (chat_id, platform)
            );

            CREATE TABLE IF NOT EXISTS active_runs (
                chat_id INTEGER NOT NULL,
                platform TEXT NOT NULL,
                started_at TEXT NOT NULL,
                PRIMARY KEY (chat_id, platform)
            );
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def clear_active_runs(self) -> None:
        self.conn.execute("DELETE FROM active_runs")
        self.conn.commit()

    def list_subscriptions(self) -> list[tuple[int, str]]:
        rows = self.conn.execute(
            "SELECT chat_id, platform FROM subscriptions ORDER BY chat_id, platform"
        ).fetchall()
        return [(int(row["chat_id"]), str(row["platform"])) for row in rows]

    def get_subscriptions(self, chat_id: int) -> list[str]:
        rows = self.conn.execute(
            "SELECT platform FROM subscriptions WHERE chat_id = ? ORDER BY platform",
            (chat_id,),
        ).fetchall()
        return [str(row["platform"]) for row in rows]

    def is_subscribed(self, chat_id: int, platform: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM subscriptions WHERE chat_id = ? AND platform = ?",
            (chat_id, platform),
        ).fetchone()
        return row is not None

    def add_subscription(self, chat_id: int, platform: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO subscriptions(chat_id, platform) VALUES (?, ?)",
            (chat_id, platform),
        )
        self.conn.commit()

    def remove_subscription(self, chat_id: int, platform: str) -> None:
        self.conn.execute(
            "DELETE FROM subscriptions WHERE chat_id = ? AND platform = ?",
            (chat_id, platform),
        )
        self.conn.execute(
            "DELETE FROM seen_job_ids WHERE chat_id = ? AND platform = ?",
            (chat_id, platform),
        )
        self.conn.execute(
            "DELETE FROM run_state WHERE chat_id = ? AND platform = ?",
            (chat_id, platform),
        )
        self.conn.execute(
            "DELETE FROM active_runs WHERE chat_id = ? AND platform = ?",
            (chat_id, platform),
        )
        self.conn.commit()

    def remove_all_subscriptions(self, chat_id: int) -> None:
        self.conn.execute("DELETE FROM subscriptions WHERE chat_id = ?", (chat_id,))
        self.conn.execute("DELETE FROM seen_job_ids WHERE chat_id = ?", (chat_id,))
        self.conn.execute("DELETE FROM run_state WHERE chat_id = ?", (chat_id,))
        self.conn.execute("DELETE FROM active_runs WHERE chat_id = ?", (chat_id,))
        self.conn.commit()

    def get_seen_ids(self, chat_id: int, platform: str) -> set[str]:
        rows = self.conn.execute(
            """
            SELECT job_id
            FROM seen_job_ids
            WHERE chat_id = ? AND platform = ?
            ORDER BY seen_order ASC
            """,
            (chat_id, platform),
        ).fetchall()
        return {str(row["job_id"]) for row in rows}

    def set_seen_ids(self, chat_id: int, platform: str, seen_ids: set[str]) -> None:
        kept_ids = sorted(seen_ids)[-self.max_seen_ids:]
        self.conn.execute(
            "DELETE FROM seen_job_ids WHERE chat_id = ? AND platform = ?",
            (chat_id, platform),
        )
        self.conn.executemany(
            """
            INSERT INTO seen_job_ids(chat_id, platform, job_id, seen_order)
            VALUES (?, ?, ?, ?)
            """,
            [
                (chat_id, platform, job_id, index)
                for index, job_id in enumerate(kept_ids, start=1)
            ],
        )
        self.conn.commit()

    def get_run_state(self, chat_id: int, platform: str) -> dict[str, Any]:
        row = self.conn.execute(
            """
            SELECT last_run_at, last_result_count, last_error
            FROM run_state
            WHERE chat_id = ? AND platform = ?
            """,
            (chat_id, platform),
        ).fetchone()
        if row is None:
            return {}
        return {
            "last_run_at": row["last_run_at"],
            "last_result_count": row["last_result_count"],
            "last_error": row["last_error"],
        }

    def set_run_state(
        self,
        chat_id: int,
        platform: str,
        *,
        last_run_at: str,
        last_result_count: int,
        last_error: str | None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO run_state(chat_id, platform, last_run_at, last_result_count, last_error)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, platform)
            DO UPDATE SET
                last_run_at = excluded.last_run_at,
                last_result_count = excluded.last_result_count,
                last_error = excluded.last_error
            """,
            (chat_id, platform, last_run_at, last_result_count, last_error),
        )
        self.conn.commit()

    def claim_run(self, chat_id: int, platform: str, *, started_at: str) -> bool:
        try:
            self.conn.execute(
                """
                INSERT INTO active_runs(chat_id, platform, started_at)
                VALUES (?, ?, ?)
                """,
                (chat_id, platform, started_at),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def release_run(self, chat_id: int, platform: str) -> None:
        self.conn.execute(
            "DELETE FROM active_runs WHERE chat_id = ? AND platform = ?",
            (chat_id, platform),
        )
        self.conn.commit()

    def is_run_active(self, chat_id: int, platform: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM active_runs WHERE chat_id = ? AND platform = ?",
            (chat_id, platform),
        ).fetchone()
        return row is not None
