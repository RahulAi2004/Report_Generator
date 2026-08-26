'use client';

import clsx from 'clsx';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Badge, Button, Checkbox, IconButton, Select, Skeleton } from '@/components/ui/primitives';
import { api } from '@/lib/api';
import { useDashboard } from '@/store/dashboard';
import { rangeLabel } from '@/lib/dashboard-types';
import type {
  DashboardDefinition,
  DashboardOptions,
  DashboardReportPanel,
} from '@/lib/dashboard-types';

/**
 * Reports on the dashboard.
 *
 * Each panel runs a saved report with the dashboard's filters applied. The
 * chips above the table say what was actually applied -- filters the report
 * could not honour are shown struck through rather than omitted, because a
 * missing chip reads as "no filter" and a present one reads as "filtered", and
 * the truth here is neither.
 */
export function ReportPanels({
  definition,
  options,
  editable = true,
}: {
  definition: DashboardDefinition;
  options?: DashboardOptions;
  editable?: boolean;
}) {
  const { addReport, removeReport, updateReport } = useDashboard();
  const [adding, setAdding] = useState(false);

  return (
    <section className="panel">
      <div className="panel-header">
        <div className="flex items-center gap-2">
          <StepBadge n={9} />
          <div>
            <h2 className="text-sm font-semibold text-ink">Reports</h2>
            <p className="text-2xs text-ink-muted">Add reports and adjust columns.</p>
          </div>
        </div>
        {editable && (
          <Button size="sm" onClick={() => setAdding((value) => !value)}>
            <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth={2}>
              <path d="M12 5v14M5 12h14" />
            </svg>
            Add Report
          </Button>
        )}
      </div>

      <div className="space-y-3 p-3">
        {adding && editable && (
          <div className="rounded-lg border border-accent-border bg-accent-soft/40 p-2.5">
            <label className="label">Choose a saved report</label>
            <Select
              value=""
              onChange={(reportId) => {
                if (!reportId) return;
                const report = options?.reports.find((r) => r.id === reportId);
                addReport(reportId, report?.name);
                setAdding(false);
              }}
              placeholder="Select a report…"
              options={(options?.reports ?? []).map((report) => ({
                value: report.id,
                label: report.module ? `${report.name}  ·  ${report.module}` : report.name,
              }))}
            />
            {options && options.reports.length === 0 && (
              <p className="mt-1.5 text-2xs text-ink-muted">
                No saved reports yet. Build one in Reports first, then add it here.
              </p>
            )}
          </div>
        )}

        {definition.reports.length === 0 && !adding && (
          <p className="rounded border border-dashed border-line-strong px-3 py-6 text-center text-xs text-ink-muted">
            No reports on this dashboard yet.
          </p>
        )}

        {definition.reports.map((panel) => (
          <ReportPanel
            key={panel.id}
            panel={panel}
            definition={definition}
            editable={editable}
            onRemove={() => removeReport(panel.id)}
            onChange={(patch) => updateReport(panel.id, patch)}
          />
        ))}
      </div>
    </section>
  );
}

