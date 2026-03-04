from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib import error, request

from .config import AudiobookConfig
from .types import AssetRef, ChapterDoc, SourceManifest


class MeiliClient:
    """Small Meilisearch HTTP client with index-scoped keys."""

    def __init__(self, cfg: AudiobookConfig) -> None:
        self.cfg = cfg

    def ensure_indexes(self) -> dict[str, Any]:
        tasks: dict[str, Any] = {}
        for index_uid in self._index_names().values():
            payload = {"uid": index_uid, "primaryKey": "id"}
            try:
                tasks[index_uid] = self._request(
                    "POST",
                    "/indexes",
                    payload=payload,
                    api_key=self.cfg.meili_master_key,
                )
            except RuntimeError as exc:
                # Index already exists returns an error object; tolerate and continue.
                if "index_already_exists" not in str(exc):
                    raise
                tasks[index_uid] = {"status": "exists"}
        return tasks

    def index_documents(
        self,
        source: SourceManifest,
        chapters: list[ChapterDoc],
        assets: list[AssetRef],
        chunk_size: int = 1200,
    ) -> dict[str, Any]:
        self.ensure_indexes()

        chunk_docs = build_chunk_documents(source.source_id, chapters, chunk_size=chunk_size)
        chapter_docs = build_chapter_documents(source.source_id, chapters)
        asset_docs = build_asset_documents(source.source_id, assets)

        result = {
            "chunks": self._add_documents(self.cfg.meili_index_doc_chunks, chunk_docs),
            "chapters": self._add_documents(self.cfg.meili_index_doc_chapters, chapter_docs),
            "assets": self._add_documents(self.cfg.meili_index_doc_assets, asset_docs),
            "counts": {
                "chunks": len(chunk_docs),
                "chapters": len(chapter_docs),
                "assets": len(asset_docs),
            },
            "indexes": self._index_names(),
        }
        return result

    def search_chunks(self, query: str, source_id: str, limit: int = 10) -> dict[str, Any]:
        payload = {
            "q": query,
            "limit": int(limit),
            "filter": f'source_id = "{source_id}"',
        }
        return self._request(
            "POST",
            f"/indexes/{self.cfg.meili_index_doc_chunks}/search",
            payload=payload,
            api_key=self.cfg.meili_key_doc_chunks,
        )

    def _add_documents(self, index_uid: str, docs: list[dict[str, Any]]) -> dict[str, Any]:
        if not docs:
            return {"taskUid": None, "count": 0}
        return self._request(
            "POST",
            f"/indexes/{index_uid}/documents",
            payload=docs,
            api_key=self._key_for_index(index_uid),
        )

    def _key_for_index(self, index_uid: str) -> str:
        if index_uid == self.cfg.meili_index_doc_chunks:
            return self.cfg.meili_key_doc_chunks or self.cfg.meili_master_key
        if index_uid == self.cfg.meili_index_doc_chapters:
            return self.cfg.meili_key_doc_chapters or self.cfg.meili_master_key
        if index_uid == self.cfg.meili_index_doc_assets:
            return self.cfg.meili_key_doc_assets or self.cfg.meili_master_key
        return self.cfg.meili_master_key

    def _index_names(self) -> dict[str, str]:
        return {
            "doc_chunks": self.cfg.meili_index_doc_chunks,
            "doc_chapters": self.cfg.meili_index_doc_chapters,
            "doc_assets": self.cfg.meili_index_doc_assets,
        }

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | list[dict[str, Any]] | None = None,
        api_key: str = "",
    ) -> dict[str, Any]:
        url = f"{self.cfg.meili_url.rstrip('/')}{path}"
        headers = {"Content-Type": "application/json"}
        key = api_key.strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"

        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = request.Request(url=url, data=data, headers=headers, method=method)

        try:
            with request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"meilisearch HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"meilisearch unavailable at {url}: {exc}") from exc


def build_chunk_documents(
    source_id: str,
    chapters: list[ChapterDoc],
    chunk_size: int = 1200,
) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for chapter in chapters:
        chunks = chunk_text(chapter.text, max_chars=chunk_size)
        for idx, chunk in enumerate(chunks):
            token = f"{source_id}:{chapter.chapter_id}:{idx}:{chunk}".encode("utf-8")
            digest = hashlib.sha1(token).hexdigest()[:12]
            docs.append(
                {
                    "id": f"chunk_{chapter.chapter_id}_{idx:04d}_{digest}",
                    "source_id": source_id,
                    "chapter_id": chapter.chapter_id,
                    "title": chapter.title,
                    "text": chunk,
                    "page_range": _page_range(chapter.page_start, chapter.page_end),
                    "asset_refs": list(chapter.asset_refs),
                }
            )
    return docs


def build_chapter_documents(source_id: str, chapters: list[ChapterDoc]) -> list[dict[str, Any]]:
    docs = []
    for chapter in chapters:
        token = f"{source_id}:{chapter.chapter_id}:{chapter.title}".encode("utf-8")
        digest = hashlib.sha1(token).hexdigest()[:12]
        docs.append(
            {
                "id": f"chapter_{chapter.chapter_id}_{digest}",
                "source_id": source_id,
                "chapter_id": chapter.chapter_id,
                "title": chapter.title,
                "text": chapter.text,
                "page_range": _page_range(chapter.page_start, chapter.page_end),
                "asset_refs": list(chapter.asset_refs),
            }
        )
    return docs


def build_asset_documents(source_id: str, assets: list[AssetRef]) -> list[dict[str, Any]]:
    docs = []
    for asset in assets:
        docs.append(
            {
                "id": f"asset_{source_id}_{asset.asset_id}",
                "source_id": source_id,
                "asset_id": asset.asset_id,
                "page_id": asset.page_id,
                "file_path": asset.file_path,
                "chapter_id": asset.chapter_id,
                "anchor_text": asset.anchor_text,
            }
        )
    return docs


def chunk_text(text: str, max_chars: int = 1200) -> list[str]:
    """Deterministic chunker favoring paragraph boundaries."""

    text = text.strip()
    if not text:
        return []

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para)
        if para_len > max_chars:
            if current:
                chunks.append("\n\n".join(current))
                current, current_len = [], 0
            chunks.extend(_split_long_paragraph(para, max_chars=max_chars))
            continue

        projected = current_len + (2 if current else 0) + para_len
        if projected <= max_chars:
            current.append(para)
            current_len = projected
        else:
            if current:
                chunks.append("\n\n".join(current))
            current = [para]
            current_len = para_len

    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _split_long_paragraph(paragraph: str, max_chars: int) -> list[str]:
    words = paragraph.split()
    if not words:
        return []

    out: list[str] = []
    cur: list[str] = []
    cur_len = 0

    for word in words:
        add = len(word) + (1 if cur else 0)
        if cur_len + add <= max_chars:
            cur.append(word)
            cur_len += add
            continue

        if cur:
            out.append(" ".join(cur))
        cur = [word]
        cur_len = len(word)

    if cur:
        out.append(" ".join(cur))
    return out


def _page_range(start: int | None, end: int | None) -> str | None:
    if start is None and end is None:
        return None
    if start is None:
        return f"{end}-{end}"
    if end is None:
        return f"{start}-{start}"
    return f"{start}-{end}"


def write_index_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
