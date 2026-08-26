'use client';

import clsx from 'clsx';
import { useState } from 'react';
import { Badge, Button, Checkbox, IconButton, Select } from '@/components/ui/primitives';
import { FieldSelect, TableSelect, useTableColumns } from './FieldPicker';
import { METRIC_ICONS } from './MetricStrip';
import { useDashboard } from '@/store/dashboard';
import { AGGREGATION_LABELS } from '@/store/builder';
import type { Aggregation } from '@/lib/types';
import type { Comparison, MetricCard, MetricFormat, Tone } from '@/lib/dashboard-types';

/**
 * Metric Cards (panel 6).
 *
 * Each card is a row that expands into its own editor rather than opening a
 * modal: the number it produces is visible in the strip above while it is being
 * edited, and losing sight of that is losing the only feedback the builder has.
 */
export function MetricCardsPanel() {
  const { definition, addMetric, removeMetric, moveMetric, selectMetric, selectedMetricId } =
    useDashboard();
  const [open, setOpen] = useState<string | null>(null);

  return (
    <section className="panel flex min-h-0 flex-col">
      <div className="panel-header">
        <div className="flex items-center gap-2">
          <StepNumber n={6} />
          <div>
            <h2 className="text-sm font-semibold text-ink">Metric Cards</h2>
            <p className="text-2xs text-ink-muted">
              Add cards with fields, calculations and filters.
            </p>
          </div>
        </div>
      </div>

      <div className="border-b border-line px-3 py-2">
        <Button
          size="sm"
          variant="primary"
          className="w-full"
          onClick={() => {
            const id = addMetric();
            setOpen(id);
          }}
        >
          <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2}>
            <path d="M12 5v14M5 12h14" />
          </svg>
          Add Metric Card
        </Button>
      </div>

      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
        {definition.metrics.length === 0 && (
          <p className="rounded border border-dashed border-line-strong px-3 py-6 text-center text-xs text-ink-muted">
            No cards yet. Each one becomes a number across the top.
          </p>
        )}

        {definition.metrics.map((card, index) => (
          <MetricRow
            key={card.id}
            card={card}
            index={index}
            total={definition.metrics.length}
            expanded={open === card.id}
            selected={selectedMetricId === card.id}
            onToggle={() => {
              setOpen(open === card.id ? null : card.id);
              selectMetric(card.id);
            }}
            onMove={(direction) => moveMetric(card.id, direction)}
            onRemove={() => {
              removeMetric(card.id);
              if (open === card.id) setOpen(null);
            }}
          />
        ))}
      </div>
    </section>
  );
}

