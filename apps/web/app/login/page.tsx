'use client';

import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { Button } from '@/components/ui/primitives';
import { api, ApiError } from '@/lib/api';

const DEMO_ACCOUNTS = [
  { email: 'admin@decoinks.local', role: 'Super Admin', note: 'everything' },
  { email: 'boss@decoinks.local', role: 'Management', note: 'reports, exports, anomalies' },
  { email: 'analyst@decoinks.local', role: 'Analyst', note: 'build reports, view SQL' },
  { email: 'viewer@decoinks.local', role: 'Viewer', note: 'run reports only' },
];

export default function LoginPage() {
  const router = useRouter();
  // Demo credentials are a development convenience only. A production build
  // must never pre-fill a password field or advertise an account.
  const showDemoAccounts = process.env.NODE_ENV !== 'production';
  const [email, setEmail] = useState(showDemoAccounts ? 'admin@decoinks.local' : '');
  const [password, setPassword] = useState(showDemoAccounts ? 'demo1234' : '');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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
      <div className="w-full max-w-[820px] overflow-hidden rounded-lg border border-line
                      bg-white shadow-panel md:grid md:grid-cols-[1fr_340px]">
        <div className="p-8">
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
                className="field"
                required
              />
            </div>
            {showDemoAccounts && (
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
          )}

            {error && (
              <p className="rounded border border-danger-border bg-danger-soft px-2.5 py-2 text-xs text-danger">
                {error}
              </p>
            )}

            <Button type="submit" variant="primary" disabled={busy} className="w-full">
              {busy ? 'Signing in…' : 'Sign in'}
            </Button>
          </form>

          <p className="mt-5 rounded border border-warn-border bg-warn-soft px-2.5 py-2 text-2xs text-warn">
            Development mode — this instance is serving seeded demo data, not the production
            database.
          </p>
        </div>

        <aside className="hidden border-l border-line bg-canvas p-6 md:block">
          <h2 className="panel-title mb-3">Demo accounts</h2>
          <p className="mb-3 text-2xs text-ink-muted">
            Every role uses the password <code className="font-mono">demo1234</code>. Sign in as
            different roles to see permissions enforced.
          </p>
          <ul className="space-y-2">
            {DEMO_ACCOUNTS.map((account) => (
              <li key={account.email}>
                <button
                  type="button"
                  onClick={() => {
                    setEmail(account.email);
                    setPassword('demo1234');
                  }}
                  className="w-full rounded border border-line bg-white px-2.5 py-2 text-left
                             transition-colors hover:border-accent-border hover:bg-accent-soft"
                >
                  <span className="block text-xs font-semibold text-ink">{account.role}</span>
                  <span className="block truncate font-mono text-2xs text-ink-muted">
                    {account.email}
                  </span>
                  <span className="block text-2xs text-ink-faint">{account.note}</span>
                </button>
              </li>
            ))}
          </ul>
        </aside>
      </div>
    </main>
  );
}
