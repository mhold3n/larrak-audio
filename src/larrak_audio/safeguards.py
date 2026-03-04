from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .config import AudiobookConfig
from .utils import read_json, utc_now_iso, write_json

STATE_FILENAME = ".api_guard_state.json"


def parse_float_setting(value: str | float | int | None, *, default: float, minimum: float = 0.0) -> float:
    try:
        parsed = float(value) if value is not None else float(default)
    except (TypeError, ValueError):
        parsed = float(default)
    if parsed < minimum:
        return minimum
    return parsed


def parse_int_setting(value: str | int | None, *, default: int, minimum: int = 0) -> int:
    try:
        parsed = int(value) if value is not None else int(default)
    except (TypeError, ValueError):
        parsed = int(default)
    if parsed < minimum:
        return minimum
    return parsed


def get_provider_state(cfg: AudiobookConfig, provider: str) -> dict[str, Any]:
    payload = _load_state(_state_path(cfg))
    row = payload.get(provider, {})
    return dict(row) if isinstance(row, dict) else {}


def enforce_min_interval(cfg: AudiobookConfig, provider: str, min_interval_s: float) -> float:
    min_interval_s = max(0.0, float(min_interval_s))
    if min_interval_s <= 0:
        return 0.0

    row = get_provider_state(cfg, provider)
    now = time.time()
    last = row.get("last_request_unix")
    if not isinstance(last, (int, float)):
        return 0.0

    elapsed = now - float(last)
    wait_s = min_interval_s - elapsed
    if wait_s <= 0:
        return 0.0

    time.sleep(wait_s)
    return wait_s


def record_provider_event(cfg: AudiobookConfig, provider: str, **fields: Any) -> None:
    path = _state_path(cfg)
    payload = _load_state(path)

    row_any = payload.get(provider, {})
    row = dict(row_any) if isinstance(row_any, dict) else {}
    row["last_request_unix"] = time.time()
    row["last_request_at"] = utc_now_iso()

    for key, value in fields.items():
        row[key] = value

    payload[provider] = row
    write_json(path, payload)


def _state_path(cfg: AudiobookConfig) -> Path:
    return Path(cfg.output_root).resolve() / "research" / STATE_FILENAME


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = read_json(path)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}
