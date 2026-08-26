'use client';

import clsx from 'clsx';
import { Badge, Button, EmptyState } from '@/components/ui/primitives';
import type { JoinStep, SchemaTable, ValidationResult } from '@/lib/types';

/**
 * Relationship diagram.
 *
 * Shows the join path the planner actually chose, not a wish. Three things the
 * reference has no way to express are surfaced here because they decide whether
 * the numbers are right:
 *
 *   - which column is the key and which is the reference (PK / FK)
 *   - which edges multiply rows (crow's foot, amber)
 *   - which branches were pre-aggregated to keep totals honest
 */
export function JoinCanvas({
  validation,
  primaryTable,
  labels,
  tables,
  onEditRelationships,
}: {
  validation: ValidationResult | null;
  primaryTable: string;
  labels: Record<string, string>;
  tables: Record<string, SchemaTable>;
  onEditRelationships?: () => void;
}) {
  const plan = validation?.join_plan;
  const steps: JoinStep[] = plan?.steps ?? [];
  const strategies = new Map(
    (validation?.fanout.branches ?? []).map((branch) => [branch.table, branch.strategy]),
  );

  const label = (table: string) => labels[table] ?? table;

  return (
    <section id="section-joins" className="panel">
      <div className="panel-header">
        <h2 className="panel-title">Relationships (Joins)</h2>
        <div className="flex items-center gap-3">
          <span className="hidden items-center gap-1 text-2xs text-ink-faint sm:flex">
            <Connector kind="one" /> One
          </span>
          <span className="hidden items-center gap-1 text-2xs text-ink-faint sm:flex">
            <Connector kind="many" /> Many (multiplies rows)
          </span>
          <Button size="sm" onClick={onEditRelationships} disabled={steps.length === 0}>
            <svg
              viewBox="0 0 24 24"
              className="h-3.5 w-3.5"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.8}
            >
              <path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />
            </svg>
            Edit Relationships
          </Button>
        </div>
      </div>

      {steps.length === 0 ? (
        <EmptyState
          title={primaryTable ? 'No joins needed' : 'No tables selected'}
          hint={
            primaryTable
              ? 'This report reads from a single table. Select another table and the relationship is discovered automatically.'
              : 'Choose a table on the left to begin.'
          }
        />
      ) : (
        <div className="overflow-x-auto px-4 py-4">
          <div className="flex min-w-max items-stretch gap-0">
            <TableCard
              name={label(primaryTable)}
              physical={primaryTable}
              isPrimary
              keys={steps
                .filter((step) => step.from_table === primaryTable)
                .map((step) => keyRole(tables, step.from_table, step.from_column))}
            />
            {steps.map((step) => (
              <div key={`${step.from_table}-${step.to_table}`} className="flex items-stretch">
                <JoinConnector step={step} />
                <TableCard
                  name={label(step.to_table)}
                  physical={step.to_table}
                  strategy={strategies.get(step.to_table)}
                  bridge={plan?.bridge_tables.includes(step.to_table)}
                  keys={[keyRole(tables, step.to_table, step.to_column)]}
                />
              </div>
            ))}
          </div>

          {/* The diagram shows the shape; this says what it means. */}
          <div className="mt-3 space-y-1 border-t border-line pt-2.5">
            {steps.map((step) => (
              <p key={`${step.from_table}-${step.to_table}-why`} className="text-2xs text-ink-muted">
                <span className="font-mono text-ink">
                  {step.from_table}.{step.from_column}
                </span>
                {' = '}
                <span className="font-mono text-ink">
                  {step.to_table}.{step.to_column}
                </span>
                {' — '}
                {step.multiplies_rows
                  ? `one ${label(step.from_table)} row can match many ${label(step.to_table)} rows`
                  : `one ${label(step.from_table)} row matches at most one ${label(step.to_table)} row`}
                {step.relationship_source === 'physical'
                  ? ', from a database foreign key.'
                  : ', matched by column name and type rather than a declared foreign key.'}
              </p>
            ))}
          </div>

          {plan?.bridge_tables && plan.bridge_tables.length > 0 && (
            <p className="mt-2 text-2xs text-ink-muted">
              <strong className="font-semibold">{plan.bridge_tables.join(', ')}</strong> was added
              automatically to connect the tables you selected.
            </p>
          )}
        </div>
      )}
    </section>
  );
}

interface KeyRole {
  column: string;
  role: 'PK' | 'FK';
}

/**
 * Whether a join column is the table's own key or a reference to another table.
 *
 * Worth showing: it is the difference between one row per customer and one row
 * per order, which is what decides whether a total is right.
 */
function keyRole(
  tables: Record<string, SchemaTable>,
  table: string,
  column: string,
): KeyRole {
  const meta = tables[table]?.columns?.find((candidate) => candidate.name === column);
  if (meta?.is_primary_key) return { column, role: 'PK' };
  if (meta?.is_foreign_key) return { column, role: 'FK' };
  // A view declares neither, so fall back to the naming convention.
  return { column, role: column.toLowerCase() === 'id' ? 'PK' : 'FK' };
}

