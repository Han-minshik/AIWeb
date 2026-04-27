import React, { useEffect, useRef } from "react";
import { createPortal } from "react-dom";

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

  const items = ["SE KB", "Requirements", "Ontology"];

  const handle = () => {
    window.alert("This page is not ready yet.");
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
            onClick={() => handle()}
          >
            {label}
          </li>
        ))}
      </ul>
    </div>,
    document.body
  );
}
