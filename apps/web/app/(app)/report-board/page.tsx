'use client';

import clsx from 'clsx';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Badge, Button, Checkbox, EmptyState, Select, Skeleton } from '@/components/ui/primitives';
import { BoardTabs, type Tab } from '@/components/board/BoardTabs';
import { ReportGrid, ReportTable, type RowActions, type SortKey } from '@/components/board/ReportRows';
import { api, ApiError } from '@/lib/api';
import { useBuilder } from '@/store/builder';
import type { BoardCount, BoardReport } from '@/lib/board-types';

/**
 * Report Board.
 *
 * A management view of every saved report: what it is, where it appears, how
 * much data it returns and who can see it.
 *
 * The listing is metadata and renders immediately. Counting is a full pass over
 * each report's data, so it is asked for separately, once the rows are on
 * screen and only for the page being looked at -- a board that runs every
 * report it lists is a board nobody can afford to open.
 */

const ALL: Tab = { module: '', section: '', label: 'All' };
const PAGE_SIZES = [10, 25, 50, 100];

export default function ReportBoardPage() {
  const router = useRouter();
  const client = useQueryClient();
  const builder = useBuilder();

  const [tab, setTab] = useState<Tab>(ALL);
  const [search, setSearch] = useState('');
  const [view, setView] = useState<'grid' | 'list'>('list');
  const [sort, setSort] = useState<SortKey>('name');
  const [direction, setDirection] = useState<'asc' | 'desc'>('asc');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [visibilities, setVisibilities] = useState<string[]>([]);
  const [onDashboardOnly, setOnDashboardOnly] = useState(false);
  const [mineOnly, setMineOnly] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const modules = useQuery({
    queryKey: ['report-modules'],
    queryFn: api.reportModules,
    staleTime: 300_000,
  });

  // Every report, so the tab counts are real before a tab is opened. Filtering
  // happens below, in the browser, which keeps typing in the search box from
  // becoming a request per keystroke.
  const listing = useQuery({
    queryKey: ['board'],
    queryFn: () => api.board(),
  });

  const tabs = useMemo<Tab[]>(() => {
    const found: Tab[] = [ALL];
    for (const module of modules.data?.modules ?? []) {
      for (const section of module.sections) {
        found.push({ module: module.name, section, label: section });
      }
    }
    return found;
  }, [modules.data]);

  const tabCounts = useMemo(() => {
    const counts: Record<string, number> = { '/': listing.data?.reports.length ?? 0 };
    for (const report of listing.data?.reports ?? []) {
      const key = `${report.module ?? ''}/${report.section ?? ''}`;
      counts[key] = (counts[key] ?? 0) + 1;
    }
    return counts;
  }, [listing.data]);

  const filtered = useMemo(() => {
    let rows = listing.data?.reports ?? [];
    if (tab.section) {
      rows = rows.filter((r) => r.module === tab.module && r.section === tab.section);
    }
    if (search.trim()) {
      const needle = search.trim().toLowerCase();
      rows = rows.filter(
        (r) =>
          r.name.toLowerCase().includes(needle) ||
          (r.description ?? '').toLowerCase().includes(needle),
      );
    }
    if (visibilities.length) rows = rows.filter((r) => visibilities.includes(r.visibility));
    if (onDashboardOnly) rows = rows.filter((r) => r.dashboards.length > 0);
    if (mineOnly) rows = rows.filter((r) => r.is_mine);
    return rows;
  }, [listing.data, tab, search, visibilities, onDashboardOnly, mineOnly]);

  // Counts are keyed by report, so sorting by them can only use what has
  // arrived. Rows still waiting sort last rather than as zero.
  const [counts, setCounts] = useState<Record<string, BoardCount>>({});

  const sorted = useMemo(() => {
    const rows = [...filtered];
    const sign = direction === 'asc' ? 1 : -1;
    rows.sort((a, b) => {
      switch (sort) {
        case 'name':
          return sign * a.name.localeCompare(b.name);
        case 'field_count':
          return sign * (a.field_count - b.field_count);
        case 'visibility':
          return sign * a.visibility.localeCompare(b.visibility);
        case 'updated_at':
          return sign * a.updated_at.localeCompare(b.updated_at);
        case 'records':
        case 'empty_records': {
          const left = counts[a.id]?.[sort];
          const right = counts[b.id]?.[sort];
          if (left == null && right == null) return 0;
          if (left == null) return 1;   // unknown sorts last, either direction
          if (right == null) return -1;
          return sign * (left - right);
        }
        default:
          return 0;
      }
    });
    return rows;
  }, [filtered, sort, direction, counts]);

  const pageCount = Math.max(1, Math.ceil(sorted.length / pageSize));
  const visible = useMemo(
    () => sorted.slice((page - 1) * pageSize, page * pageSize),
    [sorted, page, pageSize],
  );

  useEffect(() => setPage(1), [tab, search, pageSize, visibilities, onDashboardOnly, mineOnly]);

  // Ask only for the counts on screen, and only for rows not already counted.
  const wanted = visible.map((r) => r.id).filter((id) => !(id in counts));
  const wantedKey = wanted.join(',');
  const countQuery = useQuery({
    queryKey: ['board-counts', wantedKey],
    queryFn: () => api.boardCounts(wanted),
    enabled: wanted.length > 0,
  });

  useEffect(() => {
    if (countQuery.data) setCounts((current) => ({ ...current, ...countQuery.data.counts }));
  }, [countQuery.data]);

  const duplicate = useMutation({
    mutationFn: (id: string) => api.duplicateReport(id),
    onSuccess: (created) => {
      setNotice(`Copied as "${created.name}".`);
      client.invalidateQueries({ queryKey: ['board'] });
    },
    onError: (error) =>
      setNotice(error instanceof ApiError ? error.message : String(error)),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteReport(id),
    onSuccess: () => {
      setNotice('Report deleted.');
      client.invalidateQueries({ queryKey: ['board'] });
    },
    onError: (error) =>
      setNotice(error instanceof ApiError ? error.message : String(error)),
  });

  async function open(report: BoardReport) {
    const full = await api.getReport(report.id);
    builder.loadReport(full.id, full.name, full.definition);
    router.push('/reports/builder');
  }

  const actions: RowActions = {
    onRun: open,
    onEdit: open,
    onDuplicate: (report) => duplicate.mutate(report.id),
    onDelete: (report) => {
      if (window.confirm(`Delete "${report.name}"? This cannot be undone here.`)) {
        remove.mutate(report.id);
      }
    },
    onPin: () => router.push('/dashboards/builder'),
    canEdit: listing.data?.can_edit ?? false,
    canDelete: listing.data?.can_delete ?? false,
  };

  const heading = tab.section ? `${tab.section} Reports` : 'All Reports';
  const activeFilters =
    visibilities.length + (onDashboardOnly ? 1 : 0) + (mineOnly ? 1 : 0);

  return (
    <div className="flex h-full flex-col overflow-hidden bg-canvas">
      {/* Page header */}
      <header className="border-b border-line bg-white px-4 pb-3 pt-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold text-ink">Report Board</h1>
            <nav className="mt-0.5 flex items-center gap-1.5 text-2xs text-ink-muted">
              <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth={1.8}>
                <path d="M3 10.5 12 3l9 7.5" /><path d="M5 9.5V21h14V9.5" />
              </svg>
              <Link href="/dashboards" className="hover:text-accent">Dashboards</Link>
              <span className="text-ink-faint">›</span>
              <span className="text-ink">Report Board</span>
            </nav>
          </div>
          <Button
            variant="primary"
            onClick={() => {
              builder.reset();
              router.push('/reports/builder');
            }}
          >
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2}>
              <path d="M12 5v14M5 12h14" />
            </svg>
            New Report
          </Button>
        </div>
      </header>

      <BoardTabs tabs={tabs} active={tab} counts={tabCounts} onSelect={setTab} />

      {notice && (
        <p className="flex items-center justify-between border-b border-line bg-accent-soft px-4 py-1.5 text-xs text-accent">
          {notice}
          <button type="button" onClick={() => setNotice(null)} className="text-ink-faint hover:text-ink">
            Dismiss
          </button>
        </p>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <section className="panel">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
            <div>
              <h2 className="text-md font-semibold text-ink">{heading}</h2>
              <p className="text-xs text-ink-muted">
                {tab.section
                  ? `Manage and run reports related to ${tab.section}.`
                  : 'Every saved report you can see, across all sections.'}
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <label className="relative">
                <svg
                  viewBox="0 0 24 24"
                  className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-faint"
                  fill="none" stroke="currentColor" strokeWidth={1.8}
                >
                  <circle cx="11" cy="11" r="7" /><path d="m20 20-4.3-4.3" />
                </svg>
                <input
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Search reports..."
                  className="field w-[220px] pl-8"
                />
              </label>

              <FilterMenu
                open={filtersOpen}
                onToggle={() => setFiltersOpen((value) => !value)}
                count={activeFilters}
                visibilities={visibilities}
                setVisibilities={setVisibilities}
                onDashboardOnly={onDashboardOnly}
                setOnDashboardOnly={setOnDashboardOnly}
                mineOnly={mineOnly}
                setMineOnly={setMineOnly}
              />

              <div className="flex overflow-hidden rounded border border-line">
                <ViewButton
                  active={view === 'grid'}
                  onClick={() => setView('grid')}
                  title="Card view"
                >
                  <rect x="3" y="3" width="7.5" height="7.5" rx="1.5" />
                  <rect x="13.5" y="3" width="7.5" height="7.5" rx="1.5" />
                  <rect x="3" y="13.5" width="7.5" height="7.5" rx="1.5" />
                  <rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.5" />
                </ViewButton>
                <ViewButton
                  active={view === 'list'}
                  onClick={() => setView('list')}
                  title="Table view"
                >
                  <path d="M4 6h16M4 12h16M4 18h16" />
                </ViewButton>
              </div>
            </div>
          </div>

          {listing.isLoading ? (
            <div className="space-y-1.5 p-4">
              {Array.from({ length: 6 }).map((_, index) => (
                <Skeleton key={index} className="h-9 w-full" />
              ))}
            </div>
          ) : listing.isError ? (
            <p className="px-4 py-6 text-sm text-danger">
              {(listing.error as Error).message}
            </p>
          ) : visible.length === 0 ? (
            <EmptyState
              title={
                (listing.data?.reports.length ?? 0) === 0
                  ? 'No saved reports yet'
                  : 'Nothing matches'
              }
              hint={
                (listing.data?.reports.length ?? 0) === 0
                  ? 'Build a report and save it, and it appears here.'
                  : 'Try another section, or clear the search and filters.'
              }
            />
          ) : view === 'list' ? (
            <ReportTable
              reports={visible}
              counts={counts}
              countsLoading={countQuery.isFetching}
              sort={sort}
              direction={direction}
              onSort={(key) => {
                if (key === sort) setDirection(direction === 'asc' ? 'desc' : 'asc');
                else { setSort(key); setDirection('asc'); }
              }}
              actions={actions}
            />
          ) : (
            <ReportGrid
              reports={visible}
              counts={counts}
              countsLoading={countQuery.isFetching}
              actions={actions}
            />
          )}

          {visible.length > 0 && (
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-4 py-2.5">
              <span className="text-xs text-ink-muted">
                Showing {(page - 1) * pageSize + 1} to{' '}
                {Math.min(page * pageSize, sorted.length)} of {sorted.length} report
                {sorted.length === 1 ? '' : 's'}
              </span>

              <div className="flex items-center gap-1.5">
                <PageButton disabled={page === 1} onClick={() => setPage(page - 1)}>
                  <path d="M15 18l-6-6 6-6" />
                </PageButton>
                {pageNumbers(page, pageCount).map((number, index) =>
                  number === null ? (
                    <span key={`gap${index}`} className="px-1 text-xs text-ink-faint">…</span>
                  ) : (
                    <button
                      key={number}
                      type="button"
                      onClick={() => setPage(number)}
                      className={clsx(
                        'h-7 min-w-[28px] rounded border px-1.5 text-xs tabular transition-colors',
                        number === page
                          ? 'border-accent bg-accent text-white'
                          : 'border-line bg-white text-ink-muted hover:bg-canvas hover:text-ink',
                      )}
                    >
                      {number}
                    </button>
                  ),
                )}
                <PageButton disabled={page === pageCount} onClick={() => setPage(page + 1)}>
                  <path d="M9 18l6-6-6-6" />
                </PageButton>

                <Select
                  value={String(pageSize)}
                  onChange={(value) => setPageSize(Number(value))}
                  options={PAGE_SIZES.map((size) => ({
                    value: String(size),
                    label: `${size} / page`,
                  }))}
                  className="ml-1 w-[104px] py-1 text-xs"
                />
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
function FilterMenu({
  open,
  onToggle,
  count,
  visibilities,
  setVisibilities,
  onDashboardOnly,
  setOnDashboardOnly,
  mineOnly,
  setMineOnly,
}: {
  open: boolean;
  onToggle: () => void;
  count: number;
  visibilities: string[];
  setVisibilities: (value: string[]) => void;
  onDashboardOnly: boolean;
  setOnDashboardOnly: (value: boolean) => void;
  mineOnly: boolean;
  setMineOnly: (value: boolean) => void;
}) {
  const holder = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!holder.current?.contains(event.target as Node)) onToggle();
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open, onToggle]);

  const toggle = (value: string) =>
    setVisibilities(
      visibilities.includes(value)
        ? visibilities.filter((item) => item !== value)
        : [...visibilities, value],
    );

  return (
    <div ref={holder} className="relative">
      <Button onClick={onToggle}>
        <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={1.8}>
          <path d="M3 5h18l-7 8v6l-4 2v-8z" />
        </svg>
        Filter
        {count > 0 && <Badge tone="accent">{count}</Badge>}
      </Button>

      {open && (
        <div className="menu w-[220px] p-2.5">
          <p className="label">Visibility</p>
          {(['organization', 'team', 'private'] as const).map((value) => (
            <Checkbox
              key={value}
              checked={visibilities.includes(value)}
              onChange={() => toggle(value)}
              label={
                <span className="text-xs">
                  {value === 'organization' ? 'Public' : value === 'team' ? 'Team' : 'Private'}
                </span>
              }
            />
          ))}
          <div className="menu-sep my-2" />
          <Checkbox
            checked={onDashboardOnly}
            onChange={setOnDashboardOnly}
            label={<span className="text-xs">On a dashboard</span>}
          />
          <Checkbox
            checked={mineOnly}
            onChange={setMineOnly}
            label={<span className="text-xs">Created by me</span>}
          />
          {count > 0 && (
            <button
              type="button"
              onClick={() => {
                setVisibilities([]);
                setOnDashboardOnly(false);
                setMineOnly(false);
              }}
              className="mt-2 w-full rounded border border-line py-1 text-2xs font-medium text-ink-muted hover:text-ink"
            >
              Clear filters
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function ViewButton({
  active,
  onClick,
  title,
  children,
}: {
  active: boolean;
  onClick: () => void;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      aria-pressed={active}
      onClick={onClick}
      className={clsx(
        'flex h-[34px] w-9 items-center justify-center border-r border-line transition-colors last:border-r-0',
        active ? 'bg-accent text-white' : 'bg-white text-ink-muted hover:bg-canvas hover:text-ink',
      )}
    >
      <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={1.8}
           strokeLinecap="round" strokeLinejoin="round">
        {children}
      </svg>
    </button>
  );
}

function PageButton({
  disabled,
  onClick,
  children,
}: {
  disabled: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="flex h-7 w-7 items-center justify-center rounded border border-line bg-white
                 text-ink-muted transition-colors hover:bg-canvas hover:text-ink
                 disabled:cursor-not-allowed disabled:opacity-40"
    >
      <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2}>
        {children}
      </svg>
    </button>
  );
}

/** Page numbers with an ellipsis, so 40 pages do not become 40 buttons. */
function pageNumbers(current: number, total: number): (number | null)[] {
  if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1);
  const numbers: (number | null)[] = [1];
  const from = Math.max(2, current - 1);
  const to = Math.min(total - 1, current + 1);
  if (from > 2) numbers.push(null);
  for (let index = from; index <= to; index += 1) numbers.push(index);
  if (to < total - 1) numbers.push(null);
  numbers.push(total);
  return numbers;
}
