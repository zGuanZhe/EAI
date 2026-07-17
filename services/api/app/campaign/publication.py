from __future__ import annotations

import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any, Callable

from .models import (
    CampaignArtifactRef,
    CitationBinding,
    Manuscript,
    ManuscriptSection,
    PeerReview,
    RevisionRound,
)


DISCLOSURE = (
    "Disclosure: This manuscript was machine-generated with The AI Scientist and EAI Desktop. "
    "A human researcher selected the research direction, approved execution sessions, and reviewed the outputs."
)


def safe_latex(value: str) -> str:
    replacements = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}"}
    return "".join(replacements.get(char, char) for char in str(value or ""))


def parse_json_object(raw: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


class PublicationService:
    def __init__(self, planner: Callable[[str], str] | None = None):
        self.planner = planner

    def _model_json(self, prompt: str) -> dict[str, Any] | None:
        if not self.planner:
            return None
        try:
            return parse_json_object(self.planner(prompt))
        except Exception:
            return None

    def generate(self, *, campaign, branches, metrics, sources, workspace: Path, now: str) -> Manuscript:
        workspace.mkdir(parents=True, exist_ok=True)
        best = [branch for branch in branches if branch.is_best] or [branch for branch in branches if branch.status in {"succeeded", "promoted"}]
        metric_lines = []
        for metric in metrics:
            if any(branch.id == metric.branch_id for branch in best):
                metric_lines.append(f"{metric.name}={metric.value}{metric.unit} on {metric.dataset}/{metric.split}")
        source_lines = [f"{item.get('id')}: {item.get('title')}" for item in sources]
        payload = self._model_json(
            "Return JSON with keys abstract, introduction, methods, results, discussion, limitations, conclusion, chinese_summary. "
            "Do not invent citations or metrics. Every factual related-work statement must reference one supplied source id.\n"
            f"Hypothesis: {campaign.hypothesis}\nExperiments: {[item.analysis for item in best]}\n"
            f"Metrics: {metric_lines}\nSources: {source_lines}"
        ) or {}
        sections_payload = [
            ("Abstract", payload.get("abstract") or f"We investigate {campaign.hypothesis}. Results are reported only for approved experiment branches."),
            ("Introduction", payload.get("introduction") or f"This work studies {campaign.objective}. Related work is limited to the evidence records listed with this campaign."),
            ("Methods", payload.get("methods") or "We use isolated, versioned experiment branches and preserve each execution environment and code diff."),
            ("Results", payload.get("results") or ("; ".join(metric_lines) if metric_lines else "No compatible structured primary metric has been recorded.")),
            ("Discussion", payload.get("discussion") or "The current evidence supports an exploratory interpretation rather than a general claim."),
            ("Limitations", payload.get("limitations") or "Evidence coverage, compute budget, datasets, and random seeds limit the conclusions."),
            ("Conclusion", payload.get("conclusion") or "The Campaign records a reproducible path from hypothesis to the currently selected experimental result."),
            ("Disclosure", DISCLOSURE),
        ]
        manuscript_id = f"manuscript_{hashlib.sha256((campaign.id + now).encode()).hexdigest()[:16]}"
        bindings: list[CitationBinding] = []
        warnings: list[str] = []
        for index, source in enumerate(sources):
            evidence_ids = list(source.get("evidence_ids") or [])
            level = str(source.get("evidence_level") or "metadata")
            status = "verified" if evidence_ids and level == "full_text" else "limited"
            bindings.append(CitationBinding(
                id=f"binding_{manuscript_id}_{index}", manuscript_id=manuscript_id,
                citation_key=f"eai{index + 1}", source_id=str(source.get("id") or ""),
                evidence_span_ids=evidence_ids, evidence_level=level,
                claim_text=str(source.get("title") or ""), status=status,
            ))
            if status != "verified":
                warnings.append(f"{source.get('title') or source.get('id')} 只有 {level} 级证据")
        sections = [ManuscriptSection(id=f"section_{manuscript_id}_{index}", title=title, order=index, content=str(content)) for index, (title, content) in enumerate(sections_payload)]
        manuscript = Manuscript(
            id=manuscript_id, campaign_id=campaign.id, version=1, status="candidate", title=campaign.title,
            sections=sections, citation_bindings=bindings,
            chinese_summary=str(payload.get("chinese_summary") or f"本研究围绕“{campaign.hypothesis}”组织证据与实验分支，当前结论受已记录指标和证据覆盖范围约束。"),
            disclosure=DISCLOSURE, warnings=warnings, created_at=now, updated_at=now,
        )
        manuscript.latex_path = str(workspace / "manuscript.tex")
        manuscript.bibtex_path = str(workspace / "references.bib")
        Path(manuscript.latex_path).write_text(self.render_latex(manuscript), encoding="utf-8")
        Path(manuscript.bibtex_path).write_text(self.render_bibtex(manuscript, sources), encoding="utf-8")
        return manuscript

    @staticmethod
    def render_latex(manuscript: Manuscript) -> str:
        body = "\n\n".join(f"\\section{{{safe_latex(section.title)}}}\n{safe_latex(section.content)}" for section in manuscript.sections)
        return (
            "\\documentclass{article}\n\\usepackage[margin=1in]{geometry}\n\\usepackage{hyperref}\n"
            f"\\title{{{safe_latex(manuscript.title)}}}\n\\author{{EAI Desktop Research Campaign}}\n"
            "\\begin{document}\n\\maketitle\n" + body + "\n\\bibliographystyle{plain}\n\\bibliography{references}\n\\end{document}\n"
        )

    @staticmethod
    def render_bibtex(manuscript: Manuscript, sources: list[dict[str, Any]]) -> str:
        entries = []
        for index, source in enumerate(sources):
            entries.append(
                "@article{eai%d,\n  title={%s},\n  year={%s},\n  note={EAI Research Store source %s}\n}"
                % (index + 1, safe_latex(str(source.get("title") or "Untitled")), source.get("year") or "unknown", source.get("id") or "unknown")
            )
        return "\n\n".join(entries) + "\n"

    def reviews(self, manuscript: Manuscript, campaign_id: str, now: str) -> list[PeerReview]:
        roles = {
            "methods": "方法与实验有效性",
            "evidence": "证据、引用与相关工作",
            "presentation": "表达、结构与可复现性",
        }
        reviews: list[PeerReview] = []
        for role, label in roles.items():
            payload = self._model_json(
                f"Act as the {label} reviewer. Return JSON summary, strengths, weaknesses, questions, score (1-10), decision.\n"
                + "\n".join(f"## {section.title}\n{section.content}" for section in manuscript.sections)
            ) or {}
            weaknesses = list(payload.get("weaknesses") or [])
            if role == "evidence" and manuscript.warnings:
                weaknesses.extend(manuscript.warnings[:3])
            reviews.append(PeerReview(
                id=f"review_{manuscript.id}_{role}", campaign_id=campaign_id, manuscript_id=manuscript.id,
                role=role, summary=str(payload.get("summary") or f"{label}检查已完成。"),
                strengths=list(payload.get("strengths") or ["研究过程保留了分支与执行记录"]),
                weaknesses=weaknesses or ["仍需扩大实验与证据覆盖"],
                questions=list(payload.get("questions") or ["结论是否在额外数据集和随机种子上保持稳定？"]),
                score=float(payload.get("score") or (5 if manuscript.warnings else 6)),
                decision=str(payload.get("decision") or "revise") if str(payload.get("decision") or "revise") in {"accept", "revise", "reject"} else "revise",
                created_at=now,
            ))
        score = sum(item.score for item in reviews) / len(reviews)
        weaknesses = [item for review in reviews for item in review.weaknesses]
        reviews.append(PeerReview(
            id=f"review_{manuscript.id}_meta", campaign_id=campaign_id, manuscript_id=manuscript.id,
            role="meta", summary="三位审稿人的共同意见已汇总；优先修复证据边界、实验可比性和复现说明。",
            strengths=["研究轨迹可审计", "机器生成披露完整"], weaknesses=weaknesses[:6],
            questions=[item for review in reviews for item in review.questions][:6], score=score,
            decision="accept" if score >= 7 and not manuscript.warnings else "revise", created_at=now,
        ))
        return reviews

    def revise(self, manuscript: Manuscript, reviews: list[PeerReview], round_number: int, now: str) -> tuple[Manuscript, RevisionRound]:
        candidate = manuscript.model_copy(deep=True)
        candidate.id = f"manuscript_{hashlib.sha256((manuscript.id + str(round_number) + now).encode()).hexdigest()[:16]}"
        candidate.version = manuscript.version + 1
        candidate.status = "candidate"
        candidate.created_at = now
        candidate.updated_at = now
        candidate.warnings = list(manuscript.warnings)
        meta = next((item for item in reviews if item.role == "meta"), None)
        note = "Revision note: " + ((meta.weaknesses[0] if meta and meta.weaknesses else "clarified evidence and reproducibility boundaries"))
        target = next((section for section in candidate.sections if section.title == "Limitations"), candidate.sections[-1])
        before = target.content
        target.content = before + "\n\n" + note
        revision = RevisionRound(
            id=f"revision_{candidate.id}", campaign_id=manuscript.campaign_id,
            source_manuscript_id=manuscript.id, candidate_manuscript_id=candidate.id,
            round_number=round_number, summary="根据 meta-review 生成章节级候选修订。",
            section_diffs={target.id: f"- {before}\n+ {target.content}"}, created_at=now,
        )
        return candidate, revision

    def export(self, *, campaign, manuscript: Manuscript, artifacts: list[CampaignArtifactRef], workspace: Path, allow_warnings: bool) -> Path:
        invalid = [item for item in manuscript.citation_bindings if item.status in {"invalid", "unsupported"}]
        if invalid and not allow_warnings:
            raise ValueError("稿件包含无效或无依据引用，不能生成正式发布包")
        if campaign.disclosure_required and DISCLOSURE not in manuscript.disclosure:
            raise ValueError("稿件缺少 AI Scientist 机器生成披露")
        release_dir = workspace / "release"
        release_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "campaign_id": campaign.id, "manuscript_id": manuscript.id,
            "draft": bool(manuscript.warnings or invalid), "warnings": manuscript.warnings,
            "disclosure": manuscript.disclosure,
            "artifacts": [{"id": item.id, "kind": item.kind, "title": item.title, "content_hash": item.content_hash} for item in artifacts],
        }
        (release_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for source in [manuscript.latex_path, manuscript.bibtex_path, manuscript.pdf_path]:
            path = Path(source) if source else None
            if path and path.exists():
                shutil.copy2(path, release_dir / path.name)
        archive = workspace / f"{campaign.id}-research-package.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for item in release_dir.rglob("*"):
                if item.is_file():
                    bundle.write(item, item.relative_to(release_dir))
        return archive
