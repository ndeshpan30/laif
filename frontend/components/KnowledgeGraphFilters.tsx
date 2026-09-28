'use client';

import React from 'react';

export interface KnowledgeGraphFiltersProps {
  showSyllabus: boolean;
  onToggleSyllabus: () => void;
  showConstraints: boolean;
  onToggleConstraints: () => void;
  selectedSubject: string;
  onSelectSubject: (subject: string) => void;
  subjectOptions: string[];
  threshold: number;
  onChangeThreshold: (val: number) => void;
  visibleNodesCount: number;
  totalNodesCount: number;
  visibleEdgesCount: number;
  totalEdgesCount: number;
}

export const KnowledgeGraphFilters: React.FC<KnowledgeGraphFiltersProps> = ({
  showSyllabus,
  onToggleSyllabus,
  showConstraints,
  onToggleConstraints,
  selectedSubject,
  onSelectSubject,
  subjectOptions,
  threshold,
  onChangeThreshold,
  visibleNodesCount,
  totalNodesCount,
  visibleEdgesCount,
  totalEdgesCount,
}) => {
  return (
    <div className="w-full border-b border-[var(--border-line)] bg-[var(--bg-paper)] p-3 md:p-4 flex flex-wrap items-center justify-between gap-4 font-mono text-xs select-none">
      {/* Left: Type Toggles & Subject Dropdown */}
      <div className="flex flex-wrap items-center gap-3">
        {/* Type Toggles */}
        <div className="flex items-center gap-2">
          <span className="text-gray-500 uppercase tracking-widest text-[10px]">
            TYPES:
          </span>
          <button
            type="button"
            onClick={onToggleSyllabus}
            className={`px-2.5 py-1.5 border uppercase tracking-wider font-mono text-xs transition-colors rounded-none ${
              showSyllabus
                ? 'bg-[var(--text-ink)] text-[var(--bg-paper)] border-[var(--text-ink)] font-bold'
                : 'bg-transparent text-gray-400 border-[var(--border-line)] hover:border-[var(--text-ink)]'
            }`}
          >
            {showSyllabus ? '[x] SYLLABUS' : '[ ] SYLLABUS'}
          </button>

          <button
            type="button"
            onClick={onToggleConstraints}
            className={`px-2.5 py-1.5 border uppercase tracking-wider font-mono text-xs transition-colors rounded-none ${
              showConstraints
                ? 'bg-[var(--accent)] text-white border-[var(--accent)] font-bold'
                : 'bg-transparent text-gray-400 border-[var(--border-line)] hover:border-[var(--accent)]'
            }`}
          >
            {showConstraints ? '[x] CONSTRAINTS' : '[ ] CONSTRAINTS'}
          </button>
        </div>

        {/* Divider */}
        <span className="hidden sm:inline-block text-gray-300">|</span>

        {/* Subject Filter Dropdown */}
        <div className="flex items-center gap-2">
          <label htmlFor="subject-select" className="text-gray-500 uppercase tracking-widest text-[10px]">
            SUBJECT:
          </label>
          <div className="relative">
            <select
              id="subject-select"
              value={selectedSubject}
              onChange={(e) => onSelectSubject(e.target.value)}
              className="bg-[var(--bg-paper)] text-[var(--text-ink)] border border-[var(--border-line)] hover:border-[var(--text-ink)] focus:border-[var(--text-ink)] px-2.5 py-1.5 font-mono text-xs uppercase tracking-wider rounded-none outline-none cursor-pointer"
            >
              <option value="ALL">ALL SUBJECTS</option>
              {subjectOptions.map((subj) => (
                <option key={subj} value={subj}>
                  {subj}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {/* Right: Similarity Slider & Metrics */}
      <div className="flex flex-wrap items-center gap-4">
        {/* Similarity Threshold Slider */}
        <div className="flex items-center gap-2">
          <label htmlFor="similarity-slider" className="text-gray-500 uppercase tracking-widest text-[10px]">
            SIMILARITY:
          </label>
          <input
            id="similarity-slider"
            type="range"
            min="0.50"
            max="0.95"
            step="0.01"
            value={threshold}
            onChange={(e) => onChangeThreshold(parseFloat(e.target.value))}
            className="w-24 sm:w-28 accent-[var(--text-ink)] cursor-pointer"
          />
          <span className="font-mono text-xs font-bold text-[var(--text-ink)] min-w-[50px]">
            ≥ {threshold.toFixed(2)}
          </span>
        </div>

        {/* Metrics Counter */}
        <div className="flex items-center gap-2 border border-[var(--border-line)] px-2.5 py-1.5 bg-[var(--bg-paper)] text-[10px] md:text-xs font-mono uppercase tracking-wider">
          <span className="text-gray-500">NODES:</span>
          <span className="font-bold text-[var(--text-ink)]">
            {visibleNodesCount} / {totalNodesCount}
          </span>
          <span className="text-gray-300">|</span>
          <span className="text-gray-500">EDGES:</span>
          <span className="font-bold text-[var(--text-ink)]">
            {visibleEdgesCount} / {totalEdgesCount}
          </span>
        </div>
      </div>
    </div>
  );
};
