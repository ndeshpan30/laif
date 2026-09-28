'use client';

import React from 'react';

export interface BujoEntry {
  id: string;
  entry_type: 'task' | 'event' | 'note' | 'metric' | 'memory' | 'migration';
  content: string;
  timestamp: string;
  is_completed?: boolean;
}

interface BujoLogStreamProps {
  entries: BujoEntry[];
}

export const BujoLogStream: React.FC<BujoLogStreamProps> = ({ entries }) => {
  const getGlyph = (entry: BujoEntry) => {
    if (entry.is_completed) return 'X';
    switch (entry.entry_type) {
      case 'task':
        return '·';
      case 'event':
        return 'O';
      case 'metric':
        return '∷';
      case 'note':
        return '—';
      case 'migration':
        return '>';
      case 'memory':
        return '△';
      default:
        return '·';
    }
  };

  return (
    <div className="border border-[var(--border-line)] bg-[var(--bg-paper)] p-6 cutout-hover">
      <div className="flex justify-between items-center mb-6 pb-2 border-b border-[var(--border-line)]">
        <h3 className="font-mono text-xs uppercase tracking-widest text-[var(--text-ink)]">
          Rapid Log Stream
        </h3>
        <span className="font-mono text-[10px] text-gray-500 uppercase tracking-widest">
          BUJO GRAMMAR
        </span>
      </div>

      {entries.length === 0 ? (
        <div className="py-12 text-center text-gray-400 font-mono text-xs uppercase tracking-wider">
          No log entries yet. Chat to rapidly record habits, workouts, or notes.
        </div>
      ) : (
        <div className="space-y-4">
          {entries.map((item, idx) => (
            <div
              key={item.id || idx}
              className="flex items-start gap-4 pb-3 border-b border-dashed border-[var(--border-line)] last:border-0"
            >
              <span className="font-mono text-base font-bold text-[var(--text-ink)] w-5 text-center shrink-0">
                {getGlyph(item)}
              </span>
              <div className="flex-1 min-w-0">
                <p className="text-sm text-[var(--text-ink)] font-normal leading-snug break-words">
                  {item.content}
                </p>
                <p className="font-mono text-[10px] text-gray-500 uppercase tracking-wider mt-1">
                  {item.timestamp}
                </p>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
