'use client';

import { useMemo, useState } from 'react';
import { Badge, Button, EmptyState, Select } from '@/components/ui/primitives';
import type {
  JoinStep,
  JoinType,
  ReportDefinition,
  ReportJoin,
  SchemaTable,
  ValidationResult,
} from '@/lib/types';

/**
 * Edit Relationships.
 *
 * The planner picks a path automatically; this is where that choice becomes
 * visible and adjustable, for however many tables a report uses.
 *
 * Two settings actually change the answer:
 *
 *   join type    LEFT keeps parent rows with no match; INNER drops them, which
 *                is how a report quietly loses the orders that have no shipment.
 *   join columns which key joins to which. When two tables reference each other
 *                both ways, this is the difference between two different
 *                questions, and no default can be right for both.
 */
export function RelationshipsDialog({
  open,
  onClose,
  validation,
  definition,
  labels,
  tables,
  onSetJoins,
}: {
  open: boolean;
  onClose: () => void;
  validation: ValidationResult | null;
  definition: ReportDefinition;
  labels: Record<string, string>;
  tables: Record<string, SchemaTable>;
  onSetJoins: (joins: ReportJoin[]) => void;
}) {
  const steps = validation?.join_plan?.steps ?? [];
  const bridges = validation?.join_plan?.bridge_tables ?? [];
  const label = (table: string) => labels[table] ?? table;

  // Every pair the report currently joins, whether the planner worked it out or
  // the report declares it. Declared joins win, since they are the user's.
  const edges = useMemo<ReportJoin[]>(() => {
    if (definition.joins.length > 0) return definition.joins;
    return steps.map((step) => ({
      left_table: step.from_table,
      left_column: step.from_column,
      right_table: step.to_table,
      right_column: step.to_column,
      join_type: step.join_type,
      relationship_id: step.relationship_id,
    }));
  }, [definition.joins, steps]);

  const [draft, setDraft] = useState<ReportJoin[] | null>(null);
  const working = draft ?? edges;

  if (!open) return null;

  function update(index: number, patch: Partial<ReportJoin>) {
    setDraft(working.map((join, i) => (i === index ? { ...join, ...patch } : join)));
  }

  function apply() {
    onSetJoins(working);
    setDraft(null);
    onClose();
  }

  const stepFor = (join: ReportJoin) =>
    steps.find(
      (step) =>
        (step.from_table === join.left_table && step.to_table === join.right_table) ||
        (step.from_table === join.right_table && step.to_table === join.left_table),
    );

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Edit relationships"
    >
      <div
        className="flex max-h-[88vh] w-full max-w-4xl flex-col overflow-hidden rounded-lg
                   border border-line bg-white shadow-pop"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="panel-header">
          <div>
            <h2 className="text-md font-semibold">Edit Relationships</h2>
            <p className="text-xs text-ink-muted">
              {working.length} join{working.length === 1 ? '' : 's'} across{' '}
              {definition.tables.length} tables. Change which keys are matched, or how
              unmatched rows are treated.
            </p>
          </div>
          <div className="flex items-center gap-1.5">
            <Button size="sm" onClick={() => { setDraft(null); onClose(); }}>
              Cancel
            </Button>
            <Button size="sm" variant="primary" onClick={apply}>
              Apply
            </Button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {working.length === 0 ? (
            <EmptyState
              title="No relationships in use"
              hint="This report reads from a single table. Add another and the path between them is worked out automatically."
            />
          ) : (
            <div className="space-y-2">
              {working.map((join, index) => {
                const step = stepFor(join);
                const leftColumns = tables[join.left_table]?.columns ?? [];
                const rightColumns = tables[join.right_table]?.columns ?? [];

                return (
                  <div
                    key={`${join.left_table}-${join.right_table}-${index}`}
                    className="rounded-lg border border-line p-3"
                  >
                    <div className="mb-2 flex flex-wrap items-center gap-2">
                      <span className="text-sm font-semibold">{label(join.left_table)}</span>
                      <svg
                        viewBox="0 0 24 24"
                        className="h-3.5 w-3.5 text-ink-faint"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth={2}
                      >
                        <path d="M5 12h14M13 6l6 6-6 6" />
                      </svg>
                      <span className="text-sm font-semibold">{label(join.right_table)}</span>

                      {step && (
                        <Badge tone={step.multiplies_rows ? 'warn' : 'neutral'}>
                          {step.cardinality}
                        </Badge>
                      )}
                      {step?.relationship_source === 'physical' ? (
                        <Badge tone="good">Foreign key</Badge>
                      ) : (
                        <Badge tone="warn">Inferred</Badge>
                      )}
                      {bridges.includes(join.right_table) && <Badge tone="accent">Bridge</Badge>}
                    </div>

                    {/* Which key joins to which. */}
                    <div className="grid items-end gap-2 md:grid-cols-[1fr_auto_1fr_150px]">
                      <ColumnChooser
                        label={`${label(join.left_table)} key`}
                        table={join.left_table}
                        columns={leftColumns}
                        value={join.left_column}
                        onChange={(value) => update(index, { left_column: value })}
                      />

                      <span className="pb-2 text-center text-sm font-semibold text-ink-faint">
                        =
                      </span>

                      <ColumnChooser
                        label={`${label(join.right_table)} key`}
                        table={join.right_table}
                        columns={rightColumns}
                        value={join.right_column}
                        onChange={(value) => update(index, { right_column: value })}
                      />

                      <div>
                        <label className="label">Join type</label>
                        <Select
                          value={join.join_type}
                          onChange={(value) =>
                            update(index, { join_type: value as JoinType })
                          }
                          options={[
                            { value: 'left', label: 'LEFT JOIN' },
                            { value: 'inner', label: 'INNER JOIN' },
                          ]}
                          className="py-1 text-xs"
                        />
                      </div>
                    </div>

                    <p className="mt-2 text-2xs text-ink-faint">
                      {join.join_type === 'inner'
                        ? `Only ${label(join.left_table)} rows with a matching ${label(join.right_table)} row are kept.`
                        : `Every ${label(join.left_table)} row is kept, even where no ${label(join.right_table)} row exists.`}
                      {step?.multiplies_rows &&
                        ' This side returns several rows per record, so totals are pre-aggregated to stay correct.'}
                    </p>
                  </div>
                );
              })}
            </div>
          )}

          <p className="mt-4 rounded border border-line bg-canvas px-3 py-2 text-2xs text-ink-muted">
            Changing a key here applies to this report only. Relationships themselves are
            defined once under Data Sources and apply everywhere.
          </p>
        </div>
      </div>
    </div>
  );
}

