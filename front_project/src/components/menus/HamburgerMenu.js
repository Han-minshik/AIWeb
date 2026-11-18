import React, { useEffect, useRef } from "react";
import { createPortal } from "react-dom";

const AI_STUDIO_URL = import.meta.env.VITE_AI_STUDIO_URL || "http://localhost:9090";
const RTS_URL = import.meta.env.VITE_RTS_URL || "http://localhost:35176";

export default function HamburgerMenu({ open, onClose }) {
  const menuRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onEsc = (e) => e.key === "Escape" && onClose?.();
    const onDoc = (e) => {
      const el = menuRef.current;
      if (!el) return;
      if (!el.contains(e.target)) onClose?.();
    };
    document.addEventListener("keydown", onEsc, true);
    document.addEventListener("mousedown", onDoc, true);
    return () => {
      document.removeEventListener("keydown", onEsc, true);
      document.removeEventListener("mousedown", onDoc, true);
    };
  }, [open, onClose]);

  if (!open) return null;

  const items = ["AI Studio", "RTS"];

  const handle = (label) => {
    if (label === "AI Studio") {
      window.open(
        AI_STUDIO_URL,
        "_blank", 
        "noopener, noreferrer"
      );
    } else if (label === "RTS") {
      window.open(
        RTS_URL,
        "_blank",
        "noopener, noreferrer"
      );
    }
    onClose?.();
  };

  return createPortal(
    <div
      className="kb-menu"
      ref={menuRef}
      role="dialog"
      aria-label="KB Menu"
      style={{ top: 48, left: 12 }}
    >
      <ul className="kb-menu_list">
        {items.map((label) => (
          <li
            key={label}
            className="kb-menu_item"
            onClick={() => handle(label)}
          >
            {label}
          </li>
        ))}
      </ul>
    </div>,
    document.body
  );
}
