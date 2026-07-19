import { ChevronDown, RefreshCcw, Search, SlidersHorizontal } from "lucide-react";
import { useState } from "react";
import { Select } from "../../components/ui/index.jsx";

export function AtlasToolbar({
  query,
  onQuery,
  routes,
  routeFilter,
  onRouteFilter,
  years,
  yearFilter,
  onYearFilter,
  relationMode,
  onRelationMode,
  onOpenUpdate,
  routeLabel
}) {
  const [filtersOpen, setFiltersOpen] = useState(false);
  return (
    <section className="atlas-workbench-toolbar" aria-label="Atlas 工具栏">
      <label className="atlas-search-field">
        <Search size={14} />
        <input value={query} onChange={(event) => onQuery(event.target.value)} placeholder="搜索论文、路线或判断" />
      </label>
      <div className="atlas-relation-modes" aria-label="关系显示模式">
        {[{ id: "focus", label: "聚焦" }, { id: "all", label: "全部" }, { id: "hidden", label: "隐藏" }].map((mode) => (
          <button key={mode.id} type="button" className={relationMode === mode.id ? "active" : ""} onClick={() => onRelationMode(mode.id)}>{mode.label}</button>
        ))}
      </div>
      <div className="atlas-filter-menu">
        <button className="atlas-filter-toggle" type="button" aria-expanded={filtersOpen} onClick={() => setFiltersOpen((value) => !value)}>
          <SlidersHorizontal size={14} />筛选<ChevronDown size={13} />
        </button>
        <div className={`atlas-filter-fields${filtersOpen ? " open" : ""}`}>
          <Select value={routeFilter} options={[{ value: "all", label: "全部路线" }, ...routes.map((route, index) => ({ value: route.id, label: routeLabel(route, index) }))]} onChange={onRouteFilter} ariaLabel="筛选路线" compact tone="cyan" />
          <Select value={yearFilter} options={[{ value: "all", label: "全部年份" }, ...years.map((year) => ({ value: year, label: String(year) }))]} onChange={onYearFilter} ariaLabel="筛选年份" compact tone="cyan" />
          <button className="atlas-update-entry" type="button" onClick={onOpenUpdate}><RefreshCcw size={14} />论文更新</button>
        </div>
      </div>
    </section>
  );
}
