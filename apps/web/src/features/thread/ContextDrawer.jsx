import { Check, Clipboard, Copy, X } from "lucide-react";
import { ContextMaterials } from "./ContextMaterials.jsx";
import { Drawer } from "../../components/ui/index.jsx";

export function ContextDrawer({
  open,
  onClose,
  onOpenAtlas,
  attachments,
  cards,
  onOpenMaterial,
  onRemoveAttachment,
  onPromoteAttachment,
  onRemoveCard,
  onTogglePinned,
  onSetPriority,
  onSetNote,
  onExport,
  exported,
  pasteText,
  setPasteText,
  onPasteResult,
  resultPreview,
  onConfirmResultPreview,
  onCancelResultPreview,
  nodeLabels = {},
  status
}) {
  return (
    <Drawer open={open} onClose={onClose} tone="green" eyebrow="对话资料" title="明确本轮重点，不设置发送门槛" className="context-drawer">
      <div className="context-drawer-scroll">
        <ContextMaterials
          attachments={attachments}
          cards={cards}
          onOpen={onOpenMaterial}
          onRemoveAttachment={onRemoveAttachment}
          onPromoteAttachment={onPromoteAttachment}
          onRemoveCard={onRemoveCard}
          onTogglePinned={onTogglePinned}
          onSetPriority={onSetPriority}
          onSetNote={onSetNote}
          onOpenAtlas={onOpenAtlas}
        />
        <details className="context-advanced-panel">
          <summary>上下文包编辑器 / 高级手动导入</summary>
          <button className="ghost-button wide soft" type="button" onClick={onExport}><Copy size={15} /> 生成上下文包</button>
          {resultPreview ? (
            <section className="result-preview">
              <span>外部返回预览</span>
              <h3>{resultPreview.result_card_preview?.title || "待写入结果"}</h3>
              <p>将写入 {resultPreview.canvas_nodes?.length || 0} 个 Canvas 节点和 {resultPreview.canvas_edges?.length || 0} 条连接。</p>
              <div className="preview-list">
                {(resultPreview.canvas_nodes || []).map((node) => (
                  <div key={node.id}>
                    <strong>{nodeLabels[node.type] || node.type}</strong>
                    <span>{node.title}</span>
                  </div>
                ))}
              </div>
              <div className="preview-actions">
                <button className="primary-button" type="button" onClick={onConfirmResultPreview}><Check size={15} /> 确认写入</button>
                <button className="ghost-button" type="button" onClick={onCancelResultPreview}><X size={15} /> 取消</button>
              </div>
            </section>
          ) : (
            <section className="paste-return-panel">
              <div>
                <span>高级导入</span>
                <strong>手动导入返回后先预览，再写入线程</strong>
              </div>
              <textarea value={pasteText} onChange={(event) => setPasteText(event.target.value)} placeholder="粘贴 eai-result/v1 JSON 或外部返回..." />
              <button className="ghost-button wide soft" type="button" disabled={!pasteText.trim()} onClick={onPasteResult}><Clipboard size={15} /> 解析预览</button>
            </section>
          )}
          {exported && <textarea className="export-box" readOnly value={exported} />}
        </details>
        {status && status !== "就绪" && <div className="status-line">{status}</div>}
      </div>
    </Drawer>
  );
}
