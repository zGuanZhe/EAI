import { Check, ChevronDown, X } from "lucide-react";
import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
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

export function Select({
  value,
  options = [],
  onChange,
  ariaLabel,
  labelledBy,
  disabled = false,
  compact = false,
  tone = "default",
  className = "",
  placeholder = "请选择"
}) {
  const reactId = useId();
  const listboxId = `select-${reactId.replace(/:/g, "")}`;
  const triggerRef = useRef(null);
  const menuRef = useRef(null);
  const typeaheadRef = useRef({ value: "", timer: 0 });
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [position, setPosition] = useState(null);
  const normalizedOptions = useMemo(() => options.map((option) => ({
    ...option,
    value: String(option.value)
  })), [options]);
  const selectedIndex = normalizedOptions.findIndex((option) => option.value === String(value));
  const selected = normalizedOptions[selectedIndex];

  function enabledIndex(start, direction) {
    if (!normalizedOptions.length) return -1;
    let index = start;
    for (let attempt = 0; attempt < normalizedOptions.length; attempt += 1) {
      index = (index + direction + normalizedOptions.length) % normalizedOptions.length;
      if (!normalizedOptions[index].disabled) return index;
    }
    return -1;
  }

  function openMenu(direction = 1) {
    if (disabled) return;
    const fallback = enabledIndex(direction > 0 ? -1 : 0, direction);
    setActiveIndex(selectedIndex >= 0 && !normalizedOptions[selectedIndex]?.disabled ? selectedIndex : fallback);
    setOpen(true);
  }

  function closeMenu({ restoreFocus = false } = {}) {
    setOpen(false);
    if (restoreFocus) requestAnimationFrame(() => triggerRef.current?.focus());
  }

  function choose(index) {
    const option = normalizedOptions[index];
    if (!option || option.disabled) return;
    onChange?.(option.value);
    closeMenu({ restoreFocus: true });
  }

  function updatePosition() {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const margin = 8;
    const gap = 6;
    const desiredHeight = Math.min(288, normalizedOptions.length * 38 + 8);
    const roomBelow = window.innerHeight - rect.bottom - margin;
    const roomAbove = rect.top - margin;
    const above = roomBelow < Math.min(desiredHeight, 180) && roomAbove > roomBelow;
    const maxHeight = Math.max(96, Math.min(288, (above ? roomAbove : roomBelow) - gap));
    const width = Math.min(Math.max(rect.width, 176), window.innerWidth - margin * 2);
    const left = Math.min(Math.max(margin, rect.left), window.innerWidth - width - margin);
    setPosition({
      left,
      width,
      maxHeight,
      ...(above ? { bottom: window.innerHeight - rect.top + gap } : { top: rect.bottom + gap })
    });
  }

  useLayoutEffect(() => {
    if (!open) return undefined;
    updatePosition();
    const handleViewportChange = () => updatePosition();
    window.addEventListener("resize", handleViewportChange);
    window.addEventListener("scroll", handleViewportChange, true);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(handleViewportChange);
    if (triggerRef.current) observer?.observe(triggerRef.current);
    return () => {
      window.removeEventListener("resize", handleViewportChange);
      window.removeEventListener("scroll", handleViewportChange, true);
      observer?.disconnect();
    };
  }, [open, normalizedOptions.length]);

  useEffect(() => {
    if (!open) return undefined;
    const handlePointerDown = (event) => {
      if (triggerRef.current?.contains(event.target) || menuRef.current?.contains(event.target)) return;
      closeMenu();
    };
    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, [open]);

  useEffect(() => () => window.clearTimeout(typeaheadRef.current.timer), []);

  useEffect(() => {
    if (!open || activeIndex < 0) return;
    const activeOption = menuRef.current?.querySelector(`[data-option-index="${activeIndex}"]`);
    activeOption?.scrollIntoView?.({ block: "nearest" });
  }, [activeIndex, open]);

  function handleKeyDown(event) {
    if (disabled) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const direction = event.key === "ArrowDown" ? 1 : -1;
      if (!open) openMenu(direction);
      else setActiveIndex((index) => enabledIndex(index, direction));
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      if (!open) return;
      event.preventDefault();
      setActiveIndex(enabledIndex(event.key === "Home" ? -1 : 0, event.key === "Home" ? 1 : -1));
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (!open) openMenu();
      else choose(activeIndex);
      return;
    }
    if (event.key === "Escape" && open) {
      event.preventDefault();
      event.stopPropagation();
      closeMenu({ restoreFocus: true });
      return;
    }
    if (event.key === "Tab" && open) {
      closeMenu();
      return;
    }
    if (event.key.length !== 1 || event.altKey || event.ctrlKey || event.metaKey) return;
    const nextQuery = `${typeaheadRef.current.value}${event.key}`.toLocaleLowerCase();
    window.clearTimeout(typeaheadRef.current.timer);
    typeaheadRef.current.value = nextQuery;
    typeaheadRef.current.timer = window.setTimeout(() => { typeaheadRef.current.value = ""; }, 600);
    const match = normalizedOptions.findIndex((option) => !option.disabled && String(option.label).toLocaleLowerCase().startsWith(nextQuery));
    if (match >= 0) {
      event.preventDefault();
      if (!open) setOpen(true);
      setActiveIndex(match);
    }
  }

  return (
    <span className={cx("ui-select", compact && "compact", `tone-${tone}`, open && "open", disabled && "disabled", className)}>
      <button
        ref={triggerRef}
        type="button"
        role="combobox"
        aria-label={ariaLabel}
        aria-labelledby={labelledBy}
        aria-controls={listboxId}
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-activedescendant={open && activeIndex >= 0 ? `${listboxId}-option-${activeIndex}` : undefined}
        data-value={String(value)}
        disabled={disabled}
        onClick={() => open ? closeMenu() : openMenu()}
        onKeyDown={handleKeyDown}
      >
        <span>{selected?.label ?? placeholder}</span>
        <ChevronDown size={14} aria-hidden="true" />
      </button>
      {open && createPortal(
        <div
          ref={menuRef}
          id={listboxId}
          className={cx("ui-select-menu", `tone-${tone}`)}
          role="listbox"
          aria-label={ariaLabel}
          style={position || { visibility: "hidden" }}
        >
          {normalizedOptions.map((option, index) => (
            <button
              key={`${option.value}-${index}`}
              id={`${listboxId}-option-${index}`}
              type="button"
              role="option"
              aria-selected={option.value === String(value)}
              data-option-index={index}
              className={cx(index === activeIndex && "active", option.value === String(value) && "selected")}
              disabled={option.disabled}
              onPointerMove={() => !option.disabled && setActiveIndex(index)}
              onClick={() => choose(index)}
            >
              <span>{option.label}</span>
              {option.value === String(value) && <Check size={14} aria-hidden="true" />}
            </button>
          ))}
        </div>,
        document.body
      )}
    </span>
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