/**
 * Column picker that marks which columns are keys.
 *
 * Primary keys are listed first: joining on one is almost always what was meant,
 * and joining on a non-key is how a report ends up multiplying rows.
 */
function ColumnChooser({
  label,
  table,
  columns,
  value,
  onChange,
}: {
  label: string;
  table: string;
  columns: SchemaTable['columns'];
  value: string;
  onChange: (value: string) => void;
}) {
  const options = useMemo(() => {
    const list = columns ?? [];
    const rank = (column: (typeof list)[number]) =>
      column.is_primary_key ? 0 : column.name.toLowerCase() === 'id' ? 1 : column.is_foreign_key ? 2 : 3;
    return [...list]
      .sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name))
      .map((column) => ({
        value: column.name,
        label:
          column.name +
          (column.is_primary_key
            ? '  (PK)'
            : column.name.toLowerCase() === 'id'
              ? '  (key)'
              : column.is_foreign_key || column.name.toLowerCase().endsWith('_id')
                ? '  (FK)'
                : ''),
      }));
  }, [columns]);

  const chosen = (columns ?? []).find((column) => column.name === value);

  return (
    <div className="min-w-0">
      <label className="label truncate">{label}</label>
      <Select
        value={value}
        onChange={onChange}
        options={options}
        className="py-1 font-mono text-xs"
      />
      <p className="mt-0.5 truncate font-mono text-[10px] text-ink-faint">
        {table}.{value}
        {chosen ? ` · ${chosen.data_type}` : ''}
      </p>
    </div>
  );
}
