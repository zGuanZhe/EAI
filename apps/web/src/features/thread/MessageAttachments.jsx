import { Paperclip } from "lucide-react";

export function MessageAttachments({ attachments = [], onOpen }) {
  if (!attachments.length) return null;
  return (
    <div className="message-attachments" aria-label="本轮附件">
      {attachments.map((attachment, index) => (
        <button type="button" key={attachment.id || `${attachment.type}-${index}`} onClick={() => onOpen?.(attachment)}>
          <Paperclip size={12} />
          <span>{attachment.title || "未命名资料"}</span>
        </button>
      ))}
    </div>
  );
}
