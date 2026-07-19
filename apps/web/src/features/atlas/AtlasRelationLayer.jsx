import { routeAtlasRelation } from "./atlasLayout.js";

function cx(...parts) {
  return parts.filter(Boolean).join(" ");
}

export function relationTone(relation) {
  const type = String(relation.type || "").toLowerCase();
  if (type.includes("parallel") || type === "par") return "parallel";
  if (type.includes("bridge")) return "bridge";
  return "successor";
}

export function AtlasRelationLayer({
  graph,
  relations,
  selectedId,
  hoveredRelationId,
  lockedRelationId,
  onHoverRelation,
  onSelectRelation
}) {
  function selectFromEvent(event) {
    const relationId = event.target.closest?.(".atlas-relation")?.dataset.relationId || hoveredRelationId;
    const relation = relations.find((item) => item.id === relationId);
    if (!relation) return;
    event.stopPropagation();
    onSelectRelation(relation);
  }

  return (
    <svg
      className={cx("timeline-edges", selectedId && "has-selection")}
      width={graph.width}
      height={graph.height}
      viewBox={`0 0 ${graph.width} ${graph.height}`}
      aria-label="论文关系层"
      onClick={selectFromEvent}
    >
      <defs>
        <marker id="atlas-arrow-successor" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
          <path d="M 0 1 L 7 4 L 0 7 Z" />
        </marker>
        <marker id="atlas-arrow-parallel" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
          <path d="M 0 1 L 7 4 L 0 7 Z" />
        </marker>
        <marker id="atlas-arrow-bridge" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
          <path d="M 0 1 L 7 4 L 0 7 Z" />
        </marker>
      </defs>
      {relations.map((relation, index) => {
        const route = routeAtlasRelation(relation, graph, index);
        if (!route) return null;
        const tone = relationTone(relation);
        const focused = selectedId && (relation.source === selectedId || relation.target === selectedId);
        const hovered = hoveredRelationId === relation.id;
        const locked = lockedRelationId === relation.id;
        return (
          <g
            key={relation.id}
            className={cx("atlas-relation", tone, focused && "focus", hovered && "hovered", locked && "locked")}
            data-relation-id={relation.id}
          >
            <path className="timeline-edge" d={route.d} markerEnd={`url(#atlas-arrow-${tone})`} />
            <circle className="relation-port" cx={route.sourcePort.x} cy={route.sourcePort.y} r="3.2" />
            <circle className="relation-port" cx={route.targetPort.x} cy={route.targetPort.y} r="3.2" />
            {(hovered || locked) && (
              <foreignObject className="relation-label-object" x={route.labelPoint.x - 72} y={route.labelPoint.y - 15} width="144" height="30">
                <div className="relation-label">{relation.reason || relation.label || relation.type || "论文关系"}</div>
              </foreignObject>
            )}
            <path
              className="timeline-edge-hit"
              d={route.d}
              tabIndex="0"
              aria-label={relation.reason || relation.label || "论文关系"}
              onMouseEnter={() => onHoverRelation?.(relation.id)}
              onMouseLeave={() => onHoverRelation?.(null)}
              onFocus={() => onHoverRelation?.(relation.id)}
              onBlur={() => onHoverRelation?.(null)}
              onKeyDown={(event) => {
                if (event.key !== "Enter" && event.key !== " ") return;
                event.preventDefault();
                event.stopPropagation();
                onSelectRelation(relation);
              }}
            />
          </g>
        );
      })}
    </svg>
  );
}
