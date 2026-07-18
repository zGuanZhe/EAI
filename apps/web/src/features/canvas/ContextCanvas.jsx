import { useEffect, useMemo, useState } from "react";
import { Check, FlaskConical, Link2, Plus, Sparkles, X } from "lucide-react";
import { canvasReadiness } from "../../state/researchFlow.js";
import { Button, EmptyState, SegmentedControl, SurfaceHeader, cx } from "../../components/ui/index.jsx";
import { buildArgumentFlow, EDGE_LABELS, NODE_LABELS, normalizeEdgeLabel, TASK_STATUS_LABELS } from "./model.js";
import { CampaignCanvas } from "./CampaignCanvas.jsx";
import { useCampaigns } from "./useCampaigns.js";
import "./canvas.css";

const NODE_TYPES = ["question", "hypothesis", "evidence", "decision", "finding", "task"];

export function ContextCanvas({ thread, onDetail, onSave, onAskAgent, onThreadChanged, onStatus }) {
  const nodes = thread.canvas?.nodes || [];
  const edges = (thread.canvas?.edges || []).map((edge) => ({ ...edge, label: normalizeEdgeLabel(edge.label) }));
  const flow = useMemo(() => buildArgumentFlow(nodes, edges), [nodes, edges]);
  const readiness = canvasReadiness({ canvas: { nodes, edges } });
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [edgeSourceId, setEdgeSourceId] = useState(null);
  const [edgeLabel, setEdgeLabel] = useState("supports");
  const [addOpen, setAddOpen] = useState(false);
  const [view, setView] = useState(() => localStorage.getItem(`eai-canvas-view:${thread.id}`) || "argument");
  const campaigns = useCampaigns({ thread, onThreadChanged, onStatus });

  useEffect(() => {
    const stored = localStorage.getItem(`eai-canvas-view:${thread.id}`) || "argument";
    setView(stored);
  }, [thread.id]);

  function changeView(nextView) {
    setView(nextView);
    localStorage.setItem(`eai-canvas-view:${thread.id}`, nextView);
  }

  function saveCanvas(nextNodes, nextEdges = edges) {
    onSave({ nodes: nextNodes, edges: nextEdges.map((edge) => ({ ...edge, label: normalizeEdgeLabel(edge.label) })) });
  }

  function createNode(type, sourceId = selectedNodeId) {
    const node = {
      id: `node_${type}_${Date.now()}`,
      type,
      title: `新的${NODE_LABELS[type]}`,
      body: "",
      x: 0,
      y: nodes.length * 118 + 110,
      status: type === "task" ? "todo" : null,
      priority: type === "task" ? 1 : null
    };
    const nextEdges = sourceId
      ? [...edges, { id: `edge_${Date.now()}`, source: sourceId, target: node.id, label: type === "task" ? "requires" : edgeLabel }]
      : edges;
    saveCanvas([...nodes, node], nextEdges);
    setSelectedNodeId(node.id);
    setAddOpen(false);
  }

  function seedMaterials() {
    const existing = new Set(nodes.map((node) => node.card_id).filter(Boolean));
    const cards = (thread.context_cards || []).filter((card) => !existing.has(card.id));
    if (!cards.length) return;
    const question = nodes.find((node) => node.type === "question");
    const created = cards.map((card, index) => ({
      id: `node_material_${card.id}`,
      type: "material",
      title: card.title,
      body: card.summary || "",
      card_id: card.id,
      x: 0,
      y: (nodes.length + index) * 118 + 110
    }));
    const createdEdges = question ? created.map((node, index) => ({ id: `edge_material_${Date.now()}_${index}`, source: question.id, target: node.id, label: "supports" })) : [];
    saveCanvas([...nodes, ...created], [...edges, ...createdEdges]);
  }

  function updateNode(nodeId, patch) {
    saveCanvas(nodes.map((node) => node.id === nodeId ? { ...node, ...patch } : node));
  }

  function removeNode(nodeId) {
    saveCanvas(nodes.filter((node) => node.id !== nodeId), edges.filter((edge) => edge.source !== nodeId && edge.target !== nodeId));
    setSelectedNodeId(null);
  }

  function nodeDetail(node) {
    return {
      type: "canvas-node",
      value: node,
      onUpdate: (patch) => {
        const updated = { ...node, ...patch };
        updateNode(node.id, patch);
        onDetail?.(nodeDetail(updated));
      },
      onRemove: () => removeNode(node.id)
    };
  }

  function chooseNode(node) {
    if (edgeSourceId && edgeSourceId !== node.id) {
      const exists = edges.some((edge) => edge.source === edgeSourceId && edge.target === node.id && edge.label === edgeLabel);
      if (!exists) saveCanvas(nodes, [...edges, { id: `edge_${Date.now()}`, source: edgeSourceId, target: node.id, label: edgeLabel }]);
      setEdgeSourceId(null);
    }
    setSelectedNodeId(node.id);
    onDetail?.(nodeDetail(node));
  }

  function openCampaignIdeas(node) {
    setSelectedNodeId(node.id);
    changeView("campaign");
    campaigns.previewIdeas(node).catch((error) => onStatus?.(`研究想法生成失败：${error.message}`));
  }

  return (
    <section className="vertical-canvas">
      <SurfaceHeader
        tone="violet"
        eyebrow="论证工作流"
        title="Context Canvas"
        description="沿问题、假设、证据、结论和任务向下推进；实验运行会回到关联节点。"
        actions={(
          <>
            <SegmentedControl value={view} onChange={changeView} label="Canvas 视图" options={[{ value: "argument", label: "论证" }, { value: "campaign", label: "Campaign" }]} />
            {view === "argument" && <div className="canvas-add-menu">
              <Button variant="secondary" onClick={() => setAddOpen((value) => !value)}><Plus size={14} />添加节点</Button>
              {addOpen && <div>{NODE_TYPES.map((type) => <button type="button" key={type} onClick={() => createNode(type)}>{NODE_LABELS[type]}</button>)}</div>}
            </div>}
            {view === "argument" && <Button variant="primary" onClick={() => onAskAgent?.(readiness.ready ? "请检查当前 Context Canvas 的论证结构、证据支撑和下一步。" : "请帮助我搭建当前 Context Canvas：先澄清研究问题，再建议需要的证据、候选假设和一个可执行的下一步。") }><Sparkles size={14} />Agent 检查</Button>}
          </>
        )}
      />

      {view === "campaign" ? (
        <CampaignCanvas controller={campaigns} sourceNode={nodes.find((node) => node.id === selectedNodeId) || nodes.find((node) => ["question", "hypothesis"].includes(node.type))} onAskMainAgent={onAskAgent} />
      ) : <>
      <div className="canvas-readiness-line">
        {["问题", "材料", "假设/结论", "下一步"].map((label, index) => {
          const done = [readiness.hasQuestion, readiness.hasMaterial, readiness.hasArgument, readiness.hasTask][index];
          return <span className={cx(done && "done")} key={label}>{done ? <Check size={12} /> : <i />}{label}</span>;
        })}
        <p>{readiness.ready ? "论证骨架已具备，可继续验证证据与实验结果。" : "Canvas 不设门槛；先从当前最明确的问题开始。"}</p>
        {!readiness.hasMaterial && (thread.context_cards || []).length > 0 && <button type="button" onClick={seedMaterials}>放入长期资料</button>}
      </div>

      {edgeSourceId && (
        <div className="canvas-link-mode">
          <Link2 size={14} />
          <span>选择目标节点</span>
          <select value={edgeLabel} onChange={(event) => setEdgeLabel(event.target.value)}>{Object.entries(EDGE_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select>
          <button type="button" onClick={() => setEdgeSourceId(null)}><X size={14} />取消</button>
        </div>
      )}

      <div className="argument-flow">
        {!flow.items.length && <EmptyState title="还没有论证节点" description="添加一个问题，或先到 Atlas 选择材料。" action={<Button variant="primary" onClick={() => createNode("question", null)}><Plus size={14} />添加问题</Button>} />}
        {flow.items.map(({ node, depth, parentEdge, orphan }) => {
          const selected = node.id === selectedNodeId;
          return (
            <article className={cx("argument-node-row", `type-${node.type}`, selected && "selected", orphan && "orphan")} style={{ "--depth": depth }} key={node.id}>
              <div className="argument-connector"><i />{parentEdge && <span>{EDGE_LABELS[parentEdge.label] || parentEdge.label}</span>}</div>
              <button type="button" className="argument-node-main" onClick={() => chooseNode(node)}>
                <span className="argument-node-kind">{NODE_LABELS[node.type]}</span>
                <strong>{node.title}</strong>
                <p>{node.body || "尚未补充说明"}</p>
                {node.type === "task" && <small>{TASK_STATUS_LABELS[node.status || "todo"]} · P{node.priority ?? 1}</small>}
              </button>
              {selected && (
                <div className="argument-node-actions">
                  {["question", "hypothesis"].includes(node.type) && <button type="button" title="生成研究 Campaign" onClick={() => openCampaignIdeas(node)}><FlaskConical size={14} /></button>}
                  <button type="button" title="从此节点连接" onClick={() => setEdgeSourceId(node.id)}><Link2 size={14} /></button>
                  <button type="button" title="添加后续节点" onClick={() => createNode(node.type === "question" ? "hypothesis" : node.type === "hypothesis" ? "material" : node.type === "material" ? "conclusion" : "task", node.id)}><Plus size={14} /></button>
                </div>
              )}
            </article>
          );
        })}
        {!!flow.orphanIds.length && <div className="canvas-orphan-note">{flow.orphanIds.length} 个节点尚未接入主链，可选择节点后建立连接。</div>}
        {!!flow.cycleEdges.length && <div className="canvas-cycle-note">检测到 {flow.cycleEdges.length} 条循环关系，已保留数据并停止重复展开。</div>}
      </div>
      </>}
    </section>
  );
}
