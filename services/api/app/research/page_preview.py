from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import fitz

from .normalization import VERSION, normalized_hash
from .store import ResearchStore


MAX_PIXELS = 16_000_000
MAX_CACHE_BYTES = 512 * 1024 * 1024
RENDER_LIMIT = threading.BoundedSemaphore(2)


class PagePreviewService:
    def __init__(self, store: ResearchStore):
        self.store = store
        self.cache_dir = store.index_dir / "page-previews"

    def _document_path(self, document: dict) -> Path:
        path = Path(document["blob_path"]).resolve()
        if not path.is_relative_to(self.store.blob_dir.resolve()) or not path.is_file():
            raise FileNotFoundError("document blob is unavailable")
        if document.get("media_type") != "application/pdf":
            raise ValueError("document is not a PDF")
        return path

    def locator(self, evidence_id: str) -> dict:
        evidence = self.store.get_evidence_span(evidence_id)
        if not evidence:
            raise KeyError("evidence not found")
        document = self.store.get_document(evidence["document_id"])
        if not document:
            raise KeyError("document not found")
        path = self._document_path(document)
        page_number = int(evidence.get("page") or 1)
        if page_number < 1 or page_number > int(document.get("page_count") or 0):
            raise ValueError("evidence page is outside the document")
        quote, _, digest = normalized_hash(evidence.get("quote") or "")
        with fitz.open(path) as pdf:
            page = pdf.load_page(page_number - 1)
            raw_page_text = page.get_text("text")
            page_text, mapping, _ = normalized_hash(raw_page_text)
            occurrences = page_text.count(quote) if quote else 0
            rectangles: list[list[float]] = []
            if occurrences == 1:
                normalized_start = page_text.index(quote)
                normalized_end = normalized_start + len(quote)
                spans = [item for item in mapping if item[0] < normalized_end and item[1] > normalized_start]
                if spans:
                    original_start = min(item[2] for item in spans)
                    original_end = max(item[3] for item in spans)
                    search_text = raw_page_text[original_start:original_end].strip()
                    rectangles = [list(rect) for rect in page.search_for(search_text)][:16]
        status = "verified" if occurrences == 1 and rectangles else "limited"
        self.store.save_evidence_normalization(
            evidence_id, algorithm_version=VERSION, normalized_hash_value=digest,
            page=page_number, char_map=mapping,
        )
        return {
            "evidence_id": evidence_id, "document_id": evidence["document_id"], "page": page_number,
            "page_count": int(document.get("page_count") or 0), "quote": evidence.get("quote") or "",
            "rects": rectangles, "match_count": occurrences, "status": status,
            "normalization_version": VERSION, "normalized_hash": digest,
            "image_url": f"/api/vnext/knowledge/documents/{evidence['document_id']}/pages/{page_number}/image?dpi=144",
        }

    def render(self, document_id: str, page_number: int, dpi: int) -> tuple[bytes, str]:
        document = self.store.get_document(document_id)
        if not document:
            raise KeyError("document not found")
        page_count = int(document.get("page_count") or 0)
        if page_number < 1 or page_number > page_count:
            raise ValueError("page is outside the document")
        dpi = int(dpi)
        if dpi < 72 or dpi > 180:
            raise ValueError("DPI must be between 72 and 180")
        path = self._document_path(document)
        cache_key = hashlib.sha256(f"{document['content_hash']}:{page_number}:{dpi}".encode()).hexdigest()
        target = self.cache_dir / cache_key[:2] / f"{cache_key}.png"
        if target.exists():
            target.touch()
            return target.read_bytes(), cache_key
        if not RENDER_LIMIT.acquire(timeout=2):
            raise TimeoutError("page renderer is busy")
        try:
            with fitz.open(path) as pdf:
                page = pdf.load_page(page_number - 1)
                scale = dpi / 72
                pixels = int(page.rect.width * scale) * int(page.rect.height * scale)
                if pixels > MAX_PIXELS:
                    raise ValueError("rendered page exceeds pixel budget")
                payload = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".tmp")
            temporary.write_bytes(payload)
            temporary.replace(target)
            self._evict_cache()
            return payload, cache_key
        finally:
            RENDER_LIMIT.release()

    def _evict_cache(self) -> None:
        files = sorted(self.cache_dir.rglob("*.png"), key=lambda item: item.stat().st_mtime)
        total = sum(item.stat().st_size for item in files)
        for item in files:
            if total <= MAX_CACHE_BYTES:
                break
            size = item.stat().st_size
            item.unlink(missing_ok=True)
            total -= size
