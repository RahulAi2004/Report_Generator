'use client';

import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Badge, EmptyState, Skeleton } from '@/components/ui/primitives';
import { api } from '@/lib/api';

/**
 * Schema explorer (spec 36).
 *
 * Every row here came from introspecting the connected database. Nothing is
 * configured by hand, so this page is also the honest answer to "what is this
 * thing actually reading?" -- which is why it states the database name and the
 * connection mode rather than a decorative badge.
 */

interface Connection {
  database: string;
  dialect: string;
  mode: string;
  read_only_enforced: boolean;
  is_replica: boolean;
}

interface Overview {
  connection?: Connection;
  table_count: number;
  column_count: number;
  relationship_count: number;
  estimated_rows: number;
  tables_without_primary_key: string[];
}

export default function DataSourcesPage() {
  const overview = useQuery({ queryKey: ['overview'], queryFn: api.overview });
  const catalog = useQuery({ queryKey: ['tables'], queryFn: () => api.tables() });
  const [search, setSearch] = useState('');

  const stats = overview.data as Overview | undefined;
  const connection = stats?.connection;

  const categories = useMemo(() => {
    const all = catalog.data?.categories ?? [];
    if (!search.trim()) return all;
    const needle = search.trim().toLowerCase();
    return all
      .map((category) => ({
        ...category,
        tables: category.tables.filter(
          (table) =>
            table.name.toLowerCase().includes(needle) ||
            table.label.toLowerCase().includes(needle),
        ),
      }))
      .filter((category) => category.tables.length > 0);
  }, [catalog.data, search]);

  const shown = categories.reduce((total, category) => total + category.tables.length, 0);

  return (
    <>
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-line bg-white px-5">
        <div>
          <h1 className="text-md font-semibold">Data Sources</h1>
          <p className="text-xs text-ink-muted">
            Discovered by introspection. Nothing here is configured by hand.
          </p>
        </div>

        {connection && (
          <div className="flex items-center gap-2">
            {connection.mode === 'mock' ? (
              <Badge tone="warn">Demo data</Badge>
            ) : (
              <Badge tone="good">Live database</Badge>
            )}
            {connection.is_replica && <Badge>Read replica</Badge>}
            {connection.read_only_enforced && <Badge tone="accent">Read-only</Badge>}
            <span className="font-mono text-xs text-ink-muted">
              {connection.dialect} · {connection.database}
            </span>
          </div>
        )}
      </header>

      <div className="flex-1 space-y-3 overflow-y-auto p-5">
        <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
          {(
            [
              ['Tables', stats?.table_count],
              ['Columns', stats?.column_count],
              ['Relationships', stats?.relationship_count],
              ['Estimated rows', stats?.estimated_rows],
            ] as const
          ).map(([label, value]) => (
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

        <div className="flex items-center gap-3">
          <div className="relative flex-1 max-w-sm">
            <svg
              viewBox="0 0 24 24"
              className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-faint"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
            >
              <circle cx="11" cy="11" r="7" />
              <path d="m20 20-3.5-3.5" />
            </svg>
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search tables…"
              className="field pl-8"
            />
          </div>
          {search && (
            <span className="text-xs text-ink-muted">
              {shown} of {stats?.table_count ?? 0} tables
            </span>
          )}
        </div>

        {catalog.isLoading && <Skeleton className="h-64 w-full" />}

        {!catalog.isLoading && categories.length === 0 && (
          <div className="panel">
            <EmptyState
              title={search ? 'No tables match that search' : 'No tables available'}
              hint={
                search
                  ? 'Try part of a table name.'
                  : 'The connected database returned no tables you have access to.'
              }
            />
          </div>
        )}

        {categories.map((category) => (
          <section key={category.name} className="panel">
            <div className="panel-header">
              <h2 className="panel-title">{category.name}</h2>
              <span className="text-2xs text-ink-faint">
                {category.tables.length} table{category.tables.length === 1 ? '' : 's'}
              </span>
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
                    <tr
                      key={table.name}
                      className="border-b border-line/60 row-hover last:border-0"
                    >
                      <td className="px-4 py-1.5 text-sm font-medium">{table.label}</td>
                      <td className="px-4 py-1.5 font-mono text-xs text-ink-muted">
                        {table.name}
                      </td>
                      <td className="px-4 py-1.5 text-right text-sm tabular">
                        {table.column_count}
                      </td>
                      <td className="px-4 py-1.5 text-right text-sm tabular">
                        {table.estimated_rows === null ? (
                          <span className="text-ink-faint" title="Views carry no row statistics">
                            —
                          </span>
                        ) : (
                          table.estimated_rows.toLocaleString()
                        )}
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
