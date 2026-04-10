import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _safe_iso_sort_key(value: str | None) -> tuple[int, str]:
    if not value:
        return (0, "")
    return (1, value)


def load_scheduler_snapshot(db_path: str | Path) -> dict[str, Any]:
    path = Path(db_path)
    if not path.exists():
        return {
            "subscriptions": [],
            "platform_rows": [],
            "summary": {
                "scheduled_platforms": 0,
                "active_runs": 0,
                "successful_platforms": 0,
                "error_platforms": 0,
            },
        }

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        subscriptions = conn.execute(
            "SELECT chat_id, platform FROM subscriptions ORDER BY chat_id, platform"
        ).fetchall()
        run_states = conn.execute(
            "SELECT chat_id, platform, last_run_at, last_result_count, last_error FROM run_state"
        ).fetchall()
        active_runs = conn.execute(
            "SELECT chat_id, platform, started_at FROM active_runs"
        ).fetchall()
        seen_counts = conn.execute(
            """
            SELECT chat_id, platform, COUNT(*) AS seen_count
            FROM seen_job_ids
            GROUP BY chat_id, platform
            """
        ).fetchall()
    finally:
        conn.close()

    run_state_map = {
        (int(row["chat_id"]), str(row["platform"])): {
            "last_run_at": row["last_run_at"],
            "last_result_count": int(row["last_result_count"]),
            "last_error": row["last_error"],
        }
        for row in run_states
    }
    active_run_map = {
        (int(row["chat_id"]), str(row["platform"])): row["started_at"]
        for row in active_runs
    }
    seen_count_map = {
        (int(row["chat_id"]), str(row["platform"])): int(row["seen_count"])
        for row in seen_counts
    }

    platform_rows = []
    subscriptions_by_chat: dict[int, list[str]] = defaultdict(list)
    for row in subscriptions:
        chat_id = int(row["chat_id"])
        platform = str(row["platform"])
        subscriptions_by_chat[chat_id].append(platform)

        run_state = run_state_map.get((chat_id, platform), {})
        active_started_at = active_run_map.get((chat_id, platform))

        platform_rows.append(
            {
                "chat_id": chat_id,
                "platform": platform,
                "last_run_at": run_state.get("last_run_at"),
                "last_result_count": run_state.get("last_result_count", 0),
                "last_error": run_state.get("last_error"),
                "active": active_started_at is not None,
                "active_started_at": active_started_at,
                "seen_job_count": seen_count_map.get((chat_id, platform), 0),
            }
        )

    platform_rows.sort(
        key=lambda row: (
            row["chat_id"],
            row["platform"],
            _safe_iso_sort_key(row["last_run_at"]),
        )
    )

    subscription_rows = [
        {"chat_id": chat_id, "platforms": sorted(platforms)}
        for chat_id, platforms in sorted(subscriptions_by_chat.items())
    ]

    summary = {
        "scheduled_platforms": len(platform_rows),
        "active_runs": sum(1 for row in platform_rows if row["active"]),
        "successful_platforms": sum(1 for row in platform_rows if row["last_run_at"] and not row["last_error"]),
        "error_platforms": sum(1 for row in platform_rows if row["last_error"]),
    }

    return {
        "subscriptions": subscription_rows,
        "platform_rows": platform_rows,
        "summary": summary,
    }


def _read_jsonl_entries(file_path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    if not file_path.exists():
        return []

    entries: list[dict[str, Any]] = []
    with file_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    entries.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
    if limit is not None:
        return entries[:limit]
    return entries


def load_api_status(log_root: str | Path, limit_per_source: int = 200) -> dict[str, Any]:
    root = Path(log_root)
    if not root.exists():
        return {"sources": [], "summary": {"sources": 0, "healthy": 0, "errors": 0}}

    source_entries: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for file_path in root.glob("*.jsonl"):
        for entry in _read_jsonl_entries(file_path, limit=None):
            source = str(entry.get("source", "unknown"))
            if len(source_entries[source]) < limit_per_source:
                source_entries[source].append(entry)

    sources = []
    for source, entries in sorted(source_entries.items()):
        latest = entries[0] if entries else {}
        latest_status = str(latest.get("status", "unknown"))
        recent_errors = sum(1 for entry in entries[:20] if str(entry.get("status", "")).startswith(("4", "5")) or entry.get("status") == "exception")
        healthy = latest_status not in {"exception", "already_running", "missing_dependency"} and not latest_status.startswith(("4", "5"))

        sources.append(
            {
                "source": source,
                "latest_status": latest_status,
                "latest_action": latest.get("action"),
                "latest_timestamp": latest.get("timestamp"),
                "recent_error_count": recent_errors,
                "healthy": healthy,
            }
        )

    summary = {
        "sources": len(sources),
        "healthy": sum(1 for source in sources if source["healthy"]),
        "errors": sum(1 for source in sources if not source["healthy"]),
    }
    return {"sources": sources, "summary": summary}


def load_recent_api_logs(log_root: str | Path, source: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    root = Path(log_root)
    if not root.exists():
        return []

    entries: list[dict[str, Any]] = []
    for file_path in root.glob("*.jsonl"):
        entries.extend(_read_jsonl_entries(file_path))

    if source:
        entries = [entry for entry in entries if str(entry.get("source")) == source]

    entries.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
    return entries[:limit]


def load_monitor_log_tail(log_path: str | Path, limit: int = 200) -> list[str]:
    path = Path(log_path)
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8") as handle:
        lines = [line.rstrip("\n") for line in handle.readlines()]
    return lines[-limit:]


def build_dashboard_snapshot(
    db_path: str | Path,
    api_log_root: str | Path,
    monitor_log_path: str | Path,
    *,
    api_log_limit: int = 100,
    monitor_line_limit: int = 120,
) -> dict[str, Any]:
    scheduler = load_scheduler_snapshot(db_path)
    api_status = load_api_status(api_log_root)
    recent_logs = load_recent_api_logs(api_log_root, limit=api_log_limit)
    monitor_tail = load_monitor_log_tail(monitor_log_path, limit=monitor_line_limit)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scheduler": scheduler,
        "api_status": api_status,
        "recent_api_logs": recent_logs,
        "monitor_log_tail": monitor_tail,
    }
