import { Check } from "lucide-react";
import { FLOW_STEPS } from "../../state/researchFlow.js";

function cx(...parts) {
  return parts.filter(Boolean).join(" ");
}

export function ResearchFlowStrip({ flow, onStep }) {
  return (
    <nav className="research-flow-strip" aria-label="研究主流程">
      {FLOW_STEPS.map((step, index) => {
        const done = Boolean(flow?.completed?.[step.id]);
        const active = flow?.active === step.id;
        return (
          <button
            type="button"
            key={step.id}
            className={cx(done && "done", active && "active")}
            onClick={() => onStep?.(step)}
          >
            <span>{done ? <Check size={12} /> : index + 1}</span>
            {step.label}
          </button>
        );
      })}
    </nav>
  );
}
