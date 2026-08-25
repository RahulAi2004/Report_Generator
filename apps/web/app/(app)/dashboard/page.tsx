'use client';

import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { Badge, Button } from '@/components/ui/primitives';
import { api } from '@/lib/api';

/**
 * Dashboard shell.
 *
 * KPI cards show only what can be computed from real data today. Spec 26 is
 * explicit that no fabricated production values may appear, so the business
 * KPIs stay blank and labelled rather than filled with plausible noise -- a
 * dashboard showing invented numbers is worse than one showing none.
 */
export default function DashboardPage() {
  const overview = useQuery({ queryKey: ['overview'], queryFn: api.overview });
  const reports = useQuery({ queryKey: ['reports'], queryFn: api.listReports });

  const stats = overview.data as
    | { table_count?: number; relationship_count?: number }
    | undefined;

  return (
    <>
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-line bg-white px-5">
        <div>
          <h1 className="text-md font-semibold">Dashboard</h1>
          <p className="text-xs text-ink-muted">Platform status and connected database health</p>
        </div>
        <Link href="/reports/builder">
          <Button variant="primary" size="sm">
            Open Report Builder
          </Button>
        </Link>
      </header>

      <div className="flex-1 space-y-3 overflow-y-auto p-5">
        <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
          <Stat label="Tables discovered" value={stats?.table_count} />
          <Stat label="Relationships" value={stats?.relationship_count} />
          <Stat label="Saved reports" value={reports.data?.reports.length} />
          <Stat label="Connection" value="Read-only" text />
        </div>

        <section className="panel p-5">
          <h2 className="text-sm font-semibold">Business KPIs</h2>
          <p className="mt-1 max-w-2xl text-xs text-ink-muted">
            Sales trend, outstanding receivables, conversion rate and fulfilment time are defined
            in the semantic layer and arrive with it, so that every report computes them the same
            way. They are intentionally blank rather than filled with sample figures.
          </p>
          <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {['Total Sales', 'Outstanding Amount', 'Conversion Rate', 'Critical Anomalies'].map(
              (label) => (
                <div key={label} className="rounded border border-dashed border-line px-3 py-4">
                  <span className="text-xs text-ink-muted">{label}</span>
                  <span className="mt-1 block text-lg font-semibold text-ink-faint">—</span>
                  <Badge className="mt-1">Phase 9</Badge>
                </div>
              ),
            )}
          </div>
        </section>
      </div>
    </>
  );
}

function Stat({
  label,
  value,
  text,
}: {
  label: string;
  value?: number | string;
  text?: boolean;
}) {
  return (
    <div className="panel px-4 py-3">
      <span className="text-xs text-ink-muted">{label}</span>
      <span className="mt-0.5 block text-xl font-semibold tabular">
        {value == null ? '—' : text ? value : Number(value).toLocaleString()}
      </span>
    </div>
  );
}
