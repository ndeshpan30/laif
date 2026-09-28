'use client';

import React, { useEffect, useRef, useState } from 'react';
import * as d3Force from 'd3-force';

export interface GraphNode extends d3Force.SimulationNodeDatum {
  id: string;
  label: string;
  type: 'syllabus_module' | 'episodic_constraint';
  subject?: string | null;
  snippet: string;
  raw_content?: string;
  degree: number;
  created_at: string;
}

export interface GraphEdge extends d3Force.SimulationLinkDatum<GraphNode> {
  source: string | GraphNode;
  target: string | GraphNode;
  weight: number;
}

export interface KnowledgeGraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  total_nodes?: number;
  total_edges?: number;
}

interface KnowledgeGraphViewProps {
  data: KnowledgeGraphData;
  onSelectNode?: (node: GraphNode) => void;
  selectedNodeId?: string | null;
  className?: string;
  totalContextCount?: number;
}

export const KnowledgeGraphView: React.FC<KnowledgeGraphViewProps> = ({
  data,
  onSelectNode,
  selectedNodeId,
  className,
  totalContextCount,
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

    // Ensure edge source/targets are valid string IDs and connect existing nodes
    const validEdges: GraphEdge[] = [];
    for (const e of data.edges || []) {
      const srcId = typeof e.source === 'object' ? (e.source as GraphNode).id : String(e.source);
      const tgtId = typeof e.target === 'object' ? (e.target as GraphNode).id : String(e.target);
      if (nodeIds.has(srcId) && nodeIds.has(tgtId)) {
        validEdges.push({
          source: srcId,
          target: tgtId,
          weight: e.weight,
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
          .distance((d) => 120 - (d.weight || 0.75) * 50)
      )
      .force('charge', d3Force.forceManyBody().strength(-240))
      .force('center', d3Force.forceCenter(width / 2, height / 2))
      .force('collision', d3Force.forceCollide().radius((d) => 18 + (d as GraphNode).degree * 3));

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
        node.fy = Math.max(20, Math.min(dimensions.height - 20, moveEvent.clientY - rect.top));
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

  // Zero state when user has fewer than 5 context items total in database
  const baselineCount = totalContextCount ?? data.total_nodes ?? (data.nodes ? data.nodes.length : 0);
  if (baselineCount < 5) {
    return (
      <div className={className || "w-full h-[600px] border border-[var(--border-line)] bg-[var(--bg-paper)] flex flex-col items-center justify-center p-8 text-center"}>
        <div className="font-mono text-xs uppercase tracking-widest text-[var(--accent)] mb-3">
          [ CONTEXT MAP // ZERO STATE ]
        </div>
        <p className="max-w-md font-mono text-sm text-gray-500 leading-relaxed mb-6">
          Your context map fills in as you upload syllabi and declare episodic guardrails.
          At least 5 semantic context nodes are required to compute topological similarity edges.
        </p>
        <span className="font-mono text-[10px] text-gray-400 uppercase tracking-widest border border-dashed border-[var(--border-line)] px-3 py-1.5">
          CURRENT NODES: {baselineCount} / 5
        </span>
      </div>
    );
  }

  // Filter empty state when active filters match zero nodes
  if (!data.nodes || data.nodes.length === 0) {
    return (
      <div className={className || "w-full h-[600px] border border-[var(--border-line)] bg-[var(--bg-paper)] flex flex-col items-center justify-center p-8 text-center"}>
        <div className="font-mono text-xs uppercase tracking-widest text-gray-400 mb-2">
          [ NO CONTEXT NODES MATCH ACTIVE FILTERS ]
        </div>
        <p className="max-w-md font-mono text-xs text-gray-500">
          Adjust the type toggles or select a different subject above to inspect context nodes.
        </p>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className={className || "relative w-full h-[650px] border border-[var(--border-line)] bg-[var(--bg-paper)] overflow-hidden select-none"}
      onMouseMove={(e) => {
        const rect = e.currentTarget.getBoundingClientRect();
        setMousePos({ x: e.clientX - rect.left, y: e.clientY - rect.top });
      }}
    >
      {/* Editorial Legend Header Overlay */}
      <div className="absolute top-4 left-4 z-10 bg-[var(--bg-paper)]/90 backdrop-blur-sm border border-[var(--border-line)] p-3 pointer-events-none">
        <div className="font-mono text-[10px] uppercase tracking-widest font-bold text-[var(--text-ink)] mb-1.5">
          THE WEB // CONTEXT MAP
        </div>
        <div className="flex items-center gap-4 font-mono text-[10px] uppercase tracking-wider text-gray-500">
          <div className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 bg-[var(--text-ink)] border border-[var(--text-ink)] inline-block" />
            <span>Syllabus Module</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 bg-[var(--accent)] border border-[var(--accent)] inline-block" />
            <span>Episodic Guardrail</span>
          </div>
        </div>
      </div>

      {/* SVG Canvas with Dot Grid Texture */}
      <svg className="w-full h-full cursor-crosshair">
        {/* Render Edges */}
        <g className="edges">
          {simEdges.map((edge, idx) => {
            const src = typeof edge.source === 'object' ? (edge.source as GraphNode) : null;
            const tgt = typeof edge.target === 'object' ? (edge.target as GraphNode) : null;
            if (!src || !tgt || src.x == null || src.y == null || tgt.x == null || tgt.y == null) return null;

            const isIncident = hoveredNode && (hoveredNode.id === src.id || hoveredNode.id === tgt.id);
            const opacity = isIncident ? 0.9 : Math.max(0.2, (edge.weight || 0.7) * 0.7);
            const strokeWidth = isIncident ? 2.5 : Math.max(1.0, (edge.weight || 0.7) * 2.0);

            return (
              <line
                key={`edge-${idx}`}
                x1={src.x}
                y1={src.y}
                x2={tgt.x}
                y2={tgt.y}
                stroke="var(--text-ink)"
                strokeOpacity={opacity}
                strokeWidth={strokeWidth}
                strokeDasharray={edge.weight < 0.65 ? '3 3' : undefined}
              />
            );
          })}
        </g>

        {/* Render Nodes (Brutalist Sharp Rectangles) */}
        <g className="nodes">
          {simNodes.map((node) => {
            if (node.x == null || node.y == null) return null;

            const isConstraint = node.type === 'episodic_constraint';
            const isSelected = selectedNodeId === node.id;
            const isHovered = hoveredNode?.id === node.id;

            // Size scales with server-precomputed degree
            const size = Math.max(14, 14 + node.degree * 4);
            const halfSize = size / 2;

            const fillColor = isConstraint
              ? 'var(--accent)'
              : isSelected
              ? 'var(--text-ink)'
              : 'var(--bg-paper)';

            const strokeColor = isConstraint ? 'var(--accent)' : 'var(--text-ink)';
            const textColor = isSelected || isConstraint ? 'var(--bg-paper)' : 'var(--text-ink)';

            return (
              <g
                key={node.id}
                transform={`translate(${node.x}, ${node.y})`}
                className="cursor-pointer transition-transform"
                onClick={() => onSelectNode?.(node)}
                onMouseDown={(e) => handleDrag(node.id, e)}
                onMouseEnter={() => setHoveredNode(node)}
                onMouseLeave={() => setHoveredNode(null)}
              >
                {/* Node Box */}
                <rect
                  x={-halfSize}
                  y={-halfSize}
                  width={size}
                  height={size}
                  fill={fillColor}
                  stroke={strokeColor}
                  strokeWidth={isSelected ? 3 : isHovered ? 2 : 1.5}
                />

                {/* Node Glyph inside box */}
                <text
                  x="0"
                  y="3"
                  textAnchor="middle"
                  fill={textColor}
                  fontSize={size * 0.55}
                  fontFamily="JetBrains Mono, monospace"
                  fontWeight="bold"
                  pointerEvents="none"
                >
                  {isConstraint ? '!' : 'M'}
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
            top: Math.min(dimensions.height - 120, mousePos.y + 16),
          }}
        >
          <div className="flex items-center justify-between pb-1 mb-1.5 border-b border-[var(--bg-paper)]/30 text-[9px] uppercase tracking-widest opacity-80">
            <span>{hoveredNode.type.replace('_', ' ')}</span>
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
    </div>
  );
};
