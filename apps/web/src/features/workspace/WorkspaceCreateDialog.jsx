import { FolderPlus, MessageSquarePlus, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { Button, IconButton } from "../../components/ui/index.jsx";

const FOCUSABLE = "button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex='-1'])";

export function WorkspaceCreateDialog({
  kind,
  atlases,
  defaultAtlasId = "G",
  projectTitle = "",
  required = false,
  onCancel,
  onCreate
}) {
  const dialogRef = useRef(null);
  const titleInputRef = useRef(null);
  const previousFocusRef = useRef(null);
  const onCancelRef = useRef(onCancel);
  const submittingRef = useRef(false);
  const titleId = useId();
  const descriptionId = useId();
  const [title, setTitle] = useState("");
  const [goal, setGoal] = useState("");
  const [atlasId, setAtlasId] = useState(defaultAtlasId || "G");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const isProject = kind === "project";

  useEffect(() => {
    onCancelRef.current = onCancel;
  }, [onCancel]);

  useEffect(() => {
    previousFocusRef.current = document.activeElement;
    const appRoot = document.getElementById("root");
    if (appRoot) appRoot.inert = true;

    const handleKeyDown = (event) => {
      if (event.key === "Escape" && !required && !submittingRef.current) onCancelRef.current?.();
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = [...dialogRef.current.querySelectorAll(FOCUSABLE)];
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    const frame = requestAnimationFrame(() => titleInputRef.current?.focus());
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("keydown", handleKeyDown);
      if (appRoot) appRoot.inert = false;
      previousFocusRef.current?.focus?.();
    };
  }, [required]);

  async function submit(event) {
    event.preventDefault();
    const cleanTitle = title.trim();
    const cleanGoal = goal.trim();
    if (!cleanTitle || !cleanGoal) {
      setError(isProject ? "请填写成果名称和交付目标。" : "请填写问题标题和研究问题。");
      return;
    }
    submittingRef.current = true;
    setSubmitting(true);
    setError("");
    try {
      await onCreate({ title: cleanTitle, goal: cleanGoal, atlasId });
    } catch (createError) {
      setError(createError?.message || "创建失败，请稍后重试。");
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  const Icon = isProject ? FolderPlus : MessageSquarePlus;
  const heading = isProject ? "新建成果目标" : "新建研究问题";
  const description = isProject
    ? "先定义要完成的交付物"
    : projectTitle ? `归属：${projectTitle}` : "未归档成果";

  return createPortal(
    <div
      className="workspace-create-layer"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !required && !submitting) onCancel?.();
      }}
    >
      <section
        ref={dialogRef}
        className="workspace-create-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
      >
        <header>
          <span className="workspace-create-icon"><Icon size={18} /></span>
          <div>
            <h2 id={titleId}>{heading}</h2>
            <p id={descriptionId}>{description}</p>
          </div>
          {!required && (
            <IconButton label="关闭创建窗口" onClick={onCancel} disabled={submitting}>
              <X size={17} />
            </IconButton>
          )}
        </header>

        <form onSubmit={submit}>
          <label>
            <span>{isProject ? "成果名称" : "问题标题"}</span>
            <input
              ref={titleInputRef}
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder={isProject ? "例如：多模态检索综述章节" : "例如：视觉检索中的证据瓶颈"}
              maxLength={160}
              autoComplete="off"
              disabled={submitting}
            />
          </label>
          <label>
            <span>{isProject ? "交付目标" : "研究问题"}</span>
            <textarea
              value={goal}
              onChange={(event) => setGoal(event.target.value)}
              placeholder={isProject ? "说明完成标准、研究范围和预期形式" : "写下需要收集证据并形成判断的具体问题"}
              rows={4}
              maxLength={1200}
              disabled={submitting}
            />
          </label>
          <label>
            <span>{isProject ? "默认 Atlas" : "起始 Atlas"}</span>
            <select value={atlasId} onChange={(event) => setAtlasId(event.target.value)} disabled={submitting}>
              {atlases.map((atlas) => (
                <option key={atlas.id} value={atlas.id}>
                  {atlas.id} · {atlas.title_cn || atlas.title}
                </option>
              ))}
            </select>
          </label>
          {error && <div className="workspace-create-error" role="alert">{error}</div>}
          <footer>
            {!required && <Button onClick={onCancel} disabled={submitting}>取消</Button>}
            <Button variant="primary" type="submit" disabled={submitting}>
              {submitting ? "正在创建..." : isProject ? "创建成果目标" : "创建研究问题"}
            </Button>
          </footer>
        </form>
      </section>
    </div>,
    document.body
  );
}
