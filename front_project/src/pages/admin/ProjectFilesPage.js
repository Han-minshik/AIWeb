import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { http } from '../../services/http/client';
import {
  DOCUMENT_UPLOAD_ACCEPT,
  deleteDoc,
  planKiwixManifestSelection,
  uploadDocsToMinio,
} from '../../services/docs/docService';
import useDownload from '../../hooks/useDownload';

import FileTree from '../../components/admin/FileTree';
import BulkMoveModal from '../../components/admin/BulkMoveModal';
import BulkPermissionModal from '../../components/admin/BulkPermissionModal';
import FolderCleanupModal from '../../components/admin/FolderCleanupModal';
import KiwixUploadReportModal from '../../components/admin/KiwixUploadReportModal';
import DocDeleteModal from '../../components/admin/kb/DocDeleteModal';

import VersionManagerModal from '../../components/admin/kb/VersionManagerModal';


import AdminMaintenanceModal from '../../components/admin/kb/AdminMaintenanceModal';


const ITEM_TYPE_TOP_CATEGORY_ERROR =
  'item_type_code can only be changed on top-level category folders directly under the KB root.';

function kiwixUploadIssue(item) {
  const detail = item?.error?.detail;
  const structured = detail && typeof detail === 'object' ? detail : {};
  return {
    stage: structured.stage || 'upload',
    code: structured.code || (typeof detail === 'string' ? detail : null) || `HTTP_${item?.error?.status || 'ERROR'}`,
    fileName: structured.file_name || item?.file?.name || 'KIWIX Markdown',
    markdownPath: structured.markdown_path || '',
    referencePath: structured.reference_path || '',
    reason: structured.reason || item?.error?.userMessage || item?.error?.message || String(detail || 'Upload failed.'),
  };
}

function isTopCategoryRow(row) {
  if (!row?.is_folder) return false;
  const nodeId = Number(row.node_id ?? row.id);
  const parentId = Number(row.parent_id ?? row.parent);
  const kbRootId = Number(row.kb_root_id);
  return (
    Number.isFinite(nodeId) &&
    Number.isFinite(parentId) &&
    Number.isFinite(kbRootId) &&
    nodeId !== kbRootId &&
    parentId === kbRootId
  );
}

function buildUploadFolderOptions(rootId, rootName, nodes) {
  if (!Number.isFinite(rootId)) return [];
  const options = [{ id: rootId, label: rootName || `KB ${rootId}` }];
  const walk = (items, prefix = '') => {
    for (const node of items || []) {
      if (!node?.is_folder) continue;
      const label = prefix ? `${prefix} / ${node.name}` : String(node.name || 'Folder');
      const id = Number(node.node_id ?? node.id);
      if (Number.isFinite(id)) options.push({ id, label });
      walk(node.children, label);
    }
  };
  walk(nodes);
  return options;
}

function findTreeNode(nodes, nodeId) {
  for (const node of nodes || []) {
    if (Number(node?.node_id ?? node?.id) === Number(nodeId)) return node;
    const found = findTreeNode(node.children, nodeId);
    if (found) return found;
  }
  return null;
}

function directFolderMap(rootId, nodes, parentId) {
  const children = Number(parentId) === Number(rootId)
    ? (nodes || [])
    : (findTreeNode(nodes, parentId)?.children || []);
  return new Map(
    children
      .filter((node) => node?.is_folder)
      .map((node) => [
        String(node.name || node.alias || '').trim().toLocaleLowerCase(),
        Number(node.node_id ?? node.id),
      ])
  );
}



