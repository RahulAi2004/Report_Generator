'use client';

import clsx from 'clsx';
import { Skeleton } from '@/components/ui/primitives';
import type { MetricResult, Tone } from '@/lib/dashboard-types';

/**
 * The row of numbers across the top of a dashboard.
 *
 * A number this prominent is read without its context, so the caption under it
 * is not decoration -- it is the only thing saying what period the figure
 * covers and whether the dashboard's filters reached it. Both come from the
 * server alongside the value, never from what the builder thinks it asked for.
 */

const TONES: Record<Tone, { soft: string; ink: string }> = {
  blue: { soft: 'bg-accent-soft', ink: 'text-accent' },
  green: { soft: 'bg-good-soft', ink: 'text-good' },
  amber: { soft: 'bg-warn-soft', ink: 'text-warn' },
  violet: { soft: 'bg-violet-100', ink: 'text-violet-600' },
  rose: { soft: 'bg-rose-100', ink: 'text-rose-600' },
  slate: { soft: 'bg-canvas', ink: 'text-ink-muted' },
};

const stroke = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.7,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
};

export const METRIC_ICONS: Record<string, React.ReactNode> = {
  users: <><circle cx="9" cy="8" r="3.2" /><path d="M3 20c0-3.3 2.7-5 6-5s6 1.7 6 5" /><path d="M16 6.5a3 3 0 0 1 0 6M17.5 20c0-2.4-.9-4-2.2-5" /></>,
  'user-plus': <><circle cx="9" cy="8" r="3.2" /><path d="M3 20c0-3.3 2.7-5 6-5 1 0 2 .2 2.8.5" /><path d="M17 13v6M14 16h6" /></>,
  check: <><circle cx="12" cy="12" r="9" /><path d="M8.5 12.5l2.5 2.5 4.5-5" /></>,
  repeat: <><path d="M4 9h12l-3-3M20 15H8l3 3" /></>,
  money: <><circle cx="12" cy="12" r="9" /><path d="M12 7v10M14.5 9.5c0-1-1.1-1.6-2.5-1.6s-2.5.6-2.5 1.6 1 1.4 2.5 1.8 2.7.9 2.7 2-1.2 1.8-2.7 1.8-2.7-.7-2.7-1.7" /></>,
  chart: <><path d="M4 20V10M10 20V4M16 20v-7M22 20H2" /></>,
  cart: <><circle cx="9" cy="19" r="1.4" /><circle cx="17" cy="19" r="1.4" /><path d="M3 4h2l2.2 10.4a1.5 1.5 0 0 0 1.5 1.2h7.9a1.5 1.5 0 0 0 1.5-1.2L20 8H6" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5.5l3.5 2" /></>,
  hash: <><path d="M9 3L7 21M17 3l-2 18M4 8.5h16M3 15.5h16" /></>,
  percent: <><circle cx="7.5" cy="7.5" r="2.5" /><circle cx="16.5" cy="16.5" r="2.5" /><path d="M19 5L5 19" /></>,
};

