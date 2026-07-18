from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from .models import ContextManifest, ContextManifestItem, FocusRef


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(*values: Any) -> str:
    payload = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _item(
    *, kind: str, identifier: Any, title: Any, layer: str, trust: str,
    priority: int, revision: int | None = None, evidence_eligible: bool = False,
    fingerprint: Any = None,
) -> ContextManifestItem | None:
    safe_id = str(identifier or "").strip()[:240]
    if not safe_id:
        return None
    safe_title = " ".join(str(title or "").split())[:500]
    return ContextManifestItem(
        kind=kind, id=safe_id, title=safe_title, priority=priority, revision=revision,
        source_layer=layer, trust=trust, evidence_eligible=evidence_eligible,
        content_hash=_hash(kind, safe_id, safe_title, revision, fingerprint),
    )


def build_context_manifest(
    *, task_id: str, thread_id: str, interaction_lane: str, source_policy: str,
    surface: str | None, focus_ref: FocusRef | None, raw: dict[str, Any],
    attachments: list[dict[str, Any]], context_refs: list[FocusRef] | None = None,
) -> ContextManifest:
    items: list[ContextManifestItem] = []
    omitted: list[dict[str, str]] = []
    local_allowed = source_policy in {"local_only", "local_and_external"}
    atlas_allowed = source_policy in {"atlas_only", "local_only", "local_and_external"}

    def atlas_material(value: dict[str, Any]) -> bool:
        source_ref = value.get("source_ref") if isinstance(value.get("source_ref"), dict) else {}
        return bool(
            value.get("type") in {"paper", "work"}
            and source_ref.get("atlas_id")
            and (source_ref.get("paper_id") or source_ref.get("work_id"))
        )

    if local_allowed or source_policy == "atlas_only":
        for index, attachment in enumerate(attachments[:8]):
            if source_policy == "atlas_only" and not atlas_material(attachment):
                omitted.append({"kind": str(attachment.get("type") or "attachment"), "reason": "atlas_only"})
                continue
            source_ref = attachment.get("source_ref") if isinstance(attachment, dict) else {}
            value = _item(
                kind=str(attachment.get("type") or "attachment"),
                identifier=attachment.get("id") or (source_ref or {}).get("id") or f"turn-{index + 1}",
                title=attachment.get("title"), layer="turn", trust="user", priority=100 - index,
                evidence_eligible=True, fingerprint=source_ref,
            )
            if value:
                items.append(value)
    elif attachments:
        omitted.append({"kind": "turn_attachments", "reason": "source_policy_excludes_local"})

    if focus_ref:
        focus_allowed = local_allowed or (source_policy == "atlas_only" and focus_ref.type in {"paper", "work"})
        if focus_allowed or focus_ref.type in {"thread", "project"}:
            value = _item(
                kind=focus_ref.type, identifier=focus_ref.id, title=focus_ref.title,
                layer="focus", trust="user", priority=90,
                evidence_eligible=(
                    (local_allowed and focus_ref.type in {"paper", "work", "document"})
                    or (source_policy == "atlas_only" and focus_ref.type in {"paper", "work"})
                ),
            )
            if value:
                items.append(value)
        else:
            omitted.append({"kind": "focus_ref", "reason": "source_policy_excludes_local"})

    for index, ref in enumerate((context_refs or [])[:12]):
        ref_allowed = local_allowed or (source_policy == "atlas_only" and ref.type in {"paper", "work"})
        if not ref_allowed:
            omitted.append({"kind": ref.type, "reason": "source_policy_excludes_local"})
            continue
        value = _item(
            kind=ref.type, identifier=ref.id, title=ref.title, layer="thread",
            trust="user", priority=80 - index, evidence_eligible=ref.type in {"paper", "work", "document"},
        )
        if value:
            items.append(value)

    thread = raw.get("thread") or {}
    value = _item(
        kind="thread", identifier=thread.get("id") or thread_id, title=thread.get("title"),
        layer="thread", trust="system", priority=70, revision=thread.get("revision"),
        fingerprint={"goal": thread.get("goal"), "summary": thread.get("conversation_summary")},
    )
    if value:
        items.append(value)

    for index, message in enumerate((raw.get("recent_messages") or [])[-8:]):
        value = _item(
            kind=f"message:{message.get('role') or 'unknown'}",
            identifier=message.get("id") or f"recent-{index + 1}",
            title="Recent thread message",
            layer="thread",
            trust="user" if message.get("role") == "user" else "system",
            priority=68 - index,
            evidence_eligible=False,
            fingerprint={"content": message.get("content"), "created_at": message.get("created_at")},
        )
        if value:
            items.append(value)
    project = raw.get("project") or {}
    value = _item(
        kind="project", identifier=project.get("id"), title=project.get("title"),
        layer="project", trust="system", priority=60, revision=project.get("revision"),
        fingerprint=project.get("goal"),
    )
    if value:
        items.append(value)

    if local_allowed:
        for index, card in enumerate((raw.get("context_cards") or [])[:24]):
            value = _item(
                kind=str(card.get("type") or "context-card"), identifier=card.get("id"),
                title=card.get("title"), layer="thread", trust="user", priority=50 - index,
                evidence_eligible=True, fingerprint=card.get("source_ref"),
            )
            if value:
                items.append(value)
        for index, memory in enumerate((raw.get("long_term_memories") or [])[:24]):
            object_ref = memory.get("object_ref") if isinstance(memory.get("object_ref"), dict) else {}
            value = _item(
                kind="memory",
                identifier=(
                    memory.get("id")
                    or object_ref.get("object_id")
                    or _hash(object_ref)[:24]
                ),
                title=memory.get("title") or memory.get("title_snapshot"),
                layer="memory", trust="user", priority=30 - index, evidence_eligible=False,
                fingerprint=memory,
            )
            if value:
                items.append(value)
        canvas = raw.get("canvas") or {}
        if canvas:
            value = _item(
                kind="canvas", identifier=thread_id, title="Context Canvas",
                layer="project", trust="user", priority=28, evidence_eligible=False,
                fingerprint=canvas,
            )
            if value:
                items.append(value)
        graph = (raw.get("research_state") or {}).get("normalized_graph")
        if graph:
            value = _item(
                kind="research_graph", identifier=thread_id, title="Research graph",
                layer="project", trust="system", priority=26, evidence_eligible=False,
                fingerprint=graph,
            )
            if value:
                items.append(value)
        for index, campaign in enumerate((raw.get("campaign_summaries") or [])[:6]):
            value = _item(
                kind="campaign", identifier=campaign.get("id"), title=campaign.get("title"),
                layer="project", trust="system", priority=24 - index,
                evidence_eligible=False, fingerprint=campaign,
            )
            if value:
                items.append(value)
    elif atlas_allowed:
        omitted.append({"kind": "personal_context", "reason": "atlas_only"})

    unique: dict[tuple[str, str], ContextManifestItem] = {}
    for value in items:
        unique.setdefault((value.kind, value.id), value)
    ordered = sorted(unique.values(), key=lambda value: value.priority, reverse=True)
    created_at = _now()
    content_hash = _hash([value.model_dump(mode="json") for value in ordered], omitted, source_policy)
    return ContextManifest(
        id=f"context_manifest_{uuid.uuid4().hex[:16]}", task_id=task_id, thread_id=thread_id,
        interaction_lane=interaction_lane, source_policy=source_policy, surface=surface or "thread",
        items=ordered, omitted=omitted, content_hash=content_hash, created_at=created_at,
    )
