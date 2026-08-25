'use client';

import { Badge, Button, EmptyState, Select } from '@/components/ui/primitives';
import type { JoinStep, JoinType, ValidationResult } from '@/lib/types';

/**
 * Edit Relationships.
 *
 * The planner chooses a join path automatically; this is where that choice
 * becomes visible and adjustable. Join type is the part worth controlling:
 * switching a LEFT join to INNER silently drops every parent row without a
 * match, which is how a report quietly loses the orders that have no shipment.
 */
export function RelationshipsDialog({
  open,
  onClose,
  validation,
  labels,
  onSetJoinType,
}: {
  open: boolean;
  onClose: () => void;
  validation: ValidationResult | null;
  labels: Record<string, string>;
  onSetJoinType: (step: JoinStep, joinType: JoinType) => void;
}) {
  if (!open) return null;

  const steps = validation?.join_plan?.steps ?? [];
  const bridges = validation?.join_plan?.bridge_tables ?? [];
  const label = (table: string) => labels[table] ?? table;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Edit relationships"
    >
      <div
        className="flex max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-lg
                   border border-line bg-white shadow-pop"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="panel-header">
          <div>
            <h2 className="text-md font-semibold">Edit Relationships</h2>
            <p className="text-xs text-ink-muted">
              Discovered from foreign keys. Change a join type to control which rows survive.
            </p>
          </div>
          <Button size="sm" onClick={onClose}>
            Done
          </Button>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {steps.length === 0 ? (
            <EmptyState
              title="No relationships in use"
              hint="This report reads from a single table. Add another table and the path between them is worked out automatically."
            />
          ) : (
            <div className="space-y-2">
              {steps.map((step) => (
                <div
                  key={`${step.from_table}-${step.to_table}-${step.relationship_id}`}
                  className="rounded-lg border border-line p-3"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-semibold">{label(step.from_table)}</span>
                    <svg viewBox="0 0 24 24" className="h-3.5 w-3.5 text-ink-faint"
                         fill="none" stroke="currentColor" strokeWidth={2}>
                      <path d="M5 12h14M13 6l6 6-6 6" />
                    </svg>
                    <span className="text-sm font-semibold">{label(step.to_table)}</span>

                    <Badge tone={step.multiplies_rows ? 'warn' : 'neutral'}>
                      {step.cardinality}
                    </Badge>
                    {step.relationship_source === 'physical' ? (
                      <Badge tone="good">Foreign key</Badge>
                    ) : (
                      <Badge tone="warn">{step.relationship_source}</Badge>
                    )}
                    {bridges.includes(step.to_table) && <Badge tone="accent">Bridge</Badge>}

                    <div className="ml-auto w-[132px]">
                      <Select
                        value={step.join_type}
                        onChange={(value) => onSetJoinType(step, value as JoinType)}
                        options={[
                          { value: 'left', label: 'LEFT JOIN' },
                          { value: 'inner', label: 'INNER JOIN' },
                        ]}
                        className="py-1 text-xs"
                      />
                    </div>
                  </div>

                  <p className="mt-1.5 font-mono text-2xs text-ink-muted">
                    ON {step.from_table}.{step.from_column} = {step.to_table}.{step.to_column}
                  </p>

                  <p className="mt-1 text-2xs text-ink-faint">
                    {step.join_type === 'inner'
                      ? `Only ${label(step.from_table)} rows that have a matching ${label(step.to_table)} row are kept.`
                      : `Every ${label(step.from_table)} row is kept, even where no ${label(step.to_table)} row exists.`}
                    {step.multiplies_rows &&
                      ' This side returns several rows per record, so totals are pre-aggregated to stay correct.'}
                  </p>
                </div>
              ))}
            </div>
          )}

          <p className="mt-4 rounded border border-line bg-canvas px-3 py-2 text-2xs text-ink-muted">
            Relationships themselves are defined once, under Data Sources, and apply to every
            report. Only the join type is per-report.
          </p>
        </div>
      </div>
    </div>
  );
}
