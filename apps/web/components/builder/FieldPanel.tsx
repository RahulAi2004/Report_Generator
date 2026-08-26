'use client';

import clsx from 'clsx';
import { useMemo, useState } from 'react';
import { Badge, Checkbox, EmptyState, Skeleton, TypeGlyph } from '@/components/ui/primitives';
import type { SchemaColumn, SchemaTable } from '@/lib/types';

/**
 * Field list for the selected table.
 *
 * A field can be added by ticking it or by double-clicking the row. Masked and
 * sensitive fields are labelled here rather than silently returning redacted
 * values in the preview, so nobody builds a report on data they cannot read.
 */
export function FieldPanel({
  table,
  loading,
  selectedFields,
  onToggleField,
  onSelectMany,
}: {
  table: SchemaTable | null;
  loading: boolean;
  selectedFields: Set<string>;
  onToggleField: (table: string, column: SchemaColumn) => void;
  /** Add or remove a whole set at once, as one edit rather than many. */
  onSelectMany?: (table: string, columns: SchemaColumn[], selected: boolean) => void;
}) {
  const [search, setSearch] = useState('');

  const columns = useMemo(() => {
    const all = table?.columns ?? [];
    if (!search.trim()) return all;
    const needle = search.toLowerCase();
    return all.filter(
      (column) =>
        column.name.toLowerCase().includes(needle) ||
        column.label.toLowerCase().includes(needle),
    );
  }, [table, search]);

  // Select-all acts on what is currently listed. With a search active that is
  // the matching fields, which is almost always what was meant.
  const shown = columns;
  const chosen = shown.filter((column) => selectedFields.has(`${table?.name}.${column.name}`));
  const allChosen = shown.length > 0 && chosen.length === shown.length;
  const someChosen = chosen.length > 0 && !allChosen;

  function toggleAll(next: boolean) {
    if (!table || shown.length === 0) return;
    if (onSelectMany) {
      const changing = next
        ? shown.filter((c) => !selectedFields.has(`${table.name}.${c.name}`))
        : chosen;
      onSelectMany(table.name, changing, next);
      return;
    }
    for (const column of next ? shown : chosen) {
      const already = selectedFields.has(`${table.name}.${column.name}`);
      if (already !== next) onToggleField(table.name, column);
    }
  }

  return (
    <aside className="flex w-[212px] shrink-0 flex-col border-r border-line bg-white">
      <div className="px-3 pb-2 pt-3">
        <h2 className="panel-title mb-2 truncate">
          {table ? `Fields — ${table.label}` : 'Fields'}
        </h2>
        <input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search fields..."
          disabled={!table}
          className="field text-sm"
        />
      </div>

      {table && shown.length > 0 && (
        <div className="flex items-center justify-between gap-2 border-y border-line
                        bg-canvas px-3 py-1.5">
          <Checkbox
            checked={allChosen}
            indeterminate={someChosen}
            onChange={toggleAll}
            title={
              search
                ? `Select the ${shown.length} fields matching “${search}”`
                : `Select all ${shown.length} fields`
            }
            label={
              <span className="text-xs font-medium">
                {allChosen ? 'Clear all' : search ? 'Select matching' : 'Select all'}
              </span>
            }
          />
          <span className="shrink-0 text-2xs tabular text-ink-faint">
            {chosen.length}/{shown.length}
          </span>
        </div>
      )}

      <div className="flex-1 overflow-y-auto">
        {loading && (
          <div className="space-y-2 px-3">
            {Array.from({ length: 7 }).map((_, index) => (
              <Skeleton key={index} className="h-5 w-full" />
            ))}
          </div>
        )}

        {!loading && !table && (
          <EmptyState
            title="Select a table"
            hint="Choose a table on the left to see the fields it contains."
          />
        )}

        {!loading && table && columns.length === 0 && (
          <EmptyState title="No matching fields" />
        )}

        {!loading &&
          table &&
          columns.map((column) => {
            const key = `${table.name}.${column.name}`;
            const checked = selectedFields.has(key);
            return (
              <div
                key={column.name}
                onDoubleClick={() => onToggleField(table.name, column)}
                className={clsx(
                  'flex cursor-pointer items-center gap-1.5 py-[5px] pl-3 pr-2',
                  checked ? 'bg-accent-soft/70' : 'hover:bg-canvas',
                )}
                title={`${column.name} · ${column.physical_type}${
                  column.nullable ? ' · nullable' : ' · required'
                }`}
              >
                <Checkbox
                  checked={checked}
                  onChange={() => onToggleField(table.name, column)}
                />
                <TypeGlyph dataType={column.data_type} />
                <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink">
                  {column.name}
                </span>

                {column.is_primary_key && <Badge tone="accent">PK</Badge>}
                {column.is_foreign_key && !column.is_primary_key && <Badge>FK</Badge>}
                {column.is_masked && <Badge tone="warn">Masked</Badge>}
              </div>
            );
          })}
      </div>

      <div className="border-t border-line px-3 py-2">
        <button
          type="button"
          disabled
          title="Calculated fields arrive in Phase 6"
          className="flex w-full items-center gap-1.5 text-xs text-ink-faint
                     disabled:cursor-not-allowed"
        >
          <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2}>
            <path d="M12 5v14M5 12h14" />
          </svg>
          Add Custom Field
        </button>
      </div>
    </aside>
  );
}
