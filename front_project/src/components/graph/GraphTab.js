import React, { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import GraphFilter from './GraphFilter';
import GraphFilterModal from './GraphFilterModal';
import {
  fetchGraphData,
  fetchSearchTermData,
  fetchHopAnalysisData,
} from '../../services/graph/graphService';
import './GraphTab.css';

function buildVisGraph(graphData = [], searchedTerm = '', theme) {
  const nodes = [];
  const links = [];
  const nodeIds = new Set();
  const edgeSet = new Set();
  const normalizedSearch = searchedTerm.trim().toLowerCase();
  const colors = theme || {};

  const addNode = (node) => {
    if (!nodeIds.has(node.id)) {
      nodes.push(node);
      nodeIds.add(node.id);
    }
  };

  const addLink = (source, target, label) => {
    if (!source || !target) return;
    const key = `${source}->${target}->${label}`;
    if (edgeSet.has(key)) return;
    links.push({ source, target, label });
    edgeSet.add(key);
  };

  graphData.forEach((record) => {
    const category = record.c || record.category;
    const document = record.d || record.document;
    const chunk = record.ch || record.chunk;
    const term = record.t || record.term;

    if (term && term.term) {
      const termName = term.term;
      const isSearchedTerm =
        normalizedSearch && termName.toLowerCase() === normalizedSearch;
      const nid = `term:${termName}`;
      addNode({
        id: nid,
        label: termName,
        group: 'Term',
        color: isSearchedTerm ? colors.nodeTermActive : colors.nodeTerm,
        size: isSearchedTerm ? 10 : 8,
        meta: term,
      });
    }

    if (category && category.name) {
      const catName = category.name;
      const nid = `cat:${catName}`;
      addNode({
        id: nid,
        label: catName,
        group: 'Category',
        color: colors.nodeCategory,
        size: 8,
        meta: category,
      });
    }

    if (document && document.doc_id !== undefined && document.doc_id !== null) {
      const docId = String(document.doc_id);
      const docTitle = document.doc_title || docId;
      const nid = `doc:${docId}`;
      addNode({
        id: nid,
        label: docTitle,
        group: 'Document',
        color: colors.nodeDocument,
        size: 8,
        meta: document,
      });
    }

    if (chunk && chunk.id) {
      const chunkId = chunk.id;
      const nid = `ch:${chunkId}`;
      addNode({
        id: nid,
        label: chunkId,
        group: 'Chunk',
        color: colors.nodeChunk,
        size: 8,
        meta: chunk,
      });
    }

    if (
      category &&
      category.name &&
      document &&
      document.doc_id !== undefined &&
      document.doc_id !== null
    ) {
      addLink(`cat:${category.name}`, `doc:${String(document.doc_id)}`, 'CONTAINS');
    }

    if (
      document &&
      document.doc_id !== undefined &&
      document.doc_id !== null &&
      chunk &&
      chunk.id
    ) {
      addLink(`doc:${String(document.doc_id)}`, `ch:${chunk.id}`, 'HAS_CHUNK');
    }

    if (chunk && chunk.id && term && term.term) {
      addLink(`ch:${chunk.id}`, `term:${term.term}`, 'HAS_TERM');
    }

    if (
      document &&
      document.doc_id !== undefined &&
      document.doc_id !== null &&
      term &&
      term.term
    ) {
      addLink(`doc:${String(document.doc_id)}`, `term:${term.term}`, 'HAS_TERM');
    }

    if (category && category.name && term && term.term) {
      addLink(`cat:${category.name}`, `term:${term.term}`, 'HAS_TERM');
    }
  });

  return { nodes, links };
}

function buildGraphSortList(graphSortValue) {
  if (!graphSortValue) return [];
  return graphSortValue
    .split('-')
    .map((v) => v.trim())
    .filter((v) => v.length > 0);
}

function appendListParams(params, key, value) {
  if (!value) return;
  const items = value
    .split(',')
    .map((v) => v.trim())
    .filter((v) => v.length > 0);
  items.forEach((v) => params.append(key, v));
}

export default function GraphTab() {
  const containerRef = useRef(null);
  const fgRef = useRef(null);
  const resizeObserverRef = useRef(null);
  const zoomQueuedRef = useRef(false);
  const [themeKey, setThemeKey] = useState(0);

  const [graphSort, setGraphSort] = useState('category-document-chunk-term');
  const [category, setCategory] = useState('');
  const [docId, setDocId] = useState('');
  const [searchTerm, setSearchTerm] = useState('');
  const [nodes, setNodes] = useState([]);
  const [links, setLinks] = useState([]);
  const [isFilterModalOpen, setIsFilterModalOpen] = useState(false);

  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [dims, setDims] = useState({ width: 0, height: 0 });

  const getThemeColors = useCallback(() => {
    const el = containerRef.current || document.documentElement;
    const styles = el ? getComputedStyle(el) : null;
    const readVar = (name, fallback) => {
      if (!styles) return fallback;
      const value = styles.getPropertyValue(name);
      return value ? value.trim() : fallback;
    };
    return {
      nodeTerm: readVar('--graph-node-term', '#FAFD4F'),
      nodeTermActive: readVar('--graph-node-term-active', '#FFA500'),
      nodeCategory: readVar('--graph-node-category', '#FF6B8E'),
      nodeDocument: readVar('--graph-node-document', '#56F563'),
      nodeChunk: readVar('--graph-node-chunk', '#1139EB'),
      nodeDefault: readVar('--graph-node-default', '#999999'),
      nodeSelected: readVar('--graph-node-selected', '#ff4d4f'),
      linkDefault: readVar('--graph-link-default', '#888888'),
      linkActive: readVar('--graph-link-active', '#ff4d4f'),
    };
  }, []);

  const themeColors = useMemo(() => getThemeColors(), [getThemeColors, themeKey]);

  const loadGraph = useCallback(async () => {
    const params = new URLSearchParams();
    const sortList = buildGraphSortList(graphSort);
    sortList.forEach((s) => params.append('graph_sort', s));
    appendListParams(params, 'cat', category);
    appendListParams(params, 'doc_id', docId);

    const data = await fetchGraphData(params);
    const { nodes: builtNodes, links: builtLinks } = buildVisGraph(
      data.graph_data || [],
      '',
      themeColors
    );
    setNodes(builtNodes);
    setLinks(builtLinks);
    setSelectedNodeId(null);
  }, [graphSort, category, docId, themeColors]);

  const searchGraph = useCallback(async () => {
    if (!searchTerm.trim()) return;

    const params = new URLSearchParams();
    params.append('search_term', searchTerm);
    const sortList = buildGraphSortList(graphSort);
    sortList.forEach((s) => params.append('graph_sort', s));
    appendListParams(params, 'cat', category);
    appendListParams(params, 'doc_id', docId);

    const data = await fetchSearchTermData(params);
    const { nodes: builtNodes, links: builtLinks } = buildVisGraph(
      data.graph_data || [],
      searchTerm,
      themeColors
    );
    setNodes(builtNodes);
    setLinks(builtLinks);
    setSelectedNodeId(null);
  }, [searchTerm, graphSort, category, docId, themeColors]);

  const handleSearchClick = useCallback(() => {
    if (searchTerm.trim()) {
      searchGraph();
    } else {
      loadGraph();
    }
  }, [searchTerm, searchGraph, loadGraph]);

  const hopAnalysis = useCallback(
    async (nodeId, nodeType, meta) => {
      let startId = nodeId;
      if (meta) {
        if (nodeType === 'Term' && meta.term) {
          startId = meta.term;
        } else if (
          nodeType === 'Document' &&
          meta.doc_id !== undefined &&
          meta.doc_id !== null
        ) {
          startId = String(meta.doc_id);
        } else if (nodeType === 'Chunk' && meta.id) {
          startId = meta.id;
        } else if (nodeType === 'Category' && meta.name) {
          startId = meta.name;
        }
      }

      const params = new URLSearchParams();
      params.append('start_node_id', startId);
      params.append('start_node_type', nodeType);
      appendListParams(params, 'cat', category);
      appendListParams(params, 'doc_id', docId);

      const data = await fetchHopAnalysisData(params);
      const { nodes: builtNodes, links: builtLinks } = buildVisGraph(
        data.graph_data || [],
        searchTerm,
        themeColors
      );
      setNodes(builtNodes);
      setLinks(builtLinks);
    },
    [category, docId, searchTerm, themeColors]
  );

  useEffect(() => {
    if (!containerRef.current) return;

    const el = containerRef.current;

    const updateDims = () => {
      const rect = el.getBoundingClientRect();
      const width = Math.max(0, Math.floor(rect.width));
      const height = Math.max(0, Math.floor(rect.height));
      setDims((prev) =>
        prev.width === width && prev.height === height ? prev : { width, height }
      );
    };

    updateDims();

    let observer = null;
    if (typeof window !== 'undefined' && 'ResizeObserver' in window) {
      observer = new ResizeObserver(() => updateDims());
      observer.observe(el);
      resizeObserverRef.current = observer;
    }

    window.addEventListener('resize', updateDims);

    return () => {
      window.removeEventListener('resize', updateDims);
      if (observer) {
        observer.unobserve(el);
        observer.disconnect();
      }
      resizeObserverRef.current = null;
    };
  }, []);

  useEffect(() => {
    const handleThemeChange = () => {
      setThemeKey((prev) => prev + 1);
    };
    window.addEventListener('ui:theme-change', handleThemeChange);
    return () => window.removeEventListener('ui:theme-change', handleThemeChange);
  }, []);

  useEffect(() => {
    if (!nodes.length) return;
    const normalizedSearch = searchTerm.trim().toLowerCase();
    setNodes((prev) =>
      prev.map((node) => {
        if (!node || !node.group) return node;
        if (node.group === 'Term') {
          const isActive =
            normalizedSearch && String(node.label || '').toLowerCase() === normalizedSearch;
          return {
            ...node,
            color: isActive ? themeColors.nodeTermActive : themeColors.nodeTerm,
          };
        }
        if (node.group === 'Category') {
          return { ...node, color: themeColors.nodeCategory };
        }
        if (node.group === 'Document') {
          return { ...node, color: themeColors.nodeDocument };
        }
        if (node.group === 'Chunk') {
          return { ...node, color: themeColors.nodeChunk };
        }
        return { ...node, color: themeColors.nodeDefault };
      })
    );
  }, [themeColors, searchTerm, nodes.length]);

  useEffect(() => {
    if (!fgRef.current) return;
    const fg = fgRef.current;

    try {
      const linkForce = fg.d3Force('link');
      if (linkForce && typeof linkForce.distance === 'function') {
        linkForce.distance(220);
      }
    } catch (e) {}

    try {
      const chargeForce = fg.d3Force('charge');
      if (chargeForce && typeof chargeForce.strength === 'function') {
        chargeForce.strength(-400);
      }
    } catch (e) {}

    try {
      const d3 = require('d3-force');
      const collide = d3
        .forceCollide((n) => {
          const r = (n && n.size ? n.size : 8) + 24;
          return r;
        })
        .iterations(2);
      fg.d3Force('collide', collide);
    } catch (e) {}

    try {
      fg.d3VelocityDecay(0.35);
    } catch (e) {}
  }, []);

  useEffect(() => {
    if (!fgRef.current) return;
    if (!nodes.length) return;
    fgRef.current.d3ReheatSimulation();
    zoomQueuedRef.current = true;
  }, [nodes, links]);

  useEffect(() => {
    if (!fgRef.current) return;
    if (!dims.width || !dims.height) return;
    if (!nodes.length) return;
    zoomQueuedRef.current = true;
  }, [dims.width, dims.height, nodes.length]);

  const graphData = useMemo(() => ({ nodes, links }), [nodes, links]);

  const resolveNodeColor = useCallback(
    (node) => {
      if (!node) return themeColors.nodeDefault;
      if (selectedNodeId && node.id === selectedNodeId) return themeColors.nodeSelected;
      return node.color || themeColors.nodeDefault;
    },
    [selectedNodeId, themeColors]
  );

  const resolveLinkColor = useCallback(
    (link) => {
      if (!selectedNodeId) return themeColors.linkDefault;
      const s = typeof link.source === 'object' ? link.source.id : link.source;
      const t = typeof link.target === 'object' ? link.target.id : link.target;
      return s === selectedNodeId || t === selectedNodeId
        ? themeColors.linkActive
        : themeColors.linkDefault;
    },
    [selectedNodeId, themeColors]
  );

  const handleNodeClick = useCallback(
    (node) => {
      if (!node || !node.id) return;
      setSelectedNodeId(node.id);
      const nodeType = node.group || '';
      const meta = node.meta || null;
      hopAnalysis(node.id, nodeType, meta);
    },
    [hopAnalysis]
  );

  const handleEngineStop = useCallback(() => {
    if (!zoomQueuedRef.current) return;
    if (!fgRef.current) return;
    if (!dims.width || !dims.height) return;
    if (!nodes.length) return;
    zoomQueuedRef.current = false;
    fgRef.current.zoomToFit(500, 60);
  }, [dims.width, dims.height, nodes.length]);

  const paintNode = useCallback(
    (node, ctx, globalScale) => {
      if (!node) return;

      const radius = node && node.size ? node.size : 8;
      const x = node.x || 0;
      const y = node.y || 0;

      ctx.beginPath();
      ctx.arc(x, y, radius, 0, 2 * Math.PI, false);
      ctx.fillStyle = resolveNodeColor(node);
      ctx.fill();

      const text = node.label ? String(node.label) : '';
      if (!text) return;

      const maxLen = 26;
      const label = text.length > maxLen ? `${text.slice(0, maxLen - 1)}…` : text;

      const fontSize = Math.max(4, 6 / globalScale);
      ctx.font = `${fontSize}px sans-serif`;
      ctx.fillStyle = resolveNodeColor(node);
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';

      const pad = Math.max(2, 3 / globalScale);
      const labelY = y - radius - pad;

      ctx.fillText(label, x, labelY);
    },
    [resolveNodeColor]
  );

  const paintNodeArea = useCallback((node, color, ctx) => {
    if (!node) return;
    const radius = (node && node.size ? node.size : 8) + 2;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(node.x || 0, node.y || 0, radius, 0, 2 * Math.PI, false);
    ctx.fill();
  }, []);

  return (
    <div className="graph-tab">
      <div className="graph-card">
        <GraphFilter
          graphSort={graphSort}
          setGraphSort={setGraphSort}
          searchTerm={searchTerm}
          setSearchTerm={setSearchTerm}
          onSearch={handleSearchClick}
        />
      </div>

      <div className="graph-card graph-card-canvas">
        <div ref={containerRef} className="graph-canvas">
          {dims.width > 0 && dims.height > 0 && (
            <ForceGraph2D
              ref={fgRef}
              width={dims.width}
              height={dims.height}
              graphData={graphData}
              nodeId="id"
              nodeLabel="label"
              nodeColor={resolveNodeColor}
              nodeVal={(n) => (n && n.size ? n.size : 8)}
              nodeCanvasObject={paintNode}
              nodePointerAreaPaint={paintNodeArea}
              linkSource="source"
              linkTarget="target"
              linkLabel={(l) => (l && l.label ? l.label : '')}
              linkColor={resolveLinkColor}
              linkWidth={(l) => {
                if (!selectedNodeId) return 1;
                const s = typeof l.source === 'object' ? l.source.id : l.source;
                const t = typeof l.target === 'object' ? l.target.id : l.target;
                return s === selectedNodeId || t === selectedNodeId ? 2.5 : 1;
              }}
              onNodeClick={handleNodeClick}
              onBackgroundClick={() => setSelectedNodeId(null)}
              onEngineStop={handleEngineStop}
              enableNodeDrag={true}
            />
          )}
        </div>
      </div>

      {isFilterModalOpen && (
        <GraphFilterModal
          category={category}
          setCategory={setCategory}
          docId={docId}
          setDocId={setDocId}
          onClose={() => setIsFilterModalOpen(false)}
        />
      )}
    </div>
  );
}
