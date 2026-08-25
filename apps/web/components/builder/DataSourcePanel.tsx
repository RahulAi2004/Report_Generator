'use client';

import clsx from 'clsx';
import { useMemo, useState } from 'react';
import { Badge, Checkbox, EmptyState, Skeleton } from '@/components/ui/primitives';
import { compactNumber } from '@/lib/format';
import type { SchemaCategory, SchemaTable } from '@/lib/types';

/**
 * Data Sources panel.
 *
 * Categories, table names, row counts and primary keys all come from schema
 * introspection -- nothing here is hardcoded. Point the app at a different
 * database and this panel simply shows that database.
 */
export function DataSourcePanel({
  categories,
  loading,
  selectedTables,
  primaryTable,
  activeTable,
  onToggleTable,
  onSelectTable,
  onSetPrimary,
}: {
  categories: SchemaCategory[];
  loading: boolean;
  selectedTables: string[];
  primaryTable: string;
  activeTable: string | null;
  onToggleTable: (table: SchemaTable) => void;
  onSelectTable: (table: string) => void;
  onSetPrimary: (table: string) => void;
}) {
  const [search, setSearch] = useState('');
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const filtered = useMemo(() => {
    if (!search.trim()) return categories;
    const needle = search.toLowerCase();
    return categories
      .map((category) => ({
        ...category,
        tables: category.tables.filter(
          (table) =>
            table.name.toLowerCase().includes(needle) ||
            table.label.toLowerCase().includes(needle),
        ),
      }))
      .filter((category) => category.tables.length > 0);
  }, [categories, search]);

  return (
    <aside className="flex w-[224px] shrink-0 flex-col border-r border-line bg-white">
      <div className="px-3 pb-2 pt-3">
        <h2 className="panel-title mb-2">Data Sources</h2>
        <div className="relative">
          <svg
            viewBox="0 0 24 24"
            className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5
                       -translate-y-1/2 text-ink-faint"
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
            placeholder="Search tables..."
            className="field pl-7 text-sm"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto pb-3">
        {loading && (
          <div className="space-y-2 px-3">
            {Array.from({ length: 8 }).map((_, index) => (
              <Skeleton key={index} className="h-6 w-full" />
            ))}
          </div>
        )}

        {!loading && filtered.length === 0 && (
          <EmptyState
            title={search ? 'No tables match' : 'No tables available'}
            hint={
              search
                ? 'Try a different search term.'
                : 'Connect a database under Data Sources, or ask an administrator for access.'
            }
          />
        )}

        {!loading &&
          filtered.map((category) => {
            const isCollapsed = collapsed[category.name];
            return (
              <section key={category.name} className="mb-1">
                <button
                  type="button"
                  onClick={() =>
                    setCollapsed((state) => ({ ...state, [category.name]: !isCollapsed }))
                  }
                  className="flex w-full items-center gap-1 px-3 py-1.5 text-left
                             text-xs font-semibold text-ink-muted hover:text-ink"
                >
                  <svg
                    viewBox="0 0 24 24"
                    className={clsx(
                      'h-3 w-3 transition-transform',
                      isCollapsed ? '-rotate-90' : '',
                    )}
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={2.5}
                  >
                    <path d="m6 9 6 6 6-6" />
                  </svg>
                  {category.name}
                  <span className="ml-auto text-2xs font-normal text-ink-faint">
                    {category.tables.length}
                  </span>
                </button>

                {!isCollapsed &&
                  category.tables.map((table) => {
                    const selected = selectedTables.includes(table.name);
                    const isPrimary = primaryTable === table.name;
                    const isActive = activeTable === table.name;

                    return (
                      <div
                        key={table.name}
                        onClick={() => onSelectTable(table.name)}
                        className={clsx(
                          'group flex cursor-pointer items-center gap-2 py-1 pl-4 pr-2',
                          isActive ? 'bg-accent-soft' : 'hover:bg-canvas',
                        )}
                      >
                        <span onClick={(event) => event.stopPropagation()}>
                          <Checkbox
                            checked={selected}
                            onChange={() => onToggleTable(table)}
                          />
                        </span>

                        <svg
                          viewBox="0 0 24 24"
                          className={clsx(
                            'h-3.5 w-3.5 shrink-0',
                            selected ? 'text-accent' : 'text-ink-faint',
                          )}
                          fill="none"
                          stroke="currentColor"
                          strokeWidth={1.6}
                        >
                          <rect x="3" y="4" width="18" height="16" rx="2" />
                          <path d="M3 10h18M9 10v10" />
                        </svg>

                        <span
                          className="min-w-0 flex-1 truncate text-sm"
                          title={`${table.name} · ${table.estimated_rows.toLocaleString()} rows${
                            table.primary_key.length
                              ? ` · PK: ${table.primary_key.join(', ')}`
                              : ' · no primary key'
                          }`}
                        >
                          {table.label}
                        </span>

                        {isPrimary ? (
                          <Badge tone="accent">Primary</Badge>
                        ) : (
                          selected && (
                            <button
                              type="button"
                              onClick={(event) => {
                                event.stopPropagation();
                                onSetPrimary(table.name);
                              }}
                              title="Make this the primary table"
                              className="hidden text-2xs text-ink-faint hover:text-accent
                                         group-hover:block"
                            >
                              Set primary
                            </button>
                          )
                        )}

                        {!selected && !isPrimary && (
                          <span className="shrink-0 text-2xs tabular text-ink-faint">
                            {compactNumber(table.estimated_rows)}
                          </span>
                        )}
                      </div>
                    );
                  })}
              </section>
            );
          })}
      </div>
    </aside>
  );
}
