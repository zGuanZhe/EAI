const ITEMS = [
  ["hasQuestion", "问题"],
  ["hasMaterial", "材料"],
  ["hasArgument", "假设/结论"],
  ["hasTask", "下一步任务"]
];

function cx(...parts) {
  return parts.filter(Boolean).join(" ");
}

export function CanvasReadiness({ readiness, contextCount = 0, onSeedMaterials, onCreateQuestion, onCreateHypothesis, onAskAgent }) {
  const needsStructure = !readiness?.ready;
  const canSeedMaterials = contextCount > 0 && !readiness?.hasMaterial;
  return (
    <section className="canvas-readiness">
      <div className="canvas-readiness-head">
        <div>
          <strong>论证结构</strong>
          <span>{readiness?.ready ? "结构已具备基本论证元素，可以让 Main Agent 检查证据缺口。" : "补齐问题、材料和论证节点后再交给 Main Agent 检查。"}</span>
        </div>
        <button type="button" className="primary-button compact" disabled={!readiness?.ready} onClick={onAskAgent}>
          让 Main Agent 检查
        </button>
      </div>
      <div className="canvas-readiness-items">
        {ITEMS.map(([key, label]) => (
          <span key={key} className={cx(readiness?.[key] && "done")}>{label}</span>
        ))}
      </div>
      {needsStructure && (
        <div className="canvas-starter">
          <span>{contextCount ? `已选 ${contextCount} 张材料，可继续补齐结构。` : "先去 Atlas 选择证据，再回到这里编排。"}</span>
          {!readiness?.hasQuestion && <button type="button" onClick={onCreateQuestion}>生成问题节点</button>}
          {!readiness?.hasArgument && <button type="button" onClick={onCreateHypothesis}>生成假设节点</button>}
          {canSeedMaterials && <button type="button" onClick={onSeedMaterials}>放入材料列</button>}
        </div>
      )}
    </section>
  );
}
