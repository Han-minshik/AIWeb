import React, { useEffect, useMemo, useRef, useState } from 'react';
import { http } from '../../services/http/client';
import { DOCUMENT_UPLOAD_ACCEPT, uploadDocsToMinio } from '../../services/docs/docService';
import { listPipelineJobs, retryPipelineJobs, subscribePipelineJobs } from '../../services/pipeline/pipelineJobService';
import { pipelineJobLabel, pipelineStatusClass, retryableJobs } from '../../utils/pipelineJobs';
import '../../styles/components/modal.css';

function formatDate(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString();
}

function documentPath(row) {
  const parts = [row?.relative_path, row?.sub_path, row?.doc_name || row?.alias].filter(Boolean);
  return parts.join('/');
}

function formatBytes(value) {
  const bytes = Math.max(Number(value) || 0, 0);
  if (bytes === 0) return '0 B';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  const unitIndex = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const amount = bytes / (1024 ** unitIndex);
  return `${amount >= 10 || unitIndex === 0 ? amount.toFixed(0) : amount.toFixed(2)} ${units[unitIndex]}`;
}

function buildFolderOptions(kb, nodes) {
  const rootId = Number(kb?.kb_id);
  if (!Number.isFinite(rootId)) return [];
  const options = [{ id: rootId, label: kb?.name || 'Personal KB' }];
  const walk = (items, prefix = '') => {
    for (const node of items || []) {
      if (!node?.is_folder) continue;
      const label = prefix ? `${prefix} / ${node.name}` : String(node.name || 'Folder');
      options.push({ id: Number(node.node_id ?? node.id), label });
      walk(node.children, label);
    }
  };
  walk(nodes);
  return options.filter((option) => Number.isFinite(option.id));
}

function filterAiJobs(rows, query) {
  const normalized = String(query || '').trim().toLowerCase();
  if (!normalized) return rows || [];
  return (rows || []).filter((row) =>
    [
      row.job_id,
      row.ai_document_id,
      row.action,
      row.status,
      row.index_status,
      row.last_error,
      documentPath(row),
    ]
      .filter((value) => value != null)
      .some((value) => String(value).toLowerCase().includes(normalized))
  );
}

function SelectAllCheckbox({ rows, idKey, selectedIds, onToggleAll, label, retryableOnly = true }) {
  const ref = useRef(null);
  const selectableRows = retryableOnly ? retryableJobs(rows) : (rows || []);
  const ids = selectableRows.map((row) => row[idKey]).filter((id) => id != null);
  const selectedCount = ids.filter((id) => selectedIds.has(id)).length;
  const allSelected = ids.length > 0 && selectedCount === ids.length;
  const partiallySelected = selectedCount > 0 && !allSelected;

  useEffect(() => {
    if (ref.current) ref.current.indeterminate = partiallySelected;
  }, [partiallySelected]);

  return (
    <input
      ref={ref}
      type="checkbox"
      checked={allSelected}
      disabled={!ids.length}
      aria-label={label}
      onClick={(event) => event.stopPropagation()}
      onChange={(event) => onToggleAll(ids, event.target.checked)}
    />
  );
}

