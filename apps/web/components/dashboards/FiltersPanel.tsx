'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Badge, Button, Checkbox, IconButton, Select } from '@/components/ui/primitives';
import { api } from '@/lib/api';
import { FieldSelect, TableSelect, useTableColumns } from './FieldPicker';
import { StepNumber } from './MetricCardsPanel';
import { useDashboard } from '@/store/dashboard';
import type { DashboardFilter } from '@/lib/dashboard-types';

/**
 * Dashboard Filters (panel 7).
 *
 * A control here narrows every card and report below it. Values are chosen from
 * what the column actually contains rather than typed, because "delivered"
 * instead of "Delivered" produces an empty dashboard with nothing on screen to
 * explain why.
 */
export function FiltersPanel() {
  const { definition, addFilter, updateFilter, removeFilter, clearFilterValues } =
    useDashboard();
  const [editing, setEditing] = useState<string | null>(null);
  const active = definition.filters.filter((filter) => filter.values.length > 0).length;

  return (
    <section className="panel">
      <div className="panel-header">
        <div className="flex items-center gap-2">
          <StepNumber n={7} />
          <div>
            <h2 className="text-sm font-semibold text-ink">Filters</h2>
            <p className="text-2xs text-ink-muted">Add dashboard level filters.</p>
          </div>
        </div>
        <Button size="sm" onClick={() => setEditing(addFilter())}>
          <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth={2}>
            <path d="M12 5v14M5 12h14" />
          </svg>
          Add Filter
        </Button>
      </div>

      <div className="space-y-2.5 p-3">
        {definition.filters.length === 0 && (
          <p className="rounded border border-dashed border-line-strong px-3 py-5 text-center text-xs text-ink-muted">
            No filters. Add one and it narrows every card and report on this dashboard.
          </p>
        )}

        {definition.filters.map((filter) => (
          <FilterControl
            key={filter.id}
            filter={filter}
            editing={editing === filter.id}
            onEdit={() => setEditing(editing === filter.id ? null : filter.id)}
            onChange={(patch) => updateFilter(filter.id, patch)}
            onRemove={() => {
              removeFilter(filter.id);
              if (editing === filter.id) setEditing(null);
            }}
          />
        ))}

        {active > 0 && (
          <button
            type="button"
            onClick={clearFilterValues}
            className="w-full rounded border border-line py-1 text-2xs font-medium text-ink-muted hover:border-line-strong hover:text-ink"
          >
            Clear all {active} value{active === 1 ? '' : 's'}
          </button>
        )}
      </div>
    </section>
  );
}

function FilterControl({
  filter,
  editing,
  onEdit,
  onChange,
  onRemove,
}: {
  filter: DashboardFilter;
  editing: boolean;
  onEdit: () => void;
  onChange: (patch: Partial<DashboardFilter>) => void;
  onRemove: () => void;
}) {
  const { columns } = useTableColumns(filter.table);
  const column = columns.find((candidate) => candidate.name === filter.field);
  const configured = Boolean(filter.table && filter.field);

  return (
    <div className="rounded-lg border border-line">
      <div className="flex items-center gap-1 px-2 pt-1.5">
        <span className="min-w-0 flex-1 truncate text-xs font-medium text-ink">
          {filter.label}
        </span>
        {!configured && <Badge tone="warn">Not set</Badge>}
        <IconButton title="Settings" onClick={onEdit}>
          <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={1.7}>
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-2.7 1.1V21a2 2 0 1 1-4 0v-.1A1.6 1.6 0 0 0 7.5 19l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.6 1.6 0 0 0 3 13.6H3a2 2 0 1 1 0-4h.1A1.6 1.6 0 0 0 4.7 7l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 2.7-1.1V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 2.7 1.1l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0 1.1 2.7H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1.3z" />
          </svg>
        </IconButton>
        <IconButton title="Remove" onClick={onRemove}>
          <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={1.8}>
            <path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13" />
          </svg>
        </IconButton>
      </div>

      <div className="px-2 pb-2 pt-1">
        {configured ? (
          <ValueControl filter={filter} onChange={onChange} isDate={
            column?.data_type === 'date' || column?.data_type === 'datetime'
          } />
        ) : (
          <p className="text-2xs text-ink-faint">Choose a field in settings.</p>
        )}
      </div>

      {editing && (
        <div className="space-y-2 border-t border-line px-2.5 py-2.5">
          <div>
            <label className="label">Label</label>
            <input
              value={filter.label}
              onChange={(event) => onChange({ label: event.target.value })}
              className="field"
            />
          </div>
          <div>
            <label className="label">Table</label>
            <TableSelect
              value={filter.table}
              onChange={(table) => onChange({ table, field: '', values: [] })}
            />
          </div>
          <div>
            <label className="label">Field</label>
            <FieldSelect
              table={filter.table}
              value={filter.field}
              onChange={(field) => onChange({ field, values: [] })}
            />
          </div>
          <div>
            <label className="label">Match</label>
            <Select
              value={filter.operator}
              onChange={(operator) => onChange({ operator, values: [] })}
              options={(column?.operators ?? ['equals'])
                .filter((op) => !['is_null', 'is_not_null', 'is_empty', 'is_not_empty'].includes(op))
                .map((op) => ({ value: op, label: op.replace(/_/g, ' ') }))}
            />
          </div>
          <p className="rounded border border-line bg-canvas px-2 py-1.5 text-[10px] leading-snug text-ink-muted">
            This filter narrows any card or report that reads{' '}
            <strong>{filter.table || 'its table'}</strong>. Anything that does not is
            marked, rather than left looking filtered.
          </p>
        </div>
      )}
    </div>
  );
}

