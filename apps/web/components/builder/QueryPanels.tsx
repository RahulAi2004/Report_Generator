'use client';

import { Badge, Button, IconButton, Select } from '@/components/ui/primitives';
import {
  LIST_VALUE_OPERATORS,
  NO_VALUE_OPERATORS,
  OPERATOR_LABELS,
  TWO_VALUE_OPERATORS,
} from '@/lib/format';
import type {
  FilterCondition,
  ReportColumn,
  ReportDefinition,
  SchemaTable,
} from '@/lib/types';

const TrashIcon = () => (
  <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={1.8}>
    <path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" />
  </svg>
);

const PlusIcon = () => (
  <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth={2.5}>
    <path d="M12 5v14M5 12h14" />
  </svg>
);

/** Filters, Group By and Sort By -- the lower band of the reference layout. */
export function QueryPanels({
  definition,
  tables,
  onAddFilter,
  onUpdateFilter,
  onRemoveFilter,
  onSetGroupOp,
  onAddGroupBy,
  onRemoveGroupBy,
  onAddSort,
  onUpdateSort,
  onRemoveSort,
}: {
  definition: ReportDefinition;
  tables: Record<string, SchemaTable>;
  onAddFilter: (table: string, field: string, operator: string) => void;
  onUpdateFilter: (id: string, patch: Partial<FilterCondition>) => void;
  onRemoveFilter: (id: string) => void;
  onSetGroupOp: (op: 'and' | 'or') => void;
  onAddGroupBy: (table: string, field: string) => void;
  onRemoveGroupBy: (index: number) => void;
  onAddSort: (columnId: string) => void;
  onUpdateSort: (index: number, patch: { column_id?: string; direction?: 'asc' | 'desc' }) => void;
  onRemoveSort: (index: number) => void;
}) {
  const fieldOptions = definition.tables.flatMap((tableName) =>
    (tables[tableName]?.columns ?? []).map((column) => ({
      value: `${tableName}.${column.name}`,
      label: `${tables[tableName]?.label ?? tableName} · ${column.name}`,
    })),
  );

  const conditions = definition.filters.children.filter(
    (node): node is FilterCondition => node.kind === 'condition',
  );

  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1.6fr_1fr_1.1fr]">
      {/* ---------------------------------------------------------------- */}
      <section id="section-filters" className="panel">
        <div className="panel-header">
          <div className="flex items-center gap-2">
            <h2 className="panel-title">Filters</h2>
            {conditions.length > 1 && (
              <div className="flex overflow-hidden rounded border border-line">
                {(['and', 'or'] as const).map((op) => (
                  <button
                    key={op}
                    type="button"
                    onClick={() => onSetGroupOp(op)}
                    className={
                      definition.filters.op === op
                        ? 'bg-accent px-2 py-0.5 text-2xs font-semibold uppercase text-white'
                        : 'px-2 py-0.5 text-2xs font-semibold uppercase text-ink-muted hover:bg-canvas'
                    }
                  >
                    {op}
                  </button>
                ))}
              </div>
            )}
          </div>
          <AddFieldButton
            label="Add Filter"
            options={fieldOptions}
            onPick={(table, field) => {
              const meta = tables[table]?.columns?.find((c) => c.name === field);
              onAddFilter(table, field, meta?.operators[0] ?? 'equals');
            }}
          />
        </div>

        <div className="space-y-1.5 px-3 py-2.5">
          {conditions.length === 0 && (
            <p className="py-2 text-xs text-ink-faint">
              No filters. The report returns all rows, up to the row limit.
            </p>
          )}

          {conditions.map((condition) => {
            const meta = tables[condition.table]?.columns?.find(
              (column) => column.name === condition.field,
            );
            const operators = meta?.operators ?? ['equals'];
            const needsNoValue = NO_VALUE_OPERATORS.has(condition.operator);
            const needsTwo = TWO_VALUE_OPERATORS.has(condition.operator);
            const isList = LIST_VALUE_OPERATORS.has(condition.operator);
            const inputType =
              meta?.data_type === 'date'
                ? 'date'
                : meta?.data_type === 'datetime'
                  ? 'date'
                  : meta?.data_type === 'integer' || meta?.data_type === 'decimal'
                    ? 'number'
                    : 'text';

            return (
              <div key={condition.id} className="flex flex-wrap items-center gap-1.5">
                <span
                  className="w-[128px] shrink-0 truncate rounded border border-line bg-canvas
                             px-2 py-1.5 text-xs"
                  title={`${condition.table}.${condition.field}`}
                >
                  {meta?.label ?? condition.field}
                </span>

                <Select
                  value={condition.operator}
                  onChange={(operator) =>
                    onUpdateFilter(condition.id!, { operator, values: [] })
                  }
                  options={operators.map((operator) => ({
                    value: operator,
                    label: OPERATOR_LABELS[operator] ?? operator,
                  }))}
                  className="w-[124px] py-1 text-xs"
                />

                {!needsNoValue && (
                  <>
                    <input
                      type={inputType}
                      value={String(condition.values[0] ?? '')}
                      placeholder={isList ? 'Comma separated' : 'Value'}
                      onChange={(event) => {
                        const raw = event.target.value;
                        const values = isList
                          ? raw.split(',').map((part) => part.trim()).filter(Boolean)
                          : [raw];
                        onUpdateFilter(condition.id!, {
                          values: needsTwo ? [raw, condition.values[1] ?? ''] : values,
                        });
                      }}
                      className="field w-[130px] flex-1 py-1 text-xs"
                    />
                    {needsTwo && (
                      <>
                        <span className="text-2xs text-ink-faint">and</span>
                        <input
                          type={inputType}
                          value={String(condition.values[1] ?? '')}
                          onChange={(event) =>
                            onUpdateFilter(condition.id!, {
                              values: [condition.values[0] ?? '', event.target.value],
                            })
                          }
                          className="field w-[130px] flex-1 py-1 text-xs"
                        />
                      </>
                    )}
                  </>
                )}

                {condition.parameter && <Badge tone="accent">Prompted</Badge>}

                <IconButton title="Remove filter" onClick={() => onRemoveFilter(condition.id!)}>
                  <TrashIcon />
                </IconButton>
              </div>
            );
          })}

          {conditions.length > 0 && (
            <label className="flex cursor-pointer items-center gap-2 pt-1.5 text-xs text-ink-muted">
              <input
                type="checkbox"
                checked={conditions.some((condition) => condition.parameter != null)}
                onChange={(event) => {
                  const first = conditions[0];
                  if (!first) return;
                  onUpdateFilter(first.id!, {
                    parameter: event.target.checked
                      ? {
                          name: `p_${first.field}`,
                          prompt: tables[first.table]?.columns?.find(
                            (c) => c.name === first.field,
                          )?.label ?? first.field,
                          required: false,
                        }
                      : null,
                  });
                }}
                className="h-3.5 w-3.5 rounded-[3px] border-line-strong accent-accent"
              />
              Ask for values when running report
              <span
                title="Saved reports can prompt for these values at run time instead of storing a fixed filter."
                className="flex h-3.5 w-3.5 items-center justify-center rounded-full border
                           border-line-strong text-[9px] text-ink-faint"
              >
                i
              </span>
            </label>
          )}
        </div>
      </section>

      {/* ---------------------------------------------------------------- */}
      <section id="section-grouping" className="panel">
        <div className="panel-header">
          <h2 className="panel-title">Group By</h2>
          <AddFieldButton
            label="Add Group"
            options={fieldOptions}
            onPick={(table, field) => onAddGroupBy(table, field)}
          />
        </div>
        <div className="space-y-1.5 px-3 py-2.5">
          {definition.group_by.length === 0 && (
            <p className="py-2 text-xs text-ink-faint">
              No grouping. Add a group to summarise rows, for example one row per customer.
            </p>
          )}
          {definition.group_by.map((group, index) => (
            <div
              key={`${group.table}.${group.field}`}
              className="flex items-center gap-2 rounded border border-line bg-canvas px-2 py-1.5"
            >
              <svg viewBox="0 0 24 24" className="h-3 w-3 text-ink-faint" fill="none" stroke="currentColor" strokeWidth={2}>
                <path d="M4 7h16M4 12h16M4 17h10" />
              </svg>
              <span className="min-w-0 flex-1 truncate font-mono text-xs">
                {group.table}.{group.field}
              </span>
              <IconButton title="Remove group" onClick={() => onRemoveGroupBy(index)}>
                <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth={2.5}>
                  <path d="M18 6 6 18M6 6l12 12" />
                </svg>
              </IconButton>
            </div>
          ))}
        </div>
      </section>

      {/* ---------------------------------------------------------------- */}
      <section id="section-sorting" className="panel">
        <div className="panel-header">
          <h2 className="panel-title">Sort By</h2>
          <Button
            variant="ghost"
            size="sm"
            disabled={definition.columns.length === 0}
            onClick={() => onAddSort(definition.columns[0].id)}
          >
            <PlusIcon /> Add Sort
          </Button>
        </div>
        <div className="space-y-1.5 px-3 py-2.5">
          {definition.sort_by.length === 0 && (
            <p className="py-2 text-xs text-ink-faint">
              No sorting. Rows come back in database order, which is not guaranteed to be stable.
            </p>
          )}
          {definition.sort_by.map((sort, index) => (
            <div key={index} className="flex items-center gap-1.5">
              <Select
                value={sort.column_id}
                onChange={(value) => onUpdateSort(index, { column_id: value })}
                options={definition.columns.map((column: ReportColumn) => ({
                  value: column.id,
                  label: column.display_name || `${column.table}.${column.field}`,
                }))}
                className="flex-1 py-1 text-xs"
              />
              <Select
                value={sort.direction}
                onChange={(value) =>
                  onUpdateSort(index, { direction: value as 'asc' | 'desc' })
                }
                options={[
                  { value: 'asc', label: 'Ascending' },
                  { value: 'desc', label: 'Descending' },
                ]}
                className="w-[112px] py-1 text-xs"
              />
              <IconButton title="Remove sort" onClick={() => onRemoveSort(index)}>
                <TrashIcon />
              </IconButton>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function AddFieldButton({
  label,
  options,
  onPick,
}: {
  label: string;
  options: { value: string; label: string }[];
  onPick: (table: string, field: string) => void;
}) {
  return (
    <div className="relative">
      <select
        value=""
        disabled={options.length === 0}
        onChange={(event) => {
          if (!event.target.value) return;
          const [table, field] = event.target.value.split('.');
          onPick(table, field);
          event.target.value = '';
        }}
        className="absolute inset-0 cursor-pointer opacity-0 disabled:cursor-not-allowed"
        aria-label={label}
      >
        <option value="" />
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <span className="btn btn-ghost btn-sm pointer-events-none">
        <PlusIcon /> {label}
      </span>
    </div>
  );
}
