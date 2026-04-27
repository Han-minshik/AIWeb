import React, { useEffect, useMemo, useRef } from "react";
import { createPortal } from "react-dom";

export default function UserProfileMenu({
  open,
  onClose,
  onAction,
  anchorRect,
  isAdmin = false,
}) {
  const ref = useRef(null);

  const computePos = (rect) => {
    const gap = 8;
    const vw = typeof window !== "undefined" ? window.innerWidth : 0;
    const vh = typeof window !== "undefined" ? window.innerHeight : 0;
    const approxW = 200;
    const approxH = 140;

    let top = rect ? Math.round(rect.top) : 48;
    let left = rect ? Math.round(rect.right + gap) : 60;

    if (left + approxW > vw - 8) left = Math.max(8, (rect?.left ?? 0) - approxW - gap);
    if (top + approxH > vh - 8) top = Math.max(8, vh - approxH - 8);
    return { top, left };
  };

  const pos = useMemo(() => computePos(anchorRect), [anchorRect]);

  useEffect(() => {
    if (!open) return;

    const onEsc = (e) => e.key === "Escape" && onClose?.();
    const onDocClick = (e) => {
      if (ref.current && !ref.current.contains(e.target)) onClose?.();
    };
    const onWin = () => {
      const el = ref.current;
      if (!el) return;
      const p = computePos(anchorRect);
      el.style.top = `${p.top}px`;
      el.style.left = `${p.left}px`;
    };

    document.addEventListener("keydown", onEsc, true);
    document.addEventListener("mousedown", onDocClick, true);
    window.addEventListener("resize", onWin);
    window.addEventListener("scroll", onWin, true);

    return () => {
      document.removeEventListener("keydown", onEsc, true);
      document.removeEventListener("mousedown", onDocClick, true);
      window.removeEventListener("resize", onWin);
      window.removeEventListener("scroll", onWin, true);
    };
  }, [open, onClose, anchorRect]);

  if (!open) return null;

  const items = [
    { key: "settings", label: "Setting" },
    ...(isAdmin ? [{ key: "admin", label: "Go to Admin" }] : []),
    { key: "logout", label: "Logout" },
  ];

  return createPortal(
    <div
      className="dropdown"
      ref={ref}
      role="menu"
      aria-label="User Menu"
      style={{
        position: "fixed",
        top: pos.top,
        left: pos.left,
        zIndex: 2147483647,
        minWidth: 180,
      }}
    >
      {items.map((it) => (
        <button
          key={it.key}
          className="dropdown_item"
          role="menuitem"
          onClick={() => {
            onAction?.(it.key);
            onClose?.();
          }}
        >
          {it.label}
        </button>
      ))}
    </div>,
    document.body
  );
}
