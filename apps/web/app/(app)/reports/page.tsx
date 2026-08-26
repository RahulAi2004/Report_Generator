'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useMemo, useState } from 'react';
import { Menu, MenuItem, MenuSeparator } from '@/components/ui/Menu';
import { Badge, Button, EmptyState, Skeleton } from '@/components/ui/primitives';
import { api, type SavedReportSummary } from '@/lib/api';
import { useBuilder } from '@/store/builder';

/**
 * Saved reports.
 *
 * Rename, duplicate and delete live here because without them the list only
 * ever grows: a report saved with a typo, or an experiment, stays forever.
 */
export default function ReportsPage() {
  const router = useRouter();
  const builder = useBuilder();
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ['reports'], queryFn: api.listReports });

  const [search, setSearch] = useState('');
  const [renaming, setRenaming] = useState<SavedReportSummary | null>(null);
  const [deleting, setDeleting] = useState<SavedReportSummary | null>(null);
  const [draftName, setDraftName] = useState('');

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['reports'] });

  async function open(id: string) {
    const report = await api.getReport(id);
    builder.loadReport(report.id, report.name, report.definition);
    router.push('/reports/builder');
  }

  const rename = useMutation({
    // The list carries summaries only, so the definition is fetched to save it back.
    mutationFn: async ({ report, name }: { report: SavedReportSummary; name: string }) => {
      const full = await api.getReport(report.id);
      return api.updateReport(report.id, name.trim(), full.definition);
    },
    onSuccess: () => {
      setRenaming(null);
      refresh();
    },
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteReport(id),
    onSuccess: () => {
      setDeleting(null);
      refresh();
    },
  });

  const duplicate = useMutation({
    mutationFn: async (id: string) => {
      const full = await api.getReport(id);
      return api.createReport(`${full.name} (copy)`, full.definition);
    },
    onSuccess: refresh,
  });

  const reports = useMemo(() => {
    const all = data?.reports ?? [];
    if (!search.trim()) return all;
    const needle = search.toLowerCase();
    return all.filter(
      (report) =>
        report.name.toLowerCase().includes(needle) ||
        (report.description ?? '').toLowerCase().includes(needle),
    );
  }, [data, search]);

  return (
    <>
      <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-line bg-white px-5">
        <div className="min-w-0">
          <h1 className="text-md font-semibold">Reports</h1>
          <p className="truncate text-xs text-ink-muted">
            Saved as configuration, not SQL, so they survive schema changes.
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {(data?.reports.length ?? 0) > 4 && (
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search reports…"
              className="field w-48"
            />
          )}
          <Button
            variant="primary"
            size="sm"
            onClick={() => {
              builder.reset();
              router.push('/reports/builder');
            }}
          >
            New Report
          </Button>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto p-5">
        {isLoading && (
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: 4 }).map((_, index) => (
              <Skeleton key={index} className="h-28 w-full" />
            ))}
          </div>
        )}

        {!isLoading && reports.length === 0 && (
          <div className="panel">
            <EmptyState
              title={search ? 'No reports match' : 'No saved reports yet'}
              hint={
                search
                  ? 'Try a different search term.'
                  : 'Build one in the report builder and press Save. It will appear here for everyone with access.'
              }
            />
          </div>
        )}

        <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
          {reports.map((report) => (
            <div
              key={report.id}
              onClick={() => open(report.id)}
              className="panel group cursor-pointer p-4 text-left transition-colors
                         hover:border-accent-border hover:bg-accent-soft/40"
            >
              <div className="mb-1 flex items-start justify-between gap-2">
                <h2 className="truncate text-sm font-semibold">{report.name}</h2>

                <div className="flex shrink-0 items-center gap-1">
                  {report.is_template && <Badge tone="accent">Template</Badge>}

                  <span onClick={(event) => event.stopPropagation()}>
                    <Menu
                      trigger={({ toggle }) => (
                        <button
                          type="button"
                          onClick={toggle}
                          title="More actions"
                          className="hidden h-6 w-6 items-center justify-center rounded
                                     text-ink-faint hover:bg-white hover:text-ink
                                     group-hover:inline-flex"
                        >
                          <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="currentColor">
                            <circle cx="12" cy="5" r="1.6" />
                            <circle cx="12" cy="12" r="1.6" />
                            <circle cx="12" cy="19" r="1.6" />
                          </svg>
                        </button>
                      )}
                    >
                      {({ close }) => (
                        <>
                          <MenuItem
                            onClick={() => {
                              close();
                              open(report.id);
                            }}
                          >
                            Open
                          </MenuItem>
                          <MenuItem
                            onClick={() => {
                              close();
                              setRenaming(report);
                              setDraftName(report.name);
                            }}
                          >
                            Rename
                          </MenuItem>
                          <MenuItem
                            onClick={() => {
                              close();
                              duplicate.mutate(report.id);
                            }}
                          >
                            Duplicate
                          </MenuItem>
                          <MenuSeparator />
                          <MenuItem
                            onClick={() => {
                              close();
                              setDeleting(report);
                            }}
                          >
                            Delete
                          </MenuItem>
                        </>
                      )}
                    </Menu>
                  </span>
                </div>
              </div>

              {report.description && (
                <p className="mb-2 line-clamp-2 text-xs text-ink-muted">{report.description}</p>
              )}

              <div className="flex flex-wrap gap-1.5">
                <Badge>{report.summary.data_sources} tables</Badge>
                <Badge>{report.summary.fields_selected} fields</Badge>
                {report.summary.filters > 0 && <Badge>{report.summary.filters} filters</Badge>}
              </div>

              <p className="mt-2 text-2xs text-ink-faint">
                Updated {new Date(report.updated_at).toLocaleDateString()} · run{' '}
                {report.run_count} time{report.run_count === 1 ? '' : 's'}
              </p>
            </div>
          ))}
        </div>
      </div>

      {renaming && (
        <Prompt
          title="Rename report"
          confirmLabel={rename.isPending ? 'Saving…' : 'Rename'}
          disabled={!draftName.trim() || rename.isPending}
          onCancel={() => setRenaming(null)}
          onConfirm={() => rename.mutate({ report: renaming, name: draftName })}
        >
          <input
            value={draftName}
            autoFocus
            onChange={(event) => setDraftName(event.target.value)}
            className="field"
          />
        </Prompt>
      )}

      {deleting && (
        <Prompt
          title={`Delete ${deleting.name}?`}
          confirmLabel={remove.isPending ? 'Deleting…' : 'Delete'}
          destructive
          disabled={remove.isPending}
          onCancel={() => setDeleting(null)}
          onConfirm={() => remove.mutate(deleting.id)}
        >
          <p className="text-sm text-ink-muted">
            The report is archived rather than destroyed, so it can be recovered from the
            database if this was a mistake. None of your data is affected.
          </p>
        </Prompt>
      )}
    </>
  );
}

/** Small modal shared by rename and delete. */
function Prompt({
  title,
  children,
  confirmLabel,
  onConfirm,
  onCancel,
  disabled,
  destructive,
}: {
  title: string;
  children: React.ReactNode;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  disabled?: boolean;
  destructive?: boolean;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 p-4"
      onClick={onCancel}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="w-full max-w-sm rounded-lg border border-line bg-white p-5 shadow-pop"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 className="mb-3 text-sm font-semibold">{title}</h2>
        {children}
        <div className="mt-4 flex justify-end gap-2">
          <Button size="sm" onClick={onCancel}>
            Cancel
          </Button>
          <Button
            size="sm"
            variant={destructive ? 'default' : 'primary'}
            disabled={disabled}
            onClick={onConfirm}
            className={destructive ? 'border-danger bg-danger text-white hover:bg-danger/90' : ''}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
