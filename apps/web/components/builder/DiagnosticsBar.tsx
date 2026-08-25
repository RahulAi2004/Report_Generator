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
export function DiagnosticsBar({
  diagnostics,
  onNavigate,
}: {
  diagnostics: Diagnostic[];
  onNavigate: (section: string) => void;
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
              <span className="text-ink">{item.message}</span>
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
