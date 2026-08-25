'use client';

import clsx from 'clsx';
import { Badge, Button, EmptyState, Select, Skeleton } from '@/components/ui/primitives';
import { formatValue } from '@/lib/format';
import type { PreviewResult } from '@/lib/types';

/**
 * Preview table.
 *
 * Server-side paginated: the browser never receives more than one page, so a
 * report over millions of rows stays responsive (spec 14, 41).
 */
export function PreviewTable({
  result,
  loading,
  error,
  page,
  pageSize,
  onPageChange,
  onPageSizeChange,
  onRefresh,
  fullscreen,
  onToggleFullscreen,
}: {
  result: PreviewResult | null;
  loading: boolean;
  error: string | null;
  page: number;
  pageSize: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
  onRefresh: () => void;
  fullscreen: boolean;
  onToggleFullscreen: () => void;
}) {
  const columns = result?.columns.filter(() => true) ?? [];

  return (
    <section
      className={clsx(
        'panel flex flex-col',
        fullscreen && 'fixed inset-3 z-50 shadow-pop',
      )}
    >
      <div className="panel-header">
        <div className="flex items-center gap-2">
          <h2 className="panel-title">Preview</h2>
          {result && (
            <span className="text-2xs text-ink-faint">
              Showing {result.rows.length} row{result.rows.length === 1 ? '' : 's'}
              {result.duration_ms > 0 && ` · ${result.duration_ms} ms`}
            </span>
          )}
          {result?.fanout_corrected && (
            <Badge tone="good" className="gap-1">
              <svg viewBox="0 0 24 24" className="h-2.5 w-2.5" fill="none" stroke="currentColor" strokeWidth={3}>
                <path d="m5 13 4 4L19 7" />
              </svg>
              Totals corrected
            </Badge>
          )}
        </div>

        <div className="flex items-center gap-1.5">
          <Button variant="ghost" size="sm" onClick={onRefresh} disabled={loading}>
            <svg
              viewBox="0 0 24 24"
              className={clsx('h-3.5 w-3.5', loading && 'animate-spin')}
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path d="M21 12a9 9 0 1 1-2.6-6.4" />
              <path d="M21 3v6h-6" />
            </svg>
            Refresh Preview
          </Button>

          <Select
            value={String(pageSize)}
            onChange={(value) => onPageSizeChange(Number(value))}
            options={[25, 50, 100, 250].map((size) => ({
              value: String(size),
              label: `${size} Rows`,
            }))}
            className="w-[92px] py-1 text-xs"
          />

          <Button variant="ghost" size="sm" onClick={onToggleFullscreen}>
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2}>
              {fullscreen ? (
                <path d="M9 3v6H3M15 3v6h6M9 21v-6H3M15 21v-6h6" />
              ) : (
                <path d="M3 9V3h6M21 9V3h-6M3 15v6h6M21 15v6h-6" />
              )}
            </svg>
          </Button>
        </div>
      </div>

      <div className="min-h-[180px] flex-1 overflow-auto">
        {loading && (
          <div className="space-y-2 p-4">
            {Array.from({ length: 6 }).map((_, index) => (
              <Skeleton key={index} className="h-6 w-full" />
            ))}
          </div>
        )}

        {!loading && error && (
          <div className="m-4 rounded border border-danger-border bg-danger-soft px-3 py-2.5">
            <p className="text-sm font-medium text-danger">Unable to run this report</p>
            <p className="mt-0.5 text-xs text-ink-muted">{error}</p>
          </div>
        )}

        {!loading && !error && (!result || result.rows.length === 0) && (
          <EmptyState
            title={result ? 'No rows match' : 'Nothing to preview yet'}
            hint={
              result
                ? 'Loosen or remove a filter, then refresh the preview.'
                : 'Add at least one column, then press Refresh Preview.'
            }
          />
        )}

        {!loading && !error && result && result.rows.length > 0 && (
          <table className="w-full border-collapse">
            <thead className="sticky top-0 z-10 bg-canvas">
              <tr className="border-b border-line">
                {columns.map((column) => (
                  <th
                    key={column.key}
                    className={clsx(
                      'whitespace-nowrap px-3 py-2 text-xs font-semibold text-ink-muted',
                      column.align === 'right' ? 'text-right' : 'text-left',
                    )}
                    title={`${column.table}.${column.field}${
                      column.aggregation !== 'none'
                        ? ` · ${column.aggregation.toUpperCase()}`
                        : ''
                    }`}
                  >
                    {column.label}
                    {column.is_masked && <span className="ml-1 text-warn">•</span>}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.rows.map((row, index) => (
                <tr key={index} className="border-b border-line/60 row-hover last:border-0">
                  {columns.map((column) => (
                    <td
                      key={column.key}
                      className={clsx(
                        'whitespace-nowrap px-3 py-1.5 text-sm',
                        column.align === 'right' ? 'text-right tabular' : 'text-left',
                        row[column.key] == null && 'text-ink-faint',
                      )}
                    >
                      {formatValue(row[column.key], column.format, column.data_type)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="flex items-center justify-between border-t border-line px-4 py-2">
        <span className="text-xs text-ink-muted">
          {result
            ? `Page ${result.page}${result.has_more ? '' : ' · end of results'}`
            : '—'}
        </span>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            disabled={page <= 1 || loading}
            onClick={() => onPageChange(page - 1)}
          >
            Previous
          </Button>
          <span className="rounded border border-accent bg-accent px-2 py-1 text-xs text-white tabular">
            {page}
          </span>
          <Button
            variant="ghost"
            size="sm"
            disabled={!result?.has_more || loading}
            onClick={() => onPageChange(page + 1)}
          >
            Next
          </Button>
        </div>
      </div>
    </section>
  );
}
