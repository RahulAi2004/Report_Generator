'use client';

import { useMutation } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { Badge, Button, Skeleton } from '@/components/ui/primitives';
import { formatValue } from '@/lib/format';
import { api, ApiError, type PreviewResult, type SavedReportSummary } from '@/lib/api';
import { useBuilder } from '@/store/builder';

/**
 * A pinned report on the dashboard.
 *
 * Pinning is only worth anything if the card shows the answer, so it runs the
 * report rather than linking to it. A report whose author turned auto refresh
 * off waits for a click instead -- an expensive report should not run itself
 * every time someone opens the dashboard.
 *
 * A single aggregate column is shown as one large figure; anything else as the
 * first few rows.
 */
export function PinnedReport({ report }: { report: SavedReportSummary }) {
  const router = useRouter();
  const builder = useBuilder();
  const [result, setResult] = useState<PreviewResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = useMutation({
    mutationFn: async () => {
      if (!report.definition) throw new ApiError('This report has no definition.', 400);
      return api.preview(report.definition, 1, 5);
    },
    onSuccess: (data) => {
      setResult(data);
      setError(data.ok ? null : 'This report is no longer valid.');
    },
    onError: (caught) =>
      setError(caught instanceof ApiError ? caught.message : 'Could not run this report.'),
  });

  useEffect(() => {
    if (report.auto_refresh && report.definition) run.mutate();
    // Intentionally once per card: the dashboard should not re-run reports as
    // the user interacts with the page.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function open() {
    const full = await api.getReport(report.id);
    builder.loadReport(full.id, full.name, full.definition);
    router.push('/reports/builder');
  }

  const columns = result?.columns ?? [];
  const rows = result?.rows ?? [];
  // One aggregate and one row reads as a headline figure, not a table.
  const isSingleFigure =
    columns.length === 1 && columns[0].aggregation !== 'none' && rows.length === 1;

  return (
    <section className="panel flex flex-col">
      <div className="panel-header">
        <button
          type="button"
          onClick={open}
          className="min-w-0 text-left hover:text-accent"
          title="Open in the report builder"
        >
          <h3 className="truncate text-sm font-semibold">{report.name}</h3>
          {report.section && (
            <p className="truncate text-2xs text-ink-faint">
              {report.module} · {report.section}
            </p>
          )}
        </button>

        <div className="flex shrink-0 items-center gap-1.5">
          {result?.fanout_corrected && (
            <Badge tone="good" className="hidden sm:inline-flex">
              Totals corrected
            </Badge>
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => run.mutate()}
            disabled={run.isPending}
            title="Run this report again"
          >
            <svg
              viewBox="0 0 24 24"
              className={`h-3.5 w-3.5 ${run.isPending ? 'animate-spin' : ''}`}
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path d="M21 12a9 9 0 1 1-2.6-6.4" />
              <path d="M21 3v6h-6" />
            </svg>
          </Button>
        </div>
      </div>

      <div className="flex-1 p-3">
        {run.isPending && <Skeleton className="h-20 w-full" />}

        {!run.isPending && error && (
          <p className="rounded border border-danger-border bg-danger-soft px-2.5 py-2 text-xs text-danger">
            {error}
          </p>
        )}

        {!run.isPending && !error && !result && (
          <div className="flex flex-col items-start gap-2">
            <p className="text-xs text-ink-muted">
              Auto refresh is off for this report, so it runs on request.
            </p>
            <Button size="sm" onClick={() => run.mutate()}>
              Run report
            </Button>
          </div>
        )}

        {!run.isPending && !error && result && rows.length === 0 && (
          <p className="py-3 text-center text-xs text-ink-faint">No rows matched.</p>
        )}

        {!run.isPending && !error && isSingleFigure && (
          <div className="py-2">
            <span className="block text-2xl font-semibold tabular">
              {formatValue(rows[0][columns[0].key], columns[0].format, columns[0].data_type)}
            </span>
            <span className="text-xs text-ink-muted">{columns[0].label}</span>
          </div>
        )}

        {!run.isPending && !error && result && rows.length > 0 && !isSingleFigure && (
          <div className="-mx-3 overflow-x-auto">
            <table className="striped w-full border-collapse">
              <thead>
                <tr className="border-b border-line text-left text-2xs uppercase text-ink-faint">
                  {columns.slice(0, 4).map((column) => (
                    <th
                      key={column.key}
                      className={`whitespace-nowrap px-3 py-1.5 font-medium ${
                        column.align === 'right' ? 'text-right' : ''
                      }`}
                    >
                      {column.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, index) => (
                  <tr key={index} className="border-b border-line/60 last:border-0">
                    {columns.slice(0, 4).map((column) => (
                      <td
                        key={column.key}
                        className={`whitespace-nowrap px-3 py-1 text-sm ${
                          column.align === 'right' ? 'text-right tabular' : ''
                        }`}
                      >
                        {formatValue(row[column.key], column.format, column.data_type)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {result && rows.length > 0 && (
        <div className="border-t border-line px-3 py-1.5 text-2xs text-ink-faint">
          {isSingleFigure
            ? `${result.duration_ms} ms`
            : `First ${rows.length} row${rows.length === 1 ? '' : 's'} · ${result.duration_ms} ms`}
          {columns.length > 4 && ` · ${columns.length - 4} more columns in the full report`}
        </div>
      )}
    </section>
  );
}