export function MetricStrip({
  metrics,
  loading,
  placeholderCount,
  onSelect,
  selectedId,
}: {
  metrics: MetricResult[];
  loading?: boolean;
  placeholderCount?: number;
  onSelect?: (id: string) => void;
  selectedId?: string | null;
}) {
  if (loading && metrics.length === 0) {
    return (
      <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {Array.from({ length: placeholderCount ?? 4 }).map((_, index) => (
          <Skeleton key={index} className="h-[92px] w-full" />
        ))}
      </div>
    );
  }

  if (metrics.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-line-strong bg-white px-4 py-8 text-center">
        <p className="text-sm font-medium text-ink">No metric cards yet</p>
        <p className="mt-1 text-xs text-ink-muted">
          Add one on the left and its number appears here as you build it.
        </p>
      </div>
    );
  }

  return (
    <div
      className={clsx(
        'grid gap-2.5 transition-opacity sm:grid-cols-2 lg:grid-cols-3',
        metrics.length > 4 ? 'xl:grid-cols-6' : 'xl:grid-cols-4',
        loading && 'opacity-60',
      )}
    >
      {metrics.map((metric) => (
        <MetricCardView
          key={metric.id}
          metric={metric}
          selected={selectedId === metric.id}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}

function MetricCardView({
  metric,
  selected,
  onSelect,
}: {
  metric: MetricResult;
  selected?: boolean;
  onSelect?: (id: string) => void;
}) {
  const tone = TONES[metric.tone ?? 'blue'] ?? TONES.blue;
  const failed = metric.errors.length > 0;
  const missed = metric.filters?.not_applicable ?? [];

  return (
    <button
      type="button"
      onClick={() => onSelect?.(metric.id)}
      className={clsx(
        'group flex flex-col rounded-lg border bg-white p-3 text-left shadow-panel transition-all',
        selected ? 'border-accent ring-2 ring-accent/20' : 'border-line hover:border-line-strong',
      )}
    >
      <div className="flex items-start gap-2">
        <span
          className={clsx(
            'flex h-8 w-8 shrink-0 items-center justify-center rounded-full',
            tone.soft, tone.ink,
          )}
        >
          <svg viewBox="0 0 24 24" className="h-[17px] w-[17px]" {...stroke}>
            {METRIC_ICONS[metric.icon ?? 'hash'] ?? METRIC_ICONS.hash}
          </svg>
        </span>
        <span className="mt-0.5 min-w-0 flex-1 truncate text-xs font-medium text-ink-muted"
              title={metric.title}>
          {metric.title}
        </span>
      </div>

      <div className="mt-1.5">
        {failed ? (
          <p className="text-xs leading-snug text-danger" title={metric.errors.join(' ')}>
            {metric.errors[0]}
          </p>
        ) : (
          <span className="tabular text-2xl font-semibold leading-none text-ink">
            {renderValue(metric)}
          </span>
        )}
      </div>

      {!failed && (
        <div className="mt-1.5 flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
          {metric.delta ? <Delta metric={metric} /> : null}
          {metric.share != null && (
            <span className="text-2xs font-medium text-ink">{metric.share}%</span>
          )}
          <span className="truncate text-2xs text-ink-faint" title={metric.caption}>
            {metric.caption}
          </span>
        </div>
      )}

      {missed.length > 0 && (
        <p
          className="mt-1.5 rounded border border-warn-border bg-warn-soft px-1.5 py-0.5 text-[10px] leading-tight text-warn"
          title={`This card does not read ${missed.join(', ')}, so those filters could not narrow it.`}
        >
          Not filtered by {missed.join(', ')}
        </p>
      )}

      {metric.filters?.time_range_applied === false && (
        <p className="mt-1 text-[10px] leading-tight text-warn">
          No date field — showing all time
        </p>
      )}
    </button>
  );
}

function Delta({ metric }: { metric: MetricResult }) {
  const delta = metric.delta!;
  const better =
    delta.direction === 'flat'
      ? null
      : (delta.direction === 'up') === (metric.higher_is_better ?? true);

  return (
    <span
      className={clsx(
        'inline-flex items-center gap-0.5 text-2xs font-semibold',
        better === null ? 'text-ink-faint' : better ? 'text-good' : 'text-danger',
      )}
      title={`Previous period: ${formatPlain(delta.previous, metric)}`}
    >
      {delta.direction !== 'flat' && (
        <svg viewBox="0 0 24 24" className="h-3 w-3" {...stroke}>
          {delta.direction === 'up' ? <path d="M12 19V5M6 11l6-6 6 6" /> : <path d="M12 5v14M6 13l6 6 6-6" />}
        </svg>
      )}
      {delta.percent == null
        ? // No percentage against a base of zero: it is undefined, not infinite.
          `${delta.difference > 0 ? '+' : ''}${formatPlain(delta.difference, metric)}`
        : `${Math.abs(delta.percent)}%`}
    </span>
  );
}

// ---------------------------------------------------------------------------
function renderValue(metric: MetricResult): string {
  if (metric.value == null) return '—';
  return formatPlain(metric.value, metric);
}

function formatPlain(value: number | string, metric: MetricResult): string {
  const number = typeof value === 'number' ? value : Number(value);
  if (Number.isNaN(number)) return String(value);

  const decimals = metric.decimals ?? 0;
  const body = number.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });

  switch (metric.format) {
    case 'currency':
      return `${currencySymbol(metric.currency ?? 'EUR')}${body}`;
    case 'percent':
      return `${body}%`;
    case 'duration':
      return `${body}h`;
    default:
      return body;
  }
}

function currencySymbol(code: string): string {
  const symbols: Record<string, string> = {
    EUR: '€', USD: '$', GBP: '£', INR: '₹', PKR: '₨', AED: 'AED ',
  };
  return symbols[code] ?? `${code} `;
}
