import React from 'react';

function issueLabel(issue) {
  if (issue.stage === 'manifest') return 'JSON';
  if (issue.stage === 'matching') return 'File matching';
  if (issue.stage === 'asset-preflight') return 'Image check';
  if (issue.stage === 'upload') return 'Upload';
  return issue.stage || 'Upload';
}

export default function KiwixUploadReportModal({ report, onClose }) {
  if (!report?.open) return null;
  const issues = report.issues || [];
  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal kiwix-upload-report" role="dialog" aria-modal="true" aria-label="KIWIX import result">
        <div className="modal-head">
          <div>
            <div className="modal-title">KIWIX import result</div>
            <div className="kiwix-report-summary">
              Success {report.successCount || 0} · Failed {issues.length}
            </div>
          </div>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close">X</button>
        </div>
        <div className="modal-body">
          {issues.length === 0 ? (
            <div className="modal-target-card">
              <div className="modal-target-title">All selected documents were registered.</div>
            </div>
          ) : (
            <>
              <div className="modal-help warning kiwix-report-help">
                Fix the source JSON, Markdown image reference, or missing local file, then select the folder again.
              </div>
              <div className="kiwix-report-list">
                {issues.map((issue, index) => (
                  <div className="kiwix-report-item" key={`${issue.code || 'error'}-${index}`}>
                    <div className="kiwix-report-item-head">
                      <span className="kiwix-report-stage">{issueLabel(issue)}</span>
                      <code>{issue.code || 'UPLOAD_FAILED'}</code>
                    </div>
                    <strong>{issue.markdownPath || issue.fileName || 'Unknown file'}</strong>
                    {issue.row ? <div>JSON row: {issue.row}</div> : null}
                    {issue.referencePath ? <div>Missing/reference image: <code>{issue.referencePath}</code></div> : null}
                    <div className="kiwix-report-reason">{issue.reason || 'Unknown error'}</div>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
        <div className="modal-foot">
          <button type="button" className="btn ghost" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
