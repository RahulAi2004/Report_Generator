'use client';

import clsx from 'clsx';
import { useState } from 'react';
import { Badge, Checkbox, EmptyState, Select } from '@/components/ui/primitives';
import { formatValue } from '@/lib/format';
import { AGGREGATION_LABELS } from '@/store/builder';
import type { Aggregation, ColumnFormat, ReportColumn, SchemaTable } from '@/lib/types';

/**
 * Column Properties panel.
 *
 * Tabbed rather than a flat list: the reference shows eight properties, the
 * specification calls for around fourteen, and stacking them all vertically
 * would push the important ones below the fold.
 */
const TABS = ['Properties', 'Format'] as const;
type Tab = (typeof TABS)[number];

const DEFAULT_FORMAT: ColumnFormat = {
  kind: 'text',
  decimals: 2,
  currency: 'EUR',
  thousands_separator: true,
  date_pattern: 'MMM d, yyyy',
  null_display: '—',
  prefix: '',
  suffix: '',
};

export function ColumnInspector({
  column,
  tables,
  onUpdate,
}: {
  column: ReportColumn | null;
  tables: Record<string, SchemaTable>;
  onUpdate: (id: string, patch: Partial<ReportColumn>) => void;
}) {
  const [tab, setTab] = useState<Tab>('Properties');

  if (!column) {
    return (
      <aside className="flex w-[300px] shrink-0 flex-col border-l border-line bg-white">
        <div className="px-4 pb-2 pt-3">
          <h2 className="panel-title">Column Properties</h2>
        </div>
        <EmptyState
          title="No column selected"
          hint="Select a row in Report Columns to change how it is named and formatted."
        />
      </aside>
    );
  }

  const table = tables[column.table];
  const meta = table?.columns?.find((candidate) => candidate.name === column.field);
  const format = column.format ?? { ...DEFAULT_FORMAT, kind: inferKind(meta?.data_type) };
  const allowed: Aggregation[] = meta?.aggregations ?? ['none'];

  const patchFormat = (patch: Partial<ColumnFormat>) =>
    onUpdate(column.id, { format: { ...format, ...patch } });

  const sampleValue =
    format.kind === 'date' || format.kind === 'datetime'
      ? '2026-05-20T10:45:00'
      : format.kind === 'boolean'
        ? true
        : 1234.5678;

  return (
    <aside className="flex w-[300px] shrink-0 flex-col border-l border-line bg-white">
      <div className="px-4 pb-1 pt-3">
        <h2 className="panel-title mb-2">Column Properties</h2>
        <div className="flex gap-3 border-b border-line">
          {TABS.map((name) => (
            <button
              key={name}
              type="button"
              onClick={() => setTab(name)}
              className={clsx(
                '-mb-px border-b-2 px-0.5 pb-1.5 text-xs font-medium transition-colors',
                tab === name
                  ? 'border-accent text-accent'
                  : 'border-transparent text-ink-muted hover:text-ink',
              )}
            >
              {name}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto px-4 py-3">
        {tab === 'Properties' && (
          <>
            <div>
              <label className="label">Display Name</label>
              <input
                value={column.display_name ?? ''}
                onChange={(event) => onUpdate(column.id, { display_name: event.target.value })}
                className="field"
              />
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="label">Source</label>
                <input value={table?.label ?? column.table} disabled className="field" />
              </div>
              <div>
                <label className="label">Field</label>
                <input value={column.field} disabled className="field font-mono text-xs" />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="label">Aggregation</label>
                <Select
                  value={column.aggregation}
                  onChange={(value) =>
                    onUpdate(column.id, { aggregation: value as Aggregation })
                  }
                  options={allowed.map((aggregation) => ({
                    value: aggregation,
                    label: AGGREGATION_LABELS[aggregation],
                  }))}
                />
              </div>
              <div>
                <label className="label">Data Type</label>
                <input value={meta?.data_type ?? 'unknown'} disabled className="field" />
              </div>
            </div>

            {allowed.length <= 3 && (
              <p className="rounded border border-line bg-canvas px-2 py-1.5 text-2xs text-ink-muted">
                SUM and AVG are unavailable because this is a{' '}
                <strong>{meta?.data_type}</strong> field.
              </p>
            )}

            <div>
              <label className="label">Alignment</label>
              <div className="flex overflow-hidden rounded border border-line">
                {(['left', 'center', 'right'] as const).map((align) => (
                  <button
                    key={align}
                    type="button"
                    onClick={() => onUpdate(column.id, { align })}
                    className={clsx(
                      'flex-1 py-1.5 text-xs capitalize transition-colors',
                      column.align === align
                        ? 'bg-accent text-white'
                        : 'bg-white text-ink-muted hover:bg-canvas',
                    )}
                  >
                    {align}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label className="label">Width (px)</label>
              <input
                type="number"
                min={40}
                max={800}
                value={column.width ?? ''}
                placeholder="Auto"
                onChange={(event) =>
                  onUpdate(column.id, {
                    width: event.target.value ? Number(event.target.value) : null,
                  })
                }
                className="field"
              />
            </div>

            <Checkbox
              checked={column.visible}
              onChange={(visible) => onUpdate(column.id, { visible })}
              label={<span className="text-sm">Visible in Report</span>}
            />

            {meta?.is_masked && (
              <p className="rounded border border-warn-border bg-warn-soft px-2 py-1.5 text-2xs text-warn">
                This field is masked by a data policy. Values are obscured before they leave
                the server.
              </p>
            )}
          </>
        )}

        {tab === 'Format' && (
          <>
            <div>
              <label className="label">Format</label>
              <Select
                value={format.kind}
                onChange={(kind) => patchFormat({ kind: kind as ColumnFormat['kind'] })}
                options={[
                  { value: 'text', label: 'Text' },
                  { value: 'number', label: 'Number' },
                  { value: 'currency', label: 'Currency' },
                  { value: 'percent', label: 'Percentage' },
                  { value: 'date', label: 'Date' },
                  { value: 'datetime', label: 'Date and time' },
                  { value: 'boolean', label: 'Yes / No' },
                ]}
              />
            </div>

            {(format.kind === 'number' ||
              format.kind === 'currency' ||
              format.kind === 'percent') && (
              <>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="label">Decimals</label>
                    <input
                      type="number"
                      min={0}
                      max={10}
                      value={format.decimals}
                      onChange={(event) =>
                        patchFormat({ decimals: Number(event.target.value) })
                      }
                      className="field"
                    />
                  </div>
                  {format.kind === 'currency' && (
                    <div>
                      <label className="label">Currency</label>
                      <Select
                        value={format.currency}
                        onChange={(currency) => patchFormat({ currency })}
                        options={['EUR', 'USD', 'GBP', 'AED', 'PKR', 'INR'].map((code) => ({
                          value: code,
                          label: code,
                        }))}
                      />
                    </div>
                  )}
                </div>
                <Checkbox
                  checked={format.thousands_separator}
                  onChange={(value) => patchFormat({ thousands_separator: value })}
                  label={<span className="text-sm">Thousands separator</span>}
                />
              </>
            )}

            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="label">Prefix</label>
                <input
                  value={format.prefix}
                  onChange={(event) => patchFormat({ prefix: event.target.value })}
                  className="field"
                />
              </div>
              <div>
                <label className="label">Suffix</label>
                <input
                  value={format.suffix}
                  onChange={(event) => patchFormat({ suffix: event.target.value })}
                  className="field"
                />
              </div>
            </div>

            <div>
              <label className="label">Show empty values as</label>
              <input
                value={format.null_display}
                onChange={(event) => patchFormat({ null_display: event.target.value })}
                className="field"
              />
            </div>

            <div className="rounded border border-line bg-canvas px-3 py-2">
              <span className="label mb-0.5">Preview</span>
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium tabular">
                  {formatValue(sampleValue, format, meta?.data_type ?? 'text')}
                </span>
                <Badge>{format.kind}</Badge>
              </div>
            </div>
          </>
        )}
      </div>
    </aside>
  );
}

function inferKind(dataType?: string): ColumnFormat['kind'] {
  switch (dataType) {
    case 'integer':
    case 'decimal':
      return 'number';
    case 'date':
      return 'date';
    case 'datetime':
      return 'datetime';
    case 'boolean':
      return 'boolean';
    default:
      return 'text';
  }
}
