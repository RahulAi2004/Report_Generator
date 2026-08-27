'use client';

import clsx from 'clsx';
import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import { Badge, Skeleton } from '@/components/ui/primitives';
import { VISIBILITY_LABEL } from '@/lib/board-types';
import type { BoardCount, BoardReport } from '@/lib/board-types';

/**
 * The board's rows, as a table and as cards.
 *
 * One rule runs through both: a count that could not be produced is a dash, not
 * a zero. Zero is a fact about the business; a dash is a fact about the query.
 * Collapsing them would let an outage read as "this report returns nothing".
 */

const stroke = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.7,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
};

export type SortKey =
  | 'name' | 'field_count' | 'records' | 'empty_records' | 'visibility' | 'updated_at';

export interface RowActions {
  onRun: (report: BoardReport) => void;
  onEdit: (report: BoardReport) => void;
  onDuplicate: (report: BoardReport) => void;
  onDelete: (report: BoardReport) => void;
  onPin?: (report: BoardReport) => void;
  canEdit: boolean;
  canDelete: boolean;
}

function tone(visibility: BoardReport['visibility']) {
  return visibility === 'organization' ? 'good' : visibility === 'team' ? 'accent' : 'warn';
}

/** A count cell: the number, a dash when unknown, a skeleton while loading. */
function Count({
  value,
  count,
  loading,
}: {
  value: 'records' | 'empty_records';
  count?: BoardCount;
  loading: boolean;
}) {
  if (!count && loading) return <Skeleton className="ml-auto h-3.5 w-9" />;
  if (!count) return <span className="text-ink-faint">—</span>;

  const number = count[value];
  if (number == null) {
    return (
      <span
        className="cursor-help text-ink-faint"
        title={count.error ?? 'This count could not be produced.'}
      >
        —
      </span>
    );
  }
  return (
    <span className={clsx('tabular', value === 'empty_records' && number > 0 && 'text-warn')}>
      {number.toLocaleString()}
    </span>
  );
}

