'use client';

import clsx from 'clsx';
import { useState } from 'react';
import type { Diagnostic } from '@/lib/types';

/**
 * Diagnostics bar.
 *
 * The reference layout has nowhere to say "these totals are inflated", which is
 * precisely the failure a self-service report builder produces most often. This
 * strip is that missing surface: it stays visible while the report is built,
 * and a warning about wrong numbers is given the same weight as an error.
 */
interface JoinEdge {
  from_table: string;
  from_column: string;
  to_table: string;
  to_column: string;
  join_type: 'inner' | 'left' | 'right' | 'full';
  relationship_id: string;
}

export function DiagnosticsBar({
  diagnostics,
  onNavigate,
  onChooseJoinPath,
}: {
  diagnostics: Diagnostic[];
  onNavigate: (section: string) => void;
  /** Applies one of the candidate paths the planner refused to choose between. */
  onChooseJoinPath?: (edges: JoinEdge[]) => void;
}) {
  const [expanded, setExpanded] = useState(true);

  if (diagnostics.length === 0) return null;

  const errors = diagnostics.filter((item) => item.severity === 'error');
  const warnings = diagnostics.filter((item) => item.severity === 'warning');
  const infos = diagnostics.filter((item) => item.severity === 'info');

  const tone = errors.length > 0 ? 'error' : warnings.length > 0 ? 'warning' : 'info';
  const shell = {
    error: 'border-danger-border bg-danger-soft',
    warning: 'border-warn-border bg-warn-soft',
    info: 'border-accent-border bg-accent-soft',
  }[tone];

  return (
    <div className={clsx('border-b px-4 py-2', shell)}>
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="flex w-full items-center gap-2 text-left"
      >
        <Icon severity={tone} />
        <span className="text-xs font-semibold text-ink">
          {errors.length > 0
            ? `${errors.length} issue${errors.length === 1 ? '' : 's'} to resolve`
            : warnings.length > 0
              ? `${warnings.length} warning${warnings.length === 1 ? '' : 's'}`
              : `${infos.length} note${infos.length === 1 ? '' : 's'}`}
        </span>
        {errors.length === 0 && warnings.length > 0 && (
          <span className="text-2xs text-ink-muted">
            The report will still run, but check these first.
          </span>
        )}
        <svg
          viewBox="0 0 24 24"
          className={clsx('ml-auto h-3.5 w-3.5 text-ink-muted transition-transform',
            !expanded && '-rotate-90')}
          fill="none"
          stroke="currentColor"
          strokeWidth={2.5}
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>

      {expanded && (
        <ul className="mt-1.5 space-y-1">
          {[...errors, ...warnings, ...infos].map((item, index) => (
            <li key={index} className="flex items-start gap-2 text-xs">
              <span
                className={clsx(
                  'mt-1 h-1.5 w-1.5 shrink-0 rounded-full',
                  item.severity === 'error'
                    ? 'bg-danger'
                    : item.severity === 'warning'
                      ? 'bg-warn'
                      : 'bg-accent',
                )}
              />
              <span className="min-w-0 flex-1">
                <span className="text-ink">{item.message}</span>

                {/* The planner will not guess between equal paths, so the
                    choice is offered here rather than left as advice. */}
                {item.fix?.action === 'choose_join_path' && onChooseJoinPath && (
                  <span className="mt-1.5 flex flex-wrap gap-2">
                    {distinctPaths(item.fix.options as JoinEdge[][]).map((option, choice) => (
                      <button
                        key={choice}
                        type="button"
                        onClick={() => onChooseJoinPath(option)}
                        className="group rounded-lg border border-accent-border bg-white px-2.5 py-1.5
                                   text-left transition-colors hover:border-accent hover:bg-accent-soft"
                        title="Use this path"
                      >
                        <span className="block text-2xs font-semibold text-accent">
                          {describePath(option)}
                        </span>
                        {/* Two routes can visit the same tables and still mean
                            different things, so the join keys decide it. */}
                        <span className="mt-0.5 block font-mono text-[10px] text-ink-muted">
                          {option
                            .map((e) => `${e.from_table}.${e.from_column} = ${e.to_table}.${e.to_column}`)
                            .join('  ·  ')}
                        </span>
                      </button>
                    ))}
                  </span>
                )}
              </span>

              {item.section && item.section !== 'general' && (
                <button
                  type="button"
                  onClick={() => onNavigate(item.section)}
                  className="shrink-0 whitespace-nowrap font-medium text-accent hover:underline"
                >
                  Go to {item.section.replace('_', ' ')}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * Drop options that are the same join in the same order.
 *
 * Distinct routes may visit identical tables -- two entities can reference each
 * other both ways -- and those must be kept, since they answer different
 * questions. Only genuinely identical ones are removed.
 */
function distinctPaths(options: JoinEdge[][]): JoinEdge[][] {
  const seen = new Set<string>();
  return options.filter((option) => {
    const signature = option
      .map((e) => `${e.from_table}.${e.from_column}>${e.to_table}.${e.to_column}`)
      .join('|');
    if (seen.has(signature)) return false;
    seen.add(signature);
    return true;
  });
}

/** `customers → orders → invoices`, so each option is readable at a glance. */
function describePath(edges: JoinEdge[]): string {
  if (edges.length === 0) return 'direct';
  return [edges[0].from_table, ...edges.map((edge) => edge.to_table)].join(' → ');
}

function Icon({ severity }: { severity: 'error' | 'warning' | 'info' }) {
  const tone = {
    error: 'text-danger',
    warning: 'text-warn',
    info: 'text-accent',
  }[severity];

  return (
    <svg
      viewBox="0 0 24 24"
      className={clsx('h-4 w-4 shrink-0', tone)}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
    >
      {severity === 'info' ? (
        <>
          <circle cx="12" cy="12" r="9" />
          <path d="M12 16v-5M12 8h.01" />
        </>
      ) : (
        <>
          <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
          <path d="M12 9v4M12 17h.01" />
        </>
      )}
    </svg>
  );
}
