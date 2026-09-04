'use client';

import clsx from 'clsx';
import { useMemo, useState } from 'react';
import { Badge, Checkbox, EmptyState, Skeleton } from '@/components/ui/primitives';
import { ResizeHandle, useResizableWidth } from '@/components/ui/Resizable';
import { compactNumber } from '@/lib/format';
import type { SchemaCategory, SchemaTable } from '@/lib/types';

/**
 * Data Sources panel.
 *
 * Categories, table names, row counts and primary keys all come from schema
 * introspection -- nothing here is hardcoded. Point the app at a different
 * database and this panel simply shows that database.
 *
 * Two things it does deliberately.
 *
 * Tables synced from an API sit in their own section rather than mixed in with
 * the database's own. They behave identically in a report, but where a number
 * came from is the first thing anybody asks about it, and ad spend sitting
 * between two CRM tables invites the assumption that it is one of them.
 *
 * And the panel is resizable. Table names are as long as the database made
 * them; two hundred fixed pixels truncates exactly the ones that need reading.
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
  onAddAllFields,
}: {
  categories: SchemaCategory[];
  loading: boolean;
  selectedTables: string[];
  primaryTable: string;
  activeTable: string | null;
  onToggleTable: (table: SchemaTable) => void;
  onSelectTable: (table: string) => void;
  onSetPrimary: (table: string) => void;
  /** Add every field of a table in one action, without opening it first. */
  onAddAllFields?: (table: SchemaTable) => void;
}) {
  const [search, setSearch] = useState('');
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const { width, onPointerDown, reset } = useResizableWidth('data-sources', 220);

  const filtered = useMemo(() => {
    if (!search.trim()) return categories;
    const needle = search.toLowerCase();
    return categories
      .map((category) => ({
        ...category,
        tables: category.tables.filter(
          (table) =>
            table.name.toLowerCase().includes(needle) ||
            table.label.toLowerCase().includes(needle) ||
            // Somebody who knows where the data lives searches for that, not
            // for what the table happens to be called.
            (table.schema ?? '').toLowerCase().includes(needle) ||
            category.name.toLowerCase().includes(needle),
        ),
      }))
      .filter((category) => category.tables.length > 0);
  }, [categories, search]);

  // A table synced from an API executes locally, which is what `upload` means
  // here -- and is exactly the set worth keeping apart.
  const isConnected = (category: SchemaCategory) =>
    category.tables.length > 0 && category.tables.every((table) => table.kind === 'upload');

  const fromDatabase = filtered.filter((category) => !isConnected(category));
  const fromApis = filtered.filter(isConnected);
  const bothPresent = fromDatabase.length > 0 && fromApis.length > 0;

  const sectionProps = {
    collapsed,
    setCollapsed,
    selectedTables,
    primaryTable,
    activeTable,
    onToggleTable,
    onSelectTable,
    onSetPrimary,
    onAddAllFields,
  };

  return (
    <>
      <aside
        className="flex shrink-0 flex-col border-r border-line bg-white"
        style={{ width }}
      >
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
              placeholder="Search tables, or a schema…"
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
                  ? 'Try a different search term, or the name of a schema.'
                  : 'Connect a database under Data Sources, or ask an administrator for access.'
              }
            />
          )}

          {!loading && bothPresent && <GroupHeading first>From your database</GroupHeading>}
          {!loading &&
            fromDatabase.map((category) => (
              <CategorySection key={category.name} category={category} {...sectionProps} />
            ))}

          {!loading && fromApis.length > 0 && (
            <>
              <GroupHeading>Synced from an API</GroupHeading>
              {fromApis.map((category) => (
                <CategorySection key={category.name} category={category} {...sectionProps} />
              ))}
            </>
          )}
        </div>
      </aside>

      <ResizeHandle onPointerDown={onPointerDown} onReset={reset} label="the table list" />
    </>
  );
}

function GroupHeading({
  children,
  first,
}: {
  children: React.ReactNode;
  first?: boolean;
}) {
  return (
    <p
      className={clsx(
        'px-3 pb-1 text-[10px] font-semibold uppercase tracking-wide text-ink-faint',
        first ? 'pt-2' : 'mt-2 border-t border-line pt-2.5',
      )}
    >
      {children}
    </p>
  );
}

