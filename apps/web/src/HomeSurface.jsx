import { ArrowRight, Clock3, Folder, ShieldCheck } from "lucide-react";
import { ActivityRow } from "./components/ui/index.jsx";

const HOME_PROMPTS = [
  "我想找这个方向的关键证据",
  "我想整理一个综述论点",
  "我想把当前材料交给 Codex 分析"
];

function displayThreadTitle(title = "") {
  return title === "New research thread" ? "未命名研究线程" : title;
}

function tailPath(value = "") {
  const parts = String(value).split(/[\\/]/).filter(Boolean);
  return parts.slice(-3).join("/");
}

export function HomeSurface({ thread, threads = [], project, bundle, cards, onPrompt, onOpenThread, systemInfo, composer }) {
  const projectTitle = project?.id === "unfiled" ? "" : project?.title;
  const headline = projectTitle
    ? `推进「${projectTitle}」中的下一个问题`
    : "从一个研究问题开始";
  const activeAtlas = thread?.active_atlas_id || bundle?.atlas?.id || "G";

  return (
    <section className="home-surface">
      <div className="home-scroll">
        <div className="home-content">
          <header className="home-start">
            <span className="home-context">{project?.title || "未归档成果"} · Atlas {activeAtlas}</span>
            <h1>{headline}</h1>
            <p>直接描述你要判断、比较或验证的问题。Main Agent 会先读取当前线程，再按需检索 Atlas。</p>
            <div className="home-prompt-list">
              {HOME_PROMPTS.map((prompt) => (
                <button key={prompt} type="button" onClick={() => onPrompt?.(prompt)}>
                  <span>{prompt}</span><ArrowRight size={14} />
                </button>
              ))}
            </div>
          </header>

          <div className="home-resume">
            <header><span><Clock3 size={14} />继续研究</span><em>{threads.length} 个线程</em></header>
            <ActivityRow
              icon={<Folder size={15} />}
              title={displayThreadTitle(thread?.title)}
              meta={`${bundle?.papers?.length || 0} 篇论文 · ${cards.length} 张长期资料`}
              description={thread?.goal || "继续当前研究问题"}
              actions={<ArrowRight size={15} />}
              onClick={() => onOpenThread?.(thread?.id)}
            />
            {threads.filter((item) => item.id !== thread?.id).slice(0, 3).map((item) => (
              <ActivityRow key={item.id} tone="cyan" icon={<Clock3 size={15} />} title={displayThreadTitle(item.title)} meta={`${item.context_count || 0} 张资料 · Atlas ${item.active_atlas_id || activeAtlas}`} onClick={() => onOpenThread?.(item.id)} />
            ))}
            {!!(thread?.changesets || []).filter((item) => ["pending", "conflicted"].includes(item.status)).length && <ActivityRow tone="orange" icon={<ShieldCheck size={15} />} title="有待确认变更" meta={`${thread.changesets.filter((item) => ["pending", "conflicted"].includes(item.status)).length} 项`} description="进入主对话逐字段检查后再写入。" />}
            <div className="home-runtime" title={`${systemInfo?.app_root || ""}\n${systemInfo?.personal_dir || ""}\n${systemInfo?.atlas_cache_dir || ""}`}>
              <span>{systemInfo?.active_backend_id || "未识别后端"}</span><span>个人数据 {tailPath(systemInfo?.personal_dir) || "未连接"}</span>
            </div>
          </div>
        </div>
      </div>
      <footer className="home-composer-footer">{composer}</footer>
    </section>
  );
}