function TableCard({
  name,
  physical,
  keys,
  isPrimary,
  strategy,
  bridge,
}: {
  name: string;
  physical: string;
  keys: KeyRole[];
  isPrimary?: boolean;
  strategy?: string;
  bridge?: boolean;
}) {
  return (
    <div
      className={clsx(
        'w-[184px] shrink-0 rounded-lg border bg-white',
        isPrimary ? 'border-accent-border' : 'border-line',
      )}
    >
      <div className="flex items-center justify-between gap-1 border-b border-line px-2.5 py-1.5">
        <span className="truncate text-sm font-semibold text-ink" title={physical}>
          {name}
        </span>
        {isPrimary && <Badge tone="accent">Primary</Badge>}
        {bridge && <Badge>Bridge</Badge>}
      </div>

      <div className="space-y-1 px-2.5 py-2">
        {keys.length === 0 && (
          <span className="font-mono text-2xs text-ink-faint">no join key</span>
        )}
        {keys.map((key) => (
          <div key={`${key.column}-${key.role}`} className="flex items-center gap-1.5">
            <span className="min-w-0 flex-1 truncate font-mono text-2xs text-ink-muted">
              {key.column}
            </span>
            <span
              title={
                key.role === 'PK'
                  ? 'Primary key — one row per value'
                  : 'Foreign key — values repeat, so this side can multiply rows'
              }
              className={clsx(
                'shrink-0 rounded border px-1 text-[9px] font-bold',
                key.role === 'PK'
                  ? 'border-accent-border bg-accent-soft text-accent'
                  : 'border-warn-border bg-warn-soft text-warn',
              )}
            >
              {key.role}
            </span>
          </div>
        ))}
      </div>

      {strategy && strategy !== 'detail' && (
        <div
          className={clsx(
            'border-t px-2.5 py-1 text-2xs',
            strategy === 'pre_aggregate'
              ? 'border-good-border bg-good-soft text-good'
              : 'border-accent-border bg-accent-soft text-accent',
          )}
          title={
            strategy === 'pre_aggregate'
              ? 'Aggregated in its own sub-query before joining, so totals elsewhere stay correct.'
              : 'Applied as an EXISTS check, so it filters without duplicating rows.'
          }
        >
          {strategy === 'pre_aggregate' ? 'Pre-aggregated' : 'Exists filter'}
        </div>
      )}
    </div>
  );
}

function JoinConnector({ step }: { step: JoinStep }) {
  return (
    <div className="flex w-16 shrink-0 flex-col items-center justify-center gap-0.5">
      <span
        className={clsx(
          'text-2xs font-medium uppercase',
          step.multiplies_rows ? 'text-warn' : 'text-ink-faint',
        )}
      >
        {step.join_type}
      </span>

      <svg viewBox="0 0 56 16" className="h-4 w-14" fill="none" stroke="currentColor">
        <line
          x1="2"
          y1="8"
          x2="54"
          y2="8"
          strokeWidth={1.4}
          className={step.multiplies_rows ? 'text-warn' : 'text-line-strong'}
        />
        <circle cx="6" cy="8" r="2.5" strokeWidth={1.4} className="text-line-strong" />
        {step.multiplies_rows ? (
          <g strokeWidth={1.4} className="text-warn">
            <path d="M50 8l-6-4M50 8l-6 4M50 8h-6" />
          </g>
        ) : (
          <circle cx="50" cy="8" r="2.5" strokeWidth={1.4} className="text-line-strong" />
        )}
      </svg>

      <span className="text-2xs tabular text-ink-faint">{step.cardinality}</span>

      <span
        title={
          step.relationship_source === 'physical'
            ? 'Declared as a foreign key in the database'
            : 'Matched by column name and type, not declared by the database'
        }
        className={clsx(
          'text-[9px] font-semibold',
          step.relationship_source === 'physical' ? 'text-good' : 'text-warn',
        )}
      >
        {step.relationship_source === 'physical' ? 'foreign key' : 'inferred'}
      </span>
    </div>
  );
}

function Connector({ kind }: { kind: 'one' | 'many' }) {
  return (
    <svg viewBox="0 0 20 10" className="h-2.5 w-5" fill="none" stroke="currentColor" strokeWidth={1.4}>
      <line
        x1="1"
        y1="5"
        x2="19"
        y2="5"
        className={kind === 'many' ? 'text-warn' : 'text-line-strong'}
      />
      {kind === 'many' ? (
        <path d="M17 5l-5-3M17 5l-5 3" className="text-warn" />
      ) : (
        <circle cx="16" cy="5" r="2" className="text-line-strong" />
      )}
    </svg>
  );
}
