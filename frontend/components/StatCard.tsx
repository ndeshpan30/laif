'use client';

import React from 'react';

interface StatCardProps {
  label: string;
  value: string | number;
  subtext?: string;
  inverted?: boolean;
}

export const StatCard: React.FC<StatCardProps> = ({
  label,
  value,
  subtext,
  inverted = false,
}) => {
  if (inverted) {
    return (
      <div className="border border-[var(--text-ink)] bg-[var(--text-ink)] text-[var(--bg-paper)] p-5 cutout-hover">
        <p className="font-mono text-xs uppercase tracking-widest opacity-80 mb-2">
          {label}
        </p>
        <p className="font-mono text-3xl font-bold tracking-tight mb-1">
          {value}
        </p>
        {subtext && (
          <p className="font-mono text-[11px] opacity-70 uppercase tracking-wider">
            {subtext}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="border border-[var(--border-line)] bg-[var(--bg-paper)] text-[var(--text-ink)] p-5 cutout-hover">
      <p className="font-mono text-xs uppercase tracking-widest opacity-70 mb-2">
        {label}
      </p>
      <p className="font-mono text-3xl font-bold tracking-tight mb-1">
        {value}
      </p>
      {subtext && (
        <p className="font-mono text-[11px] opacity-60 uppercase tracking-wider">
          {subtext}
        </p>
      )}
    </div>
  );
};