export default function ProjectFilesPage() {
  const { downloadLatest } = useDownload();

  const [q, setQ] = useState('');

  const [kbs, setKbs] = useState([]);

  const [kbId, setKbId] = useState(null);

  const [tree, setTree] = useState({ nodes: [] });

  const [expanded, setExpanded] = useState(() => new Set());

  const [selectedNodeId, setSelectedNodeId] = useState(null);

  const [docs, setDocs] = useState([]);

  const [loading, setLoading] = useState(false);

  const [selectedNodeIds, setSelectedNodeIds] = useState([]);

  const [kbMenuOpen, setKbMenuOpen] = useState(false);

  const kbMenuRef = useRef(null);
  const uploadInputRef = useRef(null);
  const kiwixFolderInputRef = useRef(null);
  const [uploadBranch, setUploadBranch] = useState('general');
  const [uploadParentId, setUploadParentId] = useState(null);
  const [uploadingFiles, setUploadingFiles] = useState(false);
  const [kiwixUploadReport, setKiwixUploadReport] = useState({ open: false, successCount: 0, issues: [] });



  const [selectedDocIds, setSelectedDocIds] = useState(() => new Set());

  const headerCheckRef = useRef(null);

  const [ctxMenu, setCtxMenu] = useState(null);
  const [bulkMoveModal, setBulkMoveModal] = useState({
    open: false,
    initialSelectedIds: [],
    initialTargetId: null,
  });
  const [folderCleanupId, setFolderCleanupId] = useState(null);
  const [docDeleteTarget, setDocDeleteTarget] = useState(null);
  const [deletingDocument, setDeletingDocument] = useState(false);
  const [docDeleteError, setDocDeleteError] = useState('');

  const [creatingFolder, setCreatingFolder] = useState(false);

  const [verModalOpen, setVerModalOpen] = useState(false);

  const [verDoc, setVerDoc] = useState(null);

  const [maintenanceModal, setMaintenanceModal] = useState({ open: false, tab: 'groups' });

  const [policyOptions, setPolicyOptions] = useState([]);

  const [groups, setGroups] = useState([]);

  const [permissionModal, setPermissionModal] = useState({
    open: false,
    initialFolderId: null,
    initialSelectedIds: [],
  });

  const [nodeEditModal, setNodeEditModal] = useState(null);

  const [nodeEditForm, setNodeEditForm] = useState({ value: '' });

  const [kbCreateModal, setKbCreateModal] = useState(false);

  const [kbCreateName, setKbCreateName] = useState('');

  const [kbDeleteTarget, setKbDeleteTarget] = useState(null);



  const parseId = (v) => {

    const n = Number.parseInt(String(v ?? '').trim(), 10);

    return Number.isFinite(n) ? n : null;

  };



  const getKbId = useCallback((kb) => parseId(kb?.kb_id ?? kb?.id), []);

  const getKbName = useCallback(

    (kb) => (kb?.name ? String(kb.name) : `KB ${getKbId(kb)}`),

    [getKbId]

  );

  const uploadFolderOptions = useMemo(() => {
    const selectedKb = kbs.find((kb) => getKbId(kb) === kbId);
    return buildUploadFolderOptions(kbId, selectedKb ? getKbName(selectedKb) : null, tree.nodes);
  }, [kbId, kbs, tree.nodes, getKbId, getKbName]);

  useEffect(() => {
    setUploadParentId((previous) =>
      uploadFolderOptions.some((folder) => folder.id === Number(previous))
        ? Number(previous)
        : kbId
    );
  }, [kbId, uploadFolderOptions]);



  const loadKbs = useCallback(async () => {

    try {

      const res = await http.get('/kb/trees', { kb_type: 'admin' });

      const items = Array.isArray(res) ? res : res?.items || [];
      const adminItems = items.filter((item) => String(item?.kb_type || item?.kbType || 'admin').toLowerCase() !== 'personal');

      setKbs(adminItems);

      setKbId((prev) => {

        if (Number.isFinite(prev) && adminItems.some((item) => getKbId(item) === prev)) return prev;

        const first = adminItems[0] ? getKbId(adminItems[0]) : null;

        return first;

      });

    } catch (e) {

      console.error('loadKbs error', e);

      setKbs([]);

      setKbId(null);

    }

  }, [getKbId]);



  const loadPolicyOptions = useCallback(async () => {

    try {

      const res = await http.get('/kb/perm-policies');

      setPolicyOptions(Array.isArray(res) ? res : res?.items || []);

    } catch (e) {

      console.error('loadPolicyOptions error', e);

      setPolicyOptions([]);

    }

  }, []);



  const loadGroups = useCallback(async () => {

    try {

      const res = await http.get('/admin/groups', { include_inactive: false });

      setGroups(Array.isArray(res) ? res : res?.items || []);

    } catch (e) {

      console.error('loadGroups error', e);

      setGroups([]);

    }

  }, []);



  const fetchTree = useCallback(async () => {

    if (!Number.isFinite(kbId)) {

      setTree({ nodes: [] });

      return;

    }

    try {

      const t = await http.get(`/kb/trees/${kbId}/tree`, { include_empty_folders: true, admin_view: true });

      const normalize = (node) => ({

        ...node,

        is_folder: Boolean(node.is_folder) || (node.node_type || '').toLowerCase().includes('folder'),

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

        admin_view: true,

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

    loadPolicyOptions();

    loadGroups();

  }, [loadPolicyOptions, loadGroups]);



  useEffect(() => {

    fetchTree();

  }, [fetchTree]);



  useEffect(() => {

    fetchDocs();

  }, [fetchDocs]);



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
    if (Number.isFinite(Number(parentForList))) setUploadParentId(Number(parentForList));

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



  const openKbCreateModal = useCallback(() => {

    setKbCreateName('');

    setKbCreateModal(true);

  }, []);



  const saveKbCreate = async () => {

    const name = String(kbCreateName || '').trim();

    if (!name) {

      window.alert('KB name is required.');

      return;

    }

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
        setUploadParentId(newId);

        setSelectedNodeId(null);

        setSelectedNodeIds([]);

        setExpanded(new Set());

      }

      setKbMenuOpen(false);

      setKbCreateModal(false);

    } catch (e) {

      console.error('create kb error', e);

      window.alert(`KB Create Failed: ${e?.message || e}`);

    }

  };



  const onDeleteKb = async (id) => {

    if (!Number.isFinite(id)) return;

    const target = kbs.find((k) => getKbId(k) === id);

    const name = target ? getKbName(target) : `#${id}`;

    setKbDeleteTarget({ id, name });

  };



  const confirmDeleteKb = async () => {

    const id = parseId(kbDeleteTarget?.id);

    if (!Number.isFinite(id)) return;

    try {

      await http.delete(`/kb/trees/${id}`);

      await loadKbs();

      if (id === kbId) {

        setKbId(null);

        setTree({ nodes: [] });

        setDocs([]);

      }

      setKbDeleteTarget(null);

    } catch (e) {

      console.error('delete kb error', e);

      window.alert(`KB Delete Failed: ${e?.message || e}`);

    }

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

        policy_code: d.policy_code || '',

        target_group_id: d.target_group_id ?? '',

        kb_type: d.kb_type || d.meta?.kb_type || '',

        upload_mode: d.upload_mode || d.meta?.upload_mode || '',

        sort_order: Number.isFinite(Number(d.sort_order)) ? Number(d.sort_order) : 0,

        item_type_code: Number.isFinite(Number(d.item_type_code)) ? Number(d.item_type_code) : null,

      })),

    [docs]

  );



  const currentKb = useMemo(

    () => kbs.find((k) => getKbId(k) === kbId) || null,

    [kbs, kbId, getKbId]

  );



  const groupNameById = useMemo(() => {

    const map = new Map();

    for (const g of groups || []) map.set(Number(g.group_id), g.name || `Group ${g.group_id}`);

    return map;

  }, [groups]);



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



  const refreshKbData = useCallback(async () => {

    await Promise.all([fetchTree(), fetchDocs()]);

  }, [fetchTree, fetchDocs]);

  const requestDocumentDeletion = useCallback((row) => {
    const docId = Number(row?.doc_id);
    if (!Number.isFinite(docId) || row?.is_folder) return;
    setCtxMenu(null);
    setDocDeleteError('');
    setDocDeleteTarget({
      docId,
      nodeId: Number(row?.id ?? row?.node_id),
      name: row?.name || row?.alias || `Document #${docId}`,
    });
  }, []);

  const confirmDocumentDeletion = useCallback(async () => {
    if (!Number.isFinite(docDeleteTarget?.docId) || deletingDocument) return;
    setDeletingDocument(true);
    setDocDeleteError('');
    try {
      await deleteDoc(docDeleteTarget.docId, 'all');
      setSelectedDocIds((previous) => {
        const next = new Set(previous);
        if (Number.isFinite(docDeleteTarget.nodeId)) next.delete(docDeleteTarget.nodeId);
        return next;
      });
      if (Number(selectedNodeId) === Number(docDeleteTarget.nodeId)) {
        setSelectedNodeId(null);
      }
      if (Number(verDoc?.id) === Number(docDeleteTarget.docId)) {
        setVerModalOpen(false);
        setVerDoc(null);
      }
      await refreshKbData();
      setDocDeleteTarget(null);
    } catch (error) {
      setDocDeleteError(
        error?.userMessage || error?.detail || error?.message || 'Document deletion failed.'
      );
    } finally {
      setDeletingDocument(false);
    }
  }, [
    docDeleteTarget,
    deletingDocument,
    refreshKbData,
    selectedNodeId,
    verDoc,
  ]);

  const moveAdminNode = useCallback(async (sourceNode, targetNode) => {
    const sourceId = Number(sourceNode?.id ?? sourceNode?.node_id);
    const targetId = Number(targetNode?.id ?? targetNode?.node_id);
    if (!Number.isFinite(sourceId) || !Number.isFinite(targetId) || sourceId === targetId) return;
    if (Number(sourceNode?.parent ?? sourceNode?.parent_id) === targetId) return;

    const sourceName = sourceNode?.name || `#${sourceId}`;
    const targetName = targetNode?.name || `#${targetId}`;
    const sourceType = sourceNode?.is_folder ? 'folder' : 'file';
    if (!window.confirm(`Move ${sourceType} "${sourceName}" into "${targetName}"?`)) return;

    try {
      await http.patch(`/kb/trees/nodes/${sourceId}`, { parent_id: targetId });
      if (targetId !== kbId) {
        setExpanded((previous) => new Set(previous).add(targetId));
      }
      const selectedFolderId = sourceNode?.is_folder ? sourceId : targetId;
      setSelectedNodeId(selectedFolderId);
      setUploadParentId(selectedFolderId);
      await refreshKbData();
    } catch (error) {
      window.alert(error?.userMessage || error?.message || 'Folder move failed.');
    }
  }, [kbId, refreshKbData]);

  const openBulkMoveModal = useCallback((contextRow = null) => {
    const selected = new Set([...selectedNodeIds, ...Array.from(selectedDocIds)]);
    if (contextRow && !contextRow.is_folder && contextRow.id != null) selected.add(Number(contextRow.id));

    const targetId = contextRow?.is_folder
      ? Number(contextRow.id)
      : Number(contextRow?.parent_id ?? selectedNodeId ?? kbId);

    setBulkMoveModal({
      open: true,
      initialSelectedIds: Array.from(selected),
      initialTargetId: Number.isFinite(targetId) ? targetId : kbId,
    });
    setCtxMenu(null);
  }, [selectedNodeIds, selectedDocIds, selectedNodeId, kbId]);

  const moveAdminFiles = useCallback(async (files, targetId) => {
    const destinationId = Number(targetId);
    if (!Number.isFinite(destinationId)) throw new Error('Select a destination folder.');

    const failed = [];
    let moved = 0;
    for (const file of files) {
      const id = Number(file?.id ?? file?.node_id);
      if (!Number.isFinite(id) || Number(file?.parent_id ?? file?.parent) === destinationId) continue;
      try {
        await http.patch(`/kb/trees/nodes/${id}`, { parent_id: destinationId });
        moved += 1;
      } catch (error) {
        failed.push(`${file?.name || `#${id}`}: ${error?.userMessage || error?.message || 'move failed'}`);
      }
    }

    setSelectedNodeId(destinationId);
    setUploadParentId(destinationId);
    if (destinationId !== kbId) {
      setExpanded((previous) => new Set(previous).add(destinationId));
    }
    setSelectedNodeIds([]);
    setSelectedDocIds(new Set());
    await refreshKbData();

    if (failed.length) {
      throw new Error(`${moved} file(s) moved, ${failed.length} failed.\n${failed.join('\n')}`);
    }
  }, [kbId, refreshKbData]);

  const uploadFiles = useCallback(async (event) => {
    const files = Array.from(event.target.files || []);
    event.target.value = '';
    if (!files.length || !Number.isFinite(kbId)) return;
    setUploadingFiles(true);
    try {
      const defaultParentId = Number.isFinite(Number(uploadParentId)) ? Number(uploadParentId) : kbId;
      let uploadList = files;
      let assetPool = files;
      let planningIssues = [];
      const parentNodeIdByFile = new Map();
      const kiwixMetadataByFile = new Map();
      const kiwixStoredFileNameByFile = new Map();

      if (String(uploadBranch).toLowerCase() === 'kiwix') {
        const plan = await planKiwixManifestSelection(files);
        if (plan.manifestFiles.length) {
          if (!plan.entries.length) {
            setKiwixUploadReport({
              open: true,
              successCount: 0,
              issues: plan.issues?.length ? plan.issues : [{
                stage: 'matching',
                code: 'NO_MANIFEST_FILES_MATCHED',
                fileName: 'documents.json',
                reason: 'No files in the selected folder matched the JSON manifests.',
              }],
            });
            return;
          }
          uploadList = plan.entries.map((entry) => entry.file);
          assetPool = plan.assetPool;
          planningIssues = plan.issues || [];

          let projectParentId = defaultParentId;
          if (defaultParentId === kbId) {
            const rootFolders = directFolderMap(kbId, tree.nodes, kbId);
            projectParentId = rootFolders.get('0. kiwix') ?? rootFolders.get('kiwix');
            if (!Number.isFinite(projectParentId)) {
              const createdKiwix = await http.post(`/kb/trees/${kbId}/nodes`, {
                name: '0. KIWIX',
                parent_id: kbId,
                node_type: 'KB_FOLDER',
              });
              projectParentId = Number(createdKiwix.node_id ?? createdKiwix.id);
            }
          }

          const folders = directFolderMap(kbId, tree.nodes, projectParentId);
          const projectCodes = [...new Set(plan.entries.map((entry) => entry.metadata.prjCode))];
          for (const projectCode of projectCodes) {
            const key = projectCode.toLocaleLowerCase();
            if (!folders.has(key)) {
              const created = await http.post(`/kb/trees/${kbId}/nodes`, {
                name: projectCode,
                parent_id: projectParentId,
                node_type: 'KB_FOLDER',
              });
              folders.set(key, Number(created.node_id ?? created.id));
            }
          }
          for (const entry of plan.entries) {
            parentNodeIdByFile.set(entry.file, folders.get(entry.metadata.prjCode.toLocaleLowerCase()));
            kiwixMetadataByFile.set(entry.file, entry.metadata);
            if (entry.storedFileName) {
              kiwixStoredFileNameByFile.set(entry.file, entry.storedFileName);
            }
          }
        }
      }

      const results = await uploadDocsToMinio(uploadList, {
        parent_node_id: defaultParentId,
        parentNodeIdByFile,
        branch_type: uploadBranch,
        kiwixAssetPool: assetPool,
        kiwixMetadataByFile,
        kiwixStoredFileNameByFile,
        concurrency: 3,
        onNameConflict: ({ fileName }) =>
          window.confirm(
            `A document named "${fileName}" already exists in this folder. Upload it as a new version?`
          ),
      });
      const failed = results.filter((item) => !item.success);
      const issues = [...planningIssues, ...failed.map(kiwixUploadIssue)];
      if (String(uploadBranch).toLowerCase() === 'kiwix') {
        setKiwixUploadReport({
          open: true,
          successCount: results.length - failed.length,
          issues,
        });
      } else if (failed.length) {
        window.alert(`${failed.length} files failed to upload.`);
      }
      await refreshKbData();
    } catch (error) {
      if (String(uploadBranch).toLowerCase() === 'kiwix') {
        setKiwixUploadReport({
          open: true,
          successCount: 0,
          issues: [kiwixUploadIssue({ file: null, error })],
        });
      } else {
        window.alert(error?.userMessage || error?.message || 'Upload failed.');
      }
    } finally {
      setUploadingFiles(false);
    }
  }, [kbId, uploadParentId, uploadBranch, refreshKbData, tree.nodes]);



  const normalizeContextRow = useCallback((node) => {

    if (!node) return null;

    const meta = node.meta || {};

    return {

      id: node.node_id ?? node.id,

      node_id: node.node_id ?? node.id,

      parent_id: node.parent_id ?? node.parent ?? meta.parent_id ?? null,

      kb_root_id: node.kb_root_id ?? meta.kb_root_id ?? null,

      doc_id: node.doc_id ?? meta.doc_id ?? meta.object_id ?? meta.storage_object_id ?? null,

      name: node.name,

      is_folder: Boolean(node.is_folder),

      sort_order: Number.isFinite(Number(node.sort_order)) ? Number(node.sort_order) : 0,

      item_type_code: Number.isFinite(Number(node.item_type_code ?? meta.item_type_code))

        ? Number(node.item_type_code ?? meta.item_type_code)

        : null,

      policy_code: node.policy_code || '',

      target_group_id: node.target_group_id ?? meta.target_group_id ?? '',

      kb_type: node.kb_type ?? meta.kb_type ?? '',

      upload_mode: meta.upload_mode ?? node.upload_mode ?? '',

      rwx: Array.isArray(node.policy_matrix) ? node.policy_matrix : null,

      children: Array.isArray(node.children) ? node.children : [],

    };

  }, []);



  const openNodeContextMenu = useCallback((event, node) => {

    const row = normalizeContextRow(node);

    if (!row?.id) return;

    setCtxMenu({

      x: event.clientX,

      y: event.clientY,

      row,

      source: 'tree-node',

    });

  }, [normalizeContextRow]);

  const openTreeBlankContextMenu = useCallback((event) => {
    if (!Number.isFinite(kbId)) return;
    setCtxMenu({
      x: event.clientX,
      y: event.clientY,
      row: null,
      source: 'tree-root',
    });
  }, [kbId]);

  const createAdminFolder = useCallback(async (parentId) => {
    if (!Number.isFinite(kbId) || creatingFolder) return;
    const name = window.prompt('New folder name');
    if (!name?.trim()) return;
    const targetParentId = Number.isFinite(Number(parentId)) ? Number(parentId) : kbId;
    setCreatingFolder(true);
    setCtxMenu(null);
    try {
      await http.post(`/kb/trees/${kbId}/nodes`, {
        name: name.trim(),
        parent_id: targetParentId,
        node_type: 'KB_FOLDER',
      });
      if (targetParentId !== kbId) {
        setExpanded((previous) => new Set(previous).add(targetParentId));
      }
      await fetchTree();
    } catch (error) {
      window.alert(error?.userMessage || error?.message || 'Folder creation failed.');
    } finally {
      setCreatingFolder(false);
    }
  }, [kbId, creatingFolder, fetchTree]);

  const deleteAdminNodes = useCallback(async (nodes) => {
    const failures = [];
    for (const node of nodes || []) {
      const nodeId = Number(node?.id ?? node?.node_id ?? node);
      if (!Number.isFinite(nodeId)) continue;
      try {
        await http.post(`/kb/trees/${kbId}/nodes/bulk-delete`, { node_ids: [nodeId] });
      } catch (error) {
        failures.push(`${node?.name || String(nodeId)}: ${error?.userMessage || error?.message || 'delete failed'}`);
      }
    }
    await refreshKbData();
    if (failures.length) throw new Error(failures.join('\n'));
  }, [kbId, refreshKbData]);

  const requestFolderDeletion = useCallback(async (row) => {
    const folderId = Number(row?.id ?? row?.node_id);
    if (!Number.isFinite(folderId) || !row?.is_folder) return;
    setCtxMenu(null);

    if (Array.isArray(row.children) && row.children.length > 0) {
      setFolderCleanupId(folderId);
      return;
    }

    if (!window.confirm(`Delete empty folder "${row.name || String(folderId)}"?`)) return;
    try {
      await deleteAdminNodes([{ ...row, id: folderId }]);
      if (Number(selectedNodeId) === folderId) setSelectedNodeId(null);
    } catch (error) {
      window.alert(error?.userMessage || error?.message || 'Folder deletion failed.');
    }
  }, [deleteAdminNodes, selectedNodeId]);



  const openNodeEditModal = useCallback((type, row) => {

    if (!row?.id) return;

    if (type === 'item_type_code' && !isTopCategoryRow(row)) {

      window.alert(ITEM_TYPE_TOP_CATEGORY_ERROR);

      return;

    }

    const value =

      type === 'rename'

        ? row.name || ''

        : type === 'sort_order'

        ? String(row.sort_order ?? 0)

        : row.item_type_code != null

        ? String(row.item_type_code)

        : '';

    setNodeEditModal({ type, row });

    setNodeEditForm({ value });

  }, []);



  const closeNodeEditModal = useCallback(() => {

    setNodeEditModal(null);

    setNodeEditForm({ value: '' });

  }, []);



  const saveNodeEdit = useCallback(async () => {

    if (!nodeEditModal?.row?.id) return;

    const { type, row } = nodeEditModal;

    const raw = String(nodeEditForm.value ?? '').trim();

    let payload = null;

    if (type === 'rename') {

      if (!raw) {

        window.alert('Display name is required.');

        return;

      }

      payload = { name: raw };

    } else if (type === 'sort_order') {

      const sortOrder = Number.parseInt(raw, 10);

      if (!Number.isFinite(sortOrder)) {

        window.alert('sort_order must be a number.');

        return;

      }

      payload = { sort_order: sortOrder };

    } else if (type === 'item_type_code') {

      if (!isTopCategoryRow(row)) {

        window.alert(ITEM_TYPE_TOP_CATEGORY_ERROR);

        return;

      }

      const itemTypeCode = Number.parseInt(raw, 10);

      if (!Number.isFinite(itemTypeCode)) {

        window.alert('item_type_code must be a number.');

        return;

      }

      payload = { item_type_code: itemTypeCode };

    }

    if (!payload) return;

    try {

      await http.patch(`/kb/trees/nodes/${row.id}`, payload);

      closeNodeEditModal();

      await refreshKbData();

    } catch (e) {

      window.alert(`Update failed: ${e?.message || e}`);

    }

  }, [nodeEditModal, nodeEditForm, closeNodeEditModal, refreshKbData]);



  const openPermissionModal = useCallback((row = null) => {
    if (
      row &&
      (String(row.kb_type || '').toLowerCase() === 'personal' ||
        String(row.upload_mode || '').toLowerCase() === 'personal')
    ) {
      window.alert('Personal KB documents always use PRIVATE permissions.');
      return;
    }

    const initialFolderId = row?.is_folder
      ? Number(row.id)
      : Number(row?.parent_id ?? selectedNodeId ?? kbId);
    const selectedIds = new Set(Array.from(selectedDocIds).map(Number));
    if (row && !row.is_folder && Number.isFinite(Number(row.id))) {
      selectedIds.add(Number(row.id));
    }
    void loadGroups();
    setPermissionModal({
      open: true,
      initialFolderId: Number.isFinite(initialFolderId) ? initialFolderId : kbId,
      initialSelectedIds: Array.from(selectedIds),
    });
  }, [kbId, loadGroups, selectedDocIds, selectedNodeId]);

  const applyBulkPermission = useCallback(async (payload) => {
    const result = await http.post(`/kb/trees/${kbId}/permissions:bulk`, payload);
    setSelectedDocIds(new Set());
    await refreshKbData();
    if (Number(result?.skipped || 0) > 0) {
      window.alert(`${result.updated || 0} document(s) updated; ${result.skipped} skipped.`);
    }
  }, [kbId, refreshKbData]);

  const downloadAdminDocument = async (row) => {
    const docId = Number(row?.doc_id);
    setCtxMenu(null);
    if (!Number.isFinite(docId)) {
      window.alert('This document cannot be downloaded because its document ID is missing.');
      return;
    }
    try {
      await downloadLatest(docId);
    } catch (error) {
      window.alert(error?.userMessage || error?.message || 'Download failed.');
    }
  };



  return (

    <div className="panel" style={{ height: '100%' }}>

      <div className="panel-head kb-management-head">

        <div className="kb-management-title">

          <div className="panel-title">KB Management</div>

          <div className="kb-management-subtitle">Manage MinIO documents, groups, deleted docs, and AI indexing.</div>

        </div>

        <div className="panel-actions kb-management-toolbar">

          <div className="kb-toolbar-group kb-toolbar-admin">

          <select
            className='input'
            value={uploadParentId ?? kbId ?? ''}
            onChange={(event) => setUploadParentId(Number(event.target.value))}
            aria-label='Upload folder'
            disabled={!Number.isFinite(kbId) || uploadingFiles}
          >
            {uploadFolderOptions.map((folder) => (
              <option key={folder.id} value={folder.id}>{folder.label}</option>
            ))}
          </select>
          <select
            className="input"
            value={uploadBranch}
            onChange={(event) => setUploadBranch(event.target.value)}
            aria-label="Upload branch"
          >
            <option value="general">GENERAL</option>
            <option value="kiwix">KIWIX</option>
          </select>
          <input ref={uploadInputRef} type="file" accept={DOCUMENT_UPLOAD_ACCEPT} multiple hidden onChange={uploadFiles} />
          <input
            ref={kiwixFolderInputRef}
            type="file"
            multiple
            webkitdirectory=""
            directory=""
            hidden
            onChange={uploadFiles}
          />
          <button
            type="button"
            className="btn kb-tool-btn"
            onClick={() => uploadInputRef.current?.click()}
            disabled={!Number.isFinite(kbId) || uploadingFiles}
          >
            {uploadingFiles ? 'Uploading...' : 'Upload files'}
          </button>
          {uploadBranch === 'kiwix' && (
            <button
              type="button"
              className="btn kb-tool-btn"
              onClick={() => kiwixFolderInputRef.current?.click()}
              disabled={!Number.isFinite(kbId) || uploadingFiles}
              title="Select the folder containing project JSON manifests and project folders"
            >
              Import KIWIX folder
            </button>
          )}

          <button type="button" className="btn kb-tool-btn maintenance" onClick={() => setMaintenanceModal({ open: true, tab: 'groups' })}>

            Maintenance

          </button>

          <button
            type="button"
            className="btn kb-tool-btn"
            onClick={() => openPermissionModal(null)}
            disabled={!Number.isFinite(kbId)}
          >
            Bulk permissions
          </button>

          </div>

          <div className="kb-toolbar-group kb-toolbar-query">

          <input

            className="input kb-search-input"

            placeholder="Search"

            value={q}

            onChange={(e) => setQ(e.target.value)}

            onKeyDown={(e) => {

              if (e.key === 'Enter') fetchDocs();

            }}

          />



          <div ref={kbMenuRef} className="kb-select-wrap">

            <button

              type="button"

              className="input kb-select-button"

              onClick={() => setKbMenuOpen((v) => !v)}

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
                          setUploadParentId(id);

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

                      </div>

                    );

                  })

                )}

              </div>

            )}

          </div>



          </div>

          {loading && <div className="loading">Loading...</div>}

        </div>

      </div>



      <div className="panel-body split">

        <div className="card scroll tree-area">

          {!Number.isFinite(kbId) ? (

            <div className="empty" style={{ padding: 12 }}>

              Please select KB.

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

              onNodeContextMenu={openNodeContextMenu}

              onBlankContextMenu={openTreeBlankContextMenu}

              onNodeMove={moveAdminNode}

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

                <th style={{ width: 92 }}>Sort</th>

                <th style={{ width: 112 }}>Type Code</th>

                <th>Upload</th>

                <th>Update</th>

                <th style={{ width: 120 }}>Policy</th>

                <th>Permissions(Owner/Group/Other)</th>

              </tr>

            </thead>

            <tbody>

              {tableRows.length === 0 ? (

                <tr>

                  <td colSpan={8} className="empty">

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

                        source: 'table-row',

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

                    <td>{r.sort_order}</td>

                    <td>{r.item_type_code ?? '-'}</td>

                    <td className="nowrap">{r.uploaded}</td>

                    <td className="nowrap">{r.modified}</td>

                    <td title={r.target_group_id ? groupNameById.get(Number(r.target_group_id)) || '' : ''}>

                      {r.policy_code || '-'}

                      {r.target_group_id ? ` / ${groupNameById.get(Number(r.target_group_id)) || `#${r.target_group_id}`}` : ''}

                    </td>

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

          style={{
            position: 'fixed',
            top: Math.max(8, Math.min(ctxMenu.y, (typeof window !== 'undefined' ? window.innerHeight : 720) - 360)),
            left: Math.max(8, Math.min(ctxMenu.x, (typeof window !== 'undefined' ? window.innerWidth : 1280) - 260)),
            zIndex: 100000,
            maxHeight: 'calc(100vh - 16px)',
            overflowY: 'auto',
          }}

        >

          <button
            type="button"
            className="ctx-item"
            onClick={() => openBulkMoveModal(ctxMenu.row)}
          >
            Move files...
          </button>

          {ctxMenu.source === 'tree-root' && (
            <button
              type="button"
              className="ctx-item"
              disabled={creatingFolder}
              onClick={() => createAdminFolder(kbId)}
            >
              New folder
            </button>
          )}

          {ctxMenu.source === 'tree-node' && ctxMenu.row?.is_folder && (
          <>
            <button
              type="button"
              className="ctx-item"
              disabled={creatingFolder}
              onClick={() => createAdminFolder(Number(ctxMenu.row.id))}
            >
              New subfolder
            </button>
            <button
              type="button"
              className="ctx-item danger"
              onClick={() => requestFolderDeletion(ctxMenu.row)}
            >
              Delete folder
            </button>
          </>
          )}

          {ctxMenu.row && (
          <>

          <button

            type="button"

            className="ctx-item"

            onClick={() => {

              openNodeEditModal('rename', ctxMenu.row);

              setCtxMenu(null);

            }}

          >

            Rename display name
          </button>

          <button

            type="button"

            className="ctx-item"

            onClick={() => {

              openNodeEditModal('sort_order', ctxMenu.row);

              setCtxMenu(null);

            }}

          >

            Set sort_order
          </button>

          {isTopCategoryRow(ctxMenu.row) && (

          <button

            type="button"

            className="ctx-item"

            onClick={() => {

              openNodeEditModal('item_type_code', ctxMenu.row);

              setCtxMenu(null);

            }}

          >

            Change item_type_code
          </button>

          )}

          {String(ctxMenu.row?.kb_type || '').toLowerCase() !== 'personal' &&
            String(ctxMenu.row?.upload_mode || '').toLowerCase() !== 'personal' && (

          <button

            type="button"

            className="ctx-item"

            onClick={() => {

              openPermissionModal(ctxMenu.row);

              setCtxMenu(null);

            }}

          >

            {ctxMenu.row?.is_folder ? 'Change permissions in folder' : 'Change permission'}
          </button>

          )}

          {!ctxMenu.row?.is_folder && (

          <button

            type="button"

            className="ctx-item"

            onClick={() => downloadAdminDocument(ctxMenu.row)}

          >

            Download original
          </button>

          )}

          {!ctxMenu.row?.is_folder && (

          <button

            type="button"

            className="ctx-item"

            onClick={() => {

              openVersionModal(ctxMenu.row);

              setCtxMenu(null);

            }}

          >

            Version Management
          </button>

          )}

          {!ctxMenu.row?.is_folder && (

          <button

            type="button"

            className="ctx-item danger"

            onClick={() => requestDocumentDeletion(ctxMenu.row)}

          >

            Delete document
          </button>

          )}

          </>
          )}

        </div>

      )}

      <DocDeleteModal
        open={Boolean(docDeleteTarget)}
        documentName={docDeleteTarget?.name}
        busy={deletingDocument}
        error={docDeleteError}
        onClose={() => {
          if (deletingDocument) return;
          setDocDeleteTarget(null);
          setDocDeleteError('');
        }}
        onConfirm={confirmDocumentDeletion}
      />

      <KiwixUploadReportModal
        report={kiwixUploadReport}
        onClose={() => setKiwixUploadReport({ open: false, successCount: 0, issues: [] })}
      />


      <BulkMoveModal
        open={bulkMoveModal.open}
        nodes={tree.nodes}
        kbId={kbId}
        kbName={currentKb ? getKbName(currentKb) : 'KB root'}
        initialSelectedIds={bulkMoveModal.initialSelectedIds}
        initialTargetId={bulkMoveModal.initialTargetId}
        onClose={() => setBulkMoveModal({ open: false, initialSelectedIds: [], initialTargetId: null })}
        onMove={moveAdminFiles}
      />

      <FolderCleanupModal
        open={Number.isFinite(folderCleanupId)}
        folderId={folderCleanupId}
        nodes={tree.nodes}
        onClose={() => setFolderCleanupId(null)}
        onDeleteItems={deleteAdminNodes}
        onDeleteFolder={async (folderId) => {
          await deleteAdminNodes([{ id: folderId, name: 'Folder' }]);
          if (Number(selectedNodeId) === Number(folderId)) setSelectedNodeId(null);
        }}
        onRefresh={refreshKbData}
      />


      {kbCreateModal && (

        <div className="modal-backdrop" role="presentation">

          <div className="modal" role="dialog" aria-modal="true" aria-label="Create KB">

            <div className="modal-head">

              <div className="modal-title">Create KB</div>

              <button type="button" className="modal-close" onClick={() => setKbCreateModal(false)} aria-label="Close">

                X

              </button>

            </div>

            <div className="modal-body">

              <div className="modal-form">

                <label className="modal-field">

                  <span className="modal-label">KB name</span>

                  <input

                    className="input"

                    value={kbCreateName}

                    onChange={(e) => setKbCreateName(e.target.value)}

                    autoFocus

                  />

                </label>

              </div>

            </div>

            <div className="modal-foot">

              <button type="button" className="btn ghost" onClick={() => setKbCreateModal(false)}>

                Cancel

              </button>

              <button type="button" className="btn primary" onClick={saveKbCreate} disabled={!String(kbCreateName || '').trim()}>

                Create

              </button>

            </div>

          </div>

        </div>

      )}


      {kbDeleteTarget && (

        <div className="modal-backdrop" role="presentation">

          <div className="modal" role="dialog" aria-modal="true" aria-label="Delete KB">

            <div className="modal-head">

              <div className="modal-title">Delete KB</div>

              <button type="button" className="modal-close" onClick={() => setKbDeleteTarget(null)} aria-label="Close">

                X

              </button>

            </div>

            <div className="modal-body">

              <div className="modal-form">

                <div className="modal-target-card">

                  <div className="modal-kicker">Target KB</div>

                  <div className="modal-target-title">{kbDeleteTarget.name}</div>

                  <div className="modal-help warning">

                    Are you sure you want to delete this KB? This action is irreversible.

                  </div>

                </div>

              </div>

            </div>

            <div className="modal-foot">

              <button type="button" className="btn ghost" onClick={() => setKbDeleteTarget(null)}>

                Cancel

              </button>

              <button type="button" className="btn danger" onClick={confirmDeleteKb}>

                Delete

              </button>

            </div>

          </div>

        </div>

      )}


      {nodeEditModal && (

        <div className="modal-backdrop" role="presentation">

          <div className="modal" role="dialog" aria-modal="true" aria-label="Edit node">

            <div className="modal-head">

              <div className="modal-title">

                {nodeEditModal.type === 'rename'

                  ? 'Rename Display Name'

                  : nodeEditModal.type === 'sort_order'

                  ? 'Set Sort Order'

                  : 'Change item_type_code'}

              </div>

              <button type="button" className="modal-close" onClick={closeNodeEditModal} aria-label="Close">

                X

              </button>

            </div>

            <div className="modal-body">

              <div className="modal-form">

                <div className="modal-target-card">

                  <div className="modal-kicker">Target</div>

                  <div className="modal-target-title">

                    {nodeEditModal.row?.is_folder ? 'Folder' : 'Document'}: {nodeEditModal.row?.name}

                  </div>

                  {nodeEditModal.type === 'item_type_code' && (

                    <div className="modal-help">

                      Only top-level category folders directly under the KB root can change item_type_code. Child folders and documents inherit the nearest top-level category code.

                    </div>

                  )}

                </div>

                <label className="modal-field">

                  <span className="modal-label">

                    {nodeEditModal.type === 'rename'

                      ? 'Display name'

                      : nodeEditModal.type === 'sort_order'

                      ? 'sort_order'

                      : 'item_type_code'}

                  </span>

                  <input

                    className="input"

                    type={nodeEditModal.type === 'rename' ? 'text' : 'number'}

                    value={nodeEditForm.value}

                    onChange={(e) => setNodeEditForm({ value: e.target.value })}

                    autoFocus

                  />

                </label>

              </div>

            </div>

            <div className="modal-foot">

              <button type="button" className="btn ghost" onClick={closeNodeEditModal}>

                Cancel

              </button>

              <button type="button" className="btn primary" onClick={saveNodeEdit}>

                Save

              </button>

            </div>

          </div>

        </div>

      )}


      <BulkPermissionModal
        open={permissionModal.open}
        kbId={kbId}
        kbName={currentKb ? getKbName(currentKb) : 'KB root'}
        nodes={tree.nodes}
        policies={policyOptions}
        groups={groups}
        initialFolderId={permissionModal.initialFolderId}
        initialSelectedIds={permissionModal.initialSelectedIds}
        onClose={() => setPermissionModal({ open: false, initialFolderId: null, initialSelectedIds: [] })}
        onApply={applyBulkPermission}
      />



      <VersionManagerModal

        open={verModalOpen}

        docId={verDoc?.id}

        docTitle={verDoc?.title}

        onClose={() => setVerModalOpen(false)}

      />

      <AdminMaintenanceModal

        open={maintenanceModal.open}

        initialTab={maintenanceModal.tab}

        onClose={() => setMaintenanceModal({ open: false, tab: 'groups' })}

      />

    </div>

  );

}
