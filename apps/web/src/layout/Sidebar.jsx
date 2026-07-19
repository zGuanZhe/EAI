import {
  Blocks,
  Brain,
  CheckCircle2,
  ChevronDown,
  CircleHelp,
  CircleDot,
  Clock3,
  Folder,
  GitBranch,
  KeyRound,
  MessageSquare,
  PanelRight,
  Plus,
  Trash2
} from "lucide-react";
import { useState } from "react";

function cx(...parts) {
  return parts.filter(Boolean).join(" ");
}

function displayThreadTitle(title) {
  return title === "New research thread" ? "未命名研究线程" : title;
}

export function Sidebar({
  collapsed,
  onToggleSidebar,
  threads,
  activeThread,
  projects,
  activeProject,
  atlases,
  onOpenThread,
  onCreateThread,
  onCreateProject,
  onSwitchProject,
  onDeleteProject,
  onDeleteThread,
  onSetAtlas,
  onHome,
  onTools,
  onSettings,
  onHelp,
  onSwitchSurface,
  activeAtlas,
  activeSurface,
  isRunning = false,
  readOnly = false
}) {
  const [atlasPickerOpen, setAtlasPickerOpen] = useState(false);
  const [projectPickerOpen, setProjectPickerOpen] = useState(false);
  const [threadsOpen, setThreadsOpen] = useState(true);
  const currentAtlas = atlases.find((atlas) => atlas.id === activeAtlas);
  const visibleThreads = threads.filter((item) => (item.project_id || "unfiled") === activeProject?.id);
  const contextCount = (activeThread.context_cards || []).filter((card) => card.include_in_agent !== false).length;
  const pendingChanges = (activeThread.changesets || []).filter((item) => ["pending", "conflicted"].includes(item.status)).length;
  const canvasCount = activeThread.canvas?.nodes?.length || 0;
  const surfaceItems = [
    {
      id: "thread",
      label: "主对话",
      description: isRunning ? "Main Agent 正在工作" : pendingChanges ? `${pendingChanges} 项待确认变更` : "研究判断与操作记录",
      icon: MessageSquare,
      tone: "blue",
      status: isRunning ? "running" : pendingChanges ? "warning" : "idle"
    },
    {
      id: "atlas",
      label: `Atlas ${activeAtlas}`,
      description: currentAtlas?.title_cn || currentAtlas?.title || "证据路线图",
      icon: GitBranch,
      tone: "cyan",
      status: contextCount ? "ready" : "idle"
    },
    {
      id: "canvas",
      label: "Canvas",
      description: canvasCount ? `${canvasCount} 个论证节点` : "组织问题、证据与结论",
      icon: Blocks,
      tone: "violet",
      status: canvasCount ? "ready" : "idle"
    }
  ];

  return (
    <aside className={cx("sidebar", collapsed && "collapsed")} data-onboarding="navigation">
      <div className="brand-row">
        <button
          className="sidebar-brand-glyph"
          type="button"
          onClick={collapsed ? onToggleSidebar : onHome}
          title={collapsed ? "展开左栏" : "返回首页"}
        >
          <Brain size={18} />
        </button>
        <strong>EAI-Desktop</strong>
        <button className="sidebar-icon-button end" type="button" onClick={onToggleSidebar} title="收起左栏">
          <PanelRight size={18} />
        </button>
      </div>

      <button className="sidebar-primary-action" type="button" onClick={onCreateThread} disabled={readOnly}>
        <Plus size={17} /> <span>新建研究问题</span>
      </button>

      <section className="sidebar-project">
        <span className="side-title">当前项目</span>
        <button
          className="project-block active project-current"
          type="button"
          aria-expanded={projectPickerOpen}
          onClick={() => setProjectPickerOpen(!projectPickerOpen)}
        >
          <Folder size={17} />
          <div>
            <strong>{activeProject?.title || "未归档成果"}</strong>
            <span>{activeProject?.goal || "个人研究工作区"}</span>
          </div>
          <ChevronDown size={14} />
        </button>
        {projectPickerOpen && (
          <div className="project-picker">
            {projects.map((project) => (
              <article className={cx("project-picker-row", activeProject?.id === project.id && "active")} key={project.id}>
                <button
                  className="project-picker-main"
                  type="button"
                  onClick={() => {
                    onSwitchProject(project.id);
                    setProjectPickerOpen(false);
                  }}
                >
                  <strong>{project.title}</strong>
                  <span>{project.goal || `默认 Atlas ${project.default_atlas_id || "G"}`}</span>
                </button>
                {project.id !== "unfiled" && (
                  <button
                    className="project-delete"
                    type="button"
                    title="删除成果目标"
                    disabled={readOnly}
                    onClick={() => {
                      onDeleteProject(project.id);
                      setProjectPickerOpen(false);
                    }}
                  >
                    <Trash2 size={13} />
                  </button>
                )}
              </article>
            ))}
            <button
              className="project-new"
              type="button"
              onClick={() => {
                setProjectPickerOpen(false);
                onCreateProject();
              }}
              disabled={readOnly}
            >
              <Plus size={14} /> 新建成果目标
            </button>
          </div>
        )}
      </section>

      <section className="thread-workspace-nav" aria-label="当前线程工作区">
        <header>
          <span>当前线程</span>
          <strong>{displayThreadTitle(activeThread.title)}</strong>
        </header>
        <div className="surface-nav-list">
          {surfaceItems.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                type="button"
                className={cx("surface-nav-item", item.tone, activeSurface === item.id && "active")}
                onClick={() => onSwitchSurface(item.id)}
              >
                <span className="surface-nav-icon"><Icon size={16} /></span>
                <span>
                  <strong>{item.label}</strong>
                  <small>{item.description}</small>
                </span>
                {item.status === "running" ? <CircleDot className="surface-status running" size={12} /> : item.status === "ready" ? <CheckCircle2 className="surface-status ready" size={12} /> : item.status === "warning" ? <CircleDot className="surface-status warning" size={12} /> : null}
              </button>
            );
          })}
        </div>

        {activeSurface === "atlas" && (
          <div className="atlas-inline-picker">
            <button type="button" aria-expanded={atlasPickerOpen} onClick={() => setAtlasPickerOpen(!atlasPickerOpen)}>
              <span>{activeAtlas}</span>
              <em>切换研究图谱</em>
              <ChevronDown size={13} />
            </button>
            {atlasPickerOpen && (
              <div className="atlas-picker">
                {atlases.map((atlas) => (
                  <button
                    key={atlas.id}
                    type="button"
                    className={cx(activeAtlas === atlas.id && "active")}
                    disabled={readOnly}
                    onClick={() => {
                      onSetAtlas(atlas.id);
                      setAtlasPickerOpen(false);
                    }}
                  >
                    <span>{atlas.id}</span>
                    <em>{atlas.title_cn || atlas.title}</em>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </section>

      <section className={cx("side-section thread-history", !threadsOpen && "collapsed")}>
        <button className="side-title section-toggle" type="button" onClick={() => setThreadsOpen(!threadsOpen)}>
          <span>其他研究线程</span>
          <ChevronDown size={14} />
        </button>
        {threadsOpen && (
          <div className="thread-list">
            {visibleThreads.filter((item) => item.id !== activeThread.id).map((item) => (
              <article key={item.id} className="thread-item">
                <button className="thread-main" type="button" onClick={() => onOpenThread(item.id)}>
                  <span>{displayThreadTitle(item.title)}</span>
                  <small>{item.context_count} 材料 · Atlas {item.active_atlas_id}</small>
                </button>
                <button className="thread-delete" type="button" title="删除研究问题" onClick={() => onDeleteThread(item.id)} disabled={readOnly}>
                  <Trash2 size={13} />
                </button>
              </article>
            ))}
            {visibleThreads.length <= 1 && <div className="thread-empty">当前项目暂无其他线程</div>}
          </div>
        )}
      </section>

      <div className="sidebar-footer">
        <button className={cx(activeSurface === "tools" && "active")} type="button" onClick={onTools}><Clock3 size={18} /> <span>运行中心</span></button>
        <button type="button" onClick={onHelp}><CircleHelp size={18} /> <span>使用指南</span></button>
        <button type="button" onClick={onSettings} data-onboarding="setup"><KeyRound size={18} /> <span>设置</span></button>
      </div>
    </aside>
  );
}
