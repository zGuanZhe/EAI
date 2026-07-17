export function computeAtlasPositions({
  papers,
  routes,
  years,
  getRouteId,
  yearWidth = 72,
  laneWidth = 238,
  cardWidth = 198,
  cardHeight = 154,
  headerHeight = 0,
  minRowHeight = 146,
  cardGap = 12
}) {
  const positions = new Map();
  const yearRows = [];
  let y = headerHeight;

  years.forEach((year) => {
    const perLaneCounts = routes.map((route) => papers.filter((paper) => (paper.year || "----") === year && getRouteId(paper) === route.id).length);
    const rowHeight = Math.max(minRowHeight, Math.max(...perLaneCounts, 1) * (cardHeight + cardGap) + 30);
    yearRows.push({ year, y, height: rowHeight });
    routes.forEach((route, routeIndex) => {
      const lanePapers = papers
        .filter((paper) => (paper.year || "----") === year && getRouteId(paper) === route.id)
        .sort((a, b) => Number(Boolean(b.star)) - Number(Boolean(a.star)) || a.title.localeCompare(b.title));
      lanePapers.forEach((paper, paperIndex) => {
        positions.set(paper.id, {
          x: yearWidth + routeIndex * laneWidth + 18,
          y: y + 18 + paperIndex * (cardHeight + cardGap),
          routeIndex,
          year
        });
      });
    });
    y += rowHeight;
  });

  return {
    positions,
    yearRows,
    width: yearWidth + routes.length * laneWidth,
    height: Math.max(680, y + 24),
    yearWidth,
    laneWidth,
    cardWidth,
    cardHeight
  };
}

export function computeHorizontalRevealDelta({
  containerLeft,
  containerRight,
  nodeLeft,
  nodeRight,
  leadingInset = 0,
  trailingInset = 24,
  occluderLeft = null
}) {
  const safeLeft = containerLeft + Math.max(0, leadingInset);
  const visibleRight = Number.isFinite(occluderLeft)
    ? Math.min(containerRight, occluderLeft)
    : containerRight;
  const safeRight = visibleRight - Math.max(0, trailingInset);
  if (safeRight <= safeLeft) return 0;
  if (nodeRight > safeRight) return nodeRight - safeRight;
  if (nodeLeft < safeLeft) return nodeLeft - safeLeft;
  return 0;
}

function roundedOrthogonalPath(rawPoints, radius = 7) {
  const points = rawPoints.filter((point, index) => index === 0 || point.x !== rawPoints[index - 1].x || point.y !== rawPoints[index - 1].y);
  if (points.length < 2) return "";
  let path = `M ${points[0].x} ${points[0].y}`;

  for (let index = 1; index < points.length - 1; index += 1) {
    const previous = points[index - 1];
    const current = points[index];
    const next = points[index + 1];
    const beforeDistance = Math.hypot(current.x - previous.x, current.y - previous.y);
    const afterDistance = Math.hypot(next.x - current.x, next.y - current.y);
    const corner = Math.min(radius, beforeDistance / 2, afterDistance / 2);
    const before = {
      x: current.x - Math.sign(current.x - previous.x) * corner,
      y: current.y - Math.sign(current.y - previous.y) * corner
    };
    const after = {
      x: current.x + Math.sign(next.x - current.x) * corner,
      y: current.y + Math.sign(next.y - current.y) * corner
    };
    path += ` L ${before.x} ${before.y} Q ${current.x} ${current.y} ${after.x} ${after.y}`;
  }

  const last = points[points.length - 1];
  return `${path} L ${last.x} ${last.y}`;
}

function rowForPosition(graph, position) {
  return graph.yearRows.find((row) => position.y >= row.y && position.y < row.y + row.height) || graph.yearRows[0];
}

export function routeAtlasRelation(relation, graph, relationIndex = 0) {
  const source = graph.positions.get(relation.source);
  const target = graph.positions.get(relation.target);
  if (!source || !target) return null;

  const sourceRow = rowForPosition(graph, source);
  const targetRow = rowForPosition(graph, target);
  const slot = (relationIndex % 4) * 3;
  const sameRoute = source.routeIndex === target.routeIndex;
  const sameYear = source.year === target.year;
  let points;
  let sourcePort;
  let targetPort;

  if (sameRoute && !sameYear) {
    const channelX = graph.yearWidth + (source.routeIndex + 1) * graph.laneWidth - 8 - slot;
    sourcePort = { x: source.x + graph.cardWidth, y: source.y + 42 };
    targetPort = { x: target.x + graph.cardWidth, y: target.y + 42 };
    points = [sourcePort, { x: channelX, y: sourcePort.y }, { x: channelX, y: targetPort.y }, targetPort];
  } else {
    const sourceChannelY = sourceRow.y + 8 + slot;
    const targetChannelY = targetRow.y + 8 + slot;
    const direction = target.x >= source.x ? 1 : -1;
    const sourceLaneEdge = direction > 0
      ? graph.yearWidth + (source.routeIndex + 1) * graph.laneWidth - 8 - slot
      : graph.yearWidth + source.routeIndex * graph.laneWidth + 8 + slot;
    sourcePort = { x: source.x + graph.cardWidth / 2, y: source.y };
    targetPort = { x: target.x + graph.cardWidth / 2, y: target.y };

    if (sameYear) {
      points = [sourcePort, { x: sourcePort.x, y: sourceChannelY }, { x: targetPort.x, y: sourceChannelY }, targetPort];
    } else {
      points = [
        sourcePort,
        { x: sourcePort.x, y: sourceChannelY },
        { x: sourceLaneEdge, y: sourceChannelY },
        { x: sourceLaneEdge, y: targetChannelY },
        { x: targetPort.x, y: targetChannelY },
        targetPort
      ];
    }
  }

  return {
    d: roundedOrthogonalPath(points),
    sourcePort,
    targetPort,
    labelPoint: points[Math.floor(points.length / 2)]
  };
}
