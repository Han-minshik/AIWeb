import React, { useEffect, useRef, useState } from 'react';
import ChevronDown from '../../assets/icons/chevron-down.svg';
import ChevronUp from '../../assets/icons/chevron-up.svg';

export default function KBChipSelect({
  kbChipLabel,
  onChangeKBSelect,
  vaultKbId,
  activeDocDocId,
  bookmarkDocCount,
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const rootRef = useRef(null);

  const toggleOpen = (e) => {
    e.stopPropagation();
    setMenuOpen((v) => !v);
  };

  const handleOptionClick = (value) => {
    onChangeKBSelect({ target: { value } });
    setMenuOpen(false);
  };

  useEffect(() => {
    if (!menuOpen) return;

    const onDocMouseDown = (e) => {
      const el = rootRef.current;
      if (!el) return;
      if (!el.contains(e.target)) setMenuOpen(false);
    };

    const onDocKeyDown = (e) => {
      if (e.key === 'Escape') setMenuOpen(false);
    };

    document.addEventListener('mousedown', onDocMouseDown);
    document.addEventListener('keydown', onDocKeyDown);
    return () => {
      document.removeEventListener('mousedown', onDocMouseDown);
      document.removeEventListener('keydown', onDocKeyDown);
    };
  }, [menuOpen]);

  return (
    <div ref={rootRef} className="kb-chip custom-select" onClick={toggleOpen}>
      <span className="label">{kbChipLabel}</span>
      <img
        src={menuOpen ? ChevronUp : ChevronDown}
        alt=""
        style={{ marginLeft: 6, width: 18, height: 18 }}
      />
      {menuOpen && (
        <ul className="custom-options">
          <li
            onClick={(e) => {
              e.stopPropagation();
              handleOptionClick('use-vault');
            }}
            className={!vaultKbId ? 'disabled' : ''}
          >
            KB QA
          </li>
          <li
            onClick={(e) => {
              e.stopPropagation();
              handleOptionClick('use-active-doc');
            }}
            className={!activeDocDocId ? 'disabled' : ''}
          >
            Selected QA
          </li>
          <li
            onClick={(e) => {
              e.stopPropagation();
              handleOptionClick('use-bookmark');
            }}
            className={!bookmarkDocCount ? 'disabled' : ''}
          >
            Bookmark QA
          </li>
        </ul>
      )}
    </div>
  );
}
