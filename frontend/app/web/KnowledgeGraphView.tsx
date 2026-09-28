'use client';

import React, { useEffect, useRef, useState, useMemo } from 'react';
import * as d3Force from 'd3-force';

export type CanonicalNodeType =
  | 'episodic_constraint'
  | 'syllabus_module'
  | 'life_habit'
  | 'telemetry_entry'
  | 'project_goal'
  | string;

export interface GraphNode extends d3Force.SimulationNodeDatum {
  id: string;
  label: string;
  type: CanonicalNodeType;
  group?: string;
  glyph?: string;
  style?: string;
  subject?: string | null;
  snippet: string;
  raw_content?: string;
  degree: number;
  source_table?: string | null;
  source_id?: string | null;
  created_at: string;
  content_hash?: string;
  metadata?: Record<string, any>;
}

export interface GraphEdge extends d3Force.SimulationLinkDatum<GraphNode> {
  source: string | GraphNode;
  target: string | GraphNode;
  weight: number;
  kind?: 'semantic' | 'structural' | string;
  cross_domain?: boolean;
}

export interface TypePresentInfo {
  type: string;
  group: string;
  label: string;
  glyph: string;
  count: number;
}

export interface KnowledgeGraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  types_present?: TypePresentInfo[];
  truncated?: boolean;
  total_nodes?: number;
  total_edges?: number;
}

interface KnowledgeGraphViewProps {
  data: KnowledgeGraphData;
  onSelectNode?: (node: GraphNode) => void;
  selectedNodeId?: string | null;
  className?: string;
  totalContextCount?: number;
  crossDomainOnly?: boolean;
}

