from __future__ import annotations

import json
import re
from pathlib import Path
from urllib import error, request

from .config import AudiobookConfig
from .types import AssetRef, ChapterDoc

VISUAL_NOTE = "See additional materials for visual reference."
_IMAGE_MD_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_PAGE_RE = re.compile(r"_page_(\d+)_", re.IGNORECASE)


def enhance_chapters(
    chapters: list[ChapterDoc],
    assets: list[AssetRef],
    cfg: AudiobookConfig,
    enable_cleanup: bool,
) -> list[ChapterDoc]:
    """Apply deterministic visual-note insertion + optional cleanup via local Ollama."""

    assets_by_id = {asset.asset_id: asset for asset in assets}
    out: list[ChapterDoc] = []

    for chapter in chapters:
        text = insert_visual_notes(chapter.text, chapter.asset_refs, assets_by_id)
        if enable_cleanup:
            text = cleanup_text_with_ollama(text, cfg)
        out.append(
            ChapterDoc(
                chapter_id=chapter.chapter_id,
                title=chapter.title,
                text=text,
                page_start=chapter.page_start,
                page_end=chapter.page_end,
                asset_refs=list(chapter.asset_refs),
            )
        )

    return out


def insert_visual_notes(
    markdown: str,
    chapter_asset_ids: list[str],
    assets_by_id: dict[str, AssetRef],
) -> str:
    """Inject a narration-safe note for visual-only references."""

    lines = markdown.splitlines()
    out: list[str] = []

    chapter_assets = [assets_by_id[aid] for aid in chapter_asset_ids if aid in assets_by_id]
    asset_by_filename = {Path(asset.file_path).name: asset for asset in chapter_assets}

    idx = 0
    while idx < len(lines):
        line = lines[idx]
        out.append(line)

        image_match = _IMAGE_MD_RE.search(line)
        if image_match:
            asset_name = Path(image_match.group(1)).name
            asset = asset_by_filename.get(asset_name)
            out.append(_note_line_for_asset(asset_name, asset))
            idx += 1
            continue

        if _is_table_start(lines, idx):
            out.append(VISUAL_NOTE)
            idx = _consume_table_block(lines, idx, out)
            continue

        idx += 1

    # If no inline visuals but chapter has visual assets, append one trailing note.
    if chapter_assets and VISUAL_NOTE not in "\n".join(out):
        first = chapter_assets[0]
        out.append("")
        out.append(_note_line_for_asset(Path(first.file_path).name, first))

    return "\n".join(out).strip()


def _note_line_for_asset(asset_name: str, asset: AssetRef | None) -> str:
    page = asset.page_id if asset is not None else _page_from_name(asset_name)
    if page is None:
        return f"{VISUAL_NOTE} (asset: {asset_name})"
    return f"{VISUAL_NOTE} (asset: {asset_name}, page: {page})"


def _page_from_name(name: str) -> int | None:
    match = _PAGE_RE.search(name)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _is_table_start(lines: list[str], idx: int) -> bool:
    if idx + 1 >= len(lines):
        return False
    cur = lines[idx].strip()
    nxt = lines[idx + 1].strip()
    if not cur.startswith("|"):
        return False
    # Markdown table delimiter row like |---|---|
    return nxt.startswith("|") and "---" in nxt


def _consume_table_block(lines: list[str], idx: int, out: list[str]) -> int:
    cur = idx + 1
    while cur < len(lines):
        out.append(lines[cur])
        next_cur = cur + 1
        if next_cur >= len(lines) or not lines[next_cur].strip().startswith("|"):
            return next_cur
        cur = next_cur
    return cur


def cleanup_text_with_ollama(text: str, cfg: AudiobookConfig) -> str:
    """Optional local-only cleanup pass using an Ollama model."""

    payload = {
        "model": cfg.ollama_model_cleanup,
        "stream": False,
        "prompt": (
            "You are cleaning OCR markdown for audiobook narration. "
            "Do not summarize. Do not add facts. Preserve technical terms, numbers, and units. "
            "Fix broken spacing, obvious OCR artifacts, and malformed punctuation. "
            "Return plain markdown only.\n\n"
            f"INPUT:\n{text}"
        ),
    }
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url=f"{cfg.ollama_base_url.rstrip('/')}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (error.URLError, TimeoutError, json.JSONDecodeError):
        # Fail-soft: return unmodified text when cleanup backend is unavailable.
        return text

    response = str(data.get("response", "")).strip()
    return response or text