function CategorySection({
  category,
  collapsed,
  setCollapsed,
  selectedTables,
  primaryTable,
  activeTable,
  onToggleTable,
  onSelectTable,
  onSetPrimary,
  onAddAllFields,
}: {
  category: SchemaCategory;
  collapsed: Record<string, boolean>;
  setCollapsed: React.Dispatch<React.SetStateAction<Record<string, boolean>>>;
  selectedTables: string[];
  primaryTable: string;
  activeTable: string | null;
  onToggleTable: (table: SchemaTable) => void;
  onSelectTable: (table: string) => void;
  onSetPrimary: (table: string) => void;
  onAddAllFields?: (table: SchemaTable) => void;
}) {
  const isCollapsed = collapsed[category.name];

  return (
    <section className="mb-1">
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
          className={clsx('h-3 w-3 transition-transform', isCollapsed ? '-rotate-90' : '')}
          fill="none"
          stroke="currentColor"
          strokeWidth={2.5}
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
        <span className="min-w-0 truncate">{category.name}</span>
        <span className="ml-auto shrink-0 text-2xs font-normal text-ink-faint">
          {category.tables.length}
        </span>
      </button>

      {!isCollapsed &&
        category.tables.map((table) => (
          <TableRow
            key={table.name}
            table={table}
            selected={selectedTables.includes(table.name)}
            isPrimary={primaryTable === table.name}
            isActive={activeTable === table.name}
            onToggle={() => onToggleTable(table)}
            onSelect={() => onSelectTable(table.name)}
            onSetPrimary={() => onSetPrimary(table.name)}
            onAddAllFields={onAddAllFields ? () => onAddAllFields(table) : undefined}
          />
        ))}
    </section>
  );
}

function TableRow({
  table,
  selected,
  isPrimary,
  isActive,
  onToggle,
  onSelect,
  onSetPrimary,
  onAddAllFields,
}: {
  table: SchemaTable;
  selected: boolean;
  isPrimary: boolean;
  isActive: boolean;
  onToggle: () => void;
  onSelect: () => void;
  onSetPrimary: () => void;
  onAddAllFields?: () => void;
}) {
  const fromApi = table.kind === 'upload';

  return (
    <div
      onClick={onSelect}
      className={clsx(
        'group flex cursor-pointer items-center gap-2 py-1 pl-4 pr-2',
        isActive ? 'bg-accent-soft' : 'hover:bg-canvas',
      )}
    >
      <span onClick={(event) => event.stopPropagation()}>
        <Checkbox checked={selected} onChange={onToggle} />
      </span>

      <svg
        viewBox="0 0 24 24"
        className={clsx('h-3.5 w-3.5 shrink-0', selected ? 'text-accent' : 'text-ink-faint')}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.6}
      >
        {fromApi ? (
          // Distinguishable at a glance, so a synced table is never mistaken
          // for one of the database's own.
          <>
            <path d="M9 3v5M15 3v5" />
            <rect x="6" y="8" width="12" height="6" rx="2" />
            <path d="M12 14v4a3 3 0 0 0 3 3h1" />
          </>
        ) : (
          <>
            <rect x="3" y="4" width="18" height="16" rx="2" />
            <path d="M3 10h18M9 10v10" />
          </>
        )}
      </svg>

      <span
        className="min-w-0 flex-1 truncate text-sm"
        title={`${table.name}${table.schema ? ` · schema ${table.schema}` : ''} · ${
          table.estimated_rows === null
            ? 'row count unknown'
            : `${table.estimated_rows.toLocaleString()} rows`
        }${
          table.primary_key.length
            ? ` · PK: ${table.primary_key.join(', ')}`
            : ' · no primary key'
        }`}
      >
        {table.label}
      </span>

      {onAddAllFields && (
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            onAddAllFields();
          }}
          title={`Add all ${table.column_count} fields of ${table.label}`}
          className="hidden shrink-0 rounded border border-line bg-white px-1
                     text-2xs text-ink-muted hover:border-accent-border
                     hover:bg-accent-soft hover:text-accent group-hover:block"
        >
          + all
        </button>
      )}

      {isPrimary ? (
        <Badge tone="accent">Primary</Badge>
      ) : (
        selected && (
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              onSetPrimary();
            }}
            title="Make this the primary table"
            className="hidden text-2xs text-ink-faint hover:text-accent group-hover:block"
          >
            Set primary
          </button>
        )
      )}

      {!selected && !isPrimary && (
        <span className="shrink-0 text-2xs tabular text-ink-faint">
          {table.estimated_rows === null ? '' : compactNumber(table.estimated_rows)}
        </span>
      )}
    </div>
  );
}