export const KnowledgeGraphView: React.FC<KnowledgeGraphViewProps> = ({
  data,
  onSelectNode,
  selectedNodeId,
  className,
  totalContextCount,
  crossDomainOnly = false,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 });
  const [hoveredNode, setHoveredNode] = useState<GraphNode | null>(null);
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 });

  // State to hold nodes and edges positioned by simulation
  const [simNodes, setSimNodes] = useState<GraphNode[]>([]);
  const [simEdges, setSimEdges] = useState<GraphEdge[]>([]);

  // Update container dimensions on resize
  useEffect(() => {
    const updateSize = () => {
      if (containerRef.current) {
        setDimensions({
          width: containerRef.current.clientWidth || 800,
          height: containerRef.current.clientHeight || 600,
        });
      }
    };
    updateSize();
    window.addEventListener('resize', updateSize);
    return () => window.removeEventListener('resize', updateSize);
  }, []);

  // Compute set of node IDs connected by cross-domain edges for dimming
  const crossDomainNodeIds = useMemo(() => {
    const set = new Set<string>();
    for (const e of data.edges || []) {
      if (e.cross_domain) {
        const srcId = typeof e.source === 'object' ? (e.source as GraphNode).id : String(e.source);
        const tgtId = typeof e.target === 'object' ? (e.target as GraphNode).id : String(e.target);
        set.add(srcId);
        set.add(tgtId);
      }
    }
    return set;
  }, [data.edges]);

  // Run d3-force simulation
  useEffect(() => {
    if (!data.nodes || data.nodes.length === 0) {
      setSimNodes([]);
      setSimEdges([]);
      return;
    }

    const { width, height } = dimensions;

    // Deep clone nodes and edges for simulation mutability
    const nodes: GraphNode[] = data.nodes.map((d) => ({ ...d }));
    const nodeIds = new Set(nodes.map((n) => n.id));

    // Ensure edge source/targets connect existing nodes
    const validEdges: GraphEdge[] = [];
    for (const e of data.edges || []) {
      const srcId = typeof e.source === 'object' ? (e.source as GraphNode).id : String(e.source);
      const tgtId = typeof e.target === 'object' ? (e.target as GraphNode).id : String(e.target);
      if (nodeIds.has(srcId) && nodeIds.has(tgtId)) {
        validEdges.push({
          source: srcId,
          target: tgtId,
          weight: e.weight,
          kind: e.kind || 'semantic',
          cross_domain: e.cross_domain,
        });
      }
    }

    const simulation = d3Force
      .forceSimulation<GraphNode>(nodes)
      .force(
        'link',
        d3Force
          .forceLink<GraphNode, GraphEdge>(validEdges)
          .id((d) => d.id)
          .distance((d) => 130 - (d.weight || 0.70) * 55)
      )
      .force('charge', d3Force.forceManyBody().strength(-240))
      .force('center', d3Force.forceCenter(width / 2, (height - 40) / 2))
      .force('collision', d3Force.forceCollide().radius((d) => 16 + ((d as GraphNode).degree || 0) * 3));

    simulation.on('tick', () => {
      setSimNodes([...nodes]);
      setSimEdges([...validEdges]);
    });

    return () => {
      simulation.stop();
    };
  }, [data, dimensions]);

  // Handle Dragging
  const handleDrag = (nodeId: string, e: React.MouseEvent) => {
    e.preventDefault();
    const node = simNodes.find((n) => n.id === nodeId);
    if (!node) return;

    node.fx = node.x;
    node.fy = node.y;

    const onMouseMove = (moveEvent: MouseEvent) => {
      if (containerRef.current) {
        const rect = containerRef.current.getBoundingClientRect();
        node.fx = Math.max(20, Math.min(dimensions.width - 20, moveEvent.clientX - rect.left));
        node.fy = Math.max(20, Math.min(dimensions.height - 60, moveEvent.clientY - rect.top));
        setSimNodes([...simNodes]);
      }
    };

    const onMouseUp = () => {
      node.fx = null;
      node.fy = null;
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
    };

    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
  };

  // Helper: Get node glyph
  const getNodeGlyph = (type: string, fallback?: string): string => {
    switch (type) {
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
        return fallback || '•';
    }
  };

  // Helper: Get node glyph text color
  const getNodeTextColor = (type: string): string => {
    switch (type) {
      case 'episodic_constraint':
      case 'life_habit':
        return '#FFFFFF';
      case 'syllabus_module':
        return 'var(--bg-paper)';
      case 'telemetry_entry':
      case 'project_goal':
      default:
        return 'var(--text-ink)';
    }
  };

  // Zero state: Only when total_nodes === 0
  const baselineCount = totalContextCount ?? data.total_nodes ?? (data.nodes ? data.nodes.length : 0);
  if (baselineCount === 0) {
    return (
      <div
        className={
          className ||
          'w-full h-[650px] border border-[var(--border-line)] bg-[var(--bg-paper)] flex flex-col items-center justify-center p-8 text-center select-none'
        }
      >
        <div className="font-mono text-xs uppercase tracking-widest text-[var(--accent)] mb-3">
          [ CONTEXT MAP // ZERO STATE ]
        </div>
        <p className="max-w-md font-mono text-sm text-gray-500 leading-relaxed mb-6">
          Your context map fills in as you upload syllabi, log journal entries, establish habits,
          and define project milestones.
        </p>
        <span className="font-mono text-[10px] text-gray-400 uppercase tracking-widest border border-dashed border-[var(--border-line)] px-3 py-1.5">
          CURRENT NODES: 0
        </span>
      </div>
    );
  }

  // Filter empty state: Active filters match zero nodes
  if (!data.nodes || data.nodes.length === 0) {
    return (
      <div
        className={
          className ||
          'w-full h-[650px] border border-[var(--border-line)] bg-[var(--bg-paper)] flex flex-col items-center justify-center p-8 text-center select-none'
        }
      >
        <div className="font-mono text-xs uppercase tracking-widest text-gray-400 mb-2">
          [ NO CONTEXT NODES MATCH ACTIVE FILTERS ]
        </div>
        <p className="max-w-md font-mono text-xs text-gray-500">
          Adjust the group toggles or select a different subject above to inspect context nodes.
        </p>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className={
        className ||
        'relative w-full h-[650px] border border-[var(--border-line)] bg-[var(--bg-paper)] flex flex-col justify-between overflow-hidden select-none'
      }
      onMouseMove={(e) => {
        const rect = e.currentTarget.getBoundingClientRect();
        setMousePos({ x: e.clientX - rect.left, y: e.clientY - rect.top });
      }}
    >
      {/* Editorial Watermark / Header Overlay */}
      <div className="absolute top-3 left-4 z-10 bg-[var(--bg-paper)]/90 backdrop-blur-sm border border-[var(--border-line)] px-3 py-1.5 pointer-events-none">
        <div className="font-mono text-[10px] uppercase tracking-widest font-bold text-[var(--text-ink)]">
          TOPOLOGICAL CONTEXT WEB
        </div>
      </div>

      {/* SVG Canvas */}
      <svg className="w-full flex-1 cursor-crosshair">
        {/* Render Edges */}
        <g className="edges">
          {simEdges.map((edge, idx) => {
            const src = typeof edge.source === 'object' ? (edge.source as GraphNode) : null;
            const tgt = typeof edge.target === 'object' ? (edge.target as GraphNode) : null;
            if (!src || !tgt || src.x == null || src.y == null || tgt.x == null || tgt.y == null) return null;

            const isIncident = hoveredNode && (hoveredNode.id === src.id || hoveredNode.id === tgt.id);
            const isStructural = edge.kind === 'structural';
            const isCrossDomain = Boolean(edge.cross_domain);

            // Cross-domain filtering: dim non-cross-domain edges when active
            if (crossDomainOnly && !isCrossDomain) {
              return (
                <line
                  key={`edge-${idx}`}
                  x1={src.x}
                  y1={src.y}
                  x2={tgt.x}
                  y2={tgt.y}
                  stroke="var(--text-ink)"
                  strokeOpacity={0.05}
                  strokeWidth={0.75}
                />
              );
            }

            // Normal or Cross-Domain highlighted edge styling
            let opacity: number;
            let strokeWidth: number;
            let strokeColor = 'var(--text-ink)';

            if (crossDomainOnly && isCrossDomain) {
              strokeColor = 'var(--accent)';
              opacity = isIncident ? 1.0 : 0.85;
              strokeWidth = isIncident ? 3.0 : 2.0;
            } else if (isStructural) {
              opacity = isIncident ? 0.9 : 0.65;
              strokeWidth = isIncident ? 2.5 : 1.5;
            } else {
              // Solid semantic edge: opacity scaled by weight
              opacity = isIncident ? 0.95 : Math.max(0.2, (edge.weight || 0.6) * 0.85);
              strokeWidth = isIncident ? 3.0 : Math.max(1.0, (edge.weight || 0.6) * 2.2);
            }

            return (
              <line
                key={`edge-${idx}`}
                x1={src.x}
                y1={src.y}
                x2={tgt.x}
                y2={tgt.y}
                stroke={strokeColor}
                strokeOpacity={opacity}
                strokeWidth={strokeWidth}
                strokeDasharray={isStructural ? '4 3' : undefined}
              />
            );
          })}
        </g>

        {/* Render Nodes (Brutalist Sharp Rectangles scaled by Degree) */}
        <g className="nodes">
          {simNodes.map((node) => {
            if (node.x == null || node.y == null) return null;

            const isSelected = selectedNodeId === node.id;
            const isHovered = hoveredNode?.id === node.id;
            const isDimmed = crossDomainOnly && !crossDomainNodeIds.has(node.id);

            // Node Dimensions: Scale <rect> size with node degree
            const baseSize = 16;
            const size = Math.max(16, Math.min(52, baseSize + (node.degree || 0) * 3.5));
            const halfSize = size / 2;

            const type = node.type || 'other';
            const glyph = getNodeGlyph(type, node.glyph);
            const textColor = getNodeTextColor(type);

            return (
              <g
                key={node.id}
                transform={`translate(${node.x}, ${node.y})`}
                className="cursor-pointer transition-opacity duration-150"
                opacity={isDimmed ? 0.18 : 1.0}
                onClick={() => onSelectNode?.(node)}
                onMouseDown={(e) => handleDrag(node.id, e)}
                onMouseEnter={() => setHoveredNode(node)}
                onMouseLeave={() => setHoveredNode(null)}
              >
                {/* Selection Ring (outer dashed box) */}
                {isSelected && (
                  <rect
                    x={-halfSize - 3}
                    y={-halfSize - 3}
                    width={size + 6}
                    height={size + 6}
                    fill="none"
                    stroke="var(--text-ink)"
                    strokeWidth={1.5}
                    strokeDasharray="3 2"
                  />
                )}

                {/* Node Box Rendering by Type */}
                {type === 'episodic_constraint' ? (
                  // Editorial Red (var(--accent)), glyph !
                  <rect
                    x={-halfSize}
                    y={-halfSize}
                    width={size}
                    height={size}
                    fill="var(--accent)"
                    stroke="var(--accent)"
                    strokeWidth={isSelected ? 2.5 : isHovered ? 2 : 1.5}
                  />
                ) : type === 'syllabus_module' ? (
                  // Black/White, glyph M
                  <rect
                    x={-halfSize}
                    y={-halfSize}
                    width={size}
                    height={size}
                    fill="var(--text-ink)"
                    stroke="var(--text-ink)"
                    strokeWidth={isSelected ? 2.5 : isHovered ? 2 : 1.5}
                  />
                ) : type === 'life_habit' ? (
                  // Ochre fill, glyph ~
                  <rect
                    x={-halfSize}
                    y={-halfSize}
                    width={size}
                    height={size}
                    fill="var(--ochre)"
                    stroke="var(--ochre)"
                    strokeWidth={isSelected ? 2.5 : isHovered ? 2 : 1.5}
                  />
                ) : type === 'telemetry_entry' ? (
                  // Paper fill with ink border, glyph T
                  <rect
                    x={-halfSize}
                    y={-halfSize}
                    width={size}
                    height={size}
                    fill="var(--bg-paper)"
                    stroke="var(--text-ink)"
                    strokeWidth={isSelected ? 2.5 : isHovered ? 2 : 1.5}
                  />
                ) : type === 'project_goal' ? (
                  // Paper fill with double hairline outline, glyph G
                  <>
                    <rect
                      x={-halfSize}
                      y={-halfSize}
                      width={size}
                      height={size}
                      fill="var(--bg-paper)"
                      stroke="var(--text-ink)"
                      strokeWidth={1}
                    />
                    <rect
                      x={-halfSize + 2.5}
                      y={-halfSize + 2.5}
                      width={Math.max(1, size - 5)}
                      height={Math.max(1, size - 5)}
                      fill="none"
                      stroke="var(--text-ink)"
                      strokeWidth={1}
                    />
                  </>
                ) : (
                  // Other: Neutral outline, glyph •
                  <rect
                    x={-halfSize}
                    y={-halfSize}
                    width={size}
                    height={size}
                    fill="var(--bg-paper)"
                    stroke="var(--text-ink)"
                    strokeWidth={1}
                  />
                )}

                {/* Glyph inside node */}
                <text
                  x="0"
                  y={size >= 24 ? 4 : 3}
                  textAnchor="middle"
                  fill={textColor}
                  fontSize={Math.max(9, size * 0.52)}
                  fontFamily="JetBrains Mono, monospace"
                  fontWeight="bold"
                  pointerEvents="none"
                >
                  {glyph}
                </text>

                {/* Compact Node Label underneath */}
                <text
                  x="0"
                  y={halfSize + 12}
                  textAnchor="middle"
                  fill="var(--text-ink)"
                  fontSize="9px"
                  fontFamily="JetBrains Mono, monospace"
                  className="tracking-tight select-none pointer-events-none"
                  fillOpacity={isHovered || isSelected ? 1.0 : 0.75}
                >
                  {node.label.length > 20 ? node.label.slice(0, 18) + '...' : node.label}
                </text>
              </g>
            );
          })}
        </g>
      </svg>

      {/* Monospace Hover Tooltip */}
      {hoveredNode && (
        <div
          className="absolute z-20 pointer-events-none bg-[var(--text-ink)] text-[var(--bg-paper)] p-3 font-mono text-xs max-w-xs shadow-lg border border-[var(--text-ink)]"
          style={{
            left: Math.min(dimensions.width - 240, mousePos.x + 16),
            top: Math.min(dimensions.height - 140, mousePos.y + 16),
          }}
        >
          <div className="flex items-center justify-between pb-1 mb-1.5 border-b border-[var(--bg-paper)]/30 text-[9px] uppercase tracking-widest opacity-80">
            <span>{hoveredNode.type.replace(/_/g, ' ')}</span>
            <span>DEGREE: {hoveredNode.degree}</span>
          </div>
          {hoveredNode.subject && (
            <div className="font-bold text-[10px] text-[var(--accent)] uppercase mb-1">
              {hoveredNode.subject}
            </div>
          )}
          <div className="font-semibold text-xs mb-1.5 leading-snug">
            {hoveredNode.label}
          </div>
          <div className="text-[10px] opacity-75 line-clamp-3 leading-relaxed">
            {hoveredNode.snippet}
          </div>
        </div>
      )}

      {/* Editorial Legend Bar at Bottom */}
      <div className="border-t border-[var(--border-line)] bg-[var(--bg-paper)]/95 backdrop-blur-sm px-4 py-2.5 flex flex-wrap items-center justify-between gap-3 font-mono text-[10px] uppercase tracking-wider text-[var(--text-ink)] select-none">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <span className="font-bold text-gray-500 tracking-widest">LEGEND //</span>

          <div className="flex items-center gap-1.5">
            <span className="w-3.5 h-3.5 bg-[var(--accent)] text-white flex items-center justify-center font-bold text-[9px]">
              !
            </span>
            <span>CONSTRAINT</span>
          </div>

          <div className="flex items-center gap-1.5">
            <span className="w-3.5 h-3.5 bg-[var(--text-ink)] text-[var(--bg-paper)] flex items-center justify-center font-bold text-[9px]">
              M
            </span>
            <span>SYLLABUS</span>
          </div>

          <div className="flex items-center gap-1.5">
            <span className="w-3.5 h-3.5 bg-[var(--ochre)] text-white flex items-center justify-center font-bold text-[9px]">
              ~
            </span>
            <span>HABIT</span>
          </div>

          <div className="flex items-center gap-1.5">
            <span className="w-3.5 h-3.5 bg-[var(--bg-paper)] border border-[var(--text-ink)] text-[var(--text-ink)] flex items-center justify-center font-bold text-[9px]">
              T
            </span>
            <span>TELEMETRY</span>
          </div>

          <div className="flex items-center gap-1.5">
            <span className="w-3.5 h-3.5 bg-[var(--bg-paper)] border-2 border-[var(--text-ink)] text-[var(--text-ink)] flex items-center justify-center font-bold text-[9px]">
              G
            </span>
            <span>GOAL</span>
          </div>

          <div className="flex items-center gap-1.5">
            <span className="w-3.5 h-3.5 bg-[var(--bg-paper)] border border-gray-400 text-gray-600 flex items-center justify-center font-bold text-[9px]">
              •
            </span>
            <span>OTHER</span>
          </div>
        </div>

        <div className="flex items-center gap-4 text-gray-500">
          <div className="flex items-center gap-1.5">
            <span className="w-5 h-[2px] bg-[var(--text-ink)] inline-block" />
            <span>SEMANTIC (SOLID)</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-5 h-[2px] border-b-2 border-dashed border-[var(--text-ink)] inline-block" />
            <span>STRUCTURAL (DASHED)</span>
          </div>
        </div>
      </div>
    </div>
  );
};
