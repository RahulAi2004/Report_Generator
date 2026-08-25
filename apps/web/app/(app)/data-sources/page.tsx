'use client';

import { useQuery } from '@tanstack/react-query';
import { Badge, Skeleton } from '@/components/ui/primitives';
import { api } from '@/lib/api';

/** Schema explorer: what was discovered in the connected database (spec 36). */
export default function DataSourcesPage() {
  const overview = useQuery({ queryKey: ['overview'], queryFn: api.overview });
  const catalog = useQuery({ queryKey: ['tables'], queryFn: () => api.tables() });

  const stats = overview.data as
    | {
        table_count: number;
        column_count: number;
        relationship_count: number;
        estimated_rows: number;
        tables_without_primary_key: string[];
      }
    | undefined;

  return (
    <>
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-line bg-white px-5">
        <div>
          <h1 className="text-md font-semibold">Data Sources</h1>
          <p className="text-xs text-ink-muted">
            Discovered by introspection. Nothing here is configured by hand.
          </p>
        </div>
        <Badge tone="warn">Development data</Badge>
      </header>

      <div className="flex-1 space-y-3 overflow-y-auto p-5">
        <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
          {([
            ['Tables', stats?.table_count],
            ['Columns', stats?.column_count],
            ['Relationships', stats?.relationship_count],
            ['Estimated rows', stats?.estimated_rows],
          ] as const).map(([label, value]) => (
            <div key={label} className="panel px-4 py-3">
              <span className="text-xs text-ink-muted">{label}</span>
              <span className="mt-0.5 block text-xl font-semibold tabular">
                {overview.isLoading ? '—' : Number(value ?? 0).toLocaleString()}
              </span>
            </div>
          ))}
        </div>

        {stats && stats.tables_without_primary_key.length > 0 && (
          <div className="rounded-lg border border-warn-border bg-warn-soft px-4 py-2.5 text-xs text-warn">
            <strong>{stats.tables_without_primary_key.length}</strong> table(s) have no primary
            key: {stats.tables_without_primary_key.join(', ')}. Record tracing needs a stable row
            identity, so these will need a logical key defined.
          </div>
        )}

        {catalog.isLoading && <Skeleton className="h-64 w-full" />}

        {catalog.data?.categories.map((category) => (
          <section key={category.name} className="panel">
            <div className="panel-header">
              <h2 className="panel-title">{category.name}</h2>
              <span className="text-2xs text-ink-faint">{category.tables.length} tables</span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full border-collapse">
                <thead>
                  <tr className="border-b border-line text-left text-2xs uppercase text-ink-faint">
                    <th className="px-4 py-2 font-medium">Friendly name</th>
                    <th className="px-4 py-2 font-medium">Physical name</th>
                    <th className="px-4 py-2 text-right font-medium">Columns</th>
                    <th className="px-4 py-2 text-right font-medium">Rows (est.)</th>
                    <th className="px-4 py-2 font-medium">Primary key</th>
                  </tr>
                </thead>
                <tbody>
                  {category.tables.map((table) => (
                    <tr key={table.name} className="border-b border-line/60 row-hover last:border-0">
                      <td className="px-4 py-1.5 text-sm font-medium">{table.label}</td>
                      <td className="px-4 py-1.5 font-mono text-xs text-ink-muted">{table.name}</td>
                      <td className="px-4 py-1.5 text-right text-sm tabular">
                        {table.column_count}
                      </td>
                      <td className="px-4 py-1.5 text-right text-sm tabular">
                        {table.estimated_rows.toLocaleString()}
                      </td>
                      <td className="px-4 py-1.5 font-mono text-xs text-ink-muted">
                        {table.primary_key.join(', ') || <span className="text-warn">none</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ))}
      </div>
    </>
  );
}
