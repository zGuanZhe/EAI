from __future__ import annotations

import hashlib
import html
import json
import os
import re
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import httpx

from .models import SourceRecord
from .store import RuntimeStore


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_text(value: Any, limit: int = 4000) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def normalize_doi(value: Any) -> str:
    doi = clean_text(value, 300).lower()
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi).strip()


def normalized_title(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", clean_text(value, 500).lower())


def source_key(*, doi: str = "", arxiv_id: str = "", title: str = "", provider_id: str = "") -> str:
    if normalize_doi(doi):
        return f"doi:{normalize_doi(doi)}"
    if arxiv_id:
        return f"arxiv:{clean_text(arxiv_id, 120).lower()}"
    if normalized_title(title):
        return f"title:{normalized_title(title)}"
    return f"provider:{clean_text(provider_id, 240)}"


def source_id(canonical_key: str) -> str:
    return f"source_{hashlib.sha256(canonical_key.encode('utf-8')).hexdigest()[:20]}"


def content_hash(*parts: Any) -> str:
    return hashlib.sha256("\n".join(str(part or "") for part in parts).encode("utf-8")).hexdigest()


def query_terms(query: str) -> list[str]:
    latin = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{1,}", query.lower())
    chinese = re.findall(r"[\u4e00-\u9fff]{2,}", query)
    grams: list[str] = []
    for phrase in chinese:
        grams.extend(phrase[index:index + 2] for index in range(max(1, len(phrase) - 1)))
    return list(dict.fromkeys(latin + chinese + grams))[:32]


def inverted_abstract(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    positions: list[tuple[int, str]] = []
    for word, indexes in value.items():
        if isinstance(indexes, list):
            positions.extend((int(index), str(word)) for index in indexes if isinstance(index, int))
    return " ".join(word for _, word in sorted(positions))


class SourceService:
    def __init__(
        self,
        store: RuntimeStore,
        atlas_loader: Callable[[str], dict[str, Any]],
        *,
        client_factory: Callable[[], httpx.Client] | None = None,
    ):
        self.store = store
        self.atlas_loader = atlas_loader
        self.client_factory = client_factory or (lambda: httpx.Client(timeout=httpx.Timeout(12, connect=5), follow_redirects=True, headers={"User-Agent": "EAI-Desktop/0.2 research-agent"}))

    def _persist(self, task_id: str | None, source: SourceRecord) -> SourceRecord:
        source.task_id = task_id
        return self.store.save_source(source)

    def _atlas_source(self, atlas_id: str, paper: dict[str, Any], task_id: str | None, score: float = 1) -> SourceRecord:
        title = clean_text(paper.get("title") or paper.get("id"), 500)
        doi = normalize_doi(paper.get("doi"))
        arxiv_id = clean_text(paper.get("arxiv_id") or paper.get("arxiv"), 120)
        key = source_key(doi=doi, arxiv_id=arxiv_id, title=title, provider_id=str(paper.get("id")))
        abstract = clean_text(paper.get("abstract"), 3500)
        excerpt = clean_text(paper.get("summary") or paper.get("why_included") or paper.get("local_role"), 1400)
        raw_year = paper.get("year")
        try:
            year = int(raw_year) if raw_year else None
        except (TypeError, ValueError):
            year = None
        return self._persist(
            task_id,
            SourceRecord(
                id=source_id(f"atlas:{atlas_id}:{paper.get('id')}"),
                task_id=task_id,
                source_kind="atlas",
                evidence_level="curated_summary",
                title=title,
                locator={"atlas_id": atlas_id, "paper_id": paper.get("id"), "doi": doi, "arxiv_id": arxiv_id},
                authors=[clean_text(item, 160) for item in (paper.get("authors") or [])[:20]],
                year=year,
                abstract=abstract,
                excerpt=excerpt,
                canonical_key=key,
                content_hash=content_hash(title, abstract, excerpt),
                retrieved_at=utc_now(),
                confidence=min(0.98, 0.72 + score / 40),
                access="local",
                provider="atlas",
            ),
        )

    def get_atlas_paper(self, atlas_id: str, paper_id: str, task_id: str | None) -> SourceRecord | None:
        bundle = self.atlas_loader(atlas_id)
        paper = next((item for item in (bundle.get("papers", []) or []) if str(item.get("id") or "") == paper_id), None)
        return self._atlas_source(atlas_id, paper, task_id, score=10) if paper else None

    def sources_from_materials(
        self,
        materials: list[dict[str, Any]],
        task_id: str | None,
        default_atlas_id: str,
    ) -> list[SourceRecord]:
        results: list[SourceRecord] = []
        for index, material in enumerate(materials[:32]):
            source_ref = material.get("source_ref") if isinstance(material.get("source_ref"), dict) else {}
            atlas_id = clean_text(source_ref.get("atlas_id") or default_atlas_id, 80)
            paper_id = clean_text(source_ref.get("paper_id") or source_ref.get("id"), 240)
            if paper_id and atlas_id:
                try:
                    exact = self.get_atlas_paper(atlas_id, paper_id, task_id)
                except Exception:
                    exact = None
                if exact:
                    results.append(exact)
                    continue
            title = clean_text(material.get("title") or f"用户材料 {index + 1}", 500)
            excerpt = clean_text(material.get("summary") or material.get("excerpt") or material.get("agent_note"), 1800)
            canonical = f"user-material:{content_hash(title, source_ref, excerpt)}"
            results.append(
                self._persist(
                    task_id,
                    SourceRecord(
                        id=source_id(canonical),
                        task_id=task_id,
                        source_kind="workspace",
                        evidence_level="user_knowledge",
                        title=title,
                        locator=source_ref,
                        excerpt=excerpt,
                        canonical_key=canonical,
                        content_hash=content_hash(title, excerpt),
                        retrieved_at=utc_now(),
                        confidence=0.88,
                        access="local",
                        provider="user_context",
                    ),
                )
            )
        return self.deduplicate(results)

    def search_atlas(self, atlas_id: str, query: str, task_id: str | None, limit: int = 10) -> list[SourceRecord]:
        bundle = self.atlas_loader(atlas_id)
        terms = query_terms(query)
        scored: list[tuple[float, dict[str, Any]]] = []
        for paper in bundle.get("papers", []) or []:
            title = clean_text(paper.get("title"), 500).lower()
            summary = clean_text(paper.get("summary") or paper.get("why_included") or paper.get("local_role"), 1800).lower()
            abstract = clean_text(paper.get("abstract"), 3000).lower()
            contribution = clean_text(paper.get("key_contribution") or paper.get("core_innovation"), 1200).lower()
            score = sum(5 for term in terms if term in title)
            score += sum(2.5 for term in terms if term in contribution)
            score += sum(1.5 for term in terms if term in summary)
            score += sum(1 for term in terms if term in abstract)
            if score:
                scored.append((score, paper))
        results: list[SourceRecord] = []
        for score, paper in sorted(scored, key=lambda item: (item[0], str(item[1].get("year") or "")), reverse=True)[:limit]:
            results.append(self._atlas_source(atlas_id, paper, task_id, score))
        return results

    def search_documents(self, query: str, task_id: str | None, limit: int = 10) -> list[SourceRecord]:
        results: list[SourceRecord] = []
        for row in self.store.search_documents(query, limit):
            document = self.store.get_document(str(row["document_id"]))
            if not document:
                continue
            key = f"document:{document.id}:{row['chunk_id']}"
            source = SourceRecord(
                id=source_id(key),
                task_id=task_id,
                source_kind="local_document",
                evidence_level="full_text",
                title=document.title,
                locator={"document_id": document.id, "chunk_id": row["chunk_id"], "section": row["section"]},
                excerpt=clean_text(row["text"], 1800),
                canonical_key=f"document:{document.content_hash}",
                content_hash=content_hash(row["text"]),
                retrieved_at=utc_now(),
                confidence=0.9,
                access="local",
                provider="local_fts",
            )
            results.append(self._persist(task_id, source))
        return results

    def search_openalex(self, query: str, task_id: str | None, limit: int = 6) -> list[SourceRecord]:
        with self.client_factory() as client:
            response = client.get(
                "https://api.openalex.org/works",
                params={"search": query, "per-page": min(limit, 10)},
            )
            response.raise_for_status()
            items = response.json().get("results", [])
        results: list[SourceRecord] = []
        for item in items[:limit]:
            title = clean_text(item.get("display_name") or item.get("title"), 500)
            doi = normalize_doi(item.get("doi"))
            abstract = clean_text(inverted_abstract(item.get("abstract_inverted_index")), 3500)
            key = source_key(doi=doi, title=title, provider_id=item.get("id"))
            authors = [clean_text(authorship.get("author", {}).get("display_name"), 160) for authorship in (item.get("authorships") or [])[:20]]
            primary = item.get("primary_location") or {}
            landing = primary.get("landing_page_url") or item.get("doi") or item.get("id")
            pdf_url = primary.get("pdf_url")
            source = SourceRecord(
                id=source_id(key), task_id=task_id, source_kind="openalex", evidence_level="abstract" if abstract else "metadata",
                title=title, locator={"url": landing, "pdf_url": pdf_url, "doi": doi, "openalex_id": item.get("id")},
                authors=authors, year=item.get("publication_year"), abstract=abstract, excerpt=abstract[:1500], canonical_key=key,
                content_hash=content_hash(title, abstract, doi), retrieved_at=utc_now(), confidence=0.82,
                access="open" if pdf_url else "metadata_only", provider="openalex",
            )
            results.append(self._persist(task_id, source))
        return results

    def search_crossref(self, query: str, task_id: str | None, limit: int = 6) -> list[SourceRecord]:
        with self.client_factory() as client:
            response = client.get("https://api.crossref.org/works", params={"query": query, "rows": min(limit, 10)})
            response.raise_for_status()
            items = response.json().get("message", {}).get("items", [])
        results: list[SourceRecord] = []
        for item in items[:limit]:
            titles = item.get("title") or []
            title = clean_text(titles[0] if titles else item.get("DOI"), 500)
            doi = normalize_doi(item.get("DOI"))
            abstract = clean_text(item.get("abstract"), 3500)
            key = source_key(doi=doi, title=title, provider_id=doi)
            year_parts = ((item.get("published-print") or item.get("published-online") or item.get("issued") or {}).get("date-parts") or [[None]])
            year = year_parts[0][0] if year_parts and year_parts[0] else None
            authors = [clean_text(" ".join(filter(None, [author.get("given"), author.get("family")])), 160) for author in (item.get("author") or [])[:20]]
            source = SourceRecord(
                id=source_id(key), task_id=task_id, source_kind="crossref", evidence_level="abstract" if abstract else "metadata",
                title=title, locator={"url": item.get("URL"), "doi": doi}, authors=authors, year=year if isinstance(year, int) else None,
                abstract=abstract, excerpt=abstract[:1500], canonical_key=key, content_hash=content_hash(title, abstract, doi),
                retrieved_at=utc_now(), confidence=0.78, access="metadata_only", provider="crossref",
            )
            results.append(self._persist(task_id, source))
        return results

    def search_arxiv(self, query: str, task_id: str | None, limit: int = 6) -> list[SourceRecord]:
        url = f"https://export.arxiv.org/api/query?search_query=all:{quote_plus(query)}&start=0&max_results={min(limit, 10)}"
        with self.client_factory() as client:
            response = client.get(url)
            response.raise_for_status()
            root = ET.fromstring(response.text)
        namespace = {"atom": "http://www.w3.org/2005/Atom"}
        results: list[SourceRecord] = []
        for entry in root.findall("atom:entry", namespace)[:limit]:
            title = clean_text(entry.findtext("atom:title", default="", namespaces=namespace), 500)
            summary = clean_text(entry.findtext("atom:summary", default="", namespaces=namespace), 3500)
            identifier = clean_text(entry.findtext("atom:id", default="", namespaces=namespace), 300)
            arxiv_id = identifier.rsplit("/", 1)[-1]
            published = clean_text(entry.findtext("atom:published", default="", namespaces=namespace), 40)
            authors = [clean_text(author.findtext("atom:name", default="", namespaces=namespace), 160) for author in entry.findall("atom:author", namespace)[:20]]
            key = source_key(arxiv_id=arxiv_id, title=title, provider_id=identifier)
            source = SourceRecord(
                id=source_id(key), task_id=task_id, source_kind="arxiv", evidence_level="abstract", title=title,
                locator={"url": identifier, "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}", "arxiv_id": arxiv_id},
                authors=authors, year=int(published[:4]) if published[:4].isdigit() else None, abstract=summary, excerpt=summary[:1500],
                canonical_key=key, content_hash=content_hash(title, summary, arxiv_id), retrieved_at=utc_now(), confidence=0.84,
                access="open", provider="arxiv",
            )
            results.append(self._persist(task_id, source))
        return results

    def search_semantic_scholar(self, query: str, task_id: str | None, limit: int = 6) -> list[SourceRecord]:
        key_value = os.environ.get("S2_API_KEY", "")
        headers = {"x-api-key": key_value} if key_value else {}
        with self.client_factory() as client:
            response = client.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params={"query": query, "limit": min(limit, 10), "fields": "title,authors,year,abstract,url,externalIds,openAccessPdf"},
                headers=headers,
            )
            response.raise_for_status()
            items = response.json().get("data", [])
        results: list[SourceRecord] = []
        for item in items[:limit]:
            title = clean_text(item.get("title"), 500)
            external = item.get("externalIds") or {}
            doi = normalize_doi(external.get("DOI"))
            arxiv_id = clean_text(external.get("ArXiv"), 120)
            abstract = clean_text(item.get("abstract"), 3500)
            key = source_key(doi=doi, arxiv_id=arxiv_id, title=title, provider_id=item.get("paperId"))
            pdf = (item.get("openAccessPdf") or {}).get("url")
            source = SourceRecord(
                id=source_id(key), task_id=task_id, source_kind="semantic_scholar", evidence_level="abstract" if abstract else "metadata",
                title=title, locator={"url": item.get("url"), "pdf_url": pdf, "doi": doi, "arxiv_id": arxiv_id, "paper_id": item.get("paperId")},
                authors=[clean_text(author.get("name"), 160) for author in (item.get("authors") or [])[:20]], year=item.get("year"),
                abstract=abstract, excerpt=abstract[:1500], canonical_key=key, content_hash=content_hash(title, abstract, doi, arxiv_id),
                retrieved_at=utc_now(), confidence=0.8, access="open" if pdf else "metadata_only", provider="semantic_scholar",
            )
            results.append(self._persist(task_id, source))
        return results

    def search_external(self, query: str, task_id: str | None, providers: list[str] | None = None, limit: int = 18) -> tuple[list[SourceRecord], list[str]]:
        requested = providers or ["openalex", "arxiv", "crossref"]
        normalized_query = clean_text(query, 1200).lower()
        cache_key = hashlib.sha256(
            json.dumps({"query": normalized_query, "providers": sorted(requested), "limit": limit}, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        cached = self.store.get_source_query_cache(cache_key)
        if cached:
            try:
                updated_at = datetime.fromisoformat(str(cached["updated_at"]).replace("Z", "+00:00"))
                age_seconds = (datetime.now(timezone.utc) - updated_at.astimezone(timezone.utc)).total_seconds()
            except (TypeError, ValueError):
                age_seconds = float("inf")
            ttl_seconds = max(60, int(os.environ.get("EAI_SOURCE_CACHE_TTL_SECONDS", "21600")))
            if age_seconds <= ttl_seconds:
                cached_sources = []
                for source_id_value in cached.get("source_ids", []):
                    source = self.store.get_source(str(source_id_value))
                    if source:
                        source.task_id = task_id
                        cached_sources.append(self._persist(task_id, source))
                if cached_sources:
                    return self.deduplicate(cached_sources)[:limit], list(cached.get("warnings") or [])
        methods = {
            "openalex": self.search_openalex,
            "arxiv": self.search_arxiv,
            "crossref": self.search_crossref,
            "semantic_scholar": self.search_semantic_scholar,
        }
        collected: list[SourceRecord] = []
        warnings: list[str] = []
        with ThreadPoolExecutor(max_workers=min(4, len(requested) or 1)) as executor:
            futures = {executor.submit(methods[name], query, task_id, max(4, limit // max(1, len(requested)))): name for name in requested if name in methods}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    collected.extend(future.result())
                except Exception as exc:
                    warnings.append(f"{name} 暂时不可用：{clean_text(exc, 180)}")
        deduped = self.deduplicate(collected)
        deduped = [self._persist(task_id, source) for source in deduped[:limit]]
        if deduped:
            self.store.save_source_query_cache(
                cache_key,
                {"source_ids": [source.id for source in deduped], "warnings": warnings},
                utc_now(),
            )
        return deduped, warnings

    def deduplicate(self, sources: list[SourceRecord]) -> list[SourceRecord]:
        ranked = {"full_text": 7, "abstract": 6, "web_content": 5, "curated_summary": 4, "metadata": 3, "user_knowledge": 2, "system_truth": 1}
        best: dict[str, SourceRecord] = {}
        for source in sources:
            role = "curated" if source.evidence_level == "curated_summary" else "user" if source.evidence_level == "user_knowledge" else "system" if source.evidence_level == "system_truth" else "original"
            dedupe_key = f"{source.canonical_key}:{role}"
            current = best.get(dedupe_key)
            if not current or ranked.get(source.evidence_level, 0) > ranked.get(current.evidence_level, 0):
                best[dedupe_key] = source
            elif current and source.provider not in current.provider.split("+"):
                current.provider = "+".join(filter(None, [current.provider, source.provider]))
                current.locator.setdefault("corroborating", []).append(source.locator)
                self.store.save_source(current)
        return list(best.values())

    def search(
        self,
        *,
        query: str,
        atlas_id: str,
        task_id: str | None,
        source_policy: str,
        providers: list[str] | None = None,
        limit: int = 24,
    ) -> tuple[list[SourceRecord], list[str]]:
        local: list[SourceRecord] = []
        warnings: list[str] = []
        external: list[SourceRecord] = []
        if source_policy == "atlas_only":
            local = self.search_atlas(atlas_id, query, task_id, limit)
        elif source_policy in {"local_only", "local_and_external"}:
            local = self.search_documents(query, task_id, max(4, limit // 4)) + self.search_atlas(atlas_id, query, task_id, max(6, limit // 3))
        if source_policy in {"external_only", "local_and_external"}:
            external, warnings = self.search_external(query, task_id, providers, max(8, limit - len(local)))
        return self.deduplicate(local + external)[:limit], warnings
