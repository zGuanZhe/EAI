import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, ZoomIn, ZoomOut } from "lucide-react";
import { AtlasPaperNode } from "./AtlasPaperNode.jsx";
import { AtlasRelationLayer } from "./AtlasRelationLayer.jsx";
import { AtlasToolbar } from "./AtlasToolbar.jsx";
import {
  ATLAS_ZOOM_MAX,
  ATLAS_ZOOM_MIN,
  clampAtlasZoom,
  computeAtlasPositions,
  computeHorizontalRevealDelta,
  nextAtlasZoom,
  normalizePublicationYear,
  sortPublicationYearsDescending
} from "./atlasLayout.js";
import {
  FALLBACK_COLORS, cardKey, cx, getPaperLevel, getPaperRouteId, getPaperRouteName,
  objectMemoryKey, routeLabel, routeShort, tokenEstimate
} from "../../workspace/shared.js";

export function AtlasSurface({
  bundle,
  activeAtlas,
  onExport,
  onControls,
  onDetail,
  onClearDetail,
  onOpenPaperDetail,
  onAskPaper,
  onAddCard,
  onRemoveCard,
  cards,
  selectedCount = 0,
  onContinueToCanvas,
  onOpenOverview,
  memoryMap,
  atlasUpdates,
  inspectorOpen,
  focusPaperId,
  onFocusHandled,
  pathDraft,
  setPathDraft
}) {
  const [selectedId, setSelectedId] = useState(null);
  const [query, setQuery] = useState("");
  const [tierFilter, setTierFilter] = useState("all");
  const [routeFilter, setRouteFilter] = useState("all");
  const [yearFilter, setYearFilter] = useState("all");
  const [linkMode, setLinkMode] = useState("focus");
  const [hoveredRelationId, setHoveredRelationId] = useState(null);
  const [lockedRelationId, setLockedRelationId] = useState(null);
  const [isPanning, setIsPanning] = useState(false);
  const [viewScale, setViewScale] = useState(1);
  const [viewport, setViewport] = useState({ left: 0, top: 0, width: 1200, height: 900 });
  const shellRef = useRef(null);
  const paperNodeRefs = useRef(new Map());
  const panRef = useRef(null);
  const zoomAnchorRef = useRef(null);

  useEffect(() => {
    setSelectedId(null);
    setLockedRelationId(null);
    setPathDraft([]);
  }, [activeAtlas, setPathDraft]);

  const graph = useMemo(
    () => computeTimeline(bundle, { query, tierFilter, routeFilter, yearFilter }, atlasUpdates),
    [bundle, query, tierFilter, routeFilter, yearFilter, atlasUpdates]
  );

  const related = selectedId && graph ? relatedIds(selectedId, graph.relations) : new Set();
  const contextKeys = new Set((cards || []).map(cardKey));
  const visibleEdges = (graph?.relations || []).filter((rel) => {
    if (linkMode === "hidden") return false;
    if (linkMode === "all") return true;
    return selectedId && (rel.source === selectedId || rel.target === selectedId);
  });
  const visiblePapers = useMemo(() => {
    if (!graph) return [];
    const padX = 360;
    const padY = 260;
    return graph.papers.filter((paper) => {
      const pos = graph.positions.get(paper.id);
      if (!pos) return false;
      return (
        pos.x + graph.cardWidth >= viewport.left - padX &&
        pos.x <= viewport.left + viewport.width + padX &&
        pos.y + graph.cardHeight >= viewport.top - padY &&
        pos.y <= viewport.top + viewport.height + padY
      );
    });
  }, [graph, viewport]);
  const renderedEdges = useMemo(() => {
    if (!graph) return [];
    const visiblePaperIds = new Set(visiblePapers.map((paper) => paper.id));
    return visibleEdges.filter((rel) => {
      const selectedRelated = selectedId && (rel.source === selectedId || rel.target === selectedId);
      return selectedRelated || (visiblePaperIds.has(rel.source) && visiblePaperIds.has(rel.target));
    });
  }, [graph, visibleEdges, visiblePapers, selectedId]);

  function paperCard(paper) {
    if (paper.is_candidate) {
      return {
        id: `card_candidate_${paper.id}`,
        type: "paper",
        title: `候选：${paper.title}`,
        source_ref: { atlas_id: activeAtlas, candidate_id: paper.id, status: paper.candidate_status },
        summary: paper.why || paper.relevance || paper.summary || "",
        token_estimate: tokenEstimate(`${paper.title} ${paper.why || ""} ${paper.relevance || ""}`),
        selected_for_export: true,
        include_in_agent: true,
        include_in_lab: false
      };
    }
    return {
      id: `card_${paper.id}`,
      type: "paper",
      title: paper.title,
      source_ref: { atlas_id: activeAtlas, paper_id: paper.id },
      summary: paper.summary || paper.local_role || paper.why_included || "",
      token_estimate: tokenEstimate(`${paper.title} ${paper.summary || ""} ${paper.local_role || ""}`),
      selected_for_export: true,
      include_in_agent: true,
      include_in_lab: false
    };
  }

  function relationCard(rel) {
    const source = graph.paperMap.get(rel.source);
    const target = graph.paperMap.get(rel.target);
    return {
      id: `card_${rel.id}`,
      type: "relation",
      title: `${source?.title || rel.source} -> ${target?.title || rel.target}`,
      source_ref: { atlas_id: activeAtlas, relation_id: rel.id },
      summary: rel.reason || rel.label || rel.type,
      token_estimate: 180,
      selected_for_export: true,
      include_in_agent: true,
      include_in_lab: false
    };
  }

  function relationCardWithPapers(rel) {
    const source = rel.sourcePaper || graph.paperMap.get(rel.source);
    const target = rel.targetPaper || graph.paperMap.get(rel.target);
    return {
      id: `card_${rel.id}`,
      type: "relation",
      title: `${source?.title || rel.source} -> ${target?.title || rel.target}`,
      source_ref: { atlas_id: activeAtlas, relation_id: rel.id },
      summary: rel.reason || rel.label || rel.type,
      token_estimate: 180,
      selected_for_export: true,
      include_in_agent: true,
      include_in_lab: false
    };
  }

  function buildPaperOverlayDetail(paper) {
    const paperRouteId = getPaperRouteId(paper);
    const paperRelations = graph.relations
      .filter((rel) => rel.source === paper.id || rel.target === paper.id)
      .map((rel) => ({
        ...rel,
        direction: rel.source === paper.id ? "out" : "in",
        sourcePaper: graph.paperMap.get(rel.source),
        targetPaper: graph.paperMap.get(rel.target)
      }));
    return {
      type: "paper",
      value: paper,
      route: graph.routeMap.get(paperRouteId),
      routeColor: graph.routeColors.get(paperRouteId),
      memory: memoryMap.get(objectMemoryKey("paper", paper.id)),
      relations: paperRelations,
      card: paperCard(paper),
      relationCard: relationCardWithPapers,
      atlas: {
        id: activeAtlas,
        title: bundle.atlas?.title || `${activeAtlas} Atlas`,
        titleCn: bundle.atlas?.title_cn || ""
      }
    };
  }

  function openPaperOverlay(paper, event) {
    event?.stopPropagation();
    onOpenPaperDetail?.(buildPaperOverlayDetail(paper));
  }

  function addPathCard() {
    if (pathDraft.length < 2) return;
    const titles = pathDraft.map((id) => graph.paperMap.get(id)?.title || id);
    onAddCard({
      id: `card_path_${activeAtlas}_${pathDraft.join("_")}`,
      type: "path",
      title: titles.join(" -> "),
      source_ref: { atlas_id: activeAtlas, path_id: `path_${pathDraft.join("_")}`, paper_ids: pathDraft },
      summary: "用户在 Atlas 时间线中选择的推理路径。",
      token_estimate: 260 + pathDraft.length * 80,
      selected_for_export: true,
      include_in_agent: true,
      include_in_lab: false
    });
    setPathDraft([]);
  }

  function selectPaper(paper, event) {
    if (event?.shiftKey) {
      setPathDraft((prev) => (prev.includes(paper.id) ? prev : [...prev, paper.id]));
      return;
    }
    setSelectedId(paper.id);
    setLockedRelationId(null);
    onDetail({ type: "paper", value: paper, fullDetail: buildPaperOverlayDetail(paper) });
  }

  function preferredScrollBehavior() {
    return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
  }

  function revealSelectedPaper(behavior = preferredScrollBehavior()) {
    const shell = shellRef.current;
    const node = selectedId ? paperNodeRefs.current.get(selectedId) : null;
    if (!shell || !node || !graph) return;
    const shellRect = shell.getBoundingClientRect();
    const nodeRect = node.getBoundingClientRect();
    const rail = inspectorOpen ? document.querySelector(".right-rail") : null;
    const railRect = rail?.getBoundingClientRect();
    const overlapsShell = railRect && railRect.left < shellRect.right && railRect.right > shellRect.left;
    const delta = computeHorizontalRevealDelta({
      containerLeft: shellRect.left,
      containerRight: shellRect.right,
      nodeLeft: nodeRect.left,
      nodeRight: nodeRect.right,
      leadingInset: (graph.yearWidth + 18) * viewScale,
      trailingInset: 24,
      occluderLeft: overlapsShell ? railRect.left : null
    });
    if (Math.abs(delta) > 0.5) shell.scrollBy({ left: delta, behavior });
  }

  function clearFocus(event) {
    if (event.target.closest?.(".timeline-paper, .atlas-relation, .timeline-edge, .timeline-edge-hit, .paper-add, .paper-expand")) return;
    setSelectedId(null);
    setLockedRelationId(null);
    onClearDetail?.();
  }

  function canStartPan(event) {
    if (event.button !== 0) return false;
    return !event.target.closest?.(".timeline-paper, .atlas-relation, .timeline-edge, .timeline-edge-hit, .paper-add, .paper-expand, button, input, select, textarea, a");
  }

  function startPan(event) {
    if (!canStartPan(event)) return;
    const shell = event.currentTarget;
    setSelectedId(null);
    onClearDetail?.();
    panRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      scrollLeft: shell.scrollLeft,
      moved: false
    };
    shell.setPointerCapture?.(event.pointerId);
    setIsPanning(true);
  }

  function movePan(event) {
    const pan = panRef.current;
    if (!pan || pan.pointerId !== event.pointerId) return;
    const dx = event.clientX - pan.startX;
    const dy = event.clientY - pan.startY;
    if (!pan.moved && Math.abs(dy) > Math.abs(dx) + 4) {
      panRef.current = null;
      setIsPanning(false);
      event.currentTarget.releasePointerCapture?.(event.pointerId);
      return;
    }
    if (!pan.moved && Math.abs(dx) > 6) {
      pan.moved = true;
    }
    if (pan.moved) {
      event.currentTarget.scrollLeft = pan.scrollLeft - dx;
      event.preventDefault();
    }
  }

  function endPan(event) {
    const pan = panRef.current;
    if (!pan || pan.pointerId !== event.pointerId) return;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    panRef.current = null;
    setIsPanning(false);
  }

  function changeViewScale(nextScale, clientPoint = null) {
    const scale = clampAtlasZoom(nextScale);
    const shell = shellRef.current;
    if (!shell || scale === viewScale) return;
    const rect = shell.getBoundingClientRect();
    const pointerX = clientPoint ? clientPoint.x - rect.left : shell.clientWidth / 2;
    const pointerY = clientPoint ? clientPoint.y - rect.top : shell.clientHeight / 2;
    zoomAnchorRef.current = {
      pointerX,
      pointerY,
      logicalX: (shell.scrollLeft + pointerX) / viewScale,
      logicalY: (shell.scrollTop + pointerY) / viewScale
    };
    setViewScale(scale);
  }

  function updateViewport(shell, scale = viewScale) {
    setViewport({
      left: shell.scrollLeft / scale,
      top: Math.max(0, shell.scrollTop / scale - 90),
      width: (shell.clientWidth || 1200) / scale,
      height: (shell.clientHeight || 900) / scale
    });
  }

  useLayoutEffect(() => {
    const shell = shellRef.current;
    const anchor = zoomAnchorRef.current;
    if (!shell || !anchor) return;
    zoomAnchorRef.current = null;
    shell.scrollLeft = Math.max(0, anchor.logicalX * viewScale - anchor.pointerX);
    shell.scrollTop = Math.max(0, anchor.logicalY * viewScale - anchor.pointerY);
    updateViewport(shell, viewScale);
  }, [viewScale]);

  useEffect(() => {
    const shell = shellRef.current;
    if (!shell) return undefined;
    const handleWheel = (event) => {
      if (event.ctrlKey) {
        event.preventDefault();
        changeViewScale(nextAtlasZoom(viewScale, event.deltaY || event.deltaX), {
          x: event.clientX,
          y: event.clientY
        });
        return;
      }
      if (!event.shiftKey) return;
      event.preventDefault();
      shell.scrollLeft += event.deltaY + event.deltaX;
    };
    shell.addEventListener("wheel", handleWheel, { passive: false });
    return () => shell.removeEventListener("wheel", handleWheel);
  }, [viewScale]);

  useEffect(() => {
    if (!graph) {
      onControls?.(null);
      return undefined;
    }
    onControls?.({
      title: bundle.atlas?.title || `${activeAtlas} Atlas`,
      titleCn: bundle.atlas?.title_cn || "",
      description: bundle.atlas?.description || "",
      activeAtlas,
      visibleCount: graph.papers.length,
      totalCount: bundle.papers.length,
      candidateCount: graph.candidateCount || 0,
      edgeCount: visibleEdges.length,
      tiers: graph.tiers,
      routes: graph.routes.map((route, index) => ({
        id: route.id,
        label: routeLabel(route, index),
        rationale: route.rationale || "",
        color: graph.routeColors.get(route.id),
        count: graph.routeStats.find((item) => item.id === route.id)?.count || 0
      })),
      routeStats: graph.routeStats,
      yearStats: graph.yearStats,
      years: graph.allYears,
      query,
      setQuery,
      tierFilter,
      setTierFilter,
      routeFilter,
      setRouteFilter,
      yearFilter,
      setYearFilter,
      linkMode,
      toggleLinkMode: () => setLinkMode((value) => value === "focus" ? "all" : value === "all" ? "hidden" : "focus"),
      pathCount: pathDraft.length,
      addPathCard,
      onExport
    });
    return () => onControls?.(null);
  }, [
    activeAtlas,
    bundle?.atlas?.title,
    bundle?.papers?.length,
    graph,
    query,
    tierFilter,
    routeFilter,
    yearFilter,
    linkMode,
    pathDraft.length,
    visibleEdges.length
  ]);

  useEffect(() => {
    if (!focusPaperId || !graph) return;
    const paper = graph.paperMap.get(focusPaperId);
    const pos = graph.positions.get(focusPaperId);
    if (!paper || !pos) return;
    setSelectedId(focusPaperId);
    onDetail({ type: "paper", value: paper, fullDetail: buildPaperOverlayDetail(paper) });
    const shell = shellRef.current;
    if (shell) {
      shell.scrollTo({
        left: Math.max(0, (pos.x - 96) * viewScale),
        top: Math.max(0, (pos.y - 20) * viewScale),
        behavior: "smooth"
      });
    }
    onFocusHandled?.();
  }, [focusPaperId, graph, viewScale]);

  useEffect(() => {
    const shell = shellRef.current;
    if (!shell) return undefined;
    let frame = 0;
    const schedule = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        updateViewport(shell);
        revealSelectedPaper("auto");
      });
    };
    const observer = new ResizeObserver(schedule);
    observer.observe(shell);
    schedule();
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [graph?.width, graph?.height, selectedId, inspectorOpen, viewScale]);

  useEffect(() => {
    if (!selectedId) return undefined;
    let frame = requestAnimationFrame(() => {
      frame = requestAnimationFrame(() => revealSelectedPaper());
    });
    const appShell = shellRef.current?.closest(".app");
    const finishLayout = (event) => {
      if (event.propertyName === "grid-template-columns") revealSelectedPaper();
    };
    appShell?.addEventListener("transitionend", finishLayout);
    return () => {
      cancelAnimationFrame(frame);
      appShell?.removeEventListener("transitionend", finishLayout);
    };
  }, [selectedId, inspectorOpen, graph?.yearWidth, viewScale]);

  if (!bundle || !graph) {
    return <div className="empty-panel">正在加载 Atlas {activeAtlas}...</div>;
  }

  const leadingRoute = [...graph.routeStats].sort((a, b) => b.count - a.count)[0];
  const latestYear = graph.yearStats[0];
  const trendSummary = `${latestYear?.year || "近期"} 年的活跃工作主要集中在${leadingRoute?.label ? `“${leadingRoute.label}”` : "主要研究路线"}，可结合关系线判断方法演化与证据缺口。`;
  const firstVisibleRoute = Math.max(1, Math.floor(Math.max(0, viewport.left - graph.yearWidth) / graph.laneWidth) + 1);
  const lastVisibleRoute = Math.min(graph.routes.length, Math.ceil(Math.max(0, viewport.left + viewport.width - graph.yearWidth) / graph.laneWidth));
  const maxScrollLeft = Math.max(0, graph.width - viewport.width);
  const scrollProgress = maxScrollLeft ? Math.min(1, viewport.left / maxScrollLeft) : 0;

  function scrollRoutes(direction) {
    shellRef.current?.scrollBy({ left: direction * graph.laneWidth * 2 * viewScale, behavior: "smooth" });
  }

  return (
    <div className="atlas-surface">
      <section className="atlas-overview-strip">
        <div>
          <strong>{bundle.atlas?.title_cn || bundle.atlas?.title || `Atlas ${activeAtlas}`}</strong>
          <span>{trendSummary}</span>
        </div>
        <button type="button" onClick={onOpenOverview}>研究总览</button>
      </section>
      <AtlasToolbar
        query={query}
        onQuery={setQuery}
        routes={graph.routes}
        routeFilter={routeFilter}
        onRouteFilter={setRouteFilter}
        years={graph.allYears}
        yearFilter={yearFilter}
        onYearFilter={setYearFilter}
        relationMode={linkMode}
        onRelationMode={setLinkMode}
        onOpenUpdate={onOpenOverview}
        routeLabel={routeLabel}
      />
      <nav className="atlas-route-navigator" aria-label="Atlas 横向路线位置">
        <button type="button" title="向左查看路线" disabled={viewport.left <= 2} onClick={() => scrollRoutes(-1)}><ChevronLeft size={15} /></button>
        <div className="atlas-route-progress" aria-hidden="true"><span style={{ transform: `scaleX(${Math.max(0.04, scrollProgress)})` }} /></div>
        <span>路线 {firstVisibleRoute}-{Math.max(firstVisibleRoute, lastVisibleRoute)} / {graph.routes.length}</span>
        <div className="atlas-zoom-controls" aria-label="论文表大小">
          <button type="button" title="缩小论文表" disabled={viewScale <= ATLAS_ZOOM_MIN} onClick={() => changeViewScale(viewScale - 0.1)}><ZoomOut size={14} /></button>
          <button type="button" className="atlas-zoom-value" title="重置论文表大小" disabled={viewScale === 1} onClick={() => changeViewScale(1)}>{Math.round(viewScale * 100)}%</button>
          <button type="button" title="放大论文表" disabled={viewScale >= ATLAS_ZOOM_MAX} onClick={() => changeViewScale(viewScale + 0.1)}><ZoomIn size={14} /></button>
        </div>
        <button type="button" title="向右查看路线" disabled={viewport.left >= maxScrollLeft - 2} onClick={() => scrollRoutes(1)}><ChevronRight size={15} /></button>
      </nav>
      <div
        ref={shellRef}
        className={cx("timeline-shell", isPanning && "is-panning")}
        onPointerDownCapture={startPan}
        onPointerMove={movePan}
        onPointerUp={endPan}
        onPointerCancel={endPan}
        onScroll={(event) => updateViewport(event.currentTarget)}
      >
        <div
          className="timeline-header"
          style={{
            width: graph.width,
            zoom: viewScale,
            gridTemplateColumns: `${graph.yearWidth}px repeat(${graph.routes.length}, ${graph.laneWidth}px)`
          }}
        >
          <div className="atlas-table-row">
            <span>当前表格</span>
            <strong>Atlas {activeAtlas} · {bundle.atlas?.title || "研究图谱"}</strong>
            <em>{bundle.papers.length} 篇正式 · {graph.candidateCount || 0} 篇候选 · {visibleEdges.length} 条关系</em>
          </div>
          <div className="year-head">年份</div>
          {graph.routes.map((route, index) => (
            <div
              className="lane-head"
              key={route.id}
              style={{
                "--route": graph.routeColors.get(route.id),
                "--route-bg": graph.routeBgs.get(route.id)
              }}
            >
              <strong>{routeLabel(route, index)}</strong>
              <span>{route.rationale || routeShort(route, index)}</span>
            </div>
          ))}
        </div>
        <div className="timeline-map" style={{ width: graph.width, height: graph.height, zoom: viewScale }} onClick={clearFocus}>
          <AtlasRelationLayer
            graph={graph}
            relations={renderedEdges}
            selectedId={selectedId}
            hoveredRelationId={hoveredRelationId}
            lockedRelationId={lockedRelationId}
            onHoverRelation={setHoveredRelationId}
            onSelectRelation={(rel) => {
              setLockedRelationId(rel.id);
              onDetail({
                type: "relation",
                value: rel,
                source: graph.paperMap.get(rel.source),
                target: graph.paperMap.get(rel.target)
              });
            }}
          />

          {graph.yearRows.map((row) => (
            <div
              className="year-row-bg"
              key={row.year}
              style={{
                top: row.y,
                height: row.height,
                gridTemplateColumns: `${graph.yearWidth}px repeat(${graph.routes.length}, ${graph.laneWidth}px)`
              }}
            >
              <div className="year-label">{row.year}</div>
              {graph.routes.map((route) => (
                <div
                  key={route.id}
                  className="year-cell"
                  style={{ "--route": graph.routeColors.get(route.id) }}
                />
              ))}
            </div>
          ))}

          {visiblePapers.map((paper) => {
            const pos = graph.positions.get(paper.id);
            const paperRouteId = getPaperRouteId(paper);
            const route = graph.routeMap.get(paperRouteId);
            const color = graph.routeColors.get(paperRouteId);
            const memory = memoryMap.get(objectMemoryKey("paper", paper.id));
            const isSelected = selectedId === paper.id;
            const isRelated = related.has(paper.id);
            const inPath = pathDraft.includes(paper.id);
            const pathIndex = pathDraft.indexOf(paper.id);
            const card = paperCard(paper);
            const isInContext = contextKeys.has(cardKey(card));
            const paperRelations = graph.relations.filter((rel) => rel.source === paper.id || rel.target === paper.id);
            const hoveredRelation = graph.relations.find((rel) => rel.id === hoveredRelationId);
            const relationHighlighted = hoveredRelation && (hoveredRelation.source === paper.id || hoveredRelation.target === paper.id);
            return (
              <AtlasPaperNode
                key={paper.id}
                paper={paper}
                position={pos}
                cardWidth={graph.cardWidth}
                route={route}
                routeColor={color}
                routeBackground={graph.routeBgs.get(paperRouteId)}
                memory={memory}
                selected={isSelected}
                related={isRelated}
                dimmed={Boolean(selectedId && !isRelated)}
                relationHighlighted={relationHighlighted}
                inPath={inPath}
                pathIndex={pathIndex}
                inContext={isInContext}
                relationCount={paperRelations.length}
                nodeRef={(node) => {
                  if (node) paperNodeRefs.current.set(paper.id, node);
                  else paperNodeRefs.current.delete(paper.id);
                }}
                onSelect={(event) => selectPaper(paper, event)}
                onOpen={(event) => openPaperOverlay(paper, event)}
                onAsk={() => onAskPaper?.(buildPaperOverlayDetail(paper))}
                onToggleContext={() => isInContext ? onRemoveCard(card) : onAddCard(card)}
                routeLabel={(value) => routeShort(value, 0)}
              />
            );
          })}
        </div>
      </div>
    </div>
  );
}

