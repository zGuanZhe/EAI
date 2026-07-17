from __future__ import annotations

import re
import threading
import time
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import quote, quote_plus, urlparse

import httpx

from .documents import import_document
from .models import SyncJob, SyncRequest
from .store import ResearchStore


ATOM = {"atom": "http://www.w3.org/2005/Atom"}


class KnowledgeEnrichmentService:
    def __init__(self, store: ResearchStore, *, client_factory=None):
        self.store = store
        self.client_factory = client_factory or (
            lambda: httpx.Client(
                timeout=httpx.Timeout(30, connect=8), follow_redirects=True,
                headers={"User-Agent": "EAI-Desktop/0.2 knowledge-enrichment"},
            )
        )
        self._workers: dict[str, threading.Thread] = {}
        self._lock = threading.RLock()

    def start(self, request: SyncRequest) -> SyncJob:
        job = self.store.create_sync_job(request.scope, request.atlas_ids, request.work_ids)
        worker = threading.Thread(target=self._run, args=(job.id,), daemon=True, name=f"eai-knowledge-{job.id[-8:]}")
        with self._lock:
            self._workers[job.id] = worker
        worker.start()
        return job

    def ensure_initial_metadata_sync(self) -> SyncJob | None:
        latest = self.store.latest_sync_job("metadata")
        if latest and latest.status in {"pending", "running"}:
            with self._lock:
                worker = self._workers.get(latest.id)
                if not worker or not worker.is_alive():
                    worker = threading.Thread(
                        target=self._run, args=(latest.id,), daemon=True, name=f"eai-knowledge-{latest.id[-8:]}"
                    )
                    self._workers[latest.id] = worker
                    worker.start()
            return latest
        if latest and latest.status in {"paused", "done"}:
            return latest
        return self.start(SyncRequest(scope="metadata"))

    def pause(self, job_id: str) -> SyncJob:
        job = self._job(job_id)
        if job.status in {"pending", "running"}:
            job.status = "paused"
            self.store.save_sync_job(job)
            self.store.append_sync_event(job.id, "status", {"status": "paused", "label": "同步已暂停"})
        return job

    def resume(self, job_id: str) -> SyncJob:
        job = self._job(job_id)
        if job.status != "paused":
            return job
        job.status = "running"
        self.store.save_sync_job(job)
        with self._lock:
            worker = self._workers.get(job_id)
            if not worker or not worker.is_alive():
                worker = threading.Thread(target=self._run, args=(job.id,), daemon=True, name=f"eai-knowledge-{job.id[-8:]}")
                self._workers[job.id] = worker
                worker.start()
        return job

    def cancel(self, job_id: str) -> SyncJob:
        job = self._job(job_id)
        if job.status not in {"done", "failed", "cancelled"}:
            job.status = "cancelled"
            self.store.save_sync_job(job)
            self.store.append_sync_event(job.id, "done", {"status": "cancelled"})
        return job

    def _job(self, job_id: str) -> SyncJob:
        job = self.store.get_sync_job(job_id)
        if not job:
            raise KeyError(job_id)
        return job

    def _wait_if_paused(self, job_id: str) -> SyncJob:
        while True:
            job = self._job(job_id)
            if job.status != "paused":
                return job
            time.sleep(0.25)

    def _run(self, job_id: str) -> None:
        job = self._job(job_id)
        job.status = "running"
        self.store.save_sync_job(job)
        self.store.append_sync_event(job.id, "status", {"status": "running", "label": "知识同步已开始"})
        try:
            if job.scope in {"metadata", "all"}:
                self._sync_metadata(job)
            if job.scope in {"hot_fulltext", "all"}:
                self._sync_fulltext(job)
            if job.scope in {"reindex", "all"}:
                self.store.import_atlas_snapshots()
                self.store.prepare_embedding_profile(
                    progress=lambda value: self.store.append_sync_event(job.id, "model_download", value)
                )
                indexed = self.store.rebuild_embeddings()
                job.summary = f"Atlas、FTS5、图索引与 {indexed['indexed']} 条本地向量已核验。"
            current = self._job(job.id)
            if current.status != "cancelled":
                current.status = "done"
                current.progress = current.total
                current.summary = current.summary or "知识同步完成。"
                self.store.save_sync_job(current)
                self.store.append_sync_event(current.id, "done", {"status": "done", "summary": current.summary})
        except Exception as exc:
            failed = self._job(job.id)
            if failed.status != "cancelled":
                failed.status = "failed"
                failed.error = str(exc)[:500]
                failed.summary = "知识同步未完成，可稍后重试。"
                self.store.save_sync_job(failed)
                self.store.append_sync_event(failed.id, "error", {"message": failed.error, "recoverable": True})
        finally:
            with self._lock:
                self._workers.pop(job_id, None)

    def _sync_metadata(self, job: SyncJob) -> None:
        works = self.store.works_for_sync(atlas_ids=job.atlas_ids, work_ids=job.work_ids)
        pending = works
        job.total = len(pending)
        job.summary = f"准备通过 arXiv、OpenAlex 与 Crossref 核验 {job.total} 篇论文。"
        self.store.save_sync_job(job)
        for offset in range(job.progress, len(pending), 20):
            state = self._wait_if_paused(job.id)
            if state.status == "cancelled":
                return
            batch = pending[offset:offset + 20]
            ids = [work["identifiers"]["arxiv"][0] for work in batch if work["identifiers"].get("arxiv")]
            arxiv_metadata: dict[str, dict[str, Any]] = {}
            try:
                if ids:
                    arxiv_metadata = self._fetch_arxiv(ids)
            except Exception as exc:
                self.store.append_sync_event(job.id, "warning", {"message": f"arXiv 批次暂不可用：{str(exc)[:180]}", "offset": offset})
            for work in batch:
                identifiers = work.get("identifiers") or {}
                updates: list[str] = []
                if identifiers.get("arxiv"):
                    arxiv_id = re.sub(r"v\d+$", "", identifiers["arxiv"][0])
                    if item := arxiv_metadata.get(arxiv_id):
                        self.store.apply_derived_metadata(work["id"], item, provider="arxiv")
                        updates.append("arxiv")
                openalex_key = (identifiers.get("openalex") or identifiers.get("doi") or [""])[0]
                if openalex_key:
                    try:
                        item = self._fetch_openalex(openalex_key, is_doi=not bool(identifiers.get("openalex")))
                        if item:
                            self.store.apply_derived_metadata(work["id"], item, provider="openalex")
                            updates.append("openalex")
                    except Exception as exc:
                        self.store.append_sync_event(job.id, "warning", {"work_id": work["id"], "provider": "openalex", "message": str(exc)[:180]})
                if identifiers.get("doi"):
                    try:
                        item = self._fetch_crossref(identifiers["doi"][0])
                        if item:
                            self.store.apply_derived_metadata(work["id"], item, provider="crossref")
                            updates.append("crossref")
                    except Exception as exc:
                        self.store.append_sync_event(job.id, "warning", {"work_id": work["id"], "provider": "crossref", "message": str(exc)[:180]})
                if not any(identifiers.get(scheme) for scheme in ("arxiv", "doi", "openalex")):
                    try:
                        candidate = self._search_openalex_title(work["title"])
                        if candidate:
                            score = SequenceMatcher(None, work["title"].casefold(), candidate["title"].casefold()).ratio()
                            if score >= 0.72:
                                self.store.save_identity_candidate(
                                    source_work_id=work["id"], target_ref=f"openalex:{candidate['openalex']}", score=score,
                                    reasons=[{"kind": "normalized_title", "score": score}, {"metadata": candidate}],
                                )
                                self.store.append_sync_event(
                                    job.id, "identity_candidate", {"work_id": work["id"], "title": work["title"], "score": round(score, 4)}
                                )
                    except Exception as exc:
                        self.store.append_sync_event(job.id, "warning", {"work_id": work["id"], "provider": "openalex_search", "message": str(exc)[:180]})
                if updates:
                    self.store.append_sync_event(
                        job.id, "work_updated", {"work_id": work["id"], "title": work["title"], "providers": updates}
                    )
            latest = self._job(job.id)
            latest.progress = min(len(pending), offset + len(batch))
            latest.summary = f"已核验 {latest.progress}/{latest.total} 篇论文元数据。"
            self.store.save_sync_job(latest)
            time.sleep(0.35)

    def _fetch_arxiv(self, identifiers: list[str]) -> dict[str, dict[str, Any]]:
        clean_ids = [re.sub(r"v\d+$", "", identifier) for identifier in identifiers]
        url = f"https://export.arxiv.org/api/query?id_list={quote_plus(','.join(clean_ids))}&max_results={len(clean_ids)}"
        with self.client_factory() as client:
            response = client.get(url)
            response.raise_for_status()
        root = ET.fromstring(response.text)
        results: dict[str, dict[str, Any]] = {}
        for entry in root.findall("atom:entry", ATOM):
            identifier = (entry.findtext("atom:id", default="", namespaces=ATOM) or "").rsplit("/", 1)[-1]
            arxiv_id = re.sub(r"v\d+$", "", identifier)
            published = entry.findtext("atom:published", default="", namespaces=ATOM) or ""
            links = {link.attrib.get("type"): link.attrib.get("href") for link in entry.findall("atom:link", ATOM)}
            results[arxiv_id] = {
                "title": " ".join((entry.findtext("atom:title", default="", namespaces=ATOM) or "").split()),
                "authors": [author.findtext("atom:name", default="", namespaces=ATOM) for author in entry.findall("atom:author", ATOM)],
                "abstract": " ".join((entry.findtext("atom:summary", default="", namespaces=ATOM) or "").split()),
                "year": int(published[:4]) if published[:4].isdigit() else None,
                "venue": "arXiv",
                "arxiv": arxiv_id,
                "pdf_url": links.get("application/pdf") or f"https://arxiv.org/pdf/{arxiv_id}",
                "confidence": 0.9,
            }
        return results

    def _fetch_openalex(self, identifier: str, *, is_doi: bool = False) -> dict[str, Any]:
        normalized = identifier.strip()
        if is_doi:
            normalized = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", normalized, flags=re.I)
            target = f"https://doi.org/{normalized}"
        else:
            target = normalized.rsplit("/", 1)[-1]
        url = f"https://api.openalex.org/works/{quote(target, safe=':/')}"
        with self.client_factory() as client:
            response = client.get(url)
            response.raise_for_status()
            payload = response.json()
        abstract_index = payload.get("abstract_inverted_index") or {}
        ordered: list[tuple[int, str]] = []
        for token, positions in abstract_index.items():
            ordered.extend((int(position), token) for position in positions)
        abstract = " ".join(token for _, token in sorted(ordered))
        primary = payload.get("primary_location") or {}
        best = payload.get("best_oa_location") or {}
        source = primary.get("source") or {}
        doi = str(payload.get("doi") or "").removeprefix("https://doi.org/")
        return {
            "title": payload.get("display_name") or payload.get("title") or "",
            "authors": [
                ((item.get("author") or {}).get("display_name") or "")
                for item in payload.get("authorships") or []
                if (item.get("author") or {}).get("display_name")
            ],
            "abstract": abstract,
            "year": payload.get("publication_year"),
            "venue": source.get("display_name") or "",
            "doi": doi,
            "openalex": str(payload.get("id") or "").rsplit("/", 1)[-1],
            "pdf_url": best.get("pdf_url") or primary.get("pdf_url") or "",
            "confidence": 0.96,
        }

    def _search_openalex_title(self, title: str) -> dict[str, Any] | None:
        url = f"https://api.openalex.org/works?search={quote_plus(title)}&per-page=3&select=id,display_name,publication_year,authorships,doi,primary_location,best_oa_location"
        with self.client_factory() as client:
            response = client.get(url)
            response.raise_for_status()
            results = (response.json() or {}).get("results") or []
        if not results:
            return None
        payload = results[0]
        primary = payload.get("primary_location") or {}
        best = payload.get("best_oa_location") or {}
        source = primary.get("source") or {}
        return {
            "title": payload.get("display_name") or "",
            "authors": [
                ((item.get("author") or {}).get("display_name") or "")
                for item in payload.get("authorships") or []
                if (item.get("author") or {}).get("display_name")
            ],
            "year": payload.get("publication_year"), "venue": source.get("display_name") or "",
            "doi": str(payload.get("doi") or "").removeprefix("https://doi.org/"),
            "openalex": str(payload.get("id") or "").rsplit("/", 1)[-1],
            "pdf_url": best.get("pdf_url") or primary.get("pdf_url") or "", "confidence": 0.7,
        }

    def _fetch_crossref(self, doi: str) -> dict[str, Any]:
        normalized = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi.strip(), flags=re.I)
        with self.client_factory() as client:
            response = client.get(f"https://api.crossref.org/works/{quote(normalized, safe='')}")
            response.raise_for_status()
            payload = (response.json() or {}).get("message") or {}
        date_parts = ((payload.get("published-print") or payload.get("published-online") or payload.get("issued") or {}).get("date-parts") or [[]])[0]
        abstract = re.sub(r"<[^>]+>", " ", str(payload.get("abstract") or ""))
        abstract = " ".join(abstract.split())
        authors = []
        for author in payload.get("author") or []:
            name = " ".join(part for part in [author.get("given"), author.get("family")] if part)
            if name:
                authors.append(name)
        return {
            "title": ((payload.get("title") or [""])[0]),
            "authors": authors,
            "abstract": abstract,
            "year": date_parts[0] if date_parts else None,
            "venue": ((payload.get("container-title") or [""])[0]),
            "doi": payload.get("DOI") or normalized,
            "confidence": 0.97,
        }

    def _sync_fulltext(self, job: SyncJob) -> None:
        works = self.store.works_for_sync(atlas_ids=job.atlas_ids, work_ids=job.work_ids)
        selected = [work for work in works if work.get("identifiers", {}).get("arxiv")]
        job.total = len(selected)
        self.store.save_sync_job(job)
        for index, work in enumerate(selected[job.progress:], start=job.progress):
            state = self._wait_if_paused(job.id)
            if state.status == "cancelled":
                return
            if work["evidence_status"].get("full_text"):
                continue
            arxiv_id = re.sub(r"v\d+$", "", work["identifiers"]["arxiv"][0])
            url = f"https://arxiv.org/pdf/{arxiv_id}"
            try:
                content = self._download_pdf(url)
                document = import_document(
                    self.store, file_name=f"{arxiv_id}.pdf", media_type="application/pdf", content=content,
                    title=work["title"], work_id=work["id"], source_kind="open_access", access="open", evictable=True,
                )
                self.store.append_sync_event(job.id, "document_ready", {"work_id": work["id"], "document_id": document["id"], "title": work["title"]})
            except Exception as exc:
                self.store.append_sync_event(job.id, "warning", {"work_id": work["id"], "message": str(exc)[:180]})
            latest = self._job(job.id)
            latest.progress = index + 1
            latest.summary = f"已处理 {latest.progress}/{latest.total} 篇开放全文。"
            self.store.save_sync_job(latest)

    def _download_pdf(self, url: str, max_bytes: int = 100 * 1024 * 1024) -> bytes:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in {"arxiv.org", "export.arxiv.org"}:
            raise ValueError("开放全文域名未获授权")
        content = bytearray()
        with self.client_factory() as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                if response.url.host not in {"arxiv.org", "export.arxiv.org"}:
                    raise ValueError("开放全文重定向到了未授权域名")
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > max_bytes:
                        raise ValueError("开放全文超过 100 MB 限制")
        if not bytes(content[:5]).startswith(b"%PDF-"):
            raise ValueError("开放全文响应不是 PDF")
        return bytes(content)
