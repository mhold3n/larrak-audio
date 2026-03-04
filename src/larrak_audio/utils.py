from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def stable_source_id(source_path: Path, source_type: str) -> str:
    raw = f"{source_path.resolve()}::{source_type}".encode("utf-8")
    digest = hashlib.sha1(raw).hexdigest()[:10]
    slug = slugify(source_path.stem)
    return f"{slug}-{digest}"


def slugify(text: str) -> str:
    out = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return out or "source"


def write_json(path: Path, payload: dict[str, Any] | list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def infer_source_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".md":
        return "md"
    if suffix in {".txt", ".text"}:
        return "txt"
    raise ValueError(f"unsupported source extension: {path}")
