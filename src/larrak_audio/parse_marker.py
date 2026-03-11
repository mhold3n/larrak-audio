from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .types import AssetRef, ChapterDoc
from .utils import read_json

_IMAGE_MD_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_IMG_HTML_RE = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.IGNORECASE)
_PAGE_RE = re.compile(r"_page_(\d+)_", re.IGNORECASE)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def build_assets_and_chapters(
    markdown_path: Path,
    marker_output_dir: Path,
    source_id: str,
) -> tuple[list[AssetRef], list[ChapterDoc]]:
    """Parse marker outputs into chapter and asset manifests."""

    _ = source_id  # reserved for future deterministic ID expansion.

    markdown = markdown_path.read_text(encoding="utf-8")
    toc_map = _load_toc_page_map(marker_output_dir)
    chapters = _split_markdown_into_chapters(markdown)
    markdown_assets = [(path, _page_id_from_asset_name(path)) for path in _extract_asset_paths(markdown)]
    block_assets = _extract_assets_from_blocks(marker_output_dir)

    assets: list[AssetRef] = []
    all_assets = _dedupe_asset_candidates([*markdown_assets, *block_assets])
    for idx, (asset_path, page_hint) in enumerate(all_assets):
        normalized = asset_path.strip()
        page_id = page_hint if page_hint is not None else _page_id_from_asset_name(normalized)
        chapter_id, anchor = _infer_chapter_for_asset(normalized, page_id, chapters, toc_map)
        assets.append(
            AssetRef(
                asset_id=f"asset_{idx:05d}",
                page_id=page_id,
                file_path=_resolve_asset_path(normalized, marker_output_dir),
                chapter_id=chapter_id,
                anchor_text=anchor,
            )
        )

    docs: list[ChapterDoc] = []
    for chapter_id, title, text in chapters:
        chapter_asset_ids = [a.asset_id for a in assets if a.chapter_id == chapter_id]
        page_start, page_end = _page_range_for_chapter(title, text, toc_map, chapter_asset_ids, assets)
        docs.append(
            ChapterDoc(
                chapter_id=chapter_id,
                title=title,
                text=text.strip(),
                page_start=page_start,
                page_end=page_end,
                asset_refs=chapter_asset_ids,
            )
        )

    if not docs:
        docs.append(
            ChapterDoc(
                chapter_id="chapter_000",
                title="Document",
                text=markdown.strip(),
                page_start=None,
                page_end=None,
                asset_refs=[a.asset_id for a in assets],
            )
        )

    return assets, docs


def _extract_asset_paths(markdown: str) -> list[str]:
    paths = []
    paths.extend(_IMAGE_MD_RE.findall(markdown))
    paths.extend(_IMG_HTML_RE.findall(markdown))

    seen: set[str] = set()
    out: list[str] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        out.append(path)
    return out


def _extract_assets_from_blocks(marker_output_dir: Path) -> list[tuple[str, int | None]]:
    out: list[tuple[str, int | None]] = []
    visual_types = {"11", "14", "15", "16", "18", "20"}
    for blocks_path in sorted(marker_output_dir.rglob("blocks.json")):
        try:
            data = read_json(blocks_path)
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for row in data:
            if not isinstance(row, dict):
                continue
            block_type = str(row.get("block_type", ""))
            if block_type not in visual_types:
                continue

            page_id_raw = row.get("page_id")
            page_id = int(page_id_raw) if isinstance(page_id_raw, int) else None

            path = None
            for key in ("highres_image", "lowres_image"):
                val = row.get(key)
                if isinstance(val, str) and val.strip():
                    path = val.strip()
                    break

            if path is None:
                block_id = row.get("block_id", "x")
                path = f"blocks.json#page_{page_id if page_id is not None else 'x'}_block_{block_id}"
            out.append((path, page_id))

    return out


def _dedupe_asset_candidates(candidates: list[tuple[str, int | None]]) -> list[tuple[str, int | None]]:
    seen: set[tuple[str, int | None]] = set()
    out: list[tuple[str, int | None]] = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        out.append(candidate)
    return out


def _split_markdown_into_chapters(markdown: str) -> list[tuple[str, str, str]]:
    lines = markdown.splitlines()
    chapter_headers: list[tuple[int, str, int]] = []
    for idx, line in enumerate(lines):
        match = _HEADING_RE.match(line)
        if not match:
            continue
        level = len(match.group(1))
        title = match.group(2).strip()
        if level > 2:
            continue
        chapter_headers.append((idx, title, level))

    if not chapter_headers:
        return [("chapter_000", "Document", markdown)]

    chapters: list[tuple[str, str, str]] = []
    for pos, (line_idx, title, _level) in enumerate(chapter_headers):
        next_idx = chapter_headers[pos + 1][0] if pos + 1 < len(chapter_headers) else len(lines)
        chunk = "\n".join(lines[line_idx:next_idx]).strip()
        chapter_id = f"chapter_{pos:03d}"
        chapters.append((chapter_id, title, chunk))
    return chapters


