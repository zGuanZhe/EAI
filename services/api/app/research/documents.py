from __future__ import annotations

import io
import re
import uuid
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from .store import ResearchStore, content_hash, stable_id, utc_now


def section_chunks(pages: list[dict[str, Any]], *, target_chars: int = 3200, overlap_chars: int = 320) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for page in pages:
        text = re.sub(r"\x00", "", str(page.get("text") or "")).strip()
        if not text:
            continue
        start = 0
        while start < len(text):
            end = min(len(text), start + target_chars)
            if end < len(text):
                candidates = [text.rfind("\n", start + target_chars // 2, end), text.rfind("。", start + target_chars // 2, end), text.rfind(". ", start + target_chars // 2, end)]
                boundary = max(candidates)
                if boundary > start:
                    end = boundary + 1
            value = text[start:end].strip()
            if value:
                index = len(chunks)
                chunks.append({
                    "id": stable_id("chunk", page.get("page"), index, value),
                    "index": index,
                    "section": page.get("section") or f"第 {page.get('page', 1)} 页",
                    "page": page.get("page"),
                    "start_offset": start,
                    "end_offset": end,
                    "text": value,
                    "content_hash": content_hash(value),
                    "locator": {**(page.get("locator") or {}), "page": page.get("page"), "blocks": page.get("blocks") or []},
                })
            if end >= len(text):
                break
            start = max(start + 1, end - overlap_chars)
    return chunks


def parse_pdf(content: bytes) -> tuple[list[dict[str, Any]], str]:
    try:
        import fitz

        document = fitz.open(stream=content, filetype="pdf")
        pages = []
        for index, page in enumerate(document):
            blocks = []
            text_parts = []
            for block in page.get_text("blocks", sort=True):
                if len(block) < 5 or not str(block[4]).strip():
                    continue
                text_parts.append(str(block[4]).strip())
                blocks.append({"bbox": [round(float(value), 2) for value in block[:4]], "kind": int(block[6]) if len(block) > 6 else 0})
            pages.append({"page": index + 1, "section": f"第 {index + 1} 页", "text": "\n".join(text_parts), "blocks": blocks,
                          "locator": {"width": round(page.rect.width, 2), "height": round(page.rect.height, 2)}})
        return pages, "pymupdf"
    except (ImportError, RuntimeError, ValueError):
        reader = PdfReader(io.BytesIO(content))
        return [
            {"page": index + 1, "section": f"第 {index + 1} 页", "text": page.extract_text() or "", "locator": {}}
            for index, page in enumerate(reader.pages)
        ], "pypdf"


def import_document(
    store: ResearchStore,
    *,
    file_name: str,
    media_type: str,
    content: bytes,
    title: str | None = None,
    work_id: str | None = None,
    source_kind: str = "user",
    access: str = "local",
    evictable: bool = False,
) -> dict[str, Any]:
    suffix = Path(file_name).suffix.lower()
    if suffix not in {".pdf", ".txt", ".md", ".markdown"}:
        raise ValueError("仅支持 PDF、Markdown 和纯文本文件。")
    digest = content_hash(content)
    existing = store.find_document_by_hash(digest)
    if existing:
        return existing
    if suffix == ".pdf":
        pages, parser = parse_pdf(content)
    else:
        text = content.decode("utf-8", errors="replace")
        pages, parser = [{"page": 1, "section": "正文", "text": text, "locator": {}}], "plain_text"
    target_dir = store.blob_dir / digest[:2]
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{digest}{suffix}"
    if not target.exists():
        target.write_bytes(content)
    document_id = f"document_{uuid.uuid4().hex[:16]}"
    chunks = section_chunks(pages)
    document = {
        "id": document_id,
        "work_id": work_id,
        "title": (title or Path(file_name).stem).strip()[:500],
        "file_name": Path(file_name).name,
        "media_type": media_type or ("application/pdf" if suffix == ".pdf" else "text/plain"),
        "blob_path": str(target),
        "content_hash": digest,
        "source_kind": source_kind,
        "access": access,
        "evictable": evictable,
        "page_count": len(pages),
        "chunk_count": len(chunks),
        "parser": parser,
        "created_at": utc_now(),
    }
    saved = store.save_document(document, chunks)
    store.enforce_cache_limit()
    return saved
