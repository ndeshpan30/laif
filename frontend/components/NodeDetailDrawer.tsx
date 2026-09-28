'use client';

import React, { useEffect, useMemo } from 'react';
import Link from 'next/link';
import { GraphNode, GraphEdge } from './KnowledgeGraphView';

export interface ScheduleItemSummary {
  id: string;
  title: string;
  category?: string;
  start_time?: string | null;
  end_time?: string | null;
  duration_minutes?: number;
  priority?: number;
}

export interface NodeDetailDrawerProps {
  node: GraphNode | null;
  onClose: () => void;
  allNodes: GraphNode[];
  allEdges: GraphEdge[];
  onSelectNode: (node: GraphNode) => void;
  scheduleItems?: ScheduleItemSummary[];
}

export const NodeDetailDrawer: React.FC<NodeDetailDrawerProps> = ({
  node,
  onClose,
  allNodes,
  allEdges,
  onSelectNode,
  scheduleItems = [],
}) => {
  // Listen for Escape key to close drawer
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  // Formatted date string
  const formattedDate = useMemo(() => {
    if (!node?.created_at) return 'N/A';
    try {
      const d = new Date(node.created_at);
      return d.toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch {
      return node.created_at;
    }
  }, [node?.created_at]);

  // Compute connected neighbors sorted by edge weight
  const connectedNeighbors = useMemo(() => {
    if (!node) return [];

    const nodeMap = new Map<string, GraphNode>();
    for (const n of allNodes) {
      nodeMap.set(n.id, n);
    }

    const neighborList: Array<{ neighbor: GraphNode; weight: number }> = [];

    for (const edge of allEdges) {
      const srcId = typeof edge.source === 'object' ? edge.source.id : String(edge.source);
      const tgtId = typeof edge.target === 'object' ? edge.target.id : String(edge.target);

      if (srcId === node.id) {
        const neighbor = nodeMap.get(tgtId);
        if (neighbor) {
          neighborList.push({ neighbor, weight: edge.weight });
        }
      } else if (tgtId === node.id) {
        const neighbor = nodeMap.get(srcId);
        if (neighbor) {
          neighborList.push({ neighbor, weight: edge.weight });
        }
      }
    }

    // Sort descending by similarity weight
    neighborList.sort((a, b) => b.weight - a.weight);
    return neighborList;
  }, [node, allNodes, allEdges]);

  // Find related schedule blocks
  const relatedScheduleItems = useMemo(() => {
    if (!node || !scheduleItems || scheduleItems.length === 0) return [];

    const subj = (node.subject || '').toLowerCase().trim();
    const labelWords = (node.label || '')
      .toLowerCase()
      .split(/[\s,–—:;.-]+/)
      .filter((w) => w.length > 3);

    return scheduleItems.filter((item) => {
      const title = (item.title || '').toLowerCase();
      const cat = (item.category || '').toLowerCase();

      // Check subject match
      if (subj && (title.includes(subj) || cat.includes(subj))) {
        return true;
      }
      // Check significant words in label
      if (labelWords.some((w) => title.includes(w))) {
        return true;
      }
      return false;
    });
  }, [node, scheduleItems]);

  if (!node) return null;

  const isConstraint = node.type === 'episodic_constraint';
  const displayContent = node.raw_content || node.snippet;

  return (
    <aside
      aria-label="Node Details"
      className="w-full lg:w-[400px] border-t-2 lg:border-t-0 lg:border-l-2 border-[var(--text-ink)] bg-[var(--bg-paper)] p-6 flex flex-col gap-6 overflow-y-auto select-text shadow-xl lg:shadow-none"
    >
      {/* Header Bar */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <span
            className={`font-mono text-[10px] px-2 py-0.5 tracking-widest uppercase font-bold ${
              isConstraint
                ? 'bg-[var(--accent)] text-white'
                : 'bg-[var(--text-ink)] text-[var(--bg-paper)]'
            }`}
          >
            {isConstraint ? 'EPISODIC CONSTRAINT' : 'SYLLABUS MODULE'}
          </span>

          <button
            type="button"
            onClick={onClose}
            className="font-mono text-xs uppercase tracking-widest text-[var(--text-ink)] hover:text-[var(--accent)] transition-colors px-1.5 py-0.5 border border-transparent hover:border-[var(--border-line)]"
            title="Close Drawer (Esc)"
          >
            [ CLOSE ✕ ]
          </button>
        </div>

        <h2 className="font-serif text-2xl font-bold tracking-tight text-[var(--text-ink)] leading-snug">
          {node.label}
        </h2>
      </div>

      {/* Metadata Grid (Collapsed 2-column monospace block) */}
      <div className="border border-[var(--border-line)] grid grid-cols-2 divide-x divide-y divide-[var(--border-line)] font-mono text-[11px] bg-black/[0.01]">
        <div className="p-2">
          <div className="text-gray-400 text-[9px] uppercase tracking-widest">ID</div>
          <div className="font-bold text-[var(--text-ink)] truncate" title={node.id}>
            {node.id.slice(0, 8)}...
          </div>
        </div>

        <div className="p-2">
          <div className="text-gray-400 text-[9px] uppercase tracking-widest">DEGREE</div>
          <div className="font-bold text-[var(--text-ink)]">{node.degree}</div>
        </div>

        <div className="p-2">
          <div className="text-gray-400 text-[9px] uppercase tracking-widest">SUBJECT</div>
          <div className="font-bold text-[var(--text-ink)] truncate" title={node.subject || 'N/A'}>
            {node.subject || 'Personal Constraint'}
          </div>
        </div>

        <div className="p-2">
          <div className="text-gray-400 text-[9px] uppercase tracking-widest">LOGGED AT</div>
          <div className="font-bold text-[var(--text-ink)] truncate" title={formattedDate}>
            {formattedDate}
          </div>
        </div>
      </div>

      {/* Body Content */}
      <div>
        <div className="font-mono text-[10px] uppercase tracking-widest text-gray-500 mb-1.5">
          RAW CONTEXT BODY:
        </div>
        <div className="p-3.5 border border-[var(--border-line)] bg-black/[0.02] font-serif text-sm leading-relaxed text-justify text-[var(--text-ink)] whitespace-pre-wrap selection:bg-[var(--accent)] selection:text-white">
          {displayContent}
        </div>
      </div>

      {/* Connected Neighbors List */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <span className="font-mono text-[10px] uppercase tracking-widest text-gray-500">
            CONNECTED NEIGHBORS ({connectedNeighbors.length}):
          </span>
          <span className="font-mono text-[9px] text-gray-400 uppercase tracking-widest">
            SIMILARITY
          </span>
        </div>

        {connectedNeighbors.length === 0 ? (
          <div className="font-mono text-[11px] text-gray-400 p-2.5 border border-dashed border-[var(--border-line)] text-center">
            [ NO EDGES EXCEEDING CURRENT SIMILARITY THRESHOLD ]
          </div>
        ) : (
          <div className="space-y-1.5 max-h-48 overflow-y-auto pr-1">
            {connectedNeighbors.map(({ neighbor, weight }) => {
              const nIsConstraint = neighbor.type === 'episodic_constraint';
              return (
                <button
                  key={neighbor.id}
                  type="button"
                  onClick={() => onSelectNode(neighbor)}
                  className="w-full text-left p-2 border border-[var(--border-line)] hover:border-[var(--text-ink)] bg-[var(--bg-paper)] hover:bg-black/[0.03] transition-colors flex items-center justify-between gap-2 group"
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span
                      className={`w-3.5 h-3.5 flex-shrink-0 flex items-center justify-center font-mono text-[9px] font-bold ${
                        nIsConstraint
                          ? 'bg-[var(--accent)] text-white'
                          : 'bg-[var(--text-ink)] text-[var(--bg-paper)]'
                      }`}
                    >
                      {nIsConstraint ? '!' : 'M'}
                    </span>
                    <span className="font-mono text-xs text-[var(--text-ink)] truncate group-hover:underline">
                      {neighbor.label}
                    </span>
                  </div>
                  <span className="font-mono text-[10px] text-gray-500 flex-shrink-0">
                    {(weight * 100).toFixed(0)}%
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Related Schedule Items */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <span className="font-mono text-[10px] uppercase tracking-widest text-gray-500">
            RELATED SCHEDULE BLOCKS:
          </span>
          <Link
            href="/ledger"
            className="font-mono text-[10px] text-[var(--text-ink)] hover:text-[var(--accent)] uppercase tracking-wider underline transition-colors"
          >
            VIEW LEDGER ↗
          </Link>
        </div>

        {relatedScheduleItems.length === 0 ? (
          <div className="font-mono text-[11px] text-gray-400 p-2.5 border border-dashed border-[var(--border-line)] text-center">
            [ NO DIRECT SCHEDULE BLOCKS LINKED ]
          </div>
        ) : (
          <div className="space-y-1.5 max-h-40 overflow-y-auto pr-1">
            {relatedScheduleItems.map((item) => (
              <div
                key={item.id}
                className="p-2 border border-[var(--border-line)] bg-black/[0.01] flex items-center justify-between gap-2 font-mono text-xs"
              >
                <div className="min-w-0">
                  <div className="font-bold text-[var(--text-ink)] truncate">
                    {item.title}
                  </div>
                  <div className="text-[10px] text-gray-500 uppercase tracking-widest">
                    {item.category || 'General'} {item.duration_minutes ? `· ${item.duration_minutes}m` : ''}
                  </div>
                </div>
                <Link
                  href="/ledger"
                  className="font-mono text-[9px] text-[var(--accent)] hover:underline uppercase tracking-wider flex-shrink-0"
                >
                  JUMP →
                </Link>
              </div>
            ))}
          </div>
        )}
      </div>
    </aside>
  );
};