function BatchResultModal({ result, onClose }) {
  if (!result) return null;
  const failedItems = (result.items || []).filter((item) => item.success !== true);

  return (
    <div className="modal-backdrop batch-result-backdrop" role="presentation" onMouseDown={onClose}>
      <div
        className="modal batch-result-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Operation result"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="modal-head">
          <div>
            <div className="modal-title">{result.title || 'Operation result'}</div>
            <div className="modal-help">
              요청 {result.requested || 0}건: 성공 {result.succeeded || 0}건, 실패 {result.failed || 0}건
            </div>
          </div>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close">X</button>
        </div>

        <div className="batch-result-summary">
          <div className="batch-result-count success">성공 <strong>{result.succeeded || 0}</strong></div>
          <div className="batch-result-count failure">실패 <strong>{result.failed || 0}</strong></div>
        </div>

        {failedItems.length ? (
          <div className="batch-result-list">
            <table className="personal-kb-table batch-result-table">
              <thead>
                <tr>
                  <th>Target</th>
                  <th>Action</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {failedItems.map((item, index) => (
                  <tr key={`${item.job_id ?? item.deletion_id ?? 'item'}-${index}`}>
                    <td>
                      {item.file_name || (item.job_id != null ? `Job ${item.job_id}` : `Deletion ${item.deletion_id}`)}
                    </td>
                    <td>{item.action || '-'}</td>
                    <td className="batch-result-error">{item.error || 'Unknown error'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="batch-result-all-success">모든 요청이 성공적으로 처리되었습니다.</div>
        )}

        <div className="modal-foot">
          <button type="button" className="btn primary" onClick={onClose}>확인</button>
        </div>
      </div>
    </div>
  );
}

function AiJobsTable({ rows, selectedJobIds, onToggleJob, onToggleAll, includeIndex = false }) {
  const toggleRow = (row) => {
    if (row?.retry_allowed !== true) return;
    onToggleJob(row.job_id, !selectedJobIds.has(row.job_id));
  };

  return (
    <table className={`personal-kb-table${includeIndex ? ' ai-jobs-table' : ''}`}>
      <thead>
        <tr>
          <th>
            <SelectAllCheckbox
              rows={rows}
              idKey="job_id"
              selectedIds={selectedJobIds}
              onToggleAll={onToggleAll}
              label="Select all visible AI jobs"
            />
          </th>
          <th>Job</th>
          {includeIndex && <th>AI Document</th>}
          <th>Action</th>
          <th>Status</th>
          {includeIndex && <th>Index</th>}
          <th>Document</th>
          <th>Error</th>
        </tr>
      </thead>
      <tbody>
        {(rows || []).map((row) => (
          <tr
            key={row.job_id}
            className={`selectable-row${selectedJobIds.has(row.job_id) ? ' selected' : ''}`}
            onClick={() => toggleRow(row)}
          >
            <td>
              <input
                type="checkbox"
                disabled={!row.retry_allowed}
                checked={selectedJobIds.has(row.job_id)}
                aria-label={`Select AI job ${row.job_id}`}
                onClick={(event) => event.stopPropagation()}
                onChange={(event) => onToggleJob(row.job_id, event.target.checked)}
              />
            </td>
            <td>{row.job_id}</td>
            {includeIndex && <td>{row.ai_document_id}</td>}
            <td><span className={`job-action job-action-${String(row.action || '').toLowerCase()}`}>{row.action || '-'}</span></td>
            <td><span className={pipelineStatusClass(row.status)}>{pipelineJobLabel(row)}</span></td>
            {includeIndex && <td>{row.index_status || '-'}</td>}
            <td title={documentPath(row)}>{documentPath(row) || '-'}</td>
            <td title={row.last_error || row.retry_block_reason || ''}>{row.last_error || row.retry_block_reason || '-'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PersonalAiJobsFullModal({
  open,
  rows,
  query,
  onQuery,
  onClose,
  selectedJobIds,
  onToggleJob,
  onToggleAll,
  onRetrySelected,
  running,
  loading,
  error,
}) {
  if (!open) return null;
  const filtered = filterAiJobs(rows, query);

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <div className="modal ai-jobs-full-modal" role="dialog" aria-modal="true" aria-label="Personal KB AI Jobs" onMouseDown={(event) => event.stopPropagation()}>
        <div className="modal-head">
          <div>
            <div className="modal-title">Personal KB AI Jobs</div>
            <div className="modal-help">{filtered.length} / {(rows || []).length}</div>
          </div>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close">X</button>
        </div>
        <div className="ai-jobs-toolbar">
          <input
            className="input"
            value={query}
            onChange={(event) => onQuery(event.target.value)}
            placeholder="Search job, document, status, error"
          />
          <button type="button" className="btn primary" onClick={onRetrySelected} disabled={!selectedJobIds.size || running}>
            Retry Selected ({selectedJobIds.size})
          </button>
        </div>
        <div className="personal-kb-table-wrap ai-jobs-table-wrap">
          {loading ? (
            <div className="empty">Loading AI jobs...</div>
          ) : error ? (
            <div className="modal-help warning">{error}</div>
          ) : filtered.length === 0 ? (
            <div className="empty">No AI jobs.</div>
          ) : (
            <AiJobsTable
              rows={filtered}
              selectedJobIds={selectedJobIds}
              onToggleJob={onToggleJob}
              onToggleAll={onToggleAll}
              includeIndex
            />
          )}
        </div>
      </div>
    </div>
  );
}

export default function PersonalKbModal({ open, onClose }) {
  const [state, setState] = useState({ loading: false, error: '', data: null });
  const [creating, setCreating] = useState(false);
  const [deletedState, setDeletedState] = useState({ loading: false, error: '', items: [] });
  const [selectedDeletedIds, setSelectedDeletedIds] = useState(() => new Set());
  const [aiStatus, setAiStatus] = useState('failed');
  const [aiSearch, setAiSearch] = useState('');
  const [aiFullOpen, setAiFullOpen] = useState(false);
  const [aiState, setAiState] = useState({ loading: false, error: '', items: [] });
  const [aiFullState, setAiFullState] = useState({ loading: false, error: '', items: [] });
  const [selectedJobIds, setSelectedJobIds] = useState(() => new Set());
  const [batchResult, setBatchResult] = useState(null);
  const [batchRunning, setBatchRunning] = useState(false);
  const [uploadingFiles, setUploadingFiles] = useState(false);
  const [uploadItems, setUploadItems] = useState([]);
  const [folderTree, setFolderTree] = useState([]);
  const [uploadParentId, setUploadParentId] = useState(null);
  const uploadInputRef = useRef(null);

  const data = state.data || {};
  const kb = data.kb || null;
  const storage = data.storage || {};
  const storagePercent = Math.min(Math.max(Number(storage.usage_percent) || 0, 0), 100);
  const folderOptions = useMemo(() => buildFolderOptions(kb, folderTree), [kb, folderTree]);
  const pendingDeleted = useMemo(() => deletedState.items || [], [deletedState.items]);
  const knownAiRows = useMemo(() => {
    const byId = new Map();
    [...(aiState.items || []), ...(aiFullState.items || [])].forEach((row) => byId.set(row.job_id, row));
    return Array.from(byId.values());
  }, [aiState.items, aiFullState.items]);

  const load = async () => {
    setState((previous) => ({ ...previous, loading: true, error: '' }));
    try {
      const next = await http.get('/kb/personal');
      setState({ loading: false, error: '', data: next });
    } catch (error) {
      setState({ loading: false, error: error?.userMessage || error?.message || 'Failed to load personal KB.', data: null });
    }
  };

  const loadDeleted = async () => {
    setDeletedState((previous) => ({ ...previous, loading: true, error: '' }));
    try {
      const response = await http.get('/kb/personal/deleted-documents', { status: 'pending' });
      const items = Array.isArray(response) ? response : response?.items || [];
      setDeletedState({ loading: false, error: '', items });
    } catch (error) {
      setDeletedState({ loading: false, error: error?.userMessage || error?.message || 'Failed to load deleted documents.', items: [] });
    }
  };

  const loadAiJobs = async (status = aiStatus) => {
    setAiState((previous) => ({ ...previous, loading: true, error: '' }));
    try {
      const response = await listPipelineJobs({ status, scope: 'personal', limit: status === 'all' ? 1000 : 200 });
      const items = Array.isArray(response) ? response : response?.items || [];
      setAiState({ loading: false, error: '', items });
    } catch (error) {
      setAiState({ loading: false, error: error?.userMessage || error?.message || 'Failed to load AI jobs.', items: [] });
    }
  };

  const loadAllAiJobs = async ({ show = false, resetSearch = false } = {}) => {
    if (show) setAiFullOpen(true);
    if (resetSearch) setAiSearch('');
    setAiFullState((previous) => ({ ...previous, loading: true, error: '' }));
    try {
      const response = await listPipelineJobs({ status: 'all', scope: 'personal', limit: 1000 });
      const items = Array.isArray(response) ? response : response?.items || [];
      setAiFullState({ loading: false, error: '', items });
    } catch (error) {
      setAiFullState({ loading: false, error: error?.userMessage || error?.message || 'Failed to load all AI jobs.', items: [] });
    }
  };

  useEffect(() => {
    if (!open) return;
    load();
    loadDeleted();
    setSelectedDeletedIds(new Set());
    setSelectedJobIds(new Set());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    const kbId = Number(kb?.kb_id);
    if (!open || !Number.isFinite(kbId)) {
      setFolderTree([]);
      setUploadParentId(null);
      return undefined;
    }
    let cancelled = false;
    http.get(`/kb/trees/${kbId}/tree`, { include_empty_folders: true })
      .then((response) => {
        if (cancelled) return;
        const nextNodes = Array.isArray(response?.nodes) ? response.nodes : [];
        const options = buildFolderOptions(kb, nextNodes);
        setFolderTree(nextNodes);
        setUploadParentId((previous) =>
          options.some((option) => option.id === Number(previous)) ? Number(previous) : kbId
        );
      })
      .catch(() => {
        if (cancelled) return;
        setFolderTree([]);
        setUploadParentId(kbId);
      });
    return () => {
      cancelled = true;
    };
  }, [open, kb?.kb_id, kb?.name]);

  useEffect(() => {
    if (!open) return;
    setSelectedJobIds(new Set());
    loadAiJobs(aiStatus);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, aiStatus]);

  useEffect(() => {
    if (!open) return undefined;
    return subscribePipelineJobs({
      scope: 'personal',
      onSnapshot: (payload) => {
        const allRows = Array.isArray(payload?.items) ? payload.items : [];
        const visibleRows = aiStatus === 'all'
          ? allRows
          : allRows.filter((row) => row.status === aiStatus);
        setAiState({ loading: false, error: '', items: visibleRows });
        setAiFullState((previous) => ({ ...previous, loading: false, error: '', items: allRows }));
        setSelectedJobIds((previous) => new Set(
          Array.from(previous).filter((id) =>
            allRows.some((row) => row.job_id === id && row.retry_allowed === true)
          )
        ));
      },
    });
  }, [open, aiStatus]);

  const create = async () => {
    setCreating(true);
    setState((previous) => ({ ...previous, error: '' }));
    try {
      const next = await http.post('/kb/personal', {});
      setState({ loading: false, error: '', data: next });
      window.dispatchEvent(new CustomEvent('kb:personal-created', { detail: next }));
      await loadDeleted();
    } catch (error) {
      setState((previous) => ({ ...previous, error: error?.userMessage || error?.message || 'Failed to create personal KB.' }));
    } finally {
      setCreating(false);
    }
  };

  const toggleIds = (setter, ids, checked) => {
    setter((previous) => {
      const next = new Set(previous);
      ids.forEach((id) => {
        if (checked) next.add(id);
        else next.delete(id);
      });
      return next;
    });
  };

  const toggleJob = (jobId, checked) => toggleIds(setSelectedJobIds, [jobId], checked);
  const toggleDeleted = (deletionId, checked) => toggleIds(setSelectedDeletedIds, [deletionId], checked);

  const selectedJobRequests = () => {
    const rowsById = new Map(knownAiRows.map((row) => [row.job_id, row]));
    return Array.from(selectedJobIds)
      .map((jobId) => rowsById.get(jobId))
      .filter(Boolean)
      .map((row) => ({ job_id: Number(row.job_id), action: String(row.action || '').trim().toLowerCase() }));
  };

  const showBatchResult = (title, response) => {
    setBatchResult({
      title,
      requested: response?.requested || 0,
      succeeded: response?.succeeded || 0,
      failed: response?.failed || 0,
      items: response?.items || [],
    });
  };

  const showRequestFailure = (title, requests, error) => {
    const message = error?.userMessage || error?.message || 'Request failed.';
    setBatchResult({
      title,
      requested: requests.length,
      succeeded: 0,
      failed: requests.length,
      items: requests.map((request) => ({ ...request, success: false, error: message })),
    });
  };

  const refreshJobLists = async () => {
    await loadAiJobs(aiStatus);
    if (aiFullOpen) await loadAllAiJobs();
  };

  const runSelectedJobs = async () => {
    const jobs = selectedJobRequests();
    if (!jobs.length) return;
    const title = 'AI Job retry result';
    setBatchRunning(true);
    try {
      const response = await retryPipelineJobs(jobs.map((job) => job.job_id));
      showBatchResult(title, response);
      setSelectedJobIds(new Set());
      await refreshJobLists();
    } catch (error) {
      showRequestFailure(title, jobs, error);
    } finally {
      setBatchRunning(false);
    }
  };

  const runDeletedActions = async (action, deletionIds) => {
    const ids = Array.from(deletionIds || []);
    if (!ids.length) return;
    if (action === 'purge' && !window.confirm(`Permanently purge ${ids.length} document(s)?`)) return;

    const requests = ids.map((deletionId) => ({ deletion_id: Number(deletionId), action }));
    const title = action === 'restore' ? 'Deleted documents restore result' : 'Deleted documents purge result';
    setBatchRunning(true);
    try {
      const response = await http.post('/kb/personal/deleted-documents/actions', { requests });
      showBatchResult(title, response);
      if (Number(response?.succeeded) > 0) {
        const refreshEvent = action === 'restore' ? 'kb:personal-created' : 'kb:tree-refresh';
        window.dispatchEvent(new CustomEvent(refreshEvent));
        if (action === 'purge') {
          window.dispatchEvent(new Event('bookmarks:refresh'));
        }
      }
      setSelectedDeletedIds(new Set());
      await Promise.all([loadDeleted(), loadAiJobs(aiStatus)]);
    } catch (error) {
      showRequestFailure(title, requests, error);
    } finally {
      setBatchRunning(false);
    }
  };

  const uploadSelectedFiles = async (event) => {
    const files = Array.from(event.target.files || []);
    event.target.value = '';
    if (!files.length || !kb?.kb_id) return;
    setUploadingFiles(true);
    setUploadItems(files.map((file) => ({ file, status: 'queued', percent: 0 })));
    try {
      const results = await uploadDocsToMinio(files, {
        parent_node_id: Number.isFinite(Number(uploadParentId)) ? Number(uploadParentId) : kb.kb_id,
        concurrency: 3,
        onNameConflict: ({ fileName }) =>
          window.confirm(
            `A document named "${fileName}" already exists in this folder. Upload it as a new version?`
          ),
        onItemChange: (index, item) => {
          setUploadItems((previous) => {
            const next = [...previous];
            next[index] = { ...(next[index] || {}), ...item };
            return next;
          });
        },
      });
      const succeeded = results.filter((item) => item?.success).length;
      const failed = results.length - succeeded;
      setBatchResult({
        title: 'Document upload result',
        requested: results.length,
        succeeded,
        failed,
        items: results.map((item, index) => ({
          success: item?.success === true,
          action: 'upload',
          file_name: files[index]?.name,
          error: item?.error?.userMessage || item?.error?.message || null,
        })),
      });
      if (succeeded) window.dispatchEvent(new CustomEvent('kb:personal-created'));
      await Promise.all([load(), loadAiJobs(aiStatus)]);
    } finally {
      setUploadingFiles(false);
    }
  };
  if (!open) return null;

  return (
    <>
      <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
        <div className="modal personal-kb-modal" role="dialog" aria-modal="true" aria-label="Personal KB Management" onMouseDown={(event) => event.stopPropagation()}>
          <div className="modal-head">
            <div>
              <div className="modal-title">Personal KB Management</div>
              <div className="modal-help">Create, sync, and recover your personal knowledge base.</div>
            </div>
            <button type="button" className="modal-close" onClick={onClose} aria-label="Close">X</button>
          </div>

          <div className="modal-body">
            {state.loading ? (
              <div className="loading">Loading...</div>
            ) : (
              <div className="modal-form">
                {state.error && <div className="modal-help warning">{state.error}</div>}
                {data.exists && (
                  <>
                  <div className="personal-kb-actions personal-kb-upload">
                    <label>
                      <span className='modal-help'>Upload folder</span>
                      <select
                        className='input'
                        value={uploadParentId ?? kb.kb_id}
                        onChange={(event) => setUploadParentId(Number(event.target.value))}
                        disabled={uploadingFiles}
                      >
                        {folderOptions.map((folder) => (
                          <option key={folder.id} value={folder.id}>{folder.label}</option>
                        ))}
                      </select>
                    </label>
                    <input
                      ref={uploadInputRef}
                      type="file"
                      accept={DOCUMENT_UPLOAD_ACCEPT}
                      multiple
                      style={{ display: 'none' }}
                      onChange={uploadSelectedFiles}
                    />
                    <button
                      type="button"
                      className="btn primary"
                      onClick={() => uploadInputRef.current?.click()}
                      disabled={uploadingFiles}
                    >
                      {uploadingFiles ? 'Uploading...' : 'Upload documents'}
                    </button>
                  </div>
                  {uploadItems.length > 0 && (
                    <div className="modal-help">
                      {uploadItems.map((item, index) => (
                        <div key={`${item.file?.name || 'file'}-${index}`}>
                          {item.file?.name || 'file'}: {item.status}
                          {item.percent != null && item.status === 'uploading' ? ` (${item.percent}%)` : ''}
                        </div>
                      ))}
                    </div>
                  )}
                  </>
                )}

                <div className="personal-kb-storage" aria-label="Personal KB storage usage">
                  <div className="personal-kb-storage-head">
                    <span>Storage usage</span>
                    <strong>{formatBytes(storage.accounted_bytes)} / {formatBytes(storage.quota_bytes)}</strong>
                  </div>
                  <div className="personal-kb-storage-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow={storagePercent}>
                    <div className="personal-kb-storage-fill" style={{ width: `${storagePercent}%` }} />
                  </div>
                  <div className="modal-help">
                    Stored {formatBytes(storage.committed_bytes)} | Uploading {formatBytes(storage.reserved_bytes)} | Available {formatBytes(storage.available_bytes)}
                  </div>
                </div>


                {data.exists && (
                  <div className="personal-kb-deleted">
                    <div className="modal-section-head">
                      <div>
                        <div className="modal-title">AI Jobs</div>
                        <div className="modal-help">Select jobs explicitly. The server verifies each job ID, action, status, and owner.</div>
                      </div>
                      <span className="pill-count">{aiState.items.length}</span>
                    </div>
                    <div className="personal-kb-actions">
                      <select className="input" value={aiStatus} onChange={(event) => setAiStatus(event.target.value)} style={{ width: 160 }}>
                        <option value="failed">failed</option>
                        <option value="pending">pending</option>
                        <option value="running">running</option>
                        <option value="sent">sent</option>
                        <option value="completed">completed</option>
                        <option value="cancelled">cancelled</option>
                        <option value="superseded">superseded</option>
                        <option value="all">all</option>
                      </select>
                      <button type="button" className="btn primary" onClick={runSelectedJobs} disabled={!selectedJobIds.size || batchRunning}>
                        Retry Selected ({selectedJobIds.size})
                      </button>
                      <button type="button" className="btn" onClick={() => loadAiJobs(aiStatus)} disabled={aiState.loading || batchRunning}>Refresh</button>
                      <button type="button" className="btn" onClick={() => loadAllAiJobs({ show: true, resetSearch: true })}>Open All</button>
                    </div>
                    {aiState.error && <div className="modal-help warning">{aiState.error}</div>}
                    {aiState.loading ? (
                      <div className="empty">Loading AI jobs...</div>
                    ) : aiState.items.length === 0 ? (
                      <div className="empty">No AI jobs.</div>
                    ) : (
                      <div className="personal-kb-table-wrap">
                        <AiJobsTable
                          rows={aiState.items}
                          selectedJobIds={selectedJobIds}
                          onToggleJob={toggleJob}
                          onToggleAll={(ids, checked) => toggleIds(setSelectedJobIds, ids, checked)}
                        />
                      </div>
                    )}
                  </div>
                )}

                {data.exists && (
                  <div className="personal-kb-deleted">
                    <div className="modal-section-head">
                      <div>
                        <div className="modal-title">Deleted Docs</div>
                        <div className="modal-help">Restore keeps the stored source. Permanent purge completes after index and storage cleanup.</div>
                      </div>
                      <span className="pill-count">{pendingDeleted.length}</span>
                    </div>
                    <div className="personal-kb-actions">
                      <button type="button" className="btn" disabled={!selectedDeletedIds.size || batchRunning} onClick={() => runDeletedActions('restore', selectedDeletedIds)}>
                        Restore Selected ({selectedDeletedIds.size})
                      </button>
                      <button type="button" className="btn danger" disabled={!selectedDeletedIds.size || batchRunning} onClick={() => runDeletedActions('purge', selectedDeletedIds)}>
                        Purge Selected ({selectedDeletedIds.size})
                      </button>
                    </div>
                    {deletedState.error && <div className="modal-help warning">{deletedState.error}</div>}
                    {deletedState.loading ? (
                      <div className="empty">Loading deleted documents...</div>
                    ) : pendingDeleted.length === 0 ? (
                      <div className="empty">No pending deleted documents.</div>
                    ) : (
                      <div className="personal-kb-table-wrap">
                        <table className="personal-kb-table deleted-docs-table">
                          <thead>
                            <tr>
                              <th>
                                <SelectAllCheckbox
                                  rows={pendingDeleted}
                                  idKey="deletion_id"
                                  selectedIds={selectedDeletedIds}
                                  onToggleAll={(ids, checked) => toggleIds(setSelectedDeletedIds, ids, checked)}
                                  retryableOnly={false}
                                  label="Select all deleted documents"
                                />
                              </th>
                              <th>Document</th>
                              <th>Status</th>
                              <th>Detected</th>
                              <th>Remain Until</th>
                              <th>Action</th>
                            </tr>
                          </thead>
                          <tbody>
                            {pendingDeleted.map((row) => (
                              <tr
                                key={row.deletion_id}
                                className={`selectable-row${selectedDeletedIds.has(row.deletion_id) ? ' selected' : ''}`}
                                onClick={() => toggleDeleted(row.deletion_id, !selectedDeletedIds.has(row.deletion_id))}
                              >
                                <td>
                                  <input
                                    type="checkbox"
                                    checked={selectedDeletedIds.has(row.deletion_id)}
                                    aria-label={`Select deleted document ${row.deletion_id}`}
                                    onClick={(event) => event.stopPropagation()}
                                    onChange={(event) => toggleDeleted(row.deletion_id, event.target.checked)}
                                  />
                                </td>
                                <td title={documentPath(row)}>{documentPath(row) || '-'}</td>
                                <td><span className={pipelineStatusClass(row.status)}>{pipelineJobLabel(row)}</span></td>
                                <td>{formatDate(row.detected_at)}</td>
                                <td>{formatDate(row.remain_until)}</td>
                                <td>
                                  <div className="personal-kb-row-actions">
                                    <button type="button" className="btn" disabled={batchRunning} onClick={(event) => { event.stopPropagation(); runDeletedActions('restore', [row.deletion_id]); }}>Restore</button>
                                    <button type="button" className="btn danger" disabled={batchRunning} onClick={(event) => { event.stopPropagation(); runDeletedActions('purge', [row.deletion_id]); }}>Purge</button>
                                  </div>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="modal-foot">
            <button type="button" className="btn ghost" onClick={onClose}>Close</button>
            {!data.exists && <button type="button" className="btn primary" onClick={create} disabled={creating || state.loading}>{creating ? 'Creating...' : 'Create Personal KB'}</button>}
            {data.exists && <button type="button" className="btn" onClick={load} disabled={state.loading}>Refresh</button>}
          </div>
        </div>
      </div>

      <PersonalAiJobsFullModal
        open={aiFullOpen}
        rows={aiFullState.items}
        query={aiSearch}
        onQuery={setAiSearch}
        onClose={() => setAiFullOpen(false)}
        selectedJobIds={selectedJobIds}
        onToggleJob={toggleJob}
        onToggleAll={(ids, checked) => toggleIds(setSelectedJobIds, ids, checked)}
        onRetrySelected={runSelectedJobs}
        running={batchRunning}
        loading={aiFullState.loading}
        error={aiFullState.error}
      />
      <BatchResultModal result={batchResult} onClose={() => setBatchResult(null)} />
    </>
  );
}
