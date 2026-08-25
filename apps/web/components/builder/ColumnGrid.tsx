'use client';

import clsx from 'clsx';
import { Badge, EmptyState, IconButton, Select } from '@/components/ui/primitives';
import { AGGREGATION_LABELS } from '@/store/builder';
import type { Aggregation, ReportColumn, SchemaTable } from '@/lib/types';

/**
 * Report Columns grid.
 *
 * The aggregation dropdown offers only what the backend declares legal for the
 * column's type, so an invalid combination cannot be constructed in the first
 * place -- rather than being built, submitted, and rejected.
 */
export function ColumnGrid({
  columns,
  tables,
  selectedColumnId,
  onSelect,
  onUpdate,
  onRemove,
  onMove,
  onAddColumn,
}: {
  columns: ReportColumn[];
  tables: Record<string, SchemaTable>;
  selectedColumnId: string | null;
  onSelect: (id: string) => void;
  onUpdate: (id: string, patch: Partial<ReportColumn>) => void;
  onRemove: (id: string) => void;
  onMove: (id: string, direction: -1 | 1) => void;
  onAddColumn?: (table: string, field: string) => void;
}) {
  const chosen = new Set(columns.map((column) => `${column.table}.${column.field}`));
  const addable = Object.values(tables).flatMap((table) =>
    (table.columns ?? [])
      .filter((column) => !chosen.has(`${table.name}.${column.name}`))
      .map((column) => ({
        value: `${table.name}.${column.name}`,
        label: `${table.label} · ${column.name}`,
      })),
  );
  return (
    <section id="section-columns" className="panel">
      <div className="panel-header">
        <div className="flex items-center gap-2">
          <h2 className="panel-title">Report Columns</h2>
          <span className="text-2xs text-ink-faint">
            {columns.length === 0
              ? 'Tick fields on the left to add columns'
              : `${columns.length} column${columns.length === 1 ? '' : 's'}`}
          </span>
        </div>

        {/* Same result as ticking a field, for anyone who looks for a button. */}
        <div className="relative">
          <select
            value=""
            disabled={addable.length === 0}
            onChange={(event) => {
              if (!event.target.value) return;
              const [table, field] = event.target.value.split('.');
              onAddColumn?.(table, field);
              event.target.value = '';
            }}
            aria-label="Add column"
            className="absolute inset-0 cursor-pointer opacity-0 disabled:cursor-not-allowed"
          >
            <option value="" />
            {addable.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <span className="btn btn-default btn-sm pointer-events-none">
            <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth={2.5}>
              <path d="M12 5v14M5 12h14" />
            </svg>
            Add Column
          </span>
        </div>
      </div>

      {columns.length === 0 ? (
        <EmptyState
          title="No columns yet"
          hint="Pick a table, then tick the fields you want. Double-clicking a field adds it too."
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="striped w-full min-w-[680px] border-collapse">
            <thead>
              <tr className="border-b border-line text-left text-2xs uppercase tracking-wide text-ink-faint">
                <th className="w-8 px-2 py-2" />
                <th className="w-8 px-1 py-2 font-medium">#</th>
                <th className="px-2 py-2 font-medium">Display Name</th>
                <th className="px-2 py-2 font-medium">Source</th>
                <th className="w-[150px] px-2 py-2 font-medium">Aggregation</th>
                <th className="w-[92px] px-2 py-2 font-medium">Data Type</th>
                <th className="w-[72px] px-2 py-2 font-medium">Visible</th>
                <th className="w-16 px-2 py-2" />
              </tr>
            </thead>
            <tbody>
              {columns.map((column, index) => {
                const meta = tables[column.table]?.columns?.find(
                  (candidate) => candidate.name === column.field,
                );
                const allowed: Aggregation[] = meta?.aggregations ?? ['none'];
                const selected = selectedColumnId === column.id;

                return (
                  <tr
                    key={column.id}
                    onClick={() => onSelect(column.id)}
                    className={clsx(
                      'cursor-pointer border-b border-line/70 text-sm last:border-0',
                      selected ? 'bg-accent-soft' : 'hover:bg-canvas',
                    )}
                  >
                    <td className="px-2 py-1.5">
                      <div className="flex flex-col">
                        <IconButton
                          title="Move up"
                          disabled={index === 0}
                          onClick={() => onMove(column.id, -1)}
                          className="h-3.5 w-5"
                        >
                          <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth={2.5}>
                            <path d="m6 15 6-6 6 6" />
                          </svg>
                        </IconButton>
                        <IconButton
                          title="Move down"
                          disabled={index === columns.length - 1}
                          onClick={() => onMove(column.id, 1)}
                          className="h-3.5 w-5"
                        >
                          <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth={2.5}>
                            <path d="m6 9 6 6 6-6" />
                          </svg>
                        </IconButton>
                      </div>
                    </td>

                    <td className="px-1 py-1.5 text-center text-xs tabular text-ink-faint">
                      {index + 1}
                    </td>

                    <td className="px-2 py-1.5">
                      <input
                        value={column.display_name ?? ''}
                        onChange={(event) =>
                          onUpdate(column.id, { display_name: event.target.value })
                        }
                        onClick={(event) => event.stopPropagation()}
                        className="w-full rounded border border-transparent bg-transparent px-1.5
                                   py-0.5 text-sm hover:border-line focus:border-accent
                                   focus:bg-white focus:outline-none"
                      />
                    </td>

                    <td className="px-2 py-1.5 font-mono text-xs text-ink-muted">
                      {column.table}.{column.field}
                    </td>

                    <td className="px-2 py-1.5" onClick={(event) => event.stopPropagation()}>
                      <Select
                        value={column.aggregation}
                        onChange={(value) =>
                          onUpdate(column.id, { aggregation: value as Aggregation })
                        }
                        options={allowed.map((aggregation) => ({
                          value: aggregation,
                          label: AGGREGATION_LABELS[aggregation],
                        }))}
                        className="py-1 text-xs"
                      />
                    </td>

                    <td className="px-2 py-1.5">
                      <Badge>{meta?.data_type ?? 'unknown'}</Badge>
                    </td>

                    <td className="px-2 py-1.5" onClick={(event) => event.stopPropagation()}>
                      <input
                        type="checkbox"
                        checked={column.visible}
                        onChange={(event) =>
                          onUpdate(column.id, { visible: event.target.checked })
                        }
                        className="h-3.5 w-3.5 cursor-pointer rounded-[3px] border-line-strong accent-accent"
                      />
                    </td>

                    <td className="px-2 py-1.5 text-right" onClick={(event) => event.stopPropagation()}>
                      <IconButton title="Remove column" onClick={() => onRemove(column.id)}>
                        <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={1.8}>
                          <path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v6M14 11v6" />
                        </svg>
                      </IconButton>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
