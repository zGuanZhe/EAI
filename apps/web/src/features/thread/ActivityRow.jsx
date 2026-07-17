import { ChevronDown, CircleCheck } from "lucide-react";

export function ActivityRow({ messages = [] }) {
  if (!messages.length) return null;
  const last = messages[messages.length - 1];
  const label = messages.length === 1 ? last.content : `已记录 ${messages.length} 项系统活动`;
  return (
    <details className="thread-activity-row">
      <summary>
        <CircleCheck size={13} />
        <span>{label}</span>
        <ChevronDown size={13} />
      </summary>
      <div>
        {messages.map((message) => (
          <p key={message.id || `${message.kind}-${message.created_at}`}>{message.content}</p>
        ))}
      </div>
    </details>
  );
}
