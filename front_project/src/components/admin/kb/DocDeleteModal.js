import React from 'react';
import '../../../styles/admin/docDeleteModal.css';

export default function DocDeleteModal({ open, count, onClose, onConfirm }) {
  if (!open) return null;
  return (
    <div className="ddm-backdrop" onClick={onClose}>
      <div className="ddm-modal" onClick={(e) => e.stopPropagation()}>
        <div className="ddm-head">
          <div className="ddm-title">문서 삭제</div>
          <button type="button" className="ddm-close" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="ddm-body">
          <div className="ddm-desc">
            선택된 문서 {count}개를 삭제합니다.
          </div>
          <div className="ddm-actions">
            <button
              type="button"
              className="ddm-btn"
              onClick={() => onConfirm('latest')}
            >
              최신 버전만 삭제
            </button>
            <button
              type="button"
              className="ddm-btn danger"
              onClick={() => onConfirm('all')}
            >
              문서 전체 삭제
            </button>
          </div>
          <div className="ddm-note">
            최신 버전만 삭제 시 이전 버전이 최신으로 교체됩니다.
          </div>
        </div>
      </div>
    </div>
  );
}
