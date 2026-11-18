import React, { useState } from 'react';

export default function FileTree({
  nodes,
  kbid,
  expanded,
  onSelect,    
  onToggle,      
  onSelectChange,
  onNodeContextMenu,
  onBlankContextMenu,
  onNodeMove,
}) {
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [draggedNode, setDraggedNode] = useState(null);
  const [dropTargetId, setDropTargetId] = useState(null);

  const finishDrag = () => {
    setDraggedNode(null);
    setDropTargetId(null);
  };

  const dropOnRoot = (e) => {
    if (!draggedNode || e.target.closest?.('.tree-row')) return;
    e.preventDefault();
    e.stopPropagation();
    const rootId = Number(kbid);
    if (!Number.isFinite(rootId)) return finishDrag();
    const source = draggedNode;
    finishDrag();
    onNodeMove?.(source, { id: rootId, name: 'KB root', is_folder: true });
  };

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

    return (
      <div
        style={{
          paddingRight: 6,
          height: '100%',
          boxShadow: dropTargetId === `root:${kbid}` ? 'inset 0 0 0 2px var(--brand)' : 'none',
        }}
        onClick={(e) => {
          const inRow = e.target.closest?.('.tree-row');
          if (!inRow) {
            const next = new Set();
            setSelectedIds(next);
            if (onSelectChange) onSelectChange(next);
          }
        }}
        onContextMenu={(e) => {
          const inRow = e.target.closest?.('.tree-row');
          if (inRow) return;
          e.preventDefault();
          e.stopPropagation();
          onBlankContextMenu?.(e);
        }}
        onDragOver={(e) => {
          if (!draggedNode || e.target.closest?.('.tree-row')) return;
          e.preventDefault();
          e.dataTransfer.dropEffect = 'move';
          setDropTargetId(`root:${kbid}`);
        }}
        onDrop={dropOnRoot}
      >
      {!nodes.length && <div className="empty" style={{ padding: 12 }}>Empty</div>}
      <ul className="tree">
        {nodes.map((node) => (
          <TreeNode
            key={node.id}
            node={node}
            expanded={expanded}
            onSelect={onSelect}
            onToggle={onToggle}
            selectedIds={selectedIds}
            toggleSelect={toggleSelect}
            onNodeContextMenu={onNodeContextMenu}
            onNodeMove={onNodeMove}
            draggedNode={draggedNode}
            setDraggedNode={setDraggedNode}
            dropTargetId={dropTargetId}
            setDropTargetId={setDropTargetId}
            finishDrag={finishDrag}
          />
        ))}
      </ul>
    </div>
  );
}

function TreeNode({
  node,
  expanded,
  onSelect,
  onToggle,
  selectedIds,
  toggleSelect,
  onNodeContextMenu,
  onNodeMove,
  draggedNode,
  setDraggedNode,
  dropTargetId,
  setDropTargetId,
  finishDrag,
}) {
  const hasChildren = (node.children || []).length > 0;
  const isOpen = expanded.has(node.id);
  const isFolder = !!node.is_folder;
  const isSelected = selectedIds.has(node.id);
  const docStatus = String(node?.doc_status || node?.meta?.doc_status || '').toLowerCase();
  const unavailable = !isFolder && (node?.disabled || ['deleted_pending', 'inactive_path_missing'].includes(docStatus));
  const unavailableLabel = docStatus === 'inactive_path_missing' ? 'Path missing' : 'Pending delete';

  return (
    <li>
      <div
        className="tree-row"
        draggable={!unavailable && Boolean(onNodeMove)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          padding: '2px 0',
          border: isSelected ? '1px solid var(--brand)' : '1px solid transparent',
          background: dropTargetId === node.id
            ? 'rgba(0,123,255,0.18)'
            : unavailable
              ? 'rgba(148,163,184,0.12)'
              : isSelected
                ? 'rgba(0,123,255,0.1)'
                : 'transparent',
          boxShadow: dropTargetId === node.id ? 'inset 0 0 0 2px var(--brand)' : 'none',
          cursor: unavailable ? 'not-allowed' : onNodeMove ? 'grab' : 'pointer',
          opacity: unavailable ? 0.72 : 1,
        }}
        onDragStart={(e) => {
          if (unavailable || !onNodeMove) {
            e.preventDefault();
            return;
          }
          e.dataTransfer.effectAllowed = 'move';
          e.dataTransfer.setData('text/plain', String(node.id));
          setDraggedNode(node);
        }}
        onDragEnd={finishDrag}
        onDragOver={(e) => {
          if (!isFolder || !draggedNode || draggedNode.id === node.id) return;
          e.preventDefault();
          e.stopPropagation();
          e.dataTransfer.dropEffect = 'move';
          setDropTargetId(node.id);
        }}
        onDragLeave={(e) => {
          if (e.currentTarget.contains(e.relatedTarget)) return;
          if (dropTargetId === node.id) setDropTargetId(null);
        }}
        onDrop={(e) => {
          if (!isFolder || !draggedNode || draggedNode.id === node.id) return;
          e.preventDefault();
          e.stopPropagation();
          const source = draggedNode;
          finishDrag();
          onNodeMove?.(source, node);
        }}
        onContextMenu={(e) => {
          e.preventDefault();
          e.stopPropagation();
          onNodeContextMenu?.(e, node);
        }}
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
            if (unavailable) {
              e.stopPropagation();
              return;
            }
            toggleSelect(node.id, e);
            onSelect?.(node);
          }}
          title={unavailable ? 'This document is unavailable until restored.' : node.kind || ''}
          disabled={unavailable}
          draggable={!unavailable && Boolean(onNodeMove)}
          onMouseDown={(e) => e.stopPropagation()}
          style={{
            flex: 1,
            textAlign: 'left',
            background: 'none',
            border: 'none',
            color: unavailable ? 'var(--disabled-txt, #9aa0a6)' : isSelected ? 'var(--brand)' : 'inherit',
            textDecoration: unavailable ? 'line-through' : 'none',
            cursor: unavailable ? 'not-allowed' : 'pointer',
          }}
        >
          {node.name}
          {unavailable && (
            <span
              style={{
                marginLeft: 8,
                padding: '1px 6px',
                borderRadius: 999,
                background: '#fff4d6',
                border: '1px solid #f4d77e',
                color: '#8a5a00',
                fontSize: 10,
                fontWeight: 700,
                whiteSpace: 'nowrap',
              }}
            >
              {unavailableLabel}
            </span>
          )}
        </button>
      </div>

      {isFolder && isOpen && hasChildren && (
        <ul className="tree">
          {node.children.map((ch) => (
            <TreeNode
              key={ch.id}
              node={ch}
              expanded={expanded}
              onSelect={onSelect}
              onToggle={onToggle}
              selectedIds={selectedIds}
              toggleSelect={toggleSelect}
              onNodeContextMenu={onNodeContextMenu}
              onNodeMove={onNodeMove}
              draggedNode={draggedNode}
              setDraggedNode={setDraggedNode}
              dropTargetId={dropTargetId}
              setDropTargetId={setDropTargetId}
              finishDrag={finishDrag}
            />
          ))}
        </ul>
      )}
    </li>
  );
}
