'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/primitives';
import { api, ApiError } from '@/lib/api';

/**
 * Sign-in.
 *
 * Deliberately plain: one email, one password, nothing else. No accounts are
 * listed and no password is pre-filled -- a sign-in page that advertises
 * credentials is a sign-in page that does not protect anything.
 *
 * The connection strip below the form answers the first question an
 * administrator has when a report looks wrong: is the platform actually talking
 * to the database? Better to state it here than to let someone discover it
 * halfway through building a report.
 */

interface Health {
  status: string;
  database_connected: boolean;
  database_detail: string;
  mode: string;
  dialect: string;
  read_only_enforced: boolean;
  is_replica: boolean;
}

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthFailed, setHealthFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch('/api/health')
      .then((response) => (response.ok ? response.json() : Promise.reject(response.status)))
      .then((data: Health) => {
        if (!cancelled) setHealth(data);
      })
      .catch(() => {
        if (!cancelled) setHealthFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(email, password);
      router.push('/reports/builder');
      router.refresh();
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : 'Could not sign in. Please try again.',
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-full items-center justify-center bg-canvas px-4 py-10">
      <div className="w-full max-w-[400px]">
        <div className="rounded-lg border border-line bg-white p-8 shadow-panel">
          <div className="mb-6 flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-end justify-center gap-[3px] rounded bg-accent px-1.5 pb-1.5">
              <span className="h-2.5 w-[3px] rounded-sm bg-white/70" />
              <span className="h-4 w-[3px] rounded-sm bg-white" />
              <span className="h-3 w-[3px] rounded-sm bg-white/70" />
            </div>
            <div>
              <h1 className="text-md font-semibold leading-tight">
                Database Intelligence Platform
              </h1>
              <p className="text-xs text-ink-muted">Reporting, data quality and investigation</p>
            </div>
          </div>

          <form onSubmit={submit} className="space-y-3">
            <div>
              <label className="label" htmlFor="email">
                Email
              </label>
              <input
                id="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                autoComplete="username"
                autoFocus
                className="field"
                required
              />
            </div>

            <div>
              <label className="label" htmlFor="password">
                Password
              </label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="current-password"
                className="field"
                required
              />
            </div>

            {error && (
              <p
                role="alert"
                className="rounded border border-danger-border bg-danger-soft px-2.5 py-2 text-xs text-danger"
              >
                {error}
              </p>
            )}

            <Button type="submit" variant="primary" disabled={busy} className="w-full">
              {busy ? 'Signing in…' : 'Sign in'}
            </Button>
          </form>
        </div>

        <ConnectionStatus health={health} failed={healthFailed} />
      </div>
    </main>
  );
}

function ConnectionStatus({ health, failed }: { health: Health | null; failed: boolean }) {
  if (failed) {
    return (
      <Strip tone="danger" label="Service unavailable">
        The reporting service is not responding. Signing in will not work until it is back.
      </Strip>
    );
  }

  if (!health) {
    return (
      <div className="mt-3 flex items-center gap-2 px-1 text-2xs text-ink-faint">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-line-strong" />
        Checking database connection…
      </div>
    );
  }

  if (!health.database_connected) {
    return (
      <Strip tone="danger" label="Database not connected">
        {health.database_detail || 'The reporting database could not be reached.'} Reports
        will not run until the connection is restored.
      </Strip>
    );
  }

  // Serving demo data while claiming to be a reporting platform is the one
  // state worth shouting about: the numbers would look real and be fictional.
  if (health.mode === 'mock') {
    return (
      <Strip tone="warn" label="Demo data">
        This instance is serving seeded sample data, not your live database.
      </Strip>
    );
  }

  return (
    <Strip tone="good" label="Database connected">
      Live {health.dialect} connection
      {health.is_replica ? ', read replica' : ''}
      {health.read_only_enforced ? ', read-only enforced' : ''}.
    </Strip>
  );
}

function Strip({
  tone,
  label,
  children,
}: {
  tone: 'good' | 'warn' | 'danger';
  label: string;
  children: React.ReactNode;
}) {
  const styles = {
    good: 'border-good-border bg-good-soft text-good',
    warn: 'border-warn-border bg-warn-soft text-warn',
    danger: 'border-danger-border bg-danger-soft text-danger',
  }[tone];
  const dot = { good: 'bg-good', warn: 'bg-warn', danger: 'bg-danger' }[tone];

  return (
    <div className={`mt-3 rounded border px-3 py-2 ${styles}`}>
      <div className="flex items-center gap-1.5">
        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
        <span className="text-2xs font-semibold">{label}</span>
      </div>
      <p className="mt-0.5 pl-3 text-2xs opacity-90">{children}</p>
    </div>
  );
}
