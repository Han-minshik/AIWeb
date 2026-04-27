import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { uploadDoc, deleteDoc } from '../../services/docs/docService';
import { http } from '../../services/http/client';
import FileTree from '../../components/admin/FileTree';
import VersionManagerModal from '../../components/admin/kb/VersionManagerModal';
import DocDeleteModal from '../../components/admin/kb/DocDeleteModal';
import SyncManageModal from '../../components/admin/kb/SyncManageModal';

export default function ProjectFilesPage() {
  const [q, setQ] = useState('');
  const [kbs, setKbs] = useState([]);
  const [kbId, setKbId] = useState(null);
  const [tree, setTree] = useState({ nodes: [] });
  const [expanded, setExpanded] = useState(() => new Set());
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [docs, setDocs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selectedNodeIds, setSelectedNodeIds] = useState([]);
  const uploadRef = useRef(null);

  const [kbMenuOpen, setKbMenuOpen] = useState(false);
  const kbMenuRef = useRef(null);

  const [selectedDocIds, setSelectedDocIds] = useState(() => new Set());
  const headerCheckRef = useRef(null);
  const [ctxMenu, setCtxMenu] = useState(null);
  const [verModalOpen, setVerModalOpen] = useState(false);
  const [verDoc, setVerDoc] = useState(null);
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);
  const [deleteTargets, setDeleteTargets] = useState({ docIds: [] });
  const [uploading, setUploading] = useState(false);
  const [syncModalOpen, setSyncModalOpen] = useState(false);

  const parseId = (v) => {
    const n = Number.parseInt(String(v ?? '').trim(), 10);
    return Number.isFinite(n) ? n : null;
  };

  const getKbId = useCallback((kb) => parseId(kb?.kb_id ?? kb?.id), []);
  const getKbName = useCallback(
    (kb) => (kb?.name ? String(kb.name) : `KB ${getKbId(kb)}`),
    [getKbId]
  );

  const loadKbs = useCallback(async () => {
    try {
      const res = await http.get('/kb/trees', { kb_type: 'admin' });
      const items = Array.isArray(res) ? res : res?.items || [];
      setKbs(items);
      setKbId((prev) => {
        if (Number.isFinite(prev)) return prev;
        const first = items[0] ? getKbId(items[0]) : null;
        return first;
      });
    } catch (e) {
      console.error('loadKbs error', e);
      setKbs([]);
      setKbId(null);
    }
  }, [getKbId]);

  const fetchTree = useCallback(async () => {
    if (!Number.isFinite(kbId)) {
      setTree({ nodes: [] });
      return;
    }
    try {
      const t = await http.get(`/kb/trees/${kbId}/tree`);
      const normalize = (node) => ({
        ...node,
        is_folder: (node.node_type || '').toLowerCase().includes('folder'),
        children: (node.children || []).map(normalize),
      });
      const nodes = (t?.nodes || []).map(normalize);
      setTree({ nodes });
    } catch (e) {
      console.error('fetchTree error', e);
      setTree({ nodes: [] });
    }
  }, [kbId]);

  const fetchDocs = useCallback(async () => {
    if (!Number.isFinite(kbId)) {
      setDocs([]);
      return;
    }
    setLoading(true);
    try {
      const res = await http.get('/docs-tree/files', {
        kb_id: kbId,
        parent_id: Number.isFinite(selectedNodeId) ? selectedNodeId : null,
        q: (q || '').trim() || undefined,
      });
      const arr = Array.isArray(res) ? res : res?.items || [];
      setDocs(arr);
    } catch (e) {
      console.error('fetchDocs error', e);
      setDocs([]);
    } finally {
      setLoading(false);
    }
  }, [kbId, selectedNodeId, q]);

  useEffect(() => {
    loadKbs();
  }, [loadKbs]);

  useEffect(() => {
    fetchTree();
  }, [fetchTree]);

  useEffect(() => {
    fetchDocs();
  }, [fetchDocs]);

  const nodeMap = useMemo(() => {
    const map = new Map();
    const walk = (n) => {
      if (!n) return;
      map.set(n.id, n);
      (n.children || []).forEach(walk);
    };
    (tree?.nodes || []).forEach(walk);
    return map;
  }, [tree]);

  const childrenMap = useMemo(() => {
    const map = new Map();
    const walk = (n) => {
      if (!n) return;
      const pid = n.parent ?? n.parent_id ?? null;
      if (pid != null) {
        const arr = map.get(pid) || [];
        arr.push(n.id);
        map.set(pid, arr);
      }
      (n.children || []).forEach(walk);
    };
    (tree?.nodes || []).forEach(walk);
    return map;
  }, [tree]);

  const collectDescendantIds = useCallback(
    (rootId) => {
      const out = new Set();
      const stack = [rootId];
      while (stack.length) {
        const cur = stack.pop();
        const kids = childrenMap.get(cur) || [];
        for (const kid of kids) {
          if (out.has(kid)) continue;
          out.add(kid);
          stack.push(kid);
        }
      }
      return out;
    },
    [childrenMap]
  );

  useEffect(() => {
    const onDocClick = (e) => {
      if (!kbMenuRef.current) return;
      if (!kbMenuRef.current.contains(e.target)) setKbMenuOpen(false);
    };
    if (kbMenuOpen) document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [kbMenuOpen]);

  useEffect(() => {
    setSelectedDocIds(new Set());
  }, [kbId, selectedNodeId, q]);

  useEffect(() => {
    const close = () => setCtxMenu(null);
    if (ctxMenu) document.addEventListener('click', close);
    return () => document.removeEventListener('click', close);
  }, [ctxMenu]);

  const onTreeSelect = useCallback((node) => {
    const parentForList = node.is_folder
      ? node.id
      : node.parent ?? node.parent_id ?? null;
    setSelectedNodeId(parentForList);
  }, []);

  const onTreeToggle = useCallback((node) => {
    if (!node?.is_folder) return;
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(node.id)) next.delete(node.id);
      else next.add(node.id);
      return next;
    });
  }, []);

  const onUpload = async (e) => {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    e.target.value = '';
    const inferBranchType = () => {
      const lowerKbName = String(currentKb?.name || '').trim().toUpperCase();
      if (lowerKbName.includes('KIWIX')) return 'KIWIX';
      if (!Number.isFinite(selectedNodeId)) return 'GENERAL';
      let cur = selectedNodeId;
      const guard = new Set();
      while (cur != null && !guard.has(cur)) {
        guard.add(cur);
        const n = nodeMap.get(cur);
        if (!n) break;
        const parent = n.parent ?? n.parent_id ?? null;
        if (parent == null) {
          const rootName = String(n.name || '').trim().toLowerCase();
          if (rootName.includes('kiwix')) return 'KIWIX';
          break;
        }
        cur = parent;
      }
      return 'GENERAL';
    };

    setUploading(true);
    try {
      const branchType = inferBranchType();
      for (const f of files) {
        await uploadDoc(f, {
          doc_type: guessDocType(f.name),
          status: 'Draft',
          tree_id: kbId,
          parent_node_id: selectedNodeId || null,
          branch_type: branchType,
        });
      }
      await fetchTree();
      await fetchDocs();
    } catch (err) {
      console.error('upload error', err);
      window.alert(`Upload failed: ${err?.message || err}`);
    } finally {
      setUploading(false);
    }
  };

  const onCreateFolder = async () => {
    if (!Number.isFinite(kbId)) {
      window.alert('Select KB First.');
      return;
    }
    const name = window.prompt('Enter a new folder name');
    if (!name) return;

    try {
      await http.post(`/kb/trees/${kbId}/nodes`, {
        name,
        parent_id: selectedNodeId ?? null,
        node_type: 'KB_FOLDER',
      });
      if (selectedNodeId) {
        setExpanded((prev) => {
          const next = new Set(prev);
          next.add(selectedNodeId);
          return next;
        });
      }
      await fetchTree();
      await fetchDocs();
    } catch (e) {
      if (e?.status === 409) window.alert('The same name already exists.');
      else window.alert(`Folder Create Failed: ${e?.message || e}`);
    }
  };

  const onDelete = async () => {
    if (!Number.isFinite(kbId)) {
      window.alert('Select KB first.');
      return;
    }
    if (!selectedNodeIds.length) {
      window.alert('Select the items you want to delete.');
      return;
    }
    const selectedNodes = selectedNodeIds
      .map((id) => nodeMap.get(id))
      .filter(Boolean);
    const docNodes = selectedNodes.filter((n) => n?.meta?.doc_id);
    const folderNodes = selectedNodes.filter((n) => !n?.meta?.doc_id);

    if (docNodes.length && folderNodes.length) {
      window.alert('?´ë”?€ ë¬¸ì„œë¥??™ì‹œ???? œ?????†ìŠµ?ˆë‹¤. ë¬¸ì„œë§?? íƒ??ì£¼ì„¸??');
      return;
    }

    if (docNodes.length) {
      const docIds = Array.from(new Set(docNodes.map((n) => Number(n.meta.doc_id))));
      setDeleteTargets({ docIds });
      setDeleteModalOpen(true);
      return;
    }

    if (!window.confirm(`Are you sure you want to delete ${folderNodes.length} items?`)) return;

    try {
      const allIds = new Set();
      for (const n of folderNodes) {
        allIds.add(n.id);
        collectDescendantIds(n.id).forEach((id) => allIds.add(id));
      }
      await http.post(`/kb/trees/${kbId}/nodes/bulk-delete`, {
        node_ids: Array.from(allIds),
      });
      window.alert('Delete Success');
      await fetchTree();
      setSelectedNodeIds([]);
      await fetchDocs();
    } catch (e) {
      console.error('delete error', e);
      window.alert(`Delete failed: ${e?.message || e}`);
    }
  };

  const onAddKb = async () => {
    const name = window.prompt('Enter a name for the new KB');
    if (!name) return;
    try {
      const res = await http.post('/kb/trees', {
        name,
        kb_type: 'admin',
        is_user_kb: 0,
        kind: 'KB',
      });
      const newId = parseId(res?.kb_id ?? res?.id ?? res?.tree_id);
      await loadKbs();
      if (Number.isFinite(newId)) {
        setKbId(newId);
        setSelectedNodeId(null);
        setSelectedNodeIds([]);
        setExpanded(new Set());
      }
      setKbMenuOpen(false);
    } catch (e) {
      console.error('create kb error', e);
      window.alert(`KB Create Failed: ${e?.message || e}`);
    }
  };

  const onDeleteKb = async (id) => {
    if (!Number.isFinite(id)) return;
    const target = kbs.find((k) => getKbId(k) === id);
    const name = target ? getKbName(target) : `#${id}`;
    if (!window.confirm(`'${name}' Are you sure you want to delete KB? This action is irreversible.`)) return;
    try {
      await http.delete(`/kb/trees/${id}`);
      await loadKbs();
      if (id === kbId) {
        setKbId(null);
        setTree({ nodes: [] });
        setDocs([]);
      }
    } catch (e) {
      console.error('delete kb error', e);
      window.alert(`KB Delete Failed: ${e?.message || e}`);
    }
  };

  const guessDocType = (name) => {
    const low = (name || '').toLowerCase();
    if (low.endsWith('.pdf')) return 'PDF';
    if (low.endsWith('.ppt') || low.endsWith('.pptx')) return 'PPT';
    if (low.endsWith('.doc') || low.endsWith('.docx')) return 'WORD';
    if (low.endsWith('.xls') || low.endsWith('.xlsx')) return 'EXCEL';
    if (low.endsWith('.md')) return 'MD';
    return 'FILE';
  };

  const formatDate = (v) => {
    if (!v) return '-';
    const d = new Date(v);
    return d.toLocaleString('ko-KR', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const tableRows = useMemo(
    () =>
      (docs || []).map((d) => ({
        id: d.node_id ?? d.id,
        doc_id: d.doc_id ?? d.docId ?? d.object_id ?? d.objectId,
        name: d.name,
        uploaded: formatDate(d.created_at),
        modified: formatDate(d.updated_at),
        rwx:
          Array.isArray(d.policy_matrix) && d.policy_matrix.length === 3
            ? d.policy_matrix
            : null,
      })),
    [docs]
  );

  const currentKb = useMemo(
    () => kbs.find((k) => getKbId(k) === kbId) || null,
    [kbs, kbId, getKbId]
  );

  const visibleDocIds = useMemo(() => tableRows.map((r) => r.id).filter((v) => v != null), [tableRows]);

  const allSelected = useMemo(() => {
    if (visibleDocIds.length === 0) return false;
    for (const id of visibleDocIds) {
      if (!selectedDocIds.has(id)) return false;
    }
    return true;
  }, [visibleDocIds, selectedDocIds]);

  const someSelected = useMemo(() => {
    for (const id of visibleDocIds) {
      if (selectedDocIds.has(id)) return true;
    }
    return false;
  }, [visibleDocIds, selectedDocIds]);

  useEffect(() => {
    if (!headerCheckRef.current) return;
    headerCheckRef.current.indeterminate = !allSelected && someSelected;
  }, [allSelected, someSelected]);

  const toggleAllDocs = useCallback(
    (checked) => {
      if (checked) {
        const next = new Set(visibleDocIds);
        setSelectedDocIds(next);
        window.alert(`${next.size}`);
      } else {
        setSelectedDocIds(new Set());
        window.alert('0');
      }
    },
    [visibleDocIds]
  );

  const toggleOneDoc = useCallback((id, checked) => {
    setSelectedDocIds((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  const openVersionModal = useCallback(
    (row) => {
      if (!row?.doc_id) return;
      setVerDoc({ id: row.doc_id, title: row.name });
      setVerModalOpen(true);
    },
    []
  );

  const onConfirmDeleteDocs = async (mode) => {
    try {
      for (const id of deleteTargets.docIds || []) {
        await deleteDoc(id, mode);
      }
      setDeleteModalOpen(false);
      setDeleteTargets({ docIds: [] });
      setSelectedNodeIds([]);
      await fetchTree();
      await fetchDocs();
    } catch (e) {
      console.error('doc delete error', e);
      window.alert(`Delete failed: ${e?.message || e}`);
    }
  };

  return (
    <div className="panel" style={{ height: '100%' }}>
      <input
        type="file"
        ref={uploadRef}
        multiple
        style={{ display: 'none' }}
        onChange={onUpload}
      />

      <div className="panel-head">
        <div className="panel-title">KB Management</div>
        <div className="panel-actions" style={{ gap: 8 }}>
          <button type="button" className="btn" onClick={() => setSyncModalOpen(true)}>
            Sync
          </button>
          <input
            className="input"
            placeholder="Search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') fetchDocs();
            }}
            style={{ minWidth: 220 }}
          />

          <div ref={kbMenuRef} style={{ position: 'relative' }}>
            <button
              type="button"
              className="input"
              onClick={() => setKbMenuOpen((v) => !v)}
              style={{
                minWidth: 260,
                textAlign: 'left',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 8,
              }}
              title="KB Select"
            >
              <span>
                {currentKb
                  ? `${getKbName(currentKb)} (#${getKbId(currentKb)})`
                  : kbs.length
                  ? 'KB Select'
                  : 'No KB'}
              </span>
              <span style={{ opacity: 0.6 }}>v</span>
            </button>

            {kbMenuOpen && (
              <div
                className="card-se"
                style={{
                  position: 'absolute',
                  top: '100%',
                  left: 0,
                  minWidth: 280,
                  maxHeight: 280,
                  overflowY: 'auto',
                  zIndex: 20,
                  padding: 6,
                }}
              >
                <div
                  className="menu-item"
                  onClick={onAddKb}
                  style={{ padding: '6px 10px', cursor: 'pointer', fontWeight: 600 }}
                >
                  + New KB??
                </div>
                <div style={{ borderTop: '1px solid var(--border,#e5e7eb)', margin: '6px 0' }} />
                {kbs.length === 0 ? (
                  <div className="empty" style={{ padding: '8px 10px' }}>
                    No result
                  </div>
                ) : (
                  kbs.map((kb) => {
                    const id = getKbId(kb);
                    const name = getKbName(kb);
                    const selected = id === kbId;
                    return (
                      <div
                        key={id}
                        className={`menu-item${selected ? ' selected' : ''}`}
                        onClick={() => {
                          setKbId(id);
                          setSelectedNodeId(null);
                          setSelectedNodeIds([]);
                          setExpanded(new Set());
                          setKbMenuOpen(false);
                        }}
                        style={{
                          padding: '6px 10px',
                          cursor: 'pointer',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          gap: 10,
                          borderRadius: 6,
                        }}
                        title={`${name} (#${id})`}
                      >
                        <span
                          style={{
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {name} <span style={{ opacity: 0.6 }}>#{id}</span>
                        </span>
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            onDeleteKb(id);
                          }}
                          title="Delete this KB"
                          style={{
                            border: 'none',
                            background: 'transparent',
                            cursor: 'pointer',
                            fontSize: 14,
                            lineHeight: 1,
                            opacity: 0.7,
                          }}
                        >
                          Ã—
                        </button>
                      </div>
                    );
                  })
                )}
              </div>
            )}
          </div>

          <button className="btn folder" onClick={onCreateFolder}>
            New Folder
          </button>
          <button className="btn upload" onClick={() => uploadRef.current?.click()}>
            Upload
          </button>
          <button
            className="btn danger"
            onClick={onDelete}
            disabled={!selectedNodeIds.length}
          >
            Delete
          </button>
          {loading && <div className="loading">Loading...</div>}
        </div>
      </div>

      <div className="panel-body split">
        <div className="card scroll tree-area">
          {!Number.isFinite(kbId) ? (
            <div className="empty" style={{ padding: 12 }}>
              Please select KB.
            </div>
          ) : !tree?.nodes?.length ? (
            <div className="empty" style={{ padding: 12 }}>
              No result.
            </div>
          ) : (
            <FileTree
              nodes={tree.nodes}
              kbid={kbId}
              expanded={expanded}
              onSelect={onTreeSelect}
              onToggle={onTreeToggle}
              onSelectChange={(set) => {
                const ids = Array.from(set);
                setSelectedNodeIds(ids);
                if (ids.length === 0) setSelectedNodeId(null);
              }}
              selectedNodeId={selectedNodeId}
              fetchTree={fetchTree}
            />
          )}
        </div>

        <div className="card scroll table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 64 }}>
                  <input
                    ref={headerCheckRef}
                    type="checkbox"
                    checked={allSelected}
                    onChange={(e) => toggleAllDocs(e.target.checked)}
                    disabled={visibleDocIds.length === 0}
                    title="Select all"
                  />
                </th>
                <th>Name</th>
                <th>Upload</th>
                <th>Update</th>
                <th>Permissions(Owner/Group/Other)</th>
              </tr>
            </thead>
            <tbody>
              {tableRows.length === 0 ? (
                <tr>
                  <td colSpan={5} className="empty">
                    No result.
                  </td>
                </tr>
              ) : (
                tableRows.map((r) => (
                  <tr
                    key={r.id}
                    onContextMenu={(e) => {
                      e.preventDefault();
                      if (!r.doc_id) return;
                      setCtxMenu({
                        x: e.clientX,
                        y: e.clientY,
                        row: r,
                      });
                    }}
                  >
                    <td>
                      <input
                        type="checkbox"
                        checked={selectedDocIds.has(r.id)}
                        onChange={(e) => toggleOneDoc(r.id, e.target.checked)}
                      />
                    </td>
                    <td>{r.name}</td>
                    <td className="nowrap">{r.uploaded}</td>
                    <td className="nowrap">{r.modified}</td>
                    <td>
                      {Array.isArray(r.rwx) ? (
                        <span className="rwx">
                          <span className="own">{r.rwx[0]}</span>
                          <span className="grp" style={{ marginLeft: 6 }}>
                            {r.rwx[1]}
                          </span>
                          <span className="oth" style={{ marginLeft: 6 }}>
                            {r.rwx[2]}
                          </span>
                        </span>
                      ) : (
                        '-'
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
      {ctxMenu && (
        <div
          className="ctx-menu"
          style={{ position: 'fixed', top: ctxMenu.y, left: ctxMenu.x, zIndex: 100000 }}
        >
          <button
            type="button"
            className="ctx-item"
            onClick={() => {
              openVersionModal(ctxMenu.row);
              setCtxMenu(null);
            }}
          >
            ë²„ì „ ê´€ë¦?
          </button>
        </div>
      )}

      <VersionManagerModal
        open={verModalOpen}
        docId={verDoc?.id}
        docTitle={verDoc?.title}
        onClose={() => setVerModalOpen(false)}
      />
      <SyncManageModal
        open={syncModalOpen}
        kbId={kbId}
        onClose={() => setSyncModalOpen(false)}
      />
      <DocDeleteModal
        open={deleteModalOpen}
        count={(deleteTargets.docIds || []).length}
        onClose={() => setDeleteModalOpen(false)}
        onConfirm={onConfirmDeleteDocs}
      />
    </div>
  );
}