function DashboardLinks({ report }: { report: BoardReport }) {
  if (report.dashboards.length === 0) {
    return <span className="text-2xs text-ink-faint">Not on a dashboard</span>;
  }
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
      {report.dashboards.map((dashboard) => (
        <Link
          key={dashboard.id}
          href={`/dashboards/builder?id=${dashboard.id}`}
          className="inline-flex items-center gap-1 text-accent hover:underline"
        >
          {dashboard.name}
          <svg viewBox="0 0 24 24" className="h-3 w-3" {...stroke}>
            <path d="M14 4h6v6M20 4l-8 8M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
          </svg>
        </Link>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
export function ReportTable({
  reports,
  counts,
  countsLoading,
  sort,
  direction,
  onSort,
  actions,
}: {
  reports: BoardReport[];
  counts: Record<string, BoardCount>;
  countsLoading: boolean;
  sort: SortKey;
  direction: 'asc' | 'desc';
  onSort: (key: SortKey) => void;
  actions: RowActions;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[1080px] text-sm">
        <thead>
          <tr className="border-y border-line bg-canvas/60">
            <Header label="Report Name" sortKey="name" {...{ sort, direction, onSort }} />
            <th className="px-4 py-2.5 text-left text-2xs font-semibold uppercase tracking-wide text-ink-muted">
              Description
            </th>
            <th className="px-4 py-2.5 text-left text-2xs font-semibold uppercase tracking-wide text-ink-muted">
              Dashboard
            </th>
            <Header label="No. of Fields" sortKey="field_count" align="right" {...{ sort, direction, onSort }} />
            <Header label="Records" sortKey="records" align="right" {...{ sort, direction, onSort }} />
            <Header label="Empty Records" sortKey="empty_records" align="right" {...{ sort, direction, onSort }} />
            <Header label="Visibility" sortKey="visibility" {...{ sort, direction, onSort }} />
            <th className="px-4 py-2.5 text-left text-2xs font-semibold uppercase tracking-wide text-ink-muted">
              Actions
            </th>
          </tr>
        </thead>
        <tbody>
          {reports.map((report) => (
            <tr key={report.id} className="border-b border-line/70 row-hover">
              <td className="max-w-[240px] px-4 py-2.5">
                <button
                  type="button"
                  onClick={() => actions.onEdit(report)}
                  className="block max-w-full truncate text-left font-medium text-ink hover:text-accent"
                  title={report.name}
                >
                  {report.name}
                </button>
                {report.is_draft && <Badge tone="warn">Draft</Badge>}
              </td>
              <td className="max-w-[260px] px-4 py-2.5 text-ink-muted">
                <span className="block truncate" title={report.description ?? ''}>
                  {report.description || <span className="text-ink-faint">—</span>}
                </span>
              </td>
              <td className="max-w-[220px] px-4 py-2.5">
                <DashboardLinks report={report} />
              </td>
              <td className="px-4 py-2.5 text-right tabular text-ink">{report.field_count}</td>
              <td className="px-4 py-2.5 text-right text-ink">
                <Count value="records" count={counts[report.id]} loading={countsLoading} />
              </td>
              <td className="px-4 py-2.5 text-right text-ink">
                <Count value="empty_records" count={counts[report.id]} loading={countsLoading} />
              </td>
              <td className="px-4 py-2.5">
                <Badge tone={tone(report.visibility)}>
                  {VISIBILITY_LABEL[report.visibility]}
                </Badge>
              </td>
              <td className="px-4 py-2.5">
                <Actions report={report} actions={actions} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Header({
  label,
  sortKey,
  sort,
  direction,
  onSort,
  align,
}: {
  label: string;
  sortKey: SortKey;
  sort: SortKey;
  direction: 'asc' | 'desc';
  onSort: (key: SortKey) => void;
  align?: 'right';
}) {
  const active = sort === sortKey;
  return (
    <th
      className={clsx(
        'px-4 py-2.5 text-2xs font-semibold uppercase tracking-wide text-ink-muted',
        align === 'right' ? 'text-right' : 'text-left',
      )}
    >
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={clsx(
          'inline-flex items-center gap-1 transition-colors hover:text-ink',
          align === 'right' && 'flex-row-reverse',
          active && 'text-accent',
        )}
      >
        {label}
        <svg viewBox="0 0 24 24" className="h-3 w-3" {...stroke}>
          {active ? (
            direction === 'asc' ? <path d="M12 19V5M6 11l6-6 6 6" /> : <path d="M12 5v14M6 13l6 6 6-6" />
          ) : (
            <path d="M8 9l4-4 4 4M8 15l4 4 4-4" />
          )}
        </svg>
      </button>
    </th>
  );
}

// ---------------------------------------------------------------------------
export function ReportGrid({
  reports,
  counts,
  countsLoading,
  actions,
}: {
  reports: BoardReport[];
  counts: Record<string, BoardCount>;
  countsLoading: boolean;
  actions: RowActions;
}) {
  return (
    <div className="grid gap-2.5 p-4 sm:grid-cols-2 xl:grid-cols-3">
      {reports.map((report) => {
        const count = counts[report.id];
        return (
          <div key={report.id} className="rounded-lg border border-line p-3 hover:border-line-strong">
            <div className="flex items-start justify-between gap-2">
              <button
                type="button"
                onClick={() => actions.onEdit(report)}
                className="min-w-0 flex-1 truncate text-left text-sm font-semibold text-ink hover:text-accent"
                title={report.name}
              >
                {report.name}
              </button>
              <Badge tone={tone(report.visibility)}>
                {VISIBILITY_LABEL[report.visibility]}
              </Badge>
            </div>

            <p className="mt-0.5 line-clamp-2 min-h-[28px] text-2xs text-ink-muted">
              {report.description || 'No description'}
            </p>

            <div className="mt-2 grid grid-cols-3 gap-1.5 rounded border border-line bg-canvas/60 p-2 text-center">
              <Stat label="Fields" value={String(report.field_count)} />
              <Stat
                label="Records"
                value={
                  countsLoading && !count ? '…'
                  : count?.records == null ? '—'
                  : count.records.toLocaleString()
                }
              />
              <Stat
                label="Empty"
                value={
                  countsLoading && !count ? '…'
                  : count?.empty_records == null ? '—'
                  : count.empty_records.toLocaleString()
                }
                warn={Boolean(count?.empty_records)}
              />
            </div>

            <div className="mt-2 flex items-center justify-between gap-2">
              <div className="min-w-0 flex-1 truncate text-2xs">
                <DashboardLinks report={report} />
              </div>
              <Actions report={report} actions={actions} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Stat({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div>
      <p className={clsx('tabular text-sm font-semibold', warn ? 'text-warn' : 'text-ink')}>
        {value}
      </p>
      <p className="text-[10px] uppercase tracking-wide text-ink-faint">{label}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
function Actions({ report, actions }: { report: BoardReport; actions: RowActions }) {
  const [open, setOpen] = useState(false);
  const holder = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!holder.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  return (
    <div ref={holder} className="relative flex items-center gap-1">
      <Action title="Run" onClick={() => actions.onRun(report)}>
        <path d="M7 4.5l12 7.5-12 7.5z" />
      </Action>
      <Action title="Edit" onClick={() => actions.onEdit(report)} disabled={!actions.canEdit}>
        <path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />
      </Action>
      <Action
        title="Duplicate"
        onClick={() => actions.onDuplicate(report)}
        disabled={!actions.canEdit}
      >
        <rect x="9" y="9" width="11" height="11" rx="2" />
        <path d="M5 15V5a2 2 0 0 1 2-2h8" />
      </Action>
      <Action title="More" onClick={() => setOpen((value) => !value)}>
        <circle cx="12" cy="5" r="1.3" />
        <circle cx="12" cy="12" r="1.3" />
        <circle cx="12" cy="19" r="1.3" />
      </Action>

      {open && (
        <div className="menu">
          <button
            type="button"
            className="menu-item"
            onClick={() => { setOpen(false); actions.onRun(report); }}
          >
            Open in builder
          </button>
          {actions.onPin && (
            <button
              type="button"
              className="menu-item"
              onClick={() => { setOpen(false); actions.onPin!(report); }}
            >
              Add to a dashboard
            </button>
          )}
          <div className="menu-sep" />
          <button
            type="button"
            className="menu-item text-danger hover:bg-danger-soft hover:text-danger"
            disabled={!actions.canDelete && !report.is_mine}
            onClick={() => { setOpen(false); actions.onDelete(report); }}
          >
            Delete report
          </button>
        </div>
      )}
    </div>
  );
}

function Action({
  title,
  onClick,
  disabled,
  children,
}: {
  title: string;
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      disabled={disabled}
      onClick={onClick}
      className="inline-flex h-7 w-7 items-center justify-center rounded border border-line
                 bg-white text-ink-muted transition-colors hover:border-accent-border
                 hover:bg-accent-soft hover:text-accent disabled:cursor-not-allowed
                 disabled:opacity-40 disabled:hover:border-line disabled:hover:bg-white
                 disabled:hover:text-ink-muted"
    >
      <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" {...stroke}>
        {children}
      </svg>
    </button>
  );
}
