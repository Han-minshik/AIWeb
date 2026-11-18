import React from 'react';
import '../../../styles/admin/docDeleteModal.css';

export default function DocDeleteModal({
  open,
  documentName,
  busy = false,
  error = '',
  onClose,
  onConfirm,
}) {
  if (!open) return null;

  return (
    <div className="ddm-backdrop" onMouseDown={() => !busy && onClose?.()}>
      <div
        className="ddm-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="document-delete-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="ddm-head">
          <div id="document-delete-title" className="ddm-title">
            Delete document
          </div>
          <button
            type="button"
            className="ddm-close"
            onClick={onClose}
            disabled={busy}
            aria-label="Close"
          >
            &times;
          </button>
        </div>

        <div className="ddm-body">
          <div className="ddm-desc">Move this document to Deleted Docs?</div>
          <div className="ddm-target">{documentName || 'Selected document'}</div>
          <div className="ddm-note">
            The document remains recoverable until permanent purge is confirmed in Maintenance.
          </div>
          {error && (
            <div className="ddm-error" role="alert">
              {error}
            </div>
          )}
          <div className="ddm-actions">
            <button
              type="button"
              className="ddm-btn"
              onClick={onClose}
              disabled={busy}
            >
              Cancel
            </button>
            <button
              type="button"
              className="ddm-btn danger"
              onClick={onConfirm}
              disabled={busy}
            >
              {busy ? 'Deleting...' : 'Delete document'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
