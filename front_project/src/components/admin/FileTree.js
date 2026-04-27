import React, { useState, useMemo } from 'react';
import { http } from '../../services/http/client';

export default function FileTree({
  kbId,
  nodes,
  expanded,
  onSelect,    
  onToggle,      
  onSelectChange,
  selectedNodeId,
  fetchTree
}) {
  const [selectedIds, setSelectedIds] = useState(new Set());

  const parentMap = useMemo(() => {
    const map = new Map();
    const walk = (arr, parentId = null) => {
      (arr || []).forEach((n) => {
        const id = n.id;
        map.set(id, parentId);
        walk(n.children || [], id);
      });
    };
    walk(nodes, null);
    return map;
  }, [nodes]);

  const toggleSelect = (nodeId, e) => {
    if (e.ctrlKey || e.metaKey) {
      const next = new Set(selectedIds);
      if (next.has(nodeId)) next.delete(nodeId);
      else next.add(nodeId);
      setSelectedIds(next);
      if (onSelectChange) onSelectChange(next);
    } else {
      const next = new Set([nodeId]);
      setSelectedIds(next);
      if (onSelectChange) onSelectChange(next);
    }
  };

  const computeDragIds = (nodeId) => {
    if (selectedIds.has(nodeId) && selectedIds.size > 0) {
      return Array.from(selectedIds);
    }
    return [nodeId];
  };

  const onDragStartMulti = (e, nodeId) => {
    const ids = computeDragIds(nodeId);
    e.dataTransfer.setData('application/node-ids', JSON.stringify(ids));
    e.dataTransfer.setData('application/node-id', String(nodeId));
    try {
      e.dataTransfer.setData('text/plain', JSON.stringify({ type: 'node-ids', ids }));
    } catch {}
    e.stopPropagation();
  };

  const onBackgroundDrop = async (e) => {
    e.preventDefault();
    let draggedIds = [];
    try {
      draggedIds = JSON.parse(e.dataTransfer.getData('application/node-ids') || '[]');
      if ((!Array.isArray(draggedIds) || draggedIds.length === 0) && e.dataTransfer.types?.includes('text/plain')) {
        const txt = e.dataTransfer.getData('text/plain');
        const parsed = JSON.parse(txt || '{}');
        if (parsed?.type === 'node-ids' && Array.isArray(parsed?.ids)) draggedIds = parsed.ids;
      }
    } catch {}
    if (!Array.isArray(draggedIds) || draggedIds.length === 0) return;
    try {
      for (const id of draggedIds) {
        await http.patch(`/kb/trees/nodes/${id}`, { parent_id: null });
      }
    } catch (err) {
      console.error('root move error', err);
      window.alert(`Root move failed: ${err?.message || err}`);
    } finally {
      await fetchTree();
    }
  };

    return (
      <div
        style={{ paddingRight: 6, height: '100%' }}
        onClick={(e) => {
          const inRow = e.target.closest?.('.tree-row');
          if (!inRow) {
            const next = new Set();
            setSelectedIds(next);
            if (onSelectChange) onSelectChange(next);
          }
        }}
        onDragOver={(e) => e.preventDefault()}
        onDrop={onBackgroundDrop}
      >
      <ul className="tree">
        {nodes.map((node) => (
          <TreeNode
            key={node.id}
            node={node}
            kbId={kbId}
            expanded={expanded}
            onSelect={onSelect}
            onToggle={onToggle}
            selectedIds={selectedIds}
            fetchTree={fetchTree}
            onDragStartMulti={onDragStartMulti}
            parentId={null}
            parentMap={parentMap}
            toggleSelect={toggleSelect}
          />
        ))}
      </ul>
    </div>
  );
}

function TreeNode({
  node,
  kbId,
  expanded,
  onSelect,
  onToggle,
  selectedIds,
  fetchTree,
  onDragStartMulti,
  parentId,
  parentMap,
  toggleSelect
}) {
  const hasChildren = (node.children || []).length > 0;
  const isOpen = expanded.has(node.id);
  const isFolder = !!node.is_folder;
  const isSelected = selectedIds.has(node.id);

  const onDragOver = (e) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const isAncestor = (ancestorId, targetId) => {
    let cur = targetId;
    while (cur != null) {
      if (cur === ancestorId) return true;
      cur = parentMap.get(cur) ?? null;
    }
    return false;
  };

  const onDrop = async (e) => {
    e.preventDefault();
    e.stopPropagation();

    let draggedIds = [];
    try {
      draggedIds = JSON.parse(e.dataTransfer.getData('application/node-ids') || '[]');
      if ((!Array.isArray(draggedIds) || draggedIds.length === 0) && e.dataTransfer.types?.includes('text/plain')) {
        const txt = e.dataTransfer.getData('text/plain');
        const parsed = JSON.parse(txt || '{}');
        if (parsed?.type === 'node-ids' && Array.isArray(parsed?.ids)) draggedIds = parsed.ids;
      }
    } catch {}

    if (!Array.isArray(draggedIds) || draggedIds.length === 0) return;

    const targetParentId = isFolder ? node.id : parentId;

    for (const id of draggedIds) {
      if (id === targetParentId) {
        window.alert('Cannot be dropped on the same target.');
        return;
      }
      if (targetParentId != null && isAncestor(id, targetParentId)) {
        window.alert('You cannot move below yourself.');
        return;
      }
    }

    try {
      for (const id of draggedIds) {
        await http.patch(`/kb/trees/nodes/${id}`, { parent_id: targetParentId ?? null });
      }
    } catch (err) {
      console.error('DnD error', err);
      window.alert(`Drag and drop failes: ${err?.message || err}`);
    } finally {
      await fetchTree();
    }
  };

  return (
    <li>
      <div
        className="tree-row"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          padding: '2px 0',
          border: isSelected ? '1px solid var(--brand)' : '1px solid transparent',
          background: isSelected ? 'rgba(0,123,255,0.1)' : 'transparent',
          cursor: 'pointer',
        }}
        draggable
        onDragStart={(e) => onDragStartMulti(e, node.id)}
        onDragOver={onDragOver}
        onDrop={onDrop}
      >
        {isFolder ? (
          <button
            className="toggle"
            aria-label={isOpen ? 'collapse' : 'expand'}
            draggable={false}
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              onToggle?.(node);
            }}
          >
            {isOpen ? 'v' : '>'}
          </button>
        ) : (
          <span style={{ width: 16, display: 'inline-block' }} />
        )}

        <button
          className={isFolder ? 'tree-label group' : 'tree-label leaf'}
          onClick={(e) => {
            toggleSelect(node.id, e);
            onSelect?.(node);
          }}
          title={node.kind || ''}
          draggable={false}
          onMouseDown={(e) => e.stopPropagation()}
          style={{
            flex: 1,
            textAlign: 'left',
            background: 'none',
            border: 'none',
            color: isSelected ? 'var(--brand)' : 'inherit',
          }}
        >
          {node.name}
        </button>
      </div>

      {isFolder && isOpen && hasChildren && (
        <ul className="tree">
          {node.children.map((ch) => (
            <TreeNode
              key={ch.id}
              node={ch}
              kbId={kbId}
              expanded={expanded}
              onSelect={onSelect}
              onToggle={onToggle}
              selectedIds={selectedIds}
              fetchTree={fetchTree}
              onDragStartMulti={onDragStartMulti}
              parentId={node.id}
              parentMap={parentMap}
              toggleSelect={toggleSelect}
            />
          ))}
        </ul>
      )}
    </li>
  );
}
