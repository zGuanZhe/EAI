export function buildAtlasOverview(controls) {
  if (!controls) {
    return {
      focus: "正在读取当前 Atlas。",
      trend: "加载完成后会显示研究方向和路线结构。",
      activeRoutes: [],
      latestYears: []
    };
  }

  const routes = controls.routes || [];
  const routeStats = controls.routeStats || [];
  const yearStats = controls.yearStats || [];
  const activeRoutes = routeStats
    .filter((item) => item.count > 0)
    .sort((a, b) => b.count - a.count)
    .slice(0, 5);
  const latestYears = yearStats
    .filter((item) => item.count > 0)
    .sort((a, b) => Number(b.year) - Number(a.year))
    .slice(0, 3);

  const leading = activeRoutes[0]?.label || routes[0]?.label || "当前路线";
  const recent = latestYears[0]?.year ? `${latestYears[0].year}` : "最近年份";
  const focus = controls.description || `${controls.title} 用路线和关系呈现该方向的研究结构。`;
  const trend = `${recent} 的活跃论文主要集中在「${leading}」等路线，适合先用关系线确认方法演化和空白区域。`;

  return { focus, trend, activeRoutes, latestYears };
}
