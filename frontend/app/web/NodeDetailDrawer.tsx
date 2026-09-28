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

  // Compute originating source URL for the OPEN SOURCE button
  const sourceHref = useMemo(() => {
    if (!node) return '#';
    const table = node.source_table || '';
    if (table === 'tracker_definitions' || table === 'telemetry_logs') {
      return `/ledger${node.source_id ? `?highlight=${node.source_id}` : ''}`;
    }
    if (table === 'schedule_items') {
      return `/?task=${node.source_id || ''}`;
    }
    return '/ledger';
  }, [node]);

  // Compute connected neighbors sorted by edge weight
  const connectedNeighbors = useMemo(() => {
    if (!node) return [];

    const nodeMap = new Map<string, GraphNode>();
    for (const n of allNodes) {
      nodeMap.set(n.id, n);
    }

    const neighborList: Array<{ neighbor: GraphNode; edge: GraphEdge }> = [];

    for (const edge of allEdges) {
      const srcId = typeof edge.source === 'object' ? edge.source.id : String(edge.source);
      const tgtId = typeof edge.target === 'object' ? edge.target.id : String(edge.target);

      if (srcId === node.id) {
        const neighbor = nodeMap.get(tgtId);
        if (neighbor) {
          neighborList.push({ neighbor, edge });
        }
      } else if (tgtId === node.id) {
        const neighbor = nodeMap.get(srcId);
        if (neighbor) {
          neighborList.push({ neighbor, edge });
        }
      }
    }

    // Sort descending by similarity weight
    neighborList.sort((a, b) => b.edge.weight - a.edge.weight);
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

  const type = node.type || 'other';
  const isConstraint = type === 'episodic_constraint';
  const isHabit = type === 'life_habit';
  const isTelemetry = type === 'telemetry_entry';
  const isGoal = type === 'project_goal';
  const isSyllabus = type === 'syllabus_module';

  const displayContent = node.raw_content || node.snippet;
  const metadata = node.metadata || {};

  // Badge styling based on type
  const getTypeBadgeStyle = () => {
    if (isConstraint) return 'bg-[var(--accent)] text-white';
    if (isHabit) return 'bg-[var(--ochre)] text-white';
    if (isTelemetry) return 'bg-[var(--bg-paper)] text-[var(--text-ink)] border border-[var(--text-ink)]';
    if (isGoal) return 'bg-[var(--bg-paper)] text-[var(--text-ink)] border-2 border-[var(--text-ink)]';
    if (isSyllabus) return 'bg-[var(--text-ink)] text-[var(--bg-paper)]';
    return 'bg-[var(--bg-paper)] text-[var(--text-ink)] border border-gray-400';
  };

  const getGlyph = (t: string) => {
    switch (t) {
      case 'episodic_constraint':
        return '!';
      case 'syllabus_module':
        return 'M';
      case 'life_habit':
        return '~';
      case 'telemetry_entry':
        return 'T';
      case 'project_goal':
        return 'G';
      default:
        return '•';
    }
  };

  return (
    <aside
      aria-label="Node Details"
      className="w-full lg:w-[420px] border-t-2 lg:border-t-0 lg:border-l-2 border-[var(--text-ink)] bg-[var(--bg-paper)] p-6 flex flex-col gap-6 overflow-y-auto select-text shadow-xl lg:shadow-none"
    >
      {/* Header Bar */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <span
              className={`font-mono text-[10px] px-2 py-0.5 tracking-widest uppercase font-bold ${getTypeBadgeStyle()}`}
            >
              [{getGlyph(type)}] {type.replace(/_/g, ' ')}
            </span>
            {node.group && (
              <span className="font-mono text-[9px] uppercase tracking-wider text-gray-500 border border-[var(--border-line)] px-1.5 py-0.5">
                {node.group}
              </span>
            )}
          </div>

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

      {/* Action: OPEN SOURCE Button */}
      <div>
        <Link
          href={sourceHref}
          className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-[var(--text-ink)] text-[var(--bg-paper)] hover:bg-[var(--accent)] hover:text-white font-mono text-xs uppercase tracking-widest font-bold transition-colors cutout-hover"
        >
          <span>OPEN SOURCE ↗</span>
          {node.source_table && (
            <span className="opacity-70 text-[10px]">
              [{node.source_table.replace(/_/g, ' ')}]
            </span>
          )}
        </Link>
      </div>

      {/* Habit Metrics Card: Rolling Averages & Streaks for life_habit nodes */}
      {isHabit && (
        <div className="border border-[var(--ochre)] bg-[var(--ochre)]/10 p-4">
          <div className="flex items-center justify-between mb-2">
            <span className="font-mono text-[10px] uppercase tracking-widest font-bold text-[var(--ochre)]">
              HABIT METRICS & ROLLING VITALS
            </span>
            <span className="font-mono text-xs font-bold text-[var(--ochre)]">~</span>
          </div>
          <div className="grid grid-cols-2 gap-3 pt-2 border-t border-[var(--ochre)]/30 font-mono">
            <div>
              <div className="text-gray-500 text-[9px] uppercase tracking-widest">
                7-Day Rolling Avg
              </div>
              <div className="text-xl font-bold text-[var(--text-ink)] mt-0.5">
                {metadata.rolling_avg_7d != null ? metadata.rolling_avg_7d : '—'}
                {metadata.unit ? ` ${metadata.unit}` : ''}
              </div>
            </div>
            <div>
              <div className="text-gray-500 text-[9px] uppercase tracking-widest">
                Current Streak
              </div>
              <div className="text-xl font-bold text-[var(--text-ink)] mt-0.5">
                {metadata.current_streak != null ? metadata.current_streak : 0}{' '}
                <span className="text-xs font-normal">
                  {metadata.current_streak === 1 ? 'day' : 'days'}
                </span>
              </div>
            </div>
            {metadata.target_value && (
              <div className="col-span-2 pt-1 border-t border-[var(--ochre)]/20 text-[10px] text-gray-600">
                Target: {metadata.target_value} {metadata.unit || ''} ({metadata.target_frequency || 'daily'})
              </div>
            )}
          </div>
        </div>
      )}

      {/* Node Provenance Grid (Table, Source ID, Created At, Degree) */}
      <div className="border border-[var(--border-line)] grid grid-cols-2 divide-x divide-y divide-[var(--border-line)] font-mono text-[11px] bg-black/[0.01]">
        <div className="p-2">
          <div className="text-gray-400 text-[9px] uppercase tracking-widest">SOURCE TABLE</div>
          <div className="font-bold text-[var(--text-ink)] truncate" title={node.source_table || 'semantic_contexts'}>
            {node.source_table || 'semantic_contexts'}
          </div>
        </div>

        <div className="p-2">
          <div className="text-gray-400 text-[9px] uppercase tracking-widest">SOURCE ID</div>
          <div className="font-bold text-[var(--text-ink)] truncate" title={node.source_id || node.id}>
            {(node.source_id || node.id).slice(0, 8)}...
          </div>
        </div>

        <div className="p-2">
          <div className="text-gray-400 text-[9px] uppercase tracking-widest">DEGREE</div>
          <div className="font-bold text-[var(--text-ink)]">{node.degree}</div>
        </div>

        <div className="p-2">
          <div className="text-gray-400 text-[9px] uppercase tracking-widest">LOGGED AT</div>
          <div className="font-bold text-[var(--text-ink)] truncate" title={formattedDate}>
            {formattedDate}
          </div>
        </div>

        <div className="p-2 col-span-2">
          <div className="text-gray-400 text-[9px] uppercase tracking-widest">SUBJECT / DOMAIN</div>
          <div className="font-bold text-[var(--text-ink)] truncate" title={node.subject || 'Personal Context'}>
            {node.subject || 'Personal Context'}
          </div>
        </div>
      </div>

      {/* Metadata Attributes Inspector (if present) */}
      {metadata && Object.keys(metadata).length > 0 && (
        <div>
          <div className="font-mono text-[10px] uppercase tracking-widest text-gray-500 mb-1.5">
            NODE METADATA:
          </div>
          <div className="border border-[var(--border-line)] bg-black/[0.02] p-2.5 font-mono text-[10px] space-y-1">
            {Object.entries(metadata).map(([key, val]) => {
              if (val === null || val === undefined || typeof val === 'object') return null;
              return (
                <div key={key} className="flex justify-between items-center gap-2">
                  <span className="text-gray-400 uppercase tracking-wider">{key}:</span>
                  <span className="font-bold text-[var(--text-ink)] truncate max-w-[200px]">
                    {String(val)}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Raw Context Body */}
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
            {connectedNeighbors.map(({ neighbor, edge }) => {
              const nType = neighbor.type || 'other';
              const nGlyph = getGlyph(nType);
              const isStructural = edge.kind === 'structural';
              const isCross = Boolean(edge.cross_domain);

              return (
                <button
                  key={neighbor.id}
                  type="button"
                  onClick={() => onSelectNode(neighbor)}
                  className="w-full text-left p-2 border border-[var(--border-line)] hover:border-[var(--text-ink)] bg-[var(--bg-paper)] hover:bg-black/[0.03] transition-colors flex items-center justify-between gap-2 group"
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span
                      className={`w-4 h-4 flex-shrink-0 flex items-center justify-center font-mono text-[9px] font-bold ${
                        nType === 'episodic_constraint'
                          ? 'bg-[var(--accent)] text-white'
                          : nType === 'life_habit'
                          ? 'bg-[var(--ochre)] text-white'
                          : nType === 'syllabus_module'
                          ? 'bg-[var(--text-ink)] text-[var(--bg-paper)]'
                          : 'bg-[var(--bg-paper)] text-[var(--text-ink)] border border-[var(--text-ink)]'
                      }`}
                    >
                      {nGlyph}
                    </span>
                    <span className="font-mono text-xs text-[var(--text-ink)] truncate group-hover:underline">
                      {neighbor.label}
                    </span>
                    {isCross && (
                      <span className="text-[9px] font-mono text-[var(--accent)] border border-[var(--accent)] px-1 uppercase flex-shrink-0">
                        CROSS
                      </span>
                    )}
                    {isStructural && (
                      <span className="text-[9px] font-mono text-gray-400 uppercase flex-shrink-0">
                        STRUCTURAL
                      </span>
                    )}
                  </div>
                  <span className="font-mono text-[10px] text-gray-500 flex-shrink-0">
                    {(edge.weight * 100).toFixed(0)}%
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
