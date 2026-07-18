# EAI Desktop 1.0 Design

## Register

This is a product UI. Design serves repeated research work, so consistency and trust matter more than spectacle.

## Visual Direction

Codex-like research workspace:

- quiet near-white workspace
- soft gray side surfaces
- restrained multicolor tints, icons, route markers, and state dots
- smooth unframed surfaces with color used to separate roles
- rounded but not bubbly controls
- low-boundary panels
- no gradients
- no paper texture
- no decorative blur
- no backend dashboard density on the main path

## Tokens

- Radius: `8px` for compact controls, `12px` for rows, nodes, and selected states, and `16px` for the Composer, drawers, and primary input surfaces.
- Motion: `160ms` for direct feedback, `220ms` for workspace layout changes, and `240ms` for drawers, using `cubic-bezier(.22, 1, .36, 1)`.
- Shadow: avoid by default; use only small elevation for overlays or active drawers.
- Border: prefer subtle `--line` and background layering over boxed cards.
- Line weight: `1px` structure and `1.8-2.6px` graph relations. Components do not use colored outline frames.
- Accent: color marks state, route, node type, or current selection. It is not decoration.
- Typography: system UI stack, restrained scale, no fluid display type in tool surfaces.

## Layout Rules

- Left sidebar: project, current thread, stable Thread/Atlas/Canvas navigation, and low-frequency settings. It answers “where am I?”
- Center surface: one active work mode at a time. It answers “what am I doing now?”
- Right rail: at `>=1120px`, a `clamp(320px, 28vw, 400px)` source and ChangeSet inspector that smoothly pushes the center workspace; below `1120px`, it becomes an overlay drawer.
- Left sidebar: at `>=1120px`, it transitions between `248px` and `64px` while keeping navigation icons stable. Below `900px`, it uses the `64px` icon rail and an expandable overlay.
- Temporary context, settings, and advanced-import drawers always overlay and never change the center width.
- Composer: multiline Main Agent input in the Home and Thread surfaces only. It is a normal footer row, never an overlay on Atlas, Canvas, or paper reading.

## Interaction Rules

- Enter sends; Shift+Enter inserts a newline; Esc stops the active Main Agent run.
- An active ResearchTask never repurposes the Composer. The non-modal task bar owns targeted steer, pause, resume and cancel controls; the Composer continues to create ordinary AskTurns. A running AskTurn retains separate Stop and Send controls.
- `/atlas`, `/context`, and `/tools` are the only primary commands.
- Task Pack copy and manual import live only in collapsed advanced context editors.
- Ordinary conversation displays no trace. Research and operation tasks show one natural-language active state; completed work collapses to source and artifact counts, with capability and Observation audit available on demand.
- Main dialogue uses a hybrid conversation layout: a light user bubble and an unboxed, Markdown-rendered assistant answer.
- Context controls show material names, not token budgets or minimum counts. One-turn attachments clear after a successful send.
- Applied OperationBatch receipts appear below the answer with a lightweight undo action. Undo conflicts remain visible and never silently overwrite later user edits.
- Approval rows in the transcript stay compact. “检查并确认” opens the shared Inspector, where field diffs, command, resources, mounts and network scope are reviewed before approve/reject actions.
- The right rail is closed by default. On wide desktops it pushes the center workspace; on medium and narrow layouts it overlays the center without covering the left navigation.
- When Atlas layout width changes, the selected paper moves only enough to remain fully visible with a `24px` safe gap; it is never forcibly centered.
- Workspace, navigation, Inspector, node, and relation motion communicates state changes only. `prefers-reduced-motion` disables layout transitions, movement, and smooth scrolling.
- Paper pages render saved fields as prose; textareas appear only while editing one section.
- Paper evidence is shown as a quiet claim list beneath the overview. Expanding a claim reveals exact quotes and page/section locators; evidence level and source layer are always visible.
- Atlas paper nodes use one small evidence dot: gray for missing, cyan for identity metadata, violet for verified claims, and green for indexed full text.
- Citations navigate to the Atlas, Canvas, or object-memory source that produced them.
- Clicking Atlas blank space clears focus and returns the rail to Atlas overview.
- Atlas wheel scrolls vertically; Shift+wheel moves horizontally; drag is horizontal assistance only.
- Atlas relations default to focused one-hop neighborhoods. `All` and `Hidden` are explicit peer modes.
- Atlas paper nodes use soft route-tinted surfaces without full borders. Edges use deterministic ports and orthogonal year/route gutters.
- Paper click opens preview, double-click or Enter opens reading, and Context remains directly toggleable on the node.
- Context add buttons toggle between plus and green check.
- Durable writes use SQLite-transactional ChangeSets with field selection, conflict detection, receipts, and safe undo. JSON compatibility projections are replayable and eventually consistent outside that transaction.

## Component Vocabulary

- Primary button: one filled accent style per surface.
- Secondary button: low-boundary rounded button.
- Icon button: icon-first with tooltip/title.
- Segmented control: only for peer modes.
- Toast: short feedback, never a modal replacement.
- Confirmation: inline lightweight confirm, not browser `confirm`.

Shared primitives are `SurfaceHeader`, `Drawer`, `Button`, `IconButton`, `SegmentedControl`, `StatusDot`, `InlineNotice`, `EmptyState`, `FieldRow`, and `ActivityRow`. Feature pages must compose these primitives instead of inventing another panel vocabulary.

## Surface Grammar

- Home uses a left-aligned `40px` desktop title (`34px` at medium widths and `30px` on narrow screens), one content axis for prompts and recent threads, and the same Composer as a dedicated bottom footer.
- Canvas uses a stable `论证 / Campaign` segmented control. The argument view keeps a `920px` reading width; the Campaign view shows stage status, the active experiment tree and a branch Inspector.
- Campaign branches use quiet surfaces, status points and lineage connectors. Desktop branch details push the workspace with a `clamp(320px, 28vw, 400px)` Inspector; narrow layouts use an overlay Drawer and a vertical branch list.
- Only promoted hypotheses, findings, decisions and tasks enter the argument view. Raw code, stdout and failed debug branches remain in Campaign runtime history.
- Hypothesis and task nodes enter Campaign ideation. Existing legacy Lab records appear only as archived manual Campaign branches.
- Campaign uses five peer views: overview, experiment tree, artifacts, manuscript, and review. It does not create another chat surface.
- Branch-session approval shows image digest, mounts, CPU, memory, timeout and network scope before execution.
- Manuscript sections remain reading-first. Reviews and revisions use quiet row lists and section-level diffs rather than nested cards.
- Run Center uses timelines and list rows. It is not a dashboard or a grid of tool cards.
- Run Center knowledge status uses four compact line meters for identity, full text, verifiable relations, and claim coverage, followed by sync jobs with pause/resume/cancel controls.
- Source details and ChangeSets use the responsive Inspector behavior; context, settings, and advanced interoperability always use an overlay Drawer.
- Empty paper fields collapse into a compact pending list; text areas appear only for the field currently being edited.

Domain accents are stable: conversation blue, Atlas cyan, Canvas violet, Lab orange, sources green, changes amber, and errors red. Color separates roles but does not create full component outlines.

## Quality Bar

- No mojibake in visible UI, tests, Task Pack headings, or service responses.
- No full-width cards nested in cards.
- No thick colored side stripes.
- No large headings inside right rail panels.
- Loading keeps the previous stable surface or shows a lightweight skeleton.
