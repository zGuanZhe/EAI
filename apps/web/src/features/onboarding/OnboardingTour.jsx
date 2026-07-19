import { ArrowLeft, ArrowRight, X } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

export const ONBOARDING_STORAGE_KEY = "eai-onboarding-v1";

const STEPS = [
  {
    target: "[data-onboarding='setup']",
    title: "先确认准备状态",
    body: "模型已配置就可以直接开始；如果尚未配置，请先打开模型设置。"
  },
  {
    target: "[data-onboarding='composer']",
    title: "从一个问题开始",
    body: "直接写下想判断、比较或核验的问题。第一次发送会自动建立研究问题，不必先创建项目。"
  },
  {
    target: "[data-onboarding='mode']",
    title: "选择工作方式",
    body: "日常问题使用“询问”；需要持续检索和形成报告时使用“研究任务”。来源范围始终可以单独控制。"
  },
  {
    target: "[data-onboarding='navigation']",
    title: "研究工作都在左侧",
    body: "主对话负责判断，Atlas 用于查看论文关系，Canvas 组织论证，运行中心保存任务、变更和维护记录。"
  }
];

export function readOnboardingStatus() {
  try { return JSON.parse(localStorage.getItem(ONBOARDING_STORAGE_KEY) || "null"); }
  catch { return null; }
}

export function writeOnboardingStatus(status) {
  try { localStorage.setItem(ONBOARDING_STORAGE_KEY, JSON.stringify({ version: 1, status })); }
  catch { /* The in-memory session still prevents a blocking loop. */ }
}

function measureTarget(selector) {
  const target = document.querySelector(selector);
  if (!target) return null;
  const rect = target.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  return { target, rect };
}

function calloutPosition(rect) {
  const margin = 14;
  const gap = 16;
  const width = Math.min(330, window.innerWidth - margin * 2);
  if (window.innerWidth <= 760) {
    return { left: margin, bottom: margin, width, placement: "mobile" };
  }
  const estimatedHeight = 190;
  const rightRoom = window.innerWidth - rect.right;
  const leftRoom = rect.left;
  if (rightRoom >= width + gap) {
    return { left: rect.right + gap, top: Math.min(Math.max(margin, rect.top), window.innerHeight - estimatedHeight - margin), width, placement: "right" };
  }
  if (leftRoom >= width + gap) {
    return { left: rect.left - width - gap, top: Math.min(Math.max(margin, rect.top), window.innerHeight - estimatedHeight - margin), width, placement: "left" };
  }
  const below = window.innerHeight - rect.bottom;
  if (below >= estimatedHeight + gap) {
    return { left: Math.min(Math.max(margin, rect.left), window.innerWidth - width - margin), top: rect.bottom + gap, width, placement: "bottom" };
  }
  return { left: Math.min(Math.max(margin, rect.left), window.innerWidth - width - margin), bottom: window.innerHeight - rect.top + gap, width, placement: "top" };
}

export function OnboardingTour({ active, step, workspaceReady, manual = false, onStep, onSkip, onComplete }) {
  const [layout, setLayout] = useState(null);
  const headingRef = useRef(null);
  const previousFocusRef = useRef(null);
  const onSkipRef = useRef(onSkip);
  const current = STEPS[step] || STEPS[0];
  onSkipRef.current = onSkip;

  useLayoutEffect(() => {
    if (!active) return undefined;
    let frame = 0;
    const update = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const measured = measureTarget(current.target) || measureTarget("[data-onboarding='composer']");
        if (!measured) return setLayout(null);
        const { rect } = measured;
        setLayout({
          highlight: {
            left: Math.max(4, rect.left - 5),
            top: Math.max(4, rect.top - 5),
            width: Math.min(window.innerWidth - 8, rect.width + 10),
            height: Math.min(window.innerHeight - 8, rect.height + 10)
          },
          callout: calloutPosition(rect)
        });
      });
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    const observer = new MutationObserver(update);
    observer.observe(document.body, { childList: true, subtree: true });
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
      observer.disconnect();
    };
  }, [active, current.target, step]);

  useEffect(() => {
    if (!active) return undefined;
    previousFocusRef.current = document.activeElement;
    if (manual) requestAnimationFrame(() => headingRef.current?.focus());
    const onKeyDown = (event) => {
      if (event.key === "Escape") onSkipRef.current?.();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previousFocusRef.current?.focus?.();
    };
  }, [active, manual]);

  if (!active || !layout) return null;
  const waitingForFirstQuestion = step === 1 && !workspaceReady;
  return createPortal(
    <div className="onboarding-layer" aria-live="polite">
      <div className="onboarding-highlight" style={layout.highlight} aria-hidden="true" />
      <section
        className={`onboarding-callout placement-${layout.callout.placement}`}
        style={layout.callout}
        role="dialog"
        aria-modal="false"
        aria-labelledby="onboarding-title"
      >
        <header>
          <span>{step + 1} / {STEPS.length}</span>
          <button type="button" onClick={onSkip} aria-label="跳过使用指南"><X size={15} /></button>
        </header>
        <h2 id="onboarding-title" ref={headingRef} tabIndex={-1}>{current.title}</h2>
        <p>{current.body}</p>
        {waitingForFirstQuestion && <small>发送第一个问题后，指南会自动继续。</small>}
        <footer>
          <button type="button" className="onboarding-skip" onClick={onSkip}>跳过</button>
          <div>
            {step > 0 && <button type="button" onClick={() => onStep(step - 1)} aria-label="上一步"><ArrowLeft size={15} /></button>}
            {step < STEPS.length - 1 && !waitingForFirstQuestion && <button type="button" className="primary" onClick={() => onStep(step + 1)}>下一步<ArrowRight size={15} /></button>}
            {step === STEPS.length - 1 && <button type="button" className="primary" onClick={onComplete}>完成</button>}
          </div>
        </footer>
      </section>
    </div>,
    document.body
  );
}