function MetricRow({
  card,
  index,
  total,
  expanded,
  selected,
  onToggle,
  onMove,
  onRemove,
}: {
  card: MetricCard;
  index: number;
  total: number;
  expanded: boolean;
  selected: boolean;
  onToggle: () => void;
  onMove: (direction: -1 | 1) => void;
  onRemove: () => void;
}) {
  const { updateMetric } = useDashboard();
  const { columns } = useTableColumns(card.table);
  const column = columns.find((candidate) => candidate.name === card.field);
  const allowed = (column?.aggregations ?? ['count']).filter((a) => a !== 'none');

  const incomplete = !card.table || !card.field;

  return (
    <div
      className={clsx(
        'rounded-lg border bg-white transition-colors',
        selected ? 'border-accent' : 'border-line',
      )}
    >
      <div className="flex items-center gap-1.5 px-2 py-1.5">
        <span className="flex flex-col text-ink-faint">
          <button
            type="button"
            disabled={index === 0}
            onClick={() => onMove(-1)}
            className="hover:text-accent disabled:opacity-25"
            title="Move up"
          >
            <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth={2.5}>
              <path d="M6 15l6-6 6 6" />
            </svg>
          </button>
          <button
            type="button"
            disabled={index === total - 1}
            onClick={() => onMove(1)}
            className="hover:text-accent disabled:opacity-25"
            title="Move down"
          >
            <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth={2.5}>
              <path d="M6 9l6 6 6-6" />
            </svg>
          </button>
        </span>

        <button
          type="button"
          onClick={onToggle}
          className="min-w-0 flex-1 text-left"
        >
          <span className="block truncate text-sm font-medium text-ink">{card.title}</span>
          <span className="block truncate font-mono text-2xs text-ink-faint">
            {card.table && card.field
              ? `${card.aggregation === 'count' && card.distinct ? 'COUNT(DISTINCT' : `${card.aggregation.toUpperCase()}(`} ${card.field})`
              : 'not configured'}
          </span>
        </button>

        {incomplete && <Badge tone="warn">Incomplete</Badge>}

        <IconButton title={expanded ? 'Collapse' : 'Edit'} onClick={onToggle}>
          <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={1.8}>
            <path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />
          </svg>
        </IconButton>
        <IconButton title="Remove" onClick={onRemove}>
          <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={1.8}>
            <path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13" />
          </svg>
        </IconButton>
      </div>

      {expanded && (
        <div className="space-y-2.5 border-t border-line px-2.5 py-2.5">
          <div>
            <label className="label">Card title</label>
            <input
              value={card.title}
              onChange={(event) => updateMetric(card.id, { title: event.target.value })}
              className="field"
            />
          </div>

          <div>
            <label className="label">Data source</label>
            <TableSelect
              value={card.table}
              onChange={(table) =>
                // The field belongs to the old table; keeping it would name a
                // column that no longer exists.
                updateMetric(card.id, { table, field: '' })
              }
            />
          </div>

          <div>
            <label className="label">Field / Calculation</label>
            <div className="grid grid-cols-[1fr_110px] gap-1.5">
              <FieldSelect
                table={card.table}
                value={card.field}
                onChange={(field) => {
                  const meta = columns.find((c) => c.name === field);
                  const legal: Aggregation[] = (meta?.aggregations ?? []).filter((a) => a !== 'none');
                  // Keep the current aggregation only if it is still legal for
                  // the new field; the backend would reject it otherwise.
                  const aggregation = legal.includes(card.aggregation)
                    ? card.aggregation
                    : ((legal[0] ?? 'count') as Aggregation);
                  updateMetric(card.id, { field, aggregation });
                }}
              />
              <Select
                value={card.aggregation}
                onChange={(value) => updateMetric(card.id, { aggregation: value as Aggregation })}
                options={allowed.map((a) => ({ value: a, label: AGGREGATION_LABELS[a] }))}
              />
            </div>
            {card.aggregation === 'count' && (
              <div className="mt-1.5">
                <Checkbox
                  checked={card.distinct}
                  onChange={(distinct) => updateMetric(card.id, { distinct })}
                  label={
                    <span className="text-xs">
                      Count distinct values
                      <span className="ml-1 text-ink-faint">
                        (each customer once, not once per order)
                      </span>
                    </span>
                  }
                />
              </div>
            )}
          </div>

          <div className="grid grid-cols-2 gap-1.5">
            <div>
              <label className="label">Format</label>
              <Select
                value={card.format}
                onChange={(value) => updateMetric(card.id, { format: value as MetricFormat })}
                options={[
                  { value: 'number', label: 'Number' },
                  { value: 'currency', label: 'Currency' },
                  { value: 'percent', label: 'Percentage' },
                  { value: 'duration', label: 'Hours' },
                ]}
              />
            </div>
            <div>
              <label className="label">
                {card.format === 'currency' ? 'Currency' : 'Decimals'}
              </label>
              {card.format === 'currency' ? (
                <Select
                  value={card.currency}
                  onChange={(currency) => updateMetric(card.id, { currency })}
                  options={['EUR', 'USD', 'GBP', 'AED', 'PKR', 'INR'].map((code) => ({
                    value: code,
                    label: code,
                  }))}
                />
              ) : (
                <input
                  type="number"
                  min={0}
                  max={4}
                  value={card.decimals}
                  onChange={(event) =>
                    updateMetric(card.id, { decimals: Number(event.target.value) })
                  }
                  className="field"
                />
              )}
            </div>
          </div>

          <div>
            <label className="label">Caption under the number</label>
            <Select
              value={card.comparison}
              onChange={(value) => updateMetric(card.id, { comparison: value as Comparison })}
              options={[
                { value: 'none', label: 'Just the period' },
                { value: 'previous_period', label: 'Change vs previous period' },
                { value: 'share_of_total', label: 'Share of the unfiltered total' },
              ]}
            />
            {card.comparison === 'previous_period' && (
              <div className="mt-1.5">
                <Checkbox
                  checked={!card.higher_is_better}
                  onChange={(worse) => updateMetric(card.id, { higher_is_better: !worse })}
                  label={
                    <span className="text-xs">
                      An increase is bad
                      <span className="ml-1 text-ink-faint">(refunds, churn, delays)</span>
                    </span>
                  }
                />
              </div>
            )}
          </div>

          <Checkbox
            checked={card.ignore_time_range}
            onChange={(ignore_time_range) => updateMetric(card.id, { ignore_time_range })}
            label={<span className="text-xs">Always show all time, ignoring the time range</span>}
          />

          <div className="grid grid-cols-2 gap-1.5">
            <div>
              <label className="label">Icon</label>
              <Select
                value={card.icon}
                onChange={(icon) => updateMetric(card.id, { icon })}
                options={Object.keys(METRIC_ICONS).map((key) => ({ value: key, label: key }))}
              />
            </div>
            <div>
              <label className="label">Colour</label>
              <Select
                value={card.tone}
                onChange={(value) => updateMetric(card.id, { tone: value as Tone })}
                options={['blue', 'green', 'amber', 'violet', 'rose', 'slate'].map((tone) => ({
                  value: tone,
                  label: tone[0].toUpperCase() + tone.slice(1),
                }))}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export function StepNumber({ n }: { n: number }) {
  return (
    <span className="flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded bg-accent text-[10px] font-bold text-white">
      {n}
    </span>
  );
}
