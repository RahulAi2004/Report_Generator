'use client';

import clsx from 'clsx';
import { useQuery } from '@tanstack/react-query';
import { Button, Select } from '@/components/ui/primitives';
import { api } from '@/lib/api';
import { FieldSelect, TableSelect, isDateColumn } from './FieldPicker';
import { useDashboard } from '@/store/dashboard';
import type { DashboardOptions, Preset, RangeMode } from '@/lib/dashboard-types';

/**
 * The four placement fields and the time range, across the top.
 *
 * Placement uses the same taxonomy reports file into, so a dashboard and the
 * reports on it end up in the same part of the menu rather than in two parallel
 * hierarchies that drift apart.
 */
export function PlacementBar({ options }: { options?: DashboardOptions }) {
  const { definition, name, setName, setApp, setModule, dashboardId } = useDashboard();
  const apps = options?.apps ?? [];
  const modules = apps.find((app) => app.name === definition.app)?.modules ?? [];

  const saved = useQuery({
    queryKey: ['dashboards', definition.app],
    queryFn: () => api.listDashboards(definition.app ?? undefined),
    staleTime: 60_000,
  });

  return (
    <div className="grid gap-2.5 lg:grid-cols-[1fr_1fr_1.4fr_1.4fr]">
      <Field n={1} label="App" required>
        <Select
          value={definition.app ?? ''}
          onChange={setApp}
          placeholder="Select an app"
          options={apps.map((app) => ({ value: app.name, label: app.name }))}
        />
      </Field>

      <Field n={2} label="Module" required>
        <Select
          value={definition.module ?? ''}
          onChange={setModule}
          disabled={!definition.app}
          placeholder={definition.app ? 'Select a module' : 'Choose an app first'}
          options={modules.map((module) => ({ value: module, label: module }))}
        />
      </Field>

      <Field n={3} label="Dashboard Name" required>
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Customer Overview Dashboard"
          className="field"
        />
      </Field>

      <Field n={4} label="Select Existing Dashboard">
        <div className="flex gap-1.5">
          <Select
            value={dashboardId ?? ''}
            onChange={(id) => {
              if (id) window.location.href = `/dashboards/builder?id=${id}`;
            }}
            placeholder="Select Dashboard (Optional)"
            options={(saved.data?.dashboards ?? []).map((item) => ({
              value: item.id,
              label: item.name,
            }))}
            className="min-w-0 flex-1"
          />
          <Button
            size="sm"
            disabled={!dashboardId}
            onClick={() => {
              if (dashboardId) window.location.href = `/dashboards/builder?copy=${dashboardId}`;
            }}
            title="Start a new dashboard from this one"
          >
            Duplicate
          </Button>
        </div>
      </Field>
    </div>
  );
}

function Field({
  n,
  label,
  required,
  children,
}: {
  n: number;
  label: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="min-w-0">
      <label className="mb-1 flex items-center gap-1.5 text-xs font-medium text-ink">
        <span className="flex h-[16px] w-[16px] items-center justify-center rounded bg-accent text-[9px] font-bold text-white">
          {n}
        </span>
        {label}
        {required && <span className="text-danger">*</span>}
      </label>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
const PRESETS: { value: Preset; label: string }[] = [
  { value: 'daily', label: 'Daily' },
  { value: 'weekly', label: 'Weekly' },
  { value: 'monthly', label: 'Monthly' },
  { value: 'quarterly', label: 'Quarterly' },
  { value: 'yearly', label: 'Yearly' },
  { value: 'all_time', label: 'All Time' },
  { value: 'custom', label: 'Custom' },
];

const UNIT: Record<string, string> = {
  daily: 'Day', weekly: 'Week', monthly: 'Month', quarterly: 'Quarter', yearly: 'Year',
};

export function TimeRangeBar({ options }: { options?: DashboardOptions }) {
  const { definition, setPreset, setTimeRange } = useDashboard();
  const range = definition.time_range;
  const choices = options?.period_choices?.[range.preset] ?? [1, 7, 30];
  const relative = range.preset !== 'all_time' && range.preset !== 'custom';

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-white px-3 py-2 shadow-panel">
      <span className="flex items-center gap-1.5 text-xs font-medium text-ink">
        <span className="flex h-[16px] w-[16px] items-center justify-center rounded bg-accent text-[9px] font-bold text-white">
          5
        </span>
        Time Range
      </span>

      <div className="flex overflow-hidden rounded border border-line">
        {PRESETS.map((preset) => (
          <button
            key={preset.value}
            type="button"
            onClick={() => setPreset(preset.value)}
            className={clsx(
              'border-r border-line px-2.5 py-1 text-xs transition-colors last:border-r-0',
              range.preset === preset.value
                ? 'bg-accent text-white'
                : 'bg-white text-ink-muted hover:bg-canvas hover:text-ink',
            )}
          >
            {preset.label}
          </button>
        ))}
      </div>

      {relative && (
        <>
          <Select
            value={range.mode}
            onChange={(mode) => setTimeRange({ mode: mode as RangeMode })}
            options={[
              { value: 'last', label: 'Last' },
              { value: 'this', label: 'This' },
              { value: 'previous', label: 'Previous' },
            ]}
            className="w-[104px] py-1 text-xs"
          />
          {range.mode !== 'this' && (
            <Select
              value={String(range.periods)}
              onChange={(periods) => setTimeRange({ periods: Number(periods) })}
              options={choices.map((count) => ({
                value: String(count),
                label: `${range.mode === 'last' ? 'Last' : 'Previous'} ${count} ${
                  count === 1 ? UNIT[range.preset] : `${UNIT[range.preset]}s`
                }`,
              }))}
              className="w-[150px] py-1 text-xs"
            />
          )}
        </>
      )}

      {range.preset === 'custom' && (
        <div className="flex items-center gap-1.5">
          <input
            type="date"
            value={range.start ?? ''}
            onChange={(event) => setTimeRange({ start: event.target.value || null })}
            className="field w-[140px] py-1 text-xs"
          />
          <span className="text-xs text-ink-faint">to</span>
          <input
            type="date"
            value={range.end ?? ''}
            onChange={(event) => setTimeRange({ end: event.target.value || null })}
            className="field w-[140px] py-1 text-xs"
          />
        </div>
      )}

      {/* Without this the window is a period measured against nothing. */}
      {range.preset !== 'all_time' && (
        <div className="ml-auto flex items-center gap-1.5">
          <span className="text-2xs text-ink-muted">Measured on</span>
          <TableSelect
            value={range.date_field?.table ?? ''}
            onChange={(table) => setTimeRange({ date_field: { table, field: '' } })}
            className="w-[150px] py-1 text-xs"
          />
          <FieldSelect
            table={range.date_field?.table ?? ''}
            value={range.date_field?.field ?? ''}
            onChange={(field) =>
              setTimeRange({ date_field: { table: range.date_field!.table, field } })
            }
            filter={isDateColumn}
            placeholder="Date field"
            className="w-[150px] py-1 text-xs"
          />
        </div>
      )}
    </div>
  );
}
