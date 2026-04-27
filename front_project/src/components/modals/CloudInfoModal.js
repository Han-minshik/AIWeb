import React, { useEffect } from "react";
import { createPortal } from "react-dom";

export default function CloudInfoModal({ open, onClose }) {
  useEffect(() => {
    if (!open) return;
    const onEsc = (e) => e.key === "Escape" && onClose?.();
    document.addEventListener("keydown", onEsc);
    return () => document.removeEventListener("keydown", onEsc);
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div className="modal_backdrop" onClick={onClose}>
      <div
        className="modal modal-wide"
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal_header">
          <h3>Obsidian</h3>
          <button className="modal_close" onClick={onClose} aria-label="close">
            ×
          </button>
        </div>

        <div className="modal_content">
          <div className="obs_section">
            <div>
              <div className="obs_title">새 보관함 생성</div>
              <div className="obs_desc">폴더 안에 새 Obsidian 보관함을 만듭니다.</div>
            </div>
            <button className="btn">생성</button>
          </div>

          <div className="obs_section">
            <div>
              <div className="obs_title">보관함 폴더 열기</div>
              <div className="obs_desc">마크다운 파일들의 폴더를 선택합니다.</div>
            </div>
            <button className="btn btn-secondary">열기</button>
          </div>

          <div className="obs_section">
            <div>
              <div className="obs_title">Obsidian Sync 보관함 열기</div>
              <div className="obs_desc">원격 보관함과 동기화를 설정합니다.</div>
            </div>
            <button className="btn btn-secondary">설정</button>
          </div>

          <div className="obs_lang">
            <label htmlFor="lang">언어</label>
            <select id="lang" className="select">
              <option>한국어</option>
              <option>English</option>
              <option>日本語</option>
              <option>中文</option>
            </select>
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
}
