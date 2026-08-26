'use client';

import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { useMemo } from 'react';
import { PinnedReport } from '@/components/dashboard/PinnedReport';
import { Badge, Button, EmptyState, Skeleton } from '@/components/ui/primitives';
import { api } from '@/lib/api';

/**
 * Dashboard.
 *
 * Pinned reports are the substance: each card runs its report and shows the
 * answer, grouped by the section it was filed under. Platform figures are real
 * counts from the connected database -- spec 26 forbids fabricated production
 * values, and a dashboard of invented numbers is worse than one showing none.
 */
export default function DashboardPage() {
  const overview = useQuery({ queryKey: ['overview'], queryFn: api.overview });
  const pinned = useQuery({
    queryKey: ['reports', 'pinned'],
    queryFn: () => api.listReports({ pinned: true }),
  });
  const reports = useQuery({ queryKey: ['reports'], queryFn: () => api.listReports() });

  const stats = overview.data as
    | {
        table_count?: number;
        relationship_count?: number;
        column_count?: number;
        connection?: { database?: string; mode?: string; read_only_enforced?: boolean };
      }
    | undefined;

  // Grouped by "Module · Section" so the dashboard mirrors how reports were filed.
  const grouped = useMemo(() => {
    const out = new Map<string, typeof list>();
    const list = pinned.data?.reports ?? [];
    for (const report of list) {
      const key = report.module
        ? `${report.module}${report.section ? ` · ${report.section}` : ''}`
        : 'Unfiled';
      out.set(key, [...(out.get(key) ?? []), report]);
    }
    return [...out.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [pinned.data]);

  const recent = (reports.data?.reports ?? []).slice(0, 6);

  return (
    <>
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-line bg-white px-5">
        <div>
          <h1 className="text-md font-semibold">Dashboard</h1>
          <p className="text-xs text-ink-muted">
            Pinned reports and connected database health
          </p>
        </div>
        <div className="flex items-center gap-2">
          {stats?.connection && (
            <Badge tone={stats.connection.mode === 'live' ? 'good' : 'warn'}>
              {stats.connection.mode === 'live' ? 'Live database' : 'Demo data'}
            </Badge>
          )}
          <Link href="/reports/builder">
            <Button variant="primary" size="sm">
              Open Report Builder
            </Button>
          </Link>
        </div>
      </header>

      <div className="flex-1 space-y-4 overflow-y-auto p-5">
        <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
          <Stat label="Tables discovered" value={stats?.table_count} />
          <Stat label="Columns" value={stats?.column_count} />
          <Stat label="Relationships" value={stats?.relationship_count} />
          <Stat label="Saved reports" value={reports.data?.reports.length} />
        </div>

        {/* -------- pinned -------- */}
        <div>
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-semibold">Pinned reports</h2>
            {(pinned.data?.reports.length ?? 0) > 0 && (
              <span className="text-2xs text-ink-faint">
                {pinned.data?.reports.length} pinned
              </span>
            )}
          </div>

          {pinned.isLoading && (
            <div className="grid gap-2 lg:grid-cols-2 2xl:grid-cols-3">
              {Array.from({ length: 3 }).map((_, index) => (
                <Skeleton key={index} className="h-40 w-full" />
              ))}
            </div>
          )}

          {!pinned.isLoading && grouped.length === 0 && (
            <div className="panel">
              <EmptyState
                title="Nothing pinned yet"
                hint="Save a report with “Pin to section dashboard” ticked and it will appear here, showing its current figures."
              />
            </div>
          )}

          {grouped.map(([section, list]) => (
            <section key={section} className="mb-4">
              <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-ink-muted">
                {section}
              </h3>
              <div className="grid gap-2 lg:grid-cols-2 2xl:grid-cols-3">
                {list.map((report) => (
                  <PinnedReport key={report.id} report={report} />
                ))}
              </div>
            </section>
          ))}
        </div>

        {/* -------- recent -------- */}
        {recent.length > 0 && (
          <div>
            <h2 className="mb-2 text-sm font-semibold">Recently updated</h2>
            <div className="panel divide-y divide-line">
              {recent.map((report) => (
                <Link
                  key={report.id}
                  href="/reports"
                  className="flex items-center gap-3 px-4 py-2 transition-colors hover:bg-accent-soft"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium">{report.name}</span>
                    <span className="block truncate text-2xs text-ink-faint">
                      {report.module
                        ? `${report.module}${report.section ? ` · ${report.section}` : ''}`
                        : 'Unfiled'}
                      {' · '}
                      {report.summary.data_sources} table
                      {report.summary.data_sources === 1 ? '' : 's'}
                    </span>
                  </span>
                  {report.is_draft && <Badge tone="warn">Draft</Badge>}
                  <span className="shrink-0 text-2xs text-ink-faint">
                    {new Date(report.updated_at).toLocaleDateString()}
                  </span>
                </Link>
              ))}
            </div>
          </div>
        )}
      </div>
    </>
  );
}

function Stat({ label, value }: { label: string; value?: number }) {
  return (
    <div className="panel px-4 py-3">
      <span className="text-xs text-ink-muted">{label}</span>
      <span className="mt-0.5 block text-xl font-semibold tabular">
        {value === undefined ? '—' : value.toLocaleString()}
      </span>
    </div>
  );
}
