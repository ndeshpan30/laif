'use client';

import React, { useState, useEffect, useMemo, useCallback } from 'react';
import Link from 'next/link';
import { useTheme } from 'next-themes';
import { ArrowLeft, RefreshCw, Sun, Moon } from 'lucide-react';

import {
  KnowledgeGraphView,
  GraphNode,
  KnowledgeGraphData,
} from './KnowledgeGraphView';
import {
  KnowledgeGraphFilters,
  CANONICAL_UI_GROUPS,
} from './KnowledgeGraphFilters';
import { NodeDetailDrawer, ScheduleItemSummary } from './NodeDetailDrawer';

const DEFAULT_USER_ID = '00000000-0000-0000-0000-000000000001';
const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export default function KnowledgeGraphPage() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  const [isLoading, setIsLoading] = useState(true);

  // Raw Graph Data from API
  const [rawGraph, setRawGraph] = useState<KnowledgeGraphData>({
    nodes: [],
    edges: [],
    types_present: [],
    truncated: false,
    total_nodes: 0,
    total_edges: 0,
  });

  // Schedule Items for linking
  const [scheduleItems, setScheduleItems] = useState<ScheduleItemSummary[]>([]);

  // Selected Node for Detail Drawer
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);

  // Filter States: Dynamic UI Groups
  const [activeGroups, setActiveGroups] = useState<Set<string>>(
    () => new Set<string>(CANONICAL_UI_GROUPS)
  );

  // Cross-Domain Links Only Toggle
  const [crossDomainOnly, setCrossDomainOnly] = useState<boolean>(false);

  // Subject and Similarity Slider
  const [selectedSubject, setSelectedSubject] = useState('ALL');
  const [threshold, setThreshold] = useState(0.60);

  // Load Graph and Schedule Data
  const loadData = useCallback(async () => {
    setIsLoading(true);
    try {
      // 1. Fetch Knowledge Graph from backend
      const graphRes = await fetch(
        `${API_URL}/api/knowledge-graph?user_id=${DEFAULT_USER_ID}&min_similarity=0.50&limit=300`
      );
      if (graphRes.ok) {
        const data = await graphRes.json();
        setRawGraph({
          nodes: data.nodes || [],
          edges: data.edges || [],
          types_present: data.types_present || [],
          truncated: Boolean(data.truncated),
          total_nodes: data.total_nodes ?? (data.nodes ? data.nodes.length : 0),
          total_edges: data.total_edges ?? (data.edges ? data.edges.length : 0),
        });
      }

      // 2. Fetch Schedule Items for task link matching
      const schedRes = await fetch(`${API_URL}/api/schedule/items?user_id=${DEFAULT_USER_ID}`);
      if (schedRes.ok) {
        const items = await schedRes.json();
        setScheduleItems(items || []);
      }
    } catch (err) {
      console.warn('Failed to load knowledge graph data:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    setMounted(true);
    loadData();
  }, [loadData]);

  // Extract unique subjects for dropdown
  const subjectOptions = useMemo(() => {
    const subjects = new Set<string>();
    for (const node of rawGraph.nodes) {
      if (node.subject && node.subject.trim()) {
        subjects.add(node.subject.trim());
      }
    }
    return Array.from(subjects).sort();
  }, [rawGraph.nodes]);

  // Handle toggling a UI group
  const handleToggleGroup = useCallback((group: string) => {
    setActiveGroups((prev) => {
      const next = new Set(prev);
      if (next.has(group)) {
        next.delete(group);
      } else {
        next.add(group);
      }
      return next;
    });
  }, []);

  // Compute Client-Filtered Graph with Dynamic Degree Calculation
  const filteredData = useMemo<KnowledgeGraphData>(() => {
    // 1. Filter Nodes by Active UI Group and Subject
    const nodes = rawGraph.nodes.filter((node) => {
      const grp = node.group || 'OTHER';
      if (!activeGroups.has(grp)) return false;
      if (selectedSubject !== 'ALL' && node.subject !== selectedSubject) return false;
      return true;
    });

    const activeNodeIds = new Set(nodes.map((n) => n.id));

    // 2. Filter Edges by Similarity Threshold and Active Node Visibility
    const edges = rawGraph.edges.filter((edge) => {
      if (edge.weight < threshold) return false;
      const srcId = typeof edge.source === 'object' ? edge.source.id : String(edge.source);
      const tgtId = typeof edge.target === 'object' ? edge.target.id : String(edge.target);
      return activeNodeIds.has(srcId) && activeNodeIds.has(tgtId);
    });

    // 3. Recompute Node Degree Dynamically on Client
    const degreeMap = new Map<string, number>();
    for (const n of nodes) {
      degreeMap.set(n.id, 0);
    }
    for (const e of edges) {
      const srcId = typeof e.source === 'object' ? e.source.id : String(e.source);
      const tgtId = typeof e.target === 'object' ? e.target.id : String(e.target);
      degreeMap.set(srcId, (degreeMap.get(srcId) || 0) + 1);
      degreeMap.set(tgtId, (degreeMap.get(tgtId) || 0) + 1);
    }

    const updatedNodes: GraphNode[] = nodes.map((node) => ({
      ...node,
      degree: degreeMap.get(node.id) || 0,
    }));

    return {
      nodes: updatedNodes,
      edges,
      types_present: rawGraph.types_present,
      truncated: rawGraph.truncated,
      total_nodes: rawGraph.total_nodes,
      total_edges: rawGraph.total_edges,
    };
  }, [rawGraph, activeGroups, selectedSubject, threshold]);

  // Update selectedNode if its degree was recomputed in filteredData
  useEffect(() => {
    if (selectedNode) {
      const updated = filteredData.nodes.find((n) => n.id === selectedNode.id);
      if (updated && updated.degree !== selectedNode.degree) {
        setSelectedNode(updated);
      }
    }
  }, [filteredData.nodes, selectedNode]);

  return (
    <main className="min-h-screen max-w-7xl mx-auto px-4 py-8">
      {/* Editorial Header per UI_UX.md */}
      <header className="mb-6">
        <div className="flex justify-between items-center mb-4">
          <Link
            href="/"
            className="inline-flex items-center gap-2 font-mono text-xs uppercase tracking-widest text-[var(--text-ink)] hover:opacity-75 transition-opacity"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            <span>RETURN TO CONVERSATION</span>
          </Link>

          {/* Navigation Links & Action Controls */}
          <div className="flex items-center gap-3">
            <span className="flex items-center gap-1 px-3 py-1.5 bg-[var(--text-ink)] text-[var(--bg-paper)] font-mono text-xs uppercase tracking-widest select-none">
              <span>THE WEB</span>
            </span>

            <Link
              href="/ledger"
              className="flex items-center gap-1 px-3 py-1.5 border border-[var(--border-line)] text-[var(--text-ink)] hover:border-[var(--text-ink)] font-mono text-xs uppercase tracking-widest transition-colors"
            >
              <span>THE LEDGER ↗</span>
            </Link>

            <button
              onClick={loadData}
              title="Refresh Knowledge Graph"
              aria-label="Refresh Knowledge Graph"
              className="p-2 border border-[var(--border-line)] text-[var(--text-ink)] hover:border-[var(--text-ink)] transition-colors"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />
            </button>

            {mounted && (
              <button
                onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
                title="Toggle Theme"
                aria-label="Toggle Theme"
                className="p-2 border border-[var(--border-line)] text-[var(--text-ink)] hover:border-[var(--text-ink)] transition-colors"
              >
                {theme === 'dark' ? <Sun className="w-3.5 h-3.5" /> : <Moon className="w-3.5 h-3.5" />}
              </button>
            )}
          </div>
        </div>

        {/* Masthead */}
        <h1 className="font-serif text-5xl md:text-6xl font-bold tracking-tight text-[var(--text-ink)] mb-2">
          The Web.
        </h1>
        <div className="border-b-4 border-[var(--text-ink)] mb-2" />
        <p className="font-mono text-xs text-gray-500 uppercase tracking-widest">
          Vol. 1 | Topological Context Map & Multi-Domain Semantic Knowledge Graph
        </p>
      </header>

      {/* Main Container with Collapsed Grid Borders */}
      <div className="border border-[var(--border-line)] bg-[var(--bg-paper)] shadow-[3px_3px_0px_0px_rgba(0,0,0,0.06)]">
        {/* Editorial Dynamic Filter Toolbar */}
        <KnowledgeGraphFilters
          typesPresent={rawGraph.types_present || []}
          activeGroups={activeGroups}
          onToggleGroup={handleToggleGroup}
          crossDomainOnly={crossDomainOnly}
          onToggleCrossDomain={() => setCrossDomainOnly((prev) => !prev)}
          selectedSubject={selectedSubject}
          onSelectSubject={(subj) => setSelectedSubject(subj)}
          subjectOptions={subjectOptions}
          threshold={threshold}
          onChangeThreshold={(val) => setThreshold(val)}
          visibleNodesCount={filteredData.nodes.length}
          totalNodesCount={rawGraph.total_nodes || rawGraph.nodes.length}
          visibleEdgesCount={filteredData.edges.length}
          totalEdgesCount={rawGraph.total_edges || rawGraph.edges.length}
        />

        {/* Split Layout: Force-Directed Canvas + Detail Drawer */}
        <div className="flex flex-col lg:flex-row relative min-h-[650px] w-full overflow-hidden">
          {/* Main Force-Directed Graph Canvas */}
          <div className="flex-1 min-w-0 h-[650px] relative">
            <KnowledgeGraphView
              data={filteredData}
              onSelectNode={(node) => setSelectedNode(node)}
              selectedNodeId={selectedNode?.id}
              className="w-full h-full border-0"
              totalContextCount={rawGraph.total_nodes}
              crossDomainOnly={crossDomainOnly}
            />
          </div>

          {/* Click-to-Inspect Side Drawer */}
          {selectedNode && (
            <NodeDetailDrawer
              node={selectedNode}
              onClose={() => setSelectedNode(null)}
              allNodes={filteredData.nodes}
              allEdges={filteredData.edges}
              onSelectNode={(node) => setSelectedNode(node)}
              scheduleItems={scheduleItems}
            />
          )}
        </div>
      </div>
    </main>
  );
}