def _load_toc_page_map(marker_output_dir: Path) -> dict[str, int]:
    meta_candidates = sorted(marker_output_dir.rglob("*_meta.json"))
    if not meta_candidates:
        return {}

    try:
        meta = read_json(meta_candidates[0])
    except Exception:
        return {}

    toc_entries = meta.get("table_of_contents", []) if isinstance(meta, dict) else []
    mapping: dict[str, int] = {}
    for item in toc_entries:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        page_id = item.get("page_id")
        if not title or not isinstance(page_id, int):
            continue
        mapping[_norm_title(title)] = page_id
    return mapping


def _infer_chapter_for_asset(
    asset_rel_path: str,
    page_id: int | None,
    chapters: list[tuple[str, str, str]],
    toc_map: dict[str, int],
) -> tuple[str, str]:
    for chapter_id, title, text in chapters:
        if asset_rel_path in text:
            return chapter_id, title

    if page_id is not None:
        chapter = _infer_chapter_for_page(page_id, chapters, toc_map)
        if chapter is not None:
            return chapter

    if chapters:
        return chapters[0][0], chapters[0][1]
    return "chapter_000", "Document"


def _infer_chapter_for_page(
    page_id: int,
    chapters: list[tuple[str, str, str]],
    toc_map: dict[str, int],
) -> tuple[str, str] | None:
    toc_chapters: list[tuple[int, str, str]] = []
    for chapter_id, title, _ in chapters:
        toc_page = _lookup_toc_page(title, toc_map)
        if toc_page is not None:
            toc_chapters.append((toc_page, chapter_id, title))

    if not toc_chapters:
        return None

    toc_chapters.sort(key=lambda item: item[0])
    best = toc_chapters[0]
    for candidate in toc_chapters:
        if candidate[0] <= page_id:
            best = candidate
        else:
            break
    return best[1], best[2]


def _resolve_asset_path(asset_rel_path: str, marker_output_dir: Path) -> str:
    candidate = (marker_output_dir / asset_rel_path).resolve()
    if candidate.exists():
        return str(candidate)
    for found in marker_output_dir.rglob(Path(asset_rel_path).name):
        return str(found.resolve())
    return str(candidate)


def _page_id_from_asset_name(asset_rel_path: str) -> int | None:
    match = _PAGE_RE.search(Path(asset_rel_path).name)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _page_range_for_chapter(
    title: str,
    chapter_text: str,
    toc_map: dict[str, int],
    chapter_asset_ids: list[str],
    assets: list[AssetRef],
) -> tuple[int | None, int | None]:
    page_from_toc = _lookup_toc_page(title, toc_map)
    pages = [a.page_id for a in assets if a.asset_id in set(chapter_asset_ids) and a.page_id is not None]

    if page_from_toc is not None:
        if not pages:
            return page_from_toc, page_from_toc
        return min([page_from_toc, *pages]), max([page_from_toc, *pages])

    inline_pages = [_page_id_from_asset_name(path) for path in _extract_asset_paths(chapter_text)]
    inline_pages = [p for p in inline_pages if p is not None]
    if pages or inline_pages:
        merged = [*pages, *inline_pages]
        return min(merged), max(merged)
    return None, None


def _lookup_toc_page(title: str, toc_map: dict[str, int]) -> int | None:
    norm = _norm_title(title)
    if norm in toc_map:
        return toc_map[norm]

    for toc_title, page in toc_map.items():
        if norm in toc_title or toc_title in norm:
            return page
    return None


def _norm_title(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"[^a-z0-9 ]+", "", value)
    return value


def blocks_summary(blocks_path: Path) -> dict[str, Any]:
    """Lightweight block-type count helper used by diagnostics/tests."""

    if not blocks_path.exists():
        return {"block_types": {}, "total": 0}

    try:
        data = read_json(blocks_path)
    except Exception:
        return {"block_types": {}, "total": 0}

    if not isinstance(data, list):
        return {"block_types": {}, "total": 0}

    counts: dict[str, int] = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        btype = str(row.get("block_type", "unknown"))
        counts[btype] = counts.get(btype, 0) + 1
    return {"block_types": counts, "total": sum(counts.values())}
