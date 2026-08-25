'use client';

import { useQuery } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { Badge, Button, EmptyState, Skeleton } from '@/components/ui/primitives';
import { api } from '@/lib/api';
import { useBuilder } from '@/store/builder';

export default function ReportsPage() {
  const router = useRouter();
  const builder = useBuilder();
  const { data, isLoading } = useQuery({ queryKey: ['reports'], queryFn: api.listReports });

  async function open(id: string) {
    const report = await api.getReport(id);
    builder.loadReport(report.id, report.name, report.definition);
    router.push('/reports/builder');
  }

  return (
    <>
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-line bg-white px-5">
        <div>
          <h1 className="text-md font-semibold">Reports</h1>
          <p className="text-xs text-ink-muted">
            Saved reports are stored as configuration, not SQL, so they survive schema changes.
          </p>
        </div>
        <Button
          variant="primary"
          size="sm"
          onClick={() => {
            builder.reset();
            router.push('/reports/builder');
          }}
        >
          New Report
        </Button>
      </header>

      <div className="flex-1 overflow-y-auto p-5">
        {isLoading && (
          <div className="space-y-2">
            {Array.from({ length: 4 }).map((_, index) => (
              <Skeleton key={index} className="h-16 w-full" />
            ))}
          </div>
        )}

        {!isLoading && (data?.reports.length ?? 0) === 0 && (
          <div className="panel">
            <EmptyState
              title="No saved reports yet"
              hint="Build one in the report builder and press Save. It will appear here for everyone with access."
            />
          </div>
        )}

        <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
          {data?.reports.map((report) => (
            <button
              key={report.id}
              type="button"
              onClick={() => open(report.id)}
              className="panel p-4 text-left transition-colors hover:border-accent-border hover:bg-accent-soft/40"
            >
              <div className="mb-1 flex items-start justify-between gap-2">
                <h2 className="truncate text-sm font-semibold">{report.name}</h2>
                {report.is_template && <Badge tone="accent">Template</Badge>}
              </div>
              {report.description && (
                <p className="mb-2 line-clamp-2 text-xs text-ink-muted">{report.description}</p>
              )}
              <div className="flex flex-wrap gap-1.5">
                <Badge>{report.summary.data_sources} tables</Badge>
                <Badge>{report.summary.fields_selected} fields</Badge>
                {report.summary.filters > 0 && <Badge>{report.summary.filters} filters</Badge>}
              </div>
              <p className="mt-2 text-2xs text-ink-faint">
                Updated {new Date(report.updated_at).toLocaleDateString()} · run{' '}
                {report.run_count} time{report.run_count === 1 ? '' : 's'}
              </p>
            </button>
          ))}
        </div>
      </div>
    </>
  );
}
