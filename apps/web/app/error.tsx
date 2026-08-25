'use client';

import { useEffect } from 'react';
import { Button } from '@/components/ui/primitives';

/**
 * Route-level error boundary.
 *
 * Without this a single render failure blanks the whole application and the
 * user's unsaved report configuration goes with it. Here they keep the page,
 * see plain language rather than a stack trace, and can retry.
 */
export default function ErrorBoundary({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Technical detail belongs in the console and the server log, not on screen.
    console.error('Unhandled UI error:', error);
  }, [error]);

  return (
    <div className="flex min-h-full items-center justify-center bg-canvas p-6">
      <div className="panel max-w-md p-6">
        <div className="mb-3 flex items-center gap-2">
          <svg
            viewBox="0 0 24 24"
            className="h-5 w-5 text-danger"
            fill="none"
            stroke="currentColor"
            strokeWidth={1.8}
          >
            <circle cx="12" cy="12" r="9" />
            <path d="M12 8v5M12 16h.01" />
          </svg>
          <h1 className="text-md font-semibold">Something went wrong</h1>
        </div>

        <p className="text-sm text-ink-muted">
          This screen could not be displayed. Your saved reports are unaffected.
        </p>
        {error.digest && (
          <p className="mt-2 font-mono text-2xs text-ink-faint">Reference: {error.digest}</p>
        )}

        <div className="mt-4 flex gap-2">
          <Button variant="primary" size="sm" onClick={reset}>
            Try again
          </Button>
          <Button size="sm" onClick={() => (window.location.href = '/reports/builder')}>
            Back to reports
          </Button>
        </div>
      </div>
    </div>
  );
}