/** The value control itself: a picker of real values, or a date. */
function ValueControl({
  filter,
  onChange,
  isDate,
}: {
  filter: DashboardFilter;
  onChange: (patch: Partial<DashboardFilter>) => void;
  isDate: boolean;
}) {
  const values = useQuery({
    queryKey: ['column-values', filter.table, filter.field],
    queryFn: () => api.columnValues(filter.table, filter.field),
    enabled: Boolean(filter.table && filter.field) && !isDate,
    staleTime: 120_000,
  });

  if (isDate) {
    return (
      <input
        type="date"
        value={(filter.values[0] as string) ?? ''}
        onChange={(event) =>
          onChange({ values: event.target.value ? [event.target.value] : [] })
        }
        className="field py-1 text-xs"
      />
    );
  }

  const multi = filter.operator === 'in' || filter.operator === 'not_in';
  const choices = values.data?.supported ? values.data.values : [];

  if (!values.data?.supported && !values.isLoading) {
    // Free text and high-cardinality columns cannot be listed; typing is the
    // only option left, and saying so beats an empty dropdown.
    return (
      <input
        value={(filter.values[0] as string) ?? ''}
        placeholder="Type a value"
        onChange={(event) =>
          onChange({ values: event.target.value ? [event.target.value] : [] })
        }
        className="field py-1 text-xs"
      />
    );
  }

  if (multi) {
    return (
      <div className="max-h-32 space-y-0.5 overflow-y-auto rounded border border-line p-1.5">
        {choices.length === 0 && (
          <p className="text-2xs text-ink-faint">
            {values.isLoading ? 'Loading values…' : 'No values found.'}
          </p>
        )}
        {choices.map((choice) => {
          const checked = filter.values.includes(choice);
          return (
            <Checkbox
              key={choice}
              checked={checked}
              onChange={(next) =>
                onChange({
                  values: next
                    ? [...filter.values, choice]
                    : filter.values.filter((value) => value !== choice),
                })
              }
              label={<span className="text-xs">{choice}</span>}
            />
          );
        })}
      </div>
    );
  }

  return (
    <Select
      value={(filter.values[0] as string) ?? ''}
      onChange={(value) => onChange({ values: value ? [value] : [] })}
      // Empty means All -- a control that is present but not narrowing.
      placeholder={values.isLoading ? 'Loading…' : 'All'}
      options={choices.map((choice) => ({ value: choice, label: choice }))}
      className="py-1 text-xs"
    />
  );
}

/**
 * Dashboard Settings (panel 8).
 *
 * These decide what a viewer of the saved dashboard can do with it, so they are
 * stored with the dashboard rather than remembered per browser.
 */
export function SettingsPanel() {
  const { definition, setSetting } = useDashboard();
  const settings = definition.settings;

  const rows: { key: keyof typeof settings; label: string; hint: string }[] = [
    { key: 'show_time_range', label: 'Show time range selector', hint: 'Viewers can change the period.' },
    { key: 'show_refresh', label: 'Show refresh button', hint: 'Re-runs every card and report.' },
    { key: 'allow_export', label: 'Allow users to export dashboard', hint: 'Downloads what is on screen.' },
    { key: 'allow_viewers_to_save', label: 'Allow users to save changes', hint: 'Their edits become everyone’s.' },
  ];

  return (
    <section className="panel">
      <div className="panel-header">
        <div className="flex items-center gap-2">
          <StepNumber n={8} />
          <h2 className="text-sm font-semibold text-ink">Dashboard Settings</h2>
        </div>
      </div>
      <div className="space-y-2 p-3">
        {rows.map((row) => (
          <div key={row.key}>
            <Checkbox
              checked={settings[row.key]}
              onChange={(value) => setSetting(row.key, value)}
              label={<span className="text-xs text-ink">{row.label}</span>}
            />
            <p className="ml-[22px] text-[10px] text-ink-faint">{row.hint}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
