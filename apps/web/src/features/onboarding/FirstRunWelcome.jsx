import { ArrowRight, CircleHelp, FolderPlus, KeyRound, MessageSquarePlus } from "lucide-react";
import { Button, StatusDot } from "../../components/ui/index.jsx";

const STARTER_PROMPTS = [
  "帮我梳理这个研究方向最关键的三类证据",
  "比较两种方法时，应该优先核验哪些实验细节？",
  "我有一个研究想法，请帮我找出假设和证据缺口"
];

export function FirstRunWelcome({ configured, projectTitle, status, composer, onPrompt, onSettings, onHelp, onCreateThread, onCreateProject }) {
  return (
    <div className="first-run-shell">
      <header className="first-run-header">
        <strong>EAI Desktop</strong>
        <div>
          <Button compact variant="quiet" onClick={onHelp}><CircleHelp size={15} />使用指南</Button>
          <Button compact onClick={onSettings}><KeyRound size={15} />模型设置</Button>
        </div>
      </header>
      <main className="first-run-main">
        <section className="first-run-intro" data-onboarding="setup">
          <span className="first-run-kicker">{projectTitle ? `将保存到 ${projectTitle}` : "个人研究工作区"}</span>
          <h1>从一个问题开始</h1>
          <p>不需要先建立项目。写下你想判断、比较或核验的事情，EAI 会自动保存为一个研究问题。</p>
          <div className="first-run-model-state">
            <StatusDot status={configured ? "ready" : "warning"} label={configured ? "模型通道已配置" : "发送前需要配置模型通道"} />
            {!configured && <button type="button" onClick={onSettings}>打开设置 <ArrowRight size={13} /></button>}
          </div>
          <div className="first-run-prompts">
            {STARTER_PROMPTS.map((prompt) => <button key={prompt} type="button" onClick={() => onPrompt(prompt)}><span>{prompt}</span><ArrowRight size={14} /></button>)}
          </div>
        </section>
        <div className="first-run-composer">{composer}</div>
        <div className="first-run-organize">
          <span>也可以先组织工作区</span>
          <Button compact variant="quiet" onClick={onCreateThread}><MessageSquarePlus size={15} />完整创建研究问题</Button>
          <Button compact variant="quiet" onClick={onCreateProject}><FolderPlus size={15} />新建成果目标</Button>
        </div>
        {status && <span className="empty-workspace-status" role="status">{status}</span>}
      </main>
    </div>
  );
}
