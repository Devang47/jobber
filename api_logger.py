import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOG_ROOT = Path(__file__).resolve().parent / "logs" / "api"


def _sanitize(value: Any, max_string_length: int = 800, max_items: int = 25) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                sanitized["..."] = f"{len(value) - max_items} more keys"
                break
            sanitized[str(key)] = _sanitize(item, max_string_length, max_items)
        return sanitized

    if isinstance(value, (list, tuple, set)):
        items = list(value)
        sanitized = [
            _sanitize(item, max_string_length, max_items)
            for item in items[:max_items]
        ]
        if len(items) > max_items:
            sanitized.append(f"... {len(items) - max_items} more items")
        return sanitized

    if isinstance(value, str):
        if len(value) <= max_string_length:
            return value
        return f"{value[:max_string_length]}... ({len(value) - max_string_length} chars truncated)"

    return value


def log_api_event(source: str, action: str, status: str | int, payload: Any = None, **metadata: Any) -> None:
    os.makedirs(LOG_ROOT, exist_ok=True)
    now = datetime.now(timezone.utc)
    entry = {
        "timestamp": now.isoformat(),
        "source": source,
        "action": action,
        "status": str(status),
        "payload": _sanitize(payload),
        "metadata": _sanitize(metadata),
    }
    file_path = LOG_ROOT / f"{source}-{now.strftime('%Y-%m-%d')}.jsonl"
    with file_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=True) + "\n")
