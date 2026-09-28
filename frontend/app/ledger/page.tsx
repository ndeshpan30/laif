'use client';

import React, { useState, useEffect } from 'react';
import Link from 'next/link';
import { useTheme } from 'next-themes';
import { ArrowLeft, Sun, Moon, RefreshCw } from 'lucide-react';
import { AreaChartCard } from '@/components/AreaChartCard';
import { StatCard } from '@/components/StatCard';
import { BujoLogStream, BujoEntry } from '@/components/BujoLogStream';

const DEFAULT_USER_ID = '00000000-0000-0000-0000-000000000001';
const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export default function LedgerPage() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  const [isLoading, setIsLoading] = useState(true);

  // Stats
  const [streakCount, setStreakCount] = useState(0);
  const [scheduleStatus, setScheduleStatus] = useState('ON_TRACK');
  const [scheduledTasksCount, setScheduledTasksCount] = useState(0);
  const [activeTrackersCount, setActiveTrackersCount] = useState(0);

  // BuJo Entries
  const [entries, setEntries] = useState<BujoEntry[]>([]);

  // Chart data per tracker
  const [charts, setCharts] = useState<Array<{ id: string; title: string; unit: string; data: Array<{ date: string; value: number }> }>>([]);

  const loadData = async () => {
    setIsLoading(true);
    try {
      // 1. Fetch telemetry logs
      const logRes = await fetch(`${API_URL}/api/telemetry/logs?user_id=${DEFAULT_USER_ID}&limit=30`);
      if (logRes.ok) {
        const rawLogs = await logRes.json();
        const mapped: BujoEntry[] = rawLogs.map((l: any) => ({
          id: l.id,
          entry_type: l.entry_type,
          content: l.content || 'Logged entry',
          timestamp: new Date(l.logged_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
          is_completed: l.entry_type === 'task' && l.content?.includes('completed'),
        }));
        setEntries(mapped);
        setStreakCount(mapped.length);
      }

      // 2. Fetch schedule items
      const schedRes = await fetch(`${API_URL}/api/schedule/items?user_id=${DEFAULT_USER_ID}`);
      if (schedRes.ok) {
        const items = await schedRes.json();
        if (items.length > 0) {
          setScheduledTasksCount(items.length);
          const hasBumped = items.some((it: any) => it.migration_count > 0 && !it.start_time);
          if (hasBumped) {
            setScheduleStatus('LAGGING');
          }
        }
      }

      // 3. Fetch analytics
      const analyticsRes = await fetch(`${API_URL}/api/telemetry/analytics/${DEFAULT_USER_ID}`);
      if (analyticsRes.ok) {
        const analytics = await analyticsRes.json();
        // If daily metrics exist, update charts
      }
    } catch (e) {
      console.warn('API fetch warning, using ledger state:', e);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    setMounted(true);
    loadData();
  }, []);

  return (
    <main className="min-h-screen max-w-7xl mx-auto px-4 py-8">
      {/* Editorial Header per UI_UX.md Section 5 */}
      <header className="mb-8">
        <div className="flex justify-between items-center mb-4">
          <Link
            href="/"
            className="inline-flex items-center gap-2 font-mono text-xs uppercase tracking-widest text-[var(--text-ink)] hover:opacity-75 transition-opacity"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            <span>RETURN TO CONVERSATION</span>
          </Link>

          <div className="flex items-center gap-3">
            <Link
              href="/web"
              className="flex items-center gap-1 px-3 py-1.5 border border-[var(--border-line)] text-[var(--text-ink)] hover:border-[var(--text-ink)] font-mono text-xs uppercase tracking-widest transition-colors"
            >
              <span>THE WEB ↗</span>
            </Link>

            <span className="flex items-center gap-1 px-3 py-1.5 bg-[var(--text-ink)] text-[var(--bg-paper)] font-mono text-xs uppercase tracking-widest select-none">
              <span>THE LEDGER</span>
            </span>

            <button
              onClick={loadData}
              title="Refresh Ledger"
              className="p-2 border border-[var(--border-line)] text-[var(--text-ink)] hover:border-[var(--text-ink)] transition-colors"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />
            </button>

            {mounted && (
              <button
                onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
                title="Toggle Theme"
                className="p-2 border border-[var(--border-line)] text-[var(--text-ink)] hover:border-[var(--text-ink)] transition-colors"
              >
                {theme === 'dark' ? <Sun className="w-3.5 h-3.5" /> : <Moon className="w-3.5 h-3.5" />}
              </button>
            )}
          </div>
        </div>

        <h1 className="font-serif text-5xl md:text-6xl font-bold tracking-tight text-[var(--text-ink)] mb-2">
          The Ledger.
        </h1>
        <div className="border-b-4 border-[var(--text-ink)] mb-2" />
        <p className="font-mono text-xs text-gray-500 uppercase tracking-widest">
          Vol. 1 | Life Telemetry & Schedule Adherence — Google OR-Tools CP-SAT Solved
        </p>
      </header>

      {/* 12-Column Asymmetric Grid Layout per UI_UX.md */}
      <div className="grid grid-cols-1 lg:grid-cols-12 border border-[var(--border-line)] bg-[var(--bg-paper)]">
        {/* Left Column (8 cols): Charts & Stat Cards */}
        <div className="lg:col-span-8 p-6 lg:border-r border-[var(--border-line)] space-y-8">
          {/* Stat Cards Row (Newsprint rule: invert one section for visual accent) */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <StatCard
              label="STREAK"
              value={`${streakCount} DAYS`}
              subtext="WORKOUT HABIT"
              inverted={false}
            />
            <StatCard
              label="SCHEDULE"
              value={scheduleStatus}
              subtext={scheduleStatus === 'LAGGING' ? 'PREEMPTION ACTIVE' : '0 CONFLICTS'}
              inverted={true} // Inverted dark accent card per UI_UX.md
            />
            <StatCard
              label="TASKS SOLVED"
              value={scheduledTasksCount}
              subtext="15-MIN TICKS"
              inverted={false}
            />
            <StatCard
              label="SOLVE TIME"
              value="< 50ms"
              subtext="CP-SAT DETERMINISTIC"
              inverted={false}
            />
          </div>

          {/* Area Charts per Tracker */}
          <div className="space-y-6">
            <div className="flex justify-between items-center pb-2 border-b border-[var(--border-line)]">
              <h2 className="font-mono text-xs uppercase tracking-widest text-[var(--text-ink)]">
                Quantified-Self Telemetry Charts
              </h2>
              <span className="font-mono text-[10px] text-gray-500 uppercase tracking-widest">
                AREA + DOT-GRID TEXTURE
              </span>
            </div>

            <div className="grid grid-cols-1 gap-6">
              {charts.length === 0 ? (
                <div className="py-12 border border-dashed border-[var(--border-line)] text-center text-gray-400 font-mono text-xs uppercase tracking-wider">
                  No telemetry charts yet. Complete your cold-start interview to begin tracking.
                </div>
              ) : (
                charts.map((c) => (
                  <AreaChartCard
                    key={c.id}
                    id={c.id}
                    title={c.title}
                    unit={c.unit}
                    data={c.data}
                  />
                ))
              )}
            </div>
          </div>
        </div>

        {/* Right Column (4 cols): Reverse-Chronological BuJo Stream */}
        <div className="lg:col-span-4 p-6">
          <BujoLogStream entries={entries} />
        </div>
      </div>
    </main>
  );
}
