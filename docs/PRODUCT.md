# EAI-Desktop vNext Product

## Product

EAI-Desktop vNext is a Codex-like personal research workspace for turning paper graphs into research decisions. Its Main Agent can investigate the current thread, Atlas, Context Canvas, and object memory, while every durable modification remains visible, reviewable, and reversible.

## Main Loop

The product loop is:

```text
提出研究问题 -> Main Agent / Atlas 收集证据 -> Canvas 形成论证主链
-> 从假设创建研究 Campaign -> BFTS 分阶段实验树 -> 论文 / 审稿 / 修订
-> 确认发现 -> 回写 Canvas / 导出可复现研究包
```

Every visible primary action must serve one of those steps. Capabilities outside the loop belong in Tools or legacy reference areas.

## Core Objects

- Project: an outcome target such as a survey chapter, paper section, experiment report, or research memo.
- Thread: one research question under a project. Threads own messages, context cards, result cards, tool runs, and active surface.
- Atlas: the high-frequency evidence selection space, organized by route and year.
- Research Work: a canonical paper identity shared by every Atlas placement and document version.
- Claim / Evidence Span: an atomic statement linked to a real page, section, text span, table location, Atlas curation note, or explicit personal judgement.
- Evidence Bundle: the only citable context returned by mixed identity, text, vector, graph, and research-state retrieval.
- Context Canvas: one workspace with an argument view for questions, hypotheses, evidence, decisions and tasks, plus a Campaign view for progressive experiment branches.
- Research Campaign: an AI Scientist v2-derived workflow covering evidence preparation, progressive BFTS experiments, writeup, three-role review, revision, and release validation.
- Object Memory: durable personal judgement attached to papers, relations, paths, files, candidates, and reading notes.
- OperationBatch: the Runtime v2 SQLite-transactional operation proposal. It records real before/after values, requires risk-based confirmation, and supports conflict-safe undo. JSON compatibility projections are eventually consistent and are not part of that transaction.
- ChangeSet: the compatible v1 proposal format retained for historical threads.
- Task Pack: an advanced interoperability package available from the context editor, not a primary workflow step.
- Legacy Lab Run: a migration-only source. Existing records become archived manual Campaign branches; no new Lab Run is created.

## Primary Surfaces

- Home: a compact conversation starting point. The Composer is the anchor; recent threads appear below as quiet rows.
- Thread: the Main Agent hub. It owns steerable task state, validated citations, approvals, receipts, retry and cancel state.
- Atlas: evidence selection. The quiet default is table-wide overview and trend judgement, then focused details.
- Context Canvas: a vertical argument chain and the entry point for node-linked experiment runs.
- Right Rail: stable source and ChangeSet inspector for the current conversation or focused object.
- Run Center: knowledge coverage and synchronization, experiment history, pending changes, activity receipts, maintenance, and collapsed legacy references.
- Campaign Workspace: five stable views for overview, experiment tree, artifacts, manuscript, and review.

## Success Criteria

- Users always know which project, thread, backend root, personal data directory, and Atlas cache they are using.
- Plain input always starts a durable Main Agent v2 turn; only `/atlas`, `/context`, and `/tools` execute UI commands.
- Ordinary conversation has no tool, Skill, source, or audit noise. Research tasks show natural progress and collapse to source/artifact counts after completion.
- While research is running, the Composer remains usable as “追加要求”; steer messages are applied at the next safe checkpoint without creating a duplicate task.
- Waiting approvals never lock the conversation. A new question starts a normal turn while the old OperationBatch remains conflict-checked and independently resolvable.
- Composer mode can override routing with `自动 / 仅聊天 / 仅本地 / 深度研究 / 执行任务`.
- Main dialogue remains readable first: user turns use light bubbles, assistant turns use Markdown prose, and audit details collapse after completion.
- Agent retrieval works without manually selected material; papers may be attached for one turn or retained as long-term thread material without quotas.
- Paper reading is a reading-first object page. New paper questions return to the Main Agent instead of creating a second chat history.
- Paper reading distinguishes metadata, Atlas summaries, machine-derived claims, personal judgements, and quote-backed full-text evidence. A citation must resolve to its actual locator.
- Atlas nodes show only compact evidence readiness; synchronization detail remains in Run Center and never interrupts ordinary chat.
- AI output never writes directly to long-term memory. MemoryDraft and OperationBatch require confirmation; successful batches expose safe undo.
- Research prose appears only after Evidence Guard validation. Retrieved-but-unused sources remain in audit and never appear as answer citations.
- Evidence labels describe citation integrity and sufficiency, not factual truth: “引用定位已核验”, “证据有限”, or “未通过核验”.
- Thread drafts survive refresh and carry revision/updated_at. Multi-window conflicts never use silent last-write-wins, and attachments persist only as registered `{type, id}` references.
- The interface feels quiet, trustworthy, and repeatable under daily use.

## Non-goals

- Do not rebuild the legacy admin backend.
- Do not make Atlas a PDF reader.
- Do not auto-write official bundle/SQLite data.
- Do not execute arbitrary model-selected code, host commands, or unregistered capabilities. Approved commands run only in a constrained Docker workspace.
- Research Campaign may iterate automatically inside one approved branch session. Every new branch session, stage transition, durable promotion, and release selection remains explicit.
- Machine-generated manuscripts must carry the disclosure required by the vendored AI Scientist v2 license.
- Do not expose keys or secret file contents in the frontend.
