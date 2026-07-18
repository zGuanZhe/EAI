from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.storage import read_json_file
from ..repositories.personal import safe_document_path, safe_object_memory_path
from ..research.store import ResearchStore
from ..schemas.models import (
    AtlasCandidateUpdate,
    AtlasUpdateCandidate,
    AtlasUpdateDoc,
    AtlasUpdateResultPreviewRequest,
    AtlasUpdateResultPreviewResponse,
    AtlasUpdateRun,
    AtlasUpdateTaskPackRequest,
    AtlasUpdateTaskPackResponse,
    CardType,
    ObjectMemory,
)
from .projection import ProjectionService


class AtlasResourceNotFoundError(KeyError):
    pass


class AtlasCandidateNotFoundError(KeyError):
    pass


class AtlasService:
    def __init__(
        self,
        store: ResearchStore,
        *,
        atlas_cache_dir: Path,
        personal_dir: Path,
        objects_dir: Path,
        atlas_updates_dir: Path,
    ) -> None:
        self.store = store
        self.atlas_cache_dir = atlas_cache_dir.resolve()
        self.personal_dir = personal_dir.resolve()
        self.objects_dir = objects_dir.resolve()
        self.atlas_updates_dir = atlas_updates_dir.resolve()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _slug(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:16]}"

    @staticmethod
    def _safe_text(value: Any, limit: int) -> str:
        text = "" if value is None else str(value)
        return " ".join(text.replace("\x00", "").split())[:limit]

    @staticmethod
    def _normalized_title(value: str | None) -> str:
        if not value:
            return ""
        return "".join(ch.lower() for ch in str(value) if ch.isalnum())

    def _bundle_path(self, atlas_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", atlas_id):
            raise ValueError("invalid atlas id")
        path = (self.atlas_cache_dir / f"{atlas_id}.bundle.json").resolve()
        if path.parent != self.atlas_cache_dir:
            raise ValueError("invalid atlas path")
        return path

    def _object_path(self, atlas_id: str, object_type: str, object_id: str) -> Path:
        return safe_object_memory_path(self.objects_dir, atlas_id, object_type, object_id)

    def _updates_path(self, atlas_id: str) -> Path:
        return safe_document_path(self.atlas_updates_dir, atlas_id, "atlas")

    def _projection_target(self, path: Path) -> str:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.personal_dir):
            raise ValueError("projection target must remain inside personal data")
        return resolved.relative_to(self.personal_dir).as_posix()

    def _save_projected(self, kind: str, record_id: str, payload: dict[str, Any], path: Path) -> dict[str, Any]:
        saved = self.store.save_record(kind, record_id, payload, projection_target=self._projection_target(path))
        ProjectionService(self.store, self.personal_dir).replay(entity_kind=kind, entity_id=record_id, limit=20)
        return saved

    def list_atlases(self) -> list[dict[str, Any]]:
        index_path = self.atlas_cache_dir / "atlases.index.json"
        fallback_path = self.atlas_cache_dir / "atlases.json"
        if index_path.exists():
            data = read_json_file(index_path)
        elif fallback_path.exists():
            data = read_json_file(fallback_path)
        else:
            raise AtlasResourceNotFoundError("atlas index not found")
        atlases = data.get("atlases", data) if isinstance(data, dict) else data
        return [
            {
                "id": atlas.get("id"),
                "title": atlas.get("title") or atlas.get("name") or atlas.get("id"),
                "title_cn": atlas.get("title_cn") or atlas.get("cn"),
                "paper_count": atlas.get("paper_count") or atlas.get("papers") or 0,
            }
            for atlas in atlases
            if atlas.get("id")
        ]

    def get_bundle(self, atlas_id: str) -> dict[str, Any]:
        data = read_json_file(self._bundle_path(atlas_id))
        evidence_index = self.store.atlas_evidence_index(atlas_id)
        for paper in data.get("papers") or []:
            paper["evidence_status"] = evidence_index["works"].get(str(paper.get("id")), {})
        for relation in data.get("relations") or []:
            relation.update(evidence_index["relations"].get(str(relation.get("id")), {}))
        return data

    def bundle_for_update(self, atlas_id: str) -> dict[str, Any]:
        path = self._bundle_path(atlas_id)
        if not path.exists():
            raise AtlasResourceNotFoundError("atlas bundle not found")
        return read_json_file(path)

    def bundle_memory_items(self, atlas_id: str) -> list[ObjectMemory]:
        path = self._bundle_path(atlas_id)
        if not path.exists():
            return []
        data = read_json_file(path)
        items: list[ObjectMemory] = []
        for paper in data.get("papers", []):
            notes = paper.get("personal_notes") or []
            note_text = "\n".join(
                note.get("content", "")
                for note in notes
                if isinstance(note, dict) and note.get("content")
            )
            if not (paper.get("personal_star") or paper.get("personal_maturity") or note_text):
                continue
            items.append(
                ObjectMemory(
                    object_ref={
                        "atlas_id": atlas_id,
                        "object_type": "paper",
                        "object_id": paper.get("id"),
                        "source": "bundle",
                    },
                    title_snapshot=paper.get("title") or paper.get("id") or "",
                    star=bool(paper.get("personal_star")),
                    maturity=int(paper.get("personal_maturity") or 0),
                    note=note_text,
                    updated_at=paper.get("personal_last_read_at"),
                )
            )
        return items

    def load_object_memory(self, atlas_id: str, object_type: str, object_id: str) -> ObjectMemory | None:
        record_id = f"{atlas_id}:{object_type}:{object_id}"
        data = self.store.get_record("object_memory", record_id)
        if data is not None:
            return ObjectMemory.model_validate(data)
        path = self._object_path(atlas_id, object_type, object_id)
        if not path.exists():
            return None
        data = read_json_file(path)
        self.store.save_record("object_memory", record_id, data)
        return ObjectMemory.model_validate(data)

    def effective_object_memory(self, atlas_id: str, object_type: str, object_id: str) -> ObjectMemory | None:
        saved = self.load_object_memory(atlas_id, object_type, object_id)
        if saved:
            return saved
        if object_type != "paper":
            return None
        for item in self.bundle_memory_items(atlas_id):
            ref = item.object_ref
            if ref.get("object_type") == object_type and ref.get("object_id") == object_id:
                return item
        return None

    def list_object_memory(self, atlas_id: str) -> list[ObjectMemory]:
        self.objects_dir.mkdir(parents=True, exist_ok=True)
        by_key: dict[tuple[str, str], ObjectMemory] = {}
        for item in self.bundle_memory_items(atlas_id):
            ref = item.object_ref
            by_key[(ref.get("object_type"), ref.get("object_id"))] = item
        for raw in self.store.list_records("object_memory"):
            try:
                item = ObjectMemory.model_validate(raw)
            except Exception:
                continue
            ref = item.object_ref
            if ref.get("atlas_id") == atlas_id:
                by_key[(ref.get("object_type"), ref.get("object_id"))] = item
        return sorted(by_key.values(), key=lambda item: item.updated_at or "", reverse=True)

    def write_object_memory(
        self,
        atlas_id: str,
        object_type: CardType | str,
        object_id: str,
        memory: ObjectMemory,
    ) -> ObjectMemory:
        self.store.ensure_writable()
        path = self._object_path(atlas_id, str(object_type), object_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        memory.object_ref = {
            **memory.object_ref,
            "atlas_id": atlas_id,
            "object_type": object_type,
            "object_id": object_id,
        }
        memory.tags = [tag.strip() for tag in memory.tags if tag.strip()]
        memory.updated_at = self._now()
        self._save_projected(
            "object_memory",
            f"{atlas_id}:{object_type}:{object_id}",
            memory.model_dump(mode="json"),
            path,
        )
        return memory

    def load_updates(self, atlas_id: str) -> AtlasUpdateDoc:
        stored = self.store.get_record("atlas_update", atlas_id)
        if stored is not None:
            return AtlasUpdateDoc.model_validate(stored)
        path = self._updates_path(atlas_id)
        if not path.exists():
            return AtlasUpdateDoc(atlas_id=atlas_id, updated_at=self._now())
        data = read_json_file(path)
        self.store.save_record("atlas_update", atlas_id, data)
        return AtlasUpdateDoc.model_validate(data)

    def write_updates(self, doc: AtlasUpdateDoc) -> AtlasUpdateDoc:
        self.store.ensure_writable()
        doc.updated_at = self._now()
        self._save_projected(
            "atlas_update",
            doc.atlas_id,
            doc.model_dump(mode="json"),
            self._updates_path(doc.atlas_id),
        )
        return doc

    @staticmethod
    def _action_title(action: str) -> str:
        return {
            "recent": "更新最新论文",
            "all": "更新全部论文",
            "complete_cards": "补全旧论文卡",
            "evidence": "补充策展证据",
        }.get(action, "论文更新")

    def build_update_task_pack(
        self,
        atlas_id: str,
        payload: AtlasUpdateTaskPackRequest,
    ) -> AtlasUpdateTaskPackResponse:
        bundle = self.bundle_for_update(atlas_id)
        updates = self.load_updates(atlas_id)
        action_title = self._action_title(payload.action)
        routes = bundle.get("routes", []) or []
        papers = bundle.get("papers", []) or []
        relations = bundle.get("relations", []) or []
        route_lines = []
        for route in routes:
            route_id = str(route.get("id") or "")
            if payload.route_id and route_id != payload.route_id:
                continue
            route_papers = [
                paper
                for paper in papers
                if str(paper.get("route_id") or paper.get("raw_fields", {}).get("routeName") or "") == route_id
            ]
            route_lines.append(
                f"- {route_id}: {route.get('name') or route.get('cn') or route_id} | {len(route_papers)} 篇 | {route.get('rationale') or ''}"
            )
        year_counts: dict[str, int] = {}
        for paper in papers:
            year = str(paper.get("year") or "未知")
            year_counts[year] = year_counts.get(year, 0) + 1
        paper_lines = [
            f"- {paper.get('title')} ({paper.get('year') or '未知'}, {paper.get('venue') or '未知'})"
            f" | id={paper.get('id')} | route={paper.get('route_id') or paper.get('raw_fields', {}).get('routeName') or 'unrouted'}"
            f" | arxiv={paper.get('arxiv') or paper.get('arxiv_id') or ''} | doi={paper.get('doi') or ''}"
            for paper in papers[:240]
        ]
        instruction = payload.instruction_override or {
            "recent": "重点查找最近 1-2 年与本 Atlas 强相关、尚未出现的新论文。",
            "all": "从整体覆盖角度查找应收录但缺失的重要论文，不限年份。",
            "complete_cards": "审查已有论文卡，指出需要补全摘要、核心贡献、局限或路线归类的对象。",
        }.get(payload.action, "补充项目页、代码、后续工作、复现实验或关键证据来源。")
        markdown = "\n".join([
            f"# Atlas 论文更新 Task Pack：{action_title}", "",
            "你是严谨的中文论文 Atlas 更新助手。请只推荐真实存在、你有把握的论文；不确定时不要编造。", "",
            "## 当前 Atlas", f"- atlas_id: {atlas_id}",
            f"- title: {bundle.get('atlas', {}).get('title_cn') or bundle.get('atlas', {}).get('title') or atlas_id}",
            f"- papers: {len(papers)}", f"- relations: {len(relations)}",
            f"- pending_personal_candidates: {len([item for item in updates.candidates if item.status == 'pending'])}", "",
            "## 路线结构", "\n".join(route_lines) or "(暂无路线)", "",
            "## 年份分布", ", ".join(f"{year}:{count}" for year, count in sorted(year_counts.items(), reverse=True)[:24]), "",
            "## 已有论文查重清单", "\n".join(paper_lines) or "(暂无论文)", "",
            "## 本次任务", instruction, "", "## 输出要求",
            "- 使用中文解释 why / relevance。", "- 不要推荐已在查重清单中出现的论文。",
            "- 每篇候选尽量提供 title/authors/year/venue/url/doi/arxiv_id/suggested_route_id/why/relevance/confidence。",
            "- suggested_route_id 必须优先使用上方路线 ID；不确定可留空。", "", "```json",
            json.dumps({
                "schema_version": "eai-atlas-update/v1", "summary": "...",
                "candidate_papers": [{
                    "title": "...", "authors": ["..."], "year": 2026, "venue": "...", "url": "...",
                    "doi": "", "arxiv_id": "", "suggested_route_id": "...", "why": "为什么值得纳入",
                    "relevance": "与当前 Atlas/路线的关系", "confidence": 0.8,
                }], "candidate_relations": [], "card_updates": [],
            }, ensure_ascii=False, indent=2), "```",
        ])
        run = AtlasUpdateRun(
            id=self._slug("update_run"), action=payload.action, title=action_title, status="preview",
            created_at=self._now(), token_estimate=max(160, len(markdown) // 3),
            summary=f"Atlas {atlas_id} {action_title}",
        )
        return AtlasUpdateTaskPackResponse(run=run, markdown=markdown, token_estimate=run.token_estimate)

    @staticmethod
    def _parse_candidates(raw_text: str) -> list[dict[str, Any]]:
        matches = re.findall(r"```(?:json|eai-result/v1|eai-atlas-update/v1)?\s*([\s\S]*?)```", raw_text)
        candidates = matches[:]
        stripped = raw_text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            candidates.append(stripped)
        parsed: list[dict[str, Any]] = []
        for candidate in candidates:
            try:
                value = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                parsed.append(value)
        return parsed

    def _safe_id(self, value: Any, prefix: str) -> str:
        text = self._safe_text(value, 120)
        return text if text and re.fullmatch(r"[A-Za-z0-9_.:-]+", text) else self._slug(prefix)

    def _coerce_candidate(
        self,
        raw: Any,
        atlas_id: str,
        run_id: str,
        route_ids: set[str],
    ) -> AtlasUpdateCandidate | None:
        if isinstance(raw, str):
            raw = {"title": raw}
        if not isinstance(raw, dict):
            return None
        title = str(raw.get("title") or raw.get("paper_title") or "").strip()
        if not title:
            return None
        authors_raw = raw.get("authors") or []
        if isinstance(authors_raw, str):
            authors = [item.strip() for item in re.split(r",|;|\band\b", authors_raw) if item.strip()]
        elif isinstance(authors_raw, list):
            authors = [str(item).strip() for item in authors_raw if str(item).strip()]
        else:
            authors = []
        route_id = str(raw.get("suggested_route_id") or raw.get("route_id") or raw.get("route") or "").strip()
        confidence = raw.get("confidence", 0.5)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.5
        now = self._now()
        return AtlasUpdateCandidate(
            id=self._safe_id(raw.get("id"), "cand"), status="pending", title=title, authors=authors,
            year=raw.get("year"), venue=self._safe_text(raw.get("venue"), 160),
            url=self._safe_text(raw.get("url") or raw.get("paper_url"), 400),
            doi=self._safe_text(raw.get("doi"), 160),
            arxiv_id=self._safe_text(raw.get("arxiv_id") or raw.get("arxiv"), 120),
            abstract=str(raw.get("abstract") or raw.get("summary") or "")[:2400], suggested_route_id=route_id,
            why=str(raw.get("why") or raw.get("reason") or raw.get("rationale") or "")[:1600],
            relevance=str(raw.get("relevance") or raw.get("relation_to_atlas") or "")[:1600],
            confidence=max(0, min(1, confidence)), source_run_id=run_id,
            judgement=str(raw.get("judgement") or "")[:1600],
            tags=[str(item).strip() for item in (raw.get("tags") or []) if str(item).strip()]
            if isinstance(raw.get("tags"), list) else [],
            created_at=now, updated_at=now,
        )

    def _extract_candidates(self, raw_text: str, atlas_id: str, run_id: str, route_ids: set[str]) -> list[AtlasUpdateCandidate]:
        raw_candidates: list[Any] = []
        for parsed in self._parse_candidates(raw_text):
            raw_candidates.extend(parsed.get("candidate_papers") or [])
            raw_candidates.extend(parsed.get("candidates") or [])
            for note in parsed.get("notes") or []:
                if not isinstance(note, dict) or note.get("target") not in {"new_candidate", "candidate_paper", "paper"}:
                    continue
                message = note.get("message")
                if isinstance(message, str):
                    try:
                        raw_candidates.append(json.loads(message))
                    except json.JSONDecodeError:
                        raw_candidates.append({"title": message, "why": note.get("type") or ""})
                else:
                    raw_candidates.append(message)
        return [item for raw in raw_candidates if (item := self._coerce_candidate(raw, atlas_id, run_id, route_ids))]

    def _dedupe_index(self, bundle: dict[str, Any], updates: AtlasUpdateDoc | None = None) -> dict[str, str]:
        index: dict[str, str] = {}
        for paper in bundle.get("papers", []) or []:
            paper_id = str(paper.get("id") or "")
            for key in (
                f"title:{self._normalized_title(paper.get('title'))}",
                f"doi:{str(paper.get('doi') or '').lower()}",
                f"arxiv:{str(paper.get('arxiv') or paper.get('arxiv_id') or '').lower()}",
            ):
                if not key.endswith(":"):
                    index[key] = paper_id
        if updates:
            for candidate in updates.candidates:
                for key in (
                    f"title:{self._normalized_title(candidate.title)}",
                    f"doi:{candidate.doi.lower()}",
                    f"arxiv:{candidate.arxiv_id.lower()}",
                ):
                    if not key.endswith(":"):
                        index.setdefault(key, candidate.id or "")
        return index

    def preview_update_result(
        self,
        atlas_id: str,
        payload: AtlasUpdateResultPreviewRequest,
    ) -> AtlasUpdateResultPreviewResponse:
        raw = payload.raw_text.strip()
        if not raw:
            raise ValueError("empty update result text")
        bundle = self.bundle_for_update(atlas_id)
        updates = self.load_updates(atlas_id)
        route_ids = {str(route.get("id")) for route in bundle.get("routes", []) or [] if route.get("id")}
        run = AtlasUpdateRun(
            id=self._slug("update_run"), action=payload.action, title=self._action_title(payload.action),
            status="parsed", created_at=self._now(), summary=f"粘贴返回：{self._action_title(payload.action)}",
        )
        candidates = self._extract_candidates(raw, atlas_id, run.id or "", route_ids)
        index = self._dedupe_index(bundle, updates)
        existing_ids = {candidate.id for candidate in updates.candidates}
        duplicate_count = 0
        for candidate in candidates:
            if candidate.id in existing_ids:
                candidate.id = self._slug("cand")
            keys = [
                f"title:{self._normalized_title(candidate.title)}",
                f"doi:{candidate.doi.lower()}", f"arxiv:{candidate.arxiv_id.lower()}",
            ]
            duplicate = next((index[key] for key in keys if key in index), None)
            if duplicate:
                candidate.duplicate_of = duplicate
                duplicate_count += 1
            for key in keys:
                if not key.endswith(":"):
                    index.setdefault(key, candidate.id or "")
        run.candidate_count = len(candidates)
        updates.runs.insert(0, run)
        updates.candidates = candidates + updates.candidates
        self.write_updates(updates)
        return AtlasUpdateResultPreviewResponse(run=run, candidates=candidates, duplicate_count=duplicate_count)

    def update_candidate(self, atlas_id: str, candidate_id: str, payload: AtlasCandidateUpdate) -> AtlasUpdateDoc:
        self._object_path(atlas_id, "paper", candidate_id)
        updates = self.load_updates(atlas_id)
        for candidate in updates.candidates:
            if candidate.id == candidate_id:
                raw = candidate.model_dump(mode="json")
                raw.update({key: value for key, value in payload.model_dump(exclude_unset=True, mode="json").items() if value is not None})
                raw["id"] = candidate_id
                raw["updated_at"] = self._now()
                replacement = AtlasUpdateCandidate.model_validate(raw)
                updates.candidates = [replacement if item.id == candidate_id else item for item in updates.candidates]
                return self.write_updates(updates)
        raise AtlasCandidateNotFoundError(candidate_id)

    def _apply_candidate_memory(self, atlas_id: str, candidate: AtlasUpdateCandidate) -> None:
        object_id = candidate.id or self._slug("cand")
        memory = self.effective_object_memory(atlas_id, "paper", object_id) or ObjectMemory(
            object_ref={"atlas_id": atlas_id, "object_type": "paper", "object_id": object_id},
            title_snapshot=candidate.title,
        )
        memory.title_snapshot = candidate.title
        memory.tags = sorted({*memory.tags, *candidate.tags, "候选论文"})
        memory.judgement = candidate.judgement or memory.judgement or candidate.why or candidate.relevance
        if not memory.note:
            memory.note = "\n".join(part for part in [
                f"推荐理由：{candidate.why}" if candidate.why else "",
                f"与 Atlas 关系：{candidate.relevance}" if candidate.relevance else "",
                f"建议路线：{candidate.suggested_route_id}" if candidate.suggested_route_id else "",
            ] if part)
        self.write_object_memory(atlas_id, "paper", object_id, memory)

    def apply_candidate(self, atlas_id: str, candidate_id: str) -> AtlasUpdateDoc:
        self._object_path(atlas_id, "paper", candidate_id)
        updates = self.load_updates(atlas_id)
        for candidate in updates.candidates:
            if candidate.id == candidate_id:
                candidate.status = "applied"
                candidate.updated_at = self._now()
                self._apply_candidate_memory(atlas_id, candidate)
                return self.write_updates(updates)
        raise AtlasCandidateNotFoundError(candidate_id)

    def bulk_apply_candidates(self, atlas_id: str) -> AtlasUpdateResultPreviewResponse:
        updates = self.load_updates(atlas_id)
        applied = 0
        for candidate in updates.candidates:
            if candidate.status == "pending" and not candidate.duplicate_of and candidate.confidence >= 0.65:
                candidate.status = "applied"
                candidate.updated_at = self._now()
                self._apply_candidate_memory(atlas_id, candidate)
                applied += 1
        run = AtlasUpdateRun(
            id=self._slug("update_run"), action="recent", title="批量应用候选", status="done",
            created_at=self._now(), candidate_count=applied, summary=f"批量应用 {applied} 篇候选论文",
        )
        updates.runs.insert(0, run)
        self.write_updates(updates)
        return AtlasUpdateResultPreviewResponse(run=run, candidates=updates.candidates, applied_count=applied)
