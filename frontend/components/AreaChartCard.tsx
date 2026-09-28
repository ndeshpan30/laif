'use client';

import React from 'react';
import {
  AreaChart,
  Area,
  XAxis,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';

interface AreaChartCardProps {
  id: string;
  title: string;
  unit?: string;
  data: Array<{ date: string; value: number }>;
}

export const AreaChartCard: React.FC<AreaChartCardProps> = ({
  id,
  title,
  unit,
  data,
}) => {
  return (
    <div className="border border-[var(--border-line)] bg-[var(--bg-paper)] p-5 cutout-hover">
      <div className="flex justify-between items-center mb-4">
        <h3 className="font-mono text-xs uppercase tracking-widest text-[var(--text-ink)]">
          {title} {unit ? `(${unit})` : ''}
        </h3>
        <span className="font-mono text-[10px] text-gray-500 uppercase tracking-widest">
          7-DAY ROLLING
        </span>
      </div>

      <div className="w-full h-52">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart margin={{ top: 8, right: 8, bottom: 0, left: 8 }} data={data}>
            <defs>
              <pattern
                id={`dots-${id}`}
                width="16"
                height="16"
                patternUnits="userSpaceOnUse"
              >
                <circle cx="2" cy="2" r="1" fill="var(--dot-color)" />
              </pattern>
              <linearGradient id={`fillGradient-${id}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="var(--text-ink)" stopOpacity={0.25} />
                <stop offset="95%" stopColor="var(--text-ink)" stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <rect width="100%" height="100%" fill={`url(#dots-${id})`} />
            <XAxis
              dataKey="date"
              axisLine={false}
              tickLine={false}
              tick={{ fill: 'var(--text-ink)', fontSize: 10, fontFamily: 'JetBrains Mono' }}
              dy={8}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: 'var(--bg-paper)',
                borderColor: 'var(--border-line)',
                borderRadius: '0px',
                fontFamily: 'JetBrains Mono',
                fontSize: '12px',
              }}
            />
            <Area
              type="monotone"
              dataKey="value"
              stroke="var(--text-ink)"
              fillOpacity={1}
              fill={`url(#fillGradient-${id})`}
              strokeWidth={2}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};