function ReportPanel({
  panel,
  definition,
  editable,
  onRemove,
  onChange,
}: {
  panel: DashboardReportPanel;
  definition: DashboardDefinition;
  editable: boolean;
  onRemove: () => void;
  onChange: (patch: Partial<DashboardReportPanel>) => void;
}) {
  const [page, setPage] = useState(1);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const result = useQuery({
    queryKey: ['dashboard-panel', panel.id, definition, page],
    queryFn: () => api.dashboardPanel(definition, panel.id, page, panel.page_size),
    retry: false,
  });

  const data = result.data;
  const applied = data?.filters?.applied ?? [];
  const missed = data?.filters?.not_applicable ?? [];
  const windowApplied = data?.filters?.time_range_applied !== false;

  return (
    <div className="rounded-lg border border-line">
      <div className="flex items-center justify-between gap-2 border-b border-line px-3 py-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-ink">
            {panel.title || data?.title || 'Report'}
          </p>
          <p className="truncate text-2xs text-ink-muted">
            Source: {data?.source ?? '—'} · Type: Table
            {data ? ` · ${data.duration_ms}ms` : ''}
          </p>
        </div>
        {editable && (
          <div className="flex shrink-0 items-center gap-1">
            <IconButton title="Panel settings" onClick={() => setSettingsOpen((v) => !v)}>
              <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={1.8}>
                <path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />
              </svg>
            </IconButton>
            <IconButton title="Remove panel" onClick={onRemove}>
              <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={1.8}>
                <path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13" />
              </svg>
            </IconButton>
          </div>
        )}
      </div>

      {settingsOpen && editable && (
        <div className="space-y-2 border-b border-line bg-canvas/60 px-3 py-2.5">
          <div className="grid gap-2 sm:grid-cols-2">
            <div>
              <label className="label">Panel title</label>
              <input
                value={panel.title ?? ''}
                placeholder={data?.source ?? 'Report name'}
                onChange={(event) => onChange({ title: event.target.value || null })}
                className="field"
              />
            </div>
            <div>
              <label className="label">Rows per page</label>
              <Select
                value={String(panel.page_size)}
                onChange={(value) => onChange({ page_size: Number(value) })}
                options={[5, 10, 25, 50, 100].map((size) => ({
                  value: String(size),
                  label: `${size} / page`,
                }))}
              />
            </div>
          </div>
          <Checkbox
            checked={panel.ignore_dashboard_filters}
            onChange={(value) => onChange({ ignore_dashboard_filters: value })}
            label={<span className="text-xs">Ignore the dashboard filters</span>}
          />
          <Checkbox
            checked={panel.ignore_time_range}
            onChange={(value) => onChange({ ignore_time_range: value })}
            label={<span className="text-xs">Ignore the time range</span>}
          />

          {data?.columns.length ? (
            <div>
              <label className="label">Columns shown</label>
              <div className="flex max-h-28 flex-wrap gap-x-3 gap-y-1 overflow-y-auto rounded border border-line bg-white p-2">
                {data.columns.map((column) => {
                  // An empty list means "everything", which is what a new panel
                  // should do; the first uncheck is what makes it a choice.
                  const shown = panel.columns.length === 0 || panel.columns.includes(column.key);
                  return (
                    <Checkbox
                      key={column.key}
                      checked={shown}
                      onChange={(next) => {
                        const current =
                          panel.columns.length === 0
                            ? data.columns.map((c) => c.key)
                            : panel.columns;
                        onChange({
                          columns: next
                            ? [...current, column.key]
                            : current.filter((key) => key !== column.key),
                        });
                      }}
                      label={<span className="text-xs">{column.label}</span>}
                    />
                  );
                })}
              </div>
            </div>
          ) : null}
        </div>
      )}

      {/* What the dashboard actually did to this report. */}
      <div className="flex flex-wrap items-center gap-1.5 border-b border-line px-3 py-2">
        <span className="text-2xs font-medium text-ink-muted">Applied Filters (Live)</span>
        {definition.settings.show_time_range && (
          <Chip struck={!windowApplied || panel.ignore_time_range}>
            {rangeLabel(definition.time_range)}
          </Chip>
        )}
        {applied.map((label) => (
          <Chip key={label}>{label}</Chip>
        ))}
        {missed.map((label) => (
          <Chip key={label} struck title="This report does not read that table, so the filter could not narrow it.">
            {label}
          </Chip>
        ))}
        {applied.length === 0 && missed.length === 0 && (
          <span className="text-2xs text-ink-faint">None</span>
        )}
      </div>

      <div className="overflow-x-auto">
        {result.isLoading ? (
          <div className="space-y-1.5 p-3">
            {Array.from({ length: 4 }).map((_, index) => (
              <Skeleton key={index} className="h-6 w-full" />
            ))}
          </div>
        ) : result.isError ? (
          <p className="px-3 py-4 text-xs text-danger">
            {(result.error as Error).message}
          </p>
        ) : !data?.ok ? (
          <div className="px-3 py-4">
            {(data?.diagnostics ?? [])
              .filter((d) => d.severity === 'error')
              .map((d, index) => (
                <p key={index} className="text-xs text-danger">{d.message}</p>
              ))}
          </div>
        ) : data.rows.length === 0 ? (
          <p className="px-3 py-6 text-center text-xs text-ink-muted">
            No rows match the filters above.
          </p>
        ) : (
          <table className="striped w-full min-w-max text-sm">
            <thead>
              <tr className="border-b border-line bg-canvas/60">
                {data.columns.map((column) => (
                  <th
                    key={column.key}
                    className="whitespace-nowrap px-3 py-1.5 text-left text-2xs font-semibold uppercase tracking-wide text-ink-muted"
                  >
                    {column.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row, index) => (
                <tr key={index} className="border-b border-line/60">
                  {data.columns.map((column) => (
                    <td
                      key={column.key}
                      className={clsx(
                        'whitespace-nowrap px-3 py-1.5 text-ink',
                        isNumeric(column.data_type) && 'tabular text-right',
                      )}
                    >
                      {row[column.key] == null ? (
                        <span className="text-ink-faint">—</span>
                      ) : (
                        String(row[column.key])
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {data?.ok && (
        <div className="flex items-center justify-between border-t border-line px-3 py-1.5">
          <span className="text-2xs text-ink-muted">
            Showing {data.rows.length === 0 ? 0 : (page - 1) * data.page_size + 1} to{' '}
            {(page - 1) * data.page_size + data.rows.length}
            {data.has_more ? '' : ' of ' + ((page - 1) * data.page_size + data.rows.length)}
          </span>
          <div className="flex items-center gap-1">
            <Button size="sm" disabled={page === 1} onClick={() => setPage((p) => p - 1)}>
              Previous
            </Button>
            <span className="px-1.5 text-2xs tabular text-ink-muted">Page {page}</span>
            <Button size="sm" disabled={!data.has_more} onClick={() => setPage((p) => p + 1)}>
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

function Chip({
  children,
  struck,
  title,
}: {
  children: React.ReactNode;
  struck?: boolean;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={clsx(
        'chip',
        struck
          ? 'border-warn-border bg-warn-soft text-warn line-through decoration-warn/60'
          : 'border-accent-border bg-accent-soft text-accent',
      )}
    >
      {children}
    </span>
  );
}

function StepBadge({ n }: { n: number }) {
  return (
    <span className="flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded bg-accent text-[10px] font-bold text-white">
      {n}
    </span>
  );
}

const isNumeric = (dataType: string) =>
  dataType === 'integer' || dataType === 'decimal';