function candidateToPaper(candidate, fallbackRouteId) {
  return {
    id: candidate.id,
    title: candidate.title,
    authors: candidate.authors || [],
    year: candidate.year || "----",
    venue: candidate.venue || "候选",
    url: candidate.url || "",
    doi: candidate.doi || "",
    arxiv: candidate.arxiv_id || "",
    summary: candidate.abstract || candidate.why || candidate.relevance || "",
    route_id: candidate.suggested_route_id || fallbackRouteId || "candidate",
    tier: "候选",
    include_type: candidate.status === "applied" ? "个人已应用" : "待审候选",
    why: candidate.why || "",
    relevance: candidate.relevance || "",
    confidence: candidate.confidence || 0,
    candidate_status: candidate.status || "pending",
    duplicate_of: candidate.duplicate_of || "",
    is_candidate: true,
    raw_fields: {
      routeName: candidate.suggested_route_id || fallbackRouteId || "candidate",
      level: candidate.status === "applied" ? "个人已应用" : "候选论文"
    }
  };
}

function computeTimeline(bundle, filters, atlasUpdates = null) {
  if (!bundle?.papers?.length) return null;

  const candidateSource = (atlasUpdates?.candidates || [])
    .filter((candidate) => candidate.status !== "rejected")
    .map((candidate) => candidateToPaper(candidate, bundle.routes?.[0]?.id));
  const allPapers = [...bundle.papers, ...candidateSource];
  const routeIdsInPapers = [...new Set(allPapers.map(getPaperRouteId))];
  const routes = (bundle.routes?.length ? bundle.routes : routeIdsInPapers.map((id) => ({ id, name: id }))).map((route, index) => ({
    ...route,
    id: route.id || routeIdsInPapers[index] || `route_${index}`
  }));

  routeIdsInPapers.forEach((id) => {
    if (!routes.some((route) => route.id === id)) routes.push({ id, name: id });
  });

  const routeMap = new Map(routes.map((route) => [route.id, route]));
  const routeColors = new Map(routes.map((route, index) => [route.id, route.color || FALLBACK_COLORS[index % FALLBACK_COLORS.length]]));
  const routeBgs = new Map(routes.map((route, index) => [route.id, tint(route.color || FALLBACK_COLORS[index % FALLBACK_COLORS.length])]));
  const allYears = sortPublicationYearsDescending(allPapers.map((paper) => paper.year));
  const tiers = [...new Set(allPapers.map(getPaperLevel).filter(Boolean))].sort();

  const q = filters.query.trim().toLowerCase();
  const papers = allPapers.filter((paper) => {
    const text = [
      paper.title,
      paper.venue,
      paper.arxiv,
      paper.summary,
      paper.local_role,
      paper.why_included,
      paper.why,
      paper.relevance,
      paper.boundary_note,
      getPaperRouteName(paper),
      getPaperLevel(paper),
      paper.raw_fields?.routeShort,
      paper.raw_fields?.routeRationale
    ].join(" ").toLowerCase();
    if (q && !text.includes(q)) return false;
    if (filters.tierFilter !== "all" && getPaperLevel(paper) !== filters.tierFilter) return false;
    if (filters.routeFilter !== "all" && getPaperRouteId(paper) !== filters.routeFilter) return false;
    if (filters.yearFilter !== "all" && String(paper.year) !== String(filters.yearFilter)) return false;
    return true;
  });

  const paperMap = new Map(papers.map((paper) => [paper.id, paper]));
  const relations = (bundle.relations || []).filter((rel) => paperMap.has(rel.source) && paperMap.has(rel.target));
  const years = sortPublicationYearsDescending(papers.map((paper) => paper.year));
  const layout = computeAtlasPositions({ papers, routes, years, getRouteId: getPaperRouteId });

  const coreCount = papers.filter((paper) => /core|核心/i.test(getPaperLevel(paper))).length;
  const boundaryCount = papers.filter((paper) => /boundary|边界/i.test(getPaperLevel(paper))).length;
  const reviewCount = papers.filter((paper) => /review|综述/i.test(getPaperLevel(paper))).length;
  const routeStats = routes.map((route, index) => ({
    id: route.id,
    label: routeLabel(route, index),
    rationale: route.rationale || "",
    color: routeColors.get(route.id),
    count: papers.filter((paper) => getPaperRouteId(paper) === route.id).length
  }));
  const yearStats = years.map((year) => ({
    year,
    count: papers.filter((paper) => normalizePublicationYear(paper.year) === year).length
  }));

  return {
    papers,
    routes,
    routeMap,
    routeColors,
    routeBgs,
    relations,
    paperMap,
    positions: layout.positions,
    yearRows: layout.yearRows,
    allYears,
    tiers,
    width: layout.width,
    height: layout.height,
    yearWidth: layout.yearWidth,
    laneWidth: layout.laneWidth,
    cardWidth: layout.cardWidth,
    cardHeight: layout.cardHeight,
    routeStats,
    yearStats,
    candidateCount: candidateSource.length,
    coreCount,
    boundaryCount,
    reviewCount
  };
}

function tint(hex) {
  if (!hex || !hex.startsWith("#") || hex.length !== 7) return "#f4f5f7";
  const value = hex.slice(1);
  const r = parseInt(value.slice(0, 2), 16);
  const g = parseInt(value.slice(2, 4), 16);
  const b = parseInt(value.slice(4, 6), 16);
  return `rgb(${Math.round(r + (255 - r) * 0.9)}, ${Math.round(g + (255 - g) * 0.9)}, ${Math.round(b + (255 - b) * 0.9)})`;
}

function relatedIds(id, relations) {
  const ids = new Set([id]);
  relations.forEach((rel) => {
    if (rel.source === id) ids.add(rel.target);
    if (rel.target === id) ids.add(rel.source);
  });
  return ids;
}

function relationClass(rel) {
  const type = String(rel.type || "").toLowerCase();
  if (type.includes("parallel") || type === "par") return "parallel";
  if (type.includes("bridge")) return "bridge";
  return "successor";
}
