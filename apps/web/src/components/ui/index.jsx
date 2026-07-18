import { X } from "lucide-react";
import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";

export function cx(...parts) {
  return parts.filter(Boolean).join(" ");
}

export function Button({ variant = "secondary", compact = false, className = "", children, ...props }) {
  return <button className={cx("ui-button", `ui-button-${variant}`, compact && "compact", className)} type="button" {...props}>{children}</button>;
}

export function IconButton({ label, className = "", children, ...props }) {
  return <button className={cx("ui-icon-button", className)} type="button" title={label} aria-label={label} {...props}>{children}</button>;
}

export function SurfaceHeader({ tone = "blue", eyebrow, title, description, actions, children }) {
  return (
    <header className={cx("ui-surface-header", `tone-${tone}`)}>
      <div className="ui-surface-heading">
        {eyebrow && <span>{eyebrow}</span>}
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="ui-surface-actions">{actions}</div>}
      {children}
    </header>
  );
}

export function Drawer({ open, title, eyebrow, tone = "blue", onClose, children, className = "", footer, overlay = false }) {
  const drawerRef = useRef(null);
  const previousFocusRef = useRef(null);

  useEffect(() => {
    if (!open || !overlay) return undefined;
    previousFocusRef.current = document.activeElement;
    const appRoot = document.getElementById("root");
    if (appRoot) appRoot.inert = true;
    const handleKeyDown = (event) => {
      if (event.key === "Escape") onClose?.();
      if (event.key === "Tab" && drawerRef.current) {
        const focusable = [...drawerRef.current.querySelectorAll("button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex='-1'])")];
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    requestAnimationFrame(() => drawerRef.current?.querySelector("button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled])")?.focus() || drawerRef.current?.focus());
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      if (appRoot) appRoot.inert = false;
      previousFocusRef.current?.focus?.();
    };
  }, [open, overlay, onClose]);

  if (!open) return null;
  const drawer = (
    <aside
      ref={drawerRef}
      className={cx("ui-drawer", `tone-${tone}`, className)}
      aria-label={title}
      aria-modal={overlay || undefined}
      role={overlay ? "dialog" : undefined}
      tabIndex={overlay ? -1 : undefined}
    >
      <header className="ui-drawer-header">
        <div>{eyebrow && <span>{eyebrow}</span>}<strong>{title}</strong></div>
        <IconButton label={`关闭${title}`} onClick={onClose}><X size={17} /></IconButton>
      </header>
      <div className="ui-drawer-body">{children}</div>
      {footer && <footer className="ui-drawer-footer">{footer}</footer>}
    </aside>
  );
  if (!overlay) return drawer;
  return createPortal(
    <div className="ui-drawer-layer">
      <div className="ui-drawer-backdrop" aria-hidden="true" onMouseDown={onClose} />
      {drawer}
    </div>,
    document.body
  );
}

export function SegmentedControl({ value, options, onChange, label }) {
  return (
    <div className="ui-segmented" role="group" aria-label={label}>
      {options.map((item) => (
        <button key={item.value} type="button" className={cx(value === item.value && "active")} onClick={() => onChange(item.value)}>{item.label}</button>
      ))}
    </div>
  );
}

export function StatusDot({ status = "idle", label }) {
  return <span className={cx("ui-status", `status-${status}`)}><i />{label && <span>{label}</span>}</span>;
}

export function InlineNotice({ tone = "info", title, children, actions }) {
  return <div className={cx("ui-notice", `tone-${tone}`)}><div>{title && <strong>{title}</strong>}{children && <p>{children}</p>}</div>{actions && <div>{actions}</div>}</div>;
}

export function EmptyState({ icon, title, description, action }) {
  return <div className="ui-empty">{icon}<strong>{title}</strong>{description && <p>{description}</p>}{action}</div>;
}

export function FieldRow({ label, hint, children }) {
  return <label className="ui-field-row"><span><strong>{label}</strong>{hint && <small>{hint}</small>}</span><div>{children}</div></label>;
}

export function ActivityRow({ tone = "blue", icon, title, meta, description, actions, onClick }) {
  const Tag = onClick ? "button" : "div";
  return <Tag type={onClick ? "button" : undefined} className={cx("ui-activity-row", `tone-${tone}`)} onClick={onClick}><span className="ui-activity-icon">{icon}</span><div><strong>{title}</strong>{meta && <span>{meta}</span>}{description && <p>{description}</p>}</div>{actions && <aside>{actions}</aside>}</Tag>;
}
