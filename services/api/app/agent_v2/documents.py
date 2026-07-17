from __future__ import annotations

import hashlib
import io
import os
import re
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
from pypdf import PdfReader

from .models import DocumentRecord, SourceRecord
from .store import RuntimeStore


def _chunks(text: str, *, size: int = 1800, overlap: int = 180) -> list[str]:
    clean = re.sub(r"\x00", "", text or "").strip()
    if not clean:
        return []
    result: list[str] = []
    start = 0
    while start < len(clean):
        end = min(len(clean), start + size)
        if end < len(clean):
            boundary = max(clean.rfind("\n", start + size // 2, end), clean.rfind("。", start + size // 2, end))
            if boundary > start:
                end = boundary + 1
        result.append(clean[start:end].strip())
        if end >= len(clean):
            break
        start = max(start + 1, end - overlap)
    return [item for item in result if item]


def import_document(
    store: RuntimeStore,
    *,
    file_name: str,
    media_type: str,
    content: bytes,
    title: str | None,
    created_at: str,
) -> DocumentRecord:
    digest = hashlib.sha256(content).hexdigest()
    existing = store.find_document_by_hash(digest)
    if existing:
        return existing
    suffix = Path(file_name).suffix.lower()
    if suffix not in {".pdf", ".txt", ".md", ".markdown"}:
        raise ValueError("仅支持 PDF、Markdown 和纯文本文件。")
    document_id = f"document_{uuid.uuid4().hex[:16]}"
    target = store.documents_dir / f"{document_id}{suffix}"
    target.write_bytes(content)
    pages: list[tuple[str, str, dict[str, int]]] = []
    if suffix == ".pdf":
        reader = PdfReader(io.BytesIO(content))
        for page_index, page in enumerate(reader.pages):
            pages.append((f"第 {page_index + 1} 页", page.extract_text() or "", {"page": page_index + 1}))
        page_count = len(reader.pages)
    else:
        text = content.decode("utf-8", errors="replace")
        pages = [("正文", text, {"section": 1})]
        page_count = 1
    chunks: list[dict] = []
    index = 0
    for section, text, locator in pages:
        for chunk_text in _chunks(text):
            chunks.append(
                {
                    "id": f"chunk_{uuid.uuid4().hex[:16]}",
                    "index": index,
                    "section": section,
                    "text": chunk_text,
                    "locator": locator,
                }
            )
            index += 1
    document = DocumentRecord(
        id=document_id,
        title=(title or Path(file_name).stem).strip()[:240],
        file_name=Path(file_name).name,
        media_type=media_type or ("application/pdf" if suffix == ".pdf" else "text/plain"),
        path=str(target),
        content_hash=digest,
        page_count=page_count,
        chunk_count=len(chunks),
        created_at=created_at,
    )
    return store.save_document(document, chunks)


OPEN_PDF_HOSTS = {
    "arxiv.org",
    "export.arxiv.org",
    "openreview.net",
    "aclanthology.org",
}


def import_open_source(
    store: RuntimeStore,
    source: SourceRecord,
    *,
    created_at: str,
    client_factory=None,
    max_bytes: int = 100 * 1024 * 1024,
) -> DocumentRecord:
    url = str(source.locator.get("pdf_url") or "")
    parsed = urlparse(url)
    allowed_hosts = OPEN_PDF_HOSTS | {item.strip().lower() for item in os.environ.get("EAI_OPEN_PDF_HOSTS", "").split(",") if item.strip()}
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in allowed_hosts:
        raise ValueError("该开放全文域名未列入可信下载列表。")
    factory = client_factory or (lambda: httpx.Client(timeout=httpx.Timeout(90, connect=8), follow_redirects=True, headers={"User-Agent": "EAI-Desktop/0.2"}))
    content = bytearray()
    with factory() as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            final_host = (response.url.host or "").lower()
            if final_host not in allowed_hosts:
                raise ValueError("开放全文重定向到了未授权域名。")
            length = int(response.headers.get("content-length") or 0)
            if length > max_bytes:
                raise ValueError("开放全文超过 100 MB 限制。")
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > max_bytes:
                    raise ValueError("开放全文超过 100 MB 限制。")
    if not bytes(content[:5]).startswith(b"%PDF-"):
        raise ValueError("开放全文响应不是 PDF。")
    return import_document(
        store,
        file_name=f"{source.id}.pdf",
        media_type="application/pdf",
        content=bytes(content),
        title=source.title,
        created_at=created_at,
    )
