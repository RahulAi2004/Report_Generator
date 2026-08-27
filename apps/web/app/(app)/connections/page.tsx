'use client';

import clsx from 'clsx';
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Badge, Button, Checkbox, EmptyState, Select, Skeleton } from '@/components/ui/primitives';
import { api, ApiError } from '@/lib/api';
import type { Connection, ProbeResult } from '@/lib/connection-types';

/**
 * Connections.
 *
 * Adding one is the only action in this application that widens what it can
 * reach, so the screen is built around the two things that decide whether that
 * is safe: which database is being connected, and whether the account can write
 * to it.
 *
 * The flow is Test, then choose a database from what was found, then Save.
 * Typing a database name from memory is how you end up reporting on staging and
 * not knowing it.
 */
export default function ConnectionsPage() {
  const client = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [notice, setNotice] = useState<{ tone: 'good' | 'bad'; text: string } | null>(null);

  const listing = useQuery({ queryKey: ['connections'], queryFn: api.connections });

  const activate = useMutation({
    mutationFn: (id: string) => api.activateConnection(id),
    onSuccess: () => {
      setNotice({ tone: 'good', text: 'Now reading from this connection.' });
      client.invalidateQueries();
    },
    onError: (error) =>
      setNotice({ tone: 'bad', text: error instanceof ApiError ? error.message : String(error) }),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteConnection(id),
    onSuccess: () => {
      setNotice({ tone: 'good', text: 'Connection removed.' });
      client.invalidateQueries({ queryKey: ['connections'] });
    },
    onError: (error) =>
      setNotice({ tone: 'bad', text: error instanceof ApiError ? error.message : String(error) }),
  });

  const connections = listing.data?.connections ?? [];

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-canvas">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-line bg-white px-4 py-3">
        <div>
          <h1 className="text-md font-semibold text-ink">Connections</h1>
          <p className="text-xs text-ink-muted">
            The databases this application can read. One is live at a time.
          </p>
        </div>
        <Button variant="primary" onClick={() => setAdding(true)}>
          <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2}>
            <path d="M12 5v14M5 12h14" />
          </svg>
          Add Connection
        </Button>
      </header>

      {notice && (
        <p
          className={clsx(
            'flex items-center justify-between border-b px-4 py-1.5 text-xs',
            notice.tone === 'good'
              ? 'border-good-border bg-good-soft text-good'
              : 'border-danger-border bg-danger-soft text-danger',
          )}
        >
          {notice.text}
          <button type="button" onClick={() => setNotice(null)} className="opacity-70 hover:opacity-100">
            Dismiss
          </button>
        </p>
      )}

      {listing.data && !listing.data.can_store_passwords && (
        <p className="border-b border-warn-border bg-warn-soft px-4 py-2 text-xs text-warn">
          APP_SECRET is not configured on the server, so passwords cannot be stored safely.
          New connections cannot be added until it is set.
        </p>
      )}

      <div className="flex-1 space-y-3 p-4">
        {adding && (
          <ConnectionForm
            onCancel={() => setAdding(false)}
            onSaved={(name) => {
              setAdding(false);
              setNotice({ tone: 'good', text: `"${name}" saved. It is read-only.` });
              client.invalidateQueries({ queryKey: ['connections'] });
            }}
          />
        )}

        {listing.isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 2 }).map((_, index) => (
              <Skeleton key={index} className="h-24 w-full" />
            ))}
          </div>
        ) : listing.isError ? (
          <div className="panel p-4">
            <p className="text-sm text-danger">{(listing.error as Error).message}</p>
            <p className="mt-1 text-xs text-ink-muted">
              Managing connections needs an administrator account.
            </p>
          </div>
        ) : connections.length === 0 ? (
          <div className="panel">
            <EmptyState title="No connections" hint="Add one to begin." />
          </div>
        ) : (
          <div className="space-y-2.5">
            {connections.map((connection) => (
              <ConnectionCard
                key={connection.id}
                connection={connection}
                busy={activate.isPending || remove.isPending}
                onActivate={() => activate.mutate(connection.id)}
                onDelete={() => {
                  if (window.confirm(`Remove "${connection.name}"?`)) {
                    remove.mutate(connection.id);
                  }
                }}
              />
            ))}
          </div>
        )}

        <p className="rounded-lg border border-line bg-white px-4 py-3 text-2xs leading-relaxed text-ink-muted">
          <strong className="font-semibold text-ink">Read-only is enforced, not requested.</strong>{' '}
          Every connection is tested for write access before it is stored, and one that can
          write is refused rather than saved with a warning. Create a role with SELECT only —
          in PostgreSQL, <code className="font-mono">ALTER ROLE … SET default_transaction_read_only = on</code>{' '}
          is the simplest way — and connect with that.
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
function ConnectionCard({
  connection,
  busy,
  onActivate,
  onDelete,
}: {
  connection: Connection;
  busy: boolean;
  onActivate: () => void;
  onDelete: () => void;
}) {
  return (
    <div
      className={clsx(
        'panel p-3.5',
        connection.is_selected && 'border-accent ring-2 ring-accent/15',
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <h2 className="truncate text-sm font-semibold text-ink">{connection.name}</h2>
            {connection.is_selected && <Badge tone="accent">Live</Badge>}
            {connection.is_read_only ? (
              <Badge tone="good">Read-only</Badge>
            ) : (
              <Badge tone="danger">Writable</Badge>
            )}
            {connection.is_replica && <Badge>Replica</Badge>}
            {connection.is_builtin && <Badge>From the server</Badge>}
          </div>

          <p className="mt-1 font-mono text-2xs text-ink-muted">
            {connection.is_builtin
              ? `${connection.database_name} — host and credentials are part of the deployment`
              : `${connection.username}@${connection.host}:${connection.port}/${connection.database_name}`}
          </p>

          {connection.schemas.length > 0 && (
            <p className="mt-0.5 text-2xs text-ink-faint">
              Schemas: {connection.schemas.join(', ')}
            </p>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          {!connection.is_selected && (
            <Button size="sm" variant="primary" disabled={busy} onClick={onActivate}>
              Switch to this
            </Button>
          )}
          {!connection.is_builtin && (
            <Button size="sm" disabled={busy || connection.is_selected} onClick={onDelete}>
              Remove
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
const EMPTY = {
  name: '',
  host: '',
  port: 5432,
  username: '',
  password: '',
  ssl_mode: 'prefer' as const,
  is_replica: false,
};

function ConnectionForm({
  onCancel,
  onSaved,
}: {
  onCancel: () => void;
  onSaved: (name: string) => void;
}) {
  const [form, setForm] = useState(EMPTY);
  const [database, setDatabase] = useState('');
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) =>
    setForm((current) => ({ ...current, [key]: value }));

  const test = useMutation({
    // Connect to the server's default database first, purely to list what is
    // there: the database being connected to is chosen after, from real names.
    mutationFn: () =>
      api.testConnection({
        host: form.host,
        port: form.port,
        database_name: database || 'postgres',
        username: form.username,
        password: form.password,
        ssl_mode: form.ssl_mode,
      }),
    onSuccess: (result) => {
      setProbe(result);
      setError(null);
      if (!database && result.databases.length) setDatabase(result.databases[0]);
    },
    onError: (failure) => {
      setProbe(null);
      setError(failure instanceof ApiError ? failure.message : String(failure));
    },
  });

  const save = useMutation({
    mutationFn: () =>
      api.createConnection({
        name: form.name.trim(),
        host: form.host,
        port: form.port,
        database_name: database,
        username: form.username,
        password: form.password,
        ssl_mode: form.ssl_mode,
        is_replica: form.is_replica,
      }),
    onSuccess: (created) => onSaved(created.name),
    onError: (failure) =>
      setError(failure instanceof ApiError ? failure.message : String(failure)),
  });

  const canTest = form.host && form.username && form.password;
  const canSave = canTest && form.name.trim() && database && probe?.read_only;

  return (
    <section className="panel">
      <div className="panel-header">
        <h2 className="panel-title">New connection</h2>
        <Button size="sm" onClick={onCancel}>Cancel</Button>
      </div>

      <div className="space-y-3 p-4">
        <div className="grid gap-2.5 md:grid-cols-[1fr_140px]">
          <div>
            <label className="label">Host</label>
            <input
              value={form.host}
              onChange={(event) => set('host', event.target.value)}
              placeholder="decoinks_postgres"
              className="field font-mono"
            />
            <p className="mt-0.5 text-[10px] text-ink-faint">
              For a database in another container on this server, use its container name.
            </p>
          </div>
          <div>
            <label className="label">Port</label>
            <input
              type="number"
              value={form.port}
              onChange={(event) => set('port', Number(event.target.value))}
              className="field"
            />
          </div>
        </div>

        <div className="grid gap-2.5 md:grid-cols-2">
          <div>
            <label className="label">Username</label>
            <input
              value={form.username}
              onChange={(event) => set('username', event.target.value)}
              placeholder="reporting_readonly"
              className="field font-mono"
            />
          </div>
          <div>
            <label className="label">Password</label>
            <input
              type="password"
              value={form.password}
              onChange={(event) => set('password', event.target.value)}
              autoComplete="new-password"
              className="field"
            />
            <p className="mt-0.5 text-[10px] text-ink-faint">
              Encrypted before storage. It is never sent back to this screen.
            </p>
          </div>
        </div>

        <div className="grid gap-2.5 md:grid-cols-[160px_1fr]">
          <div>
            <label className="label">SSL</label>
            <Select
              value={form.ssl_mode}
              onChange={(value) => set('ssl_mode', value as typeof form.ssl_mode)}
              options={['disable', 'allow', 'prefer', 'require'].map((mode) => ({
                value: mode,
                label: mode,
              }))}
            />
          </div>
          <div className="flex items-end">
            <Button
              disabled={!canTest || test.isPending}
              onClick={() => test.mutate()}
            >
              {test.isPending ? 'Testing…' : 'Test connection'}
            </Button>
          </div>
        </div>

        {error && (
          <p className="rounded border border-danger-border bg-danger-soft px-3 py-2 text-xs text-danger">
            {error}
          </p>
        )}

        {probe && (
          <div
            className={clsx(
              'rounded-lg border px-3 py-2.5',
              probe.read_only
                ? 'border-good-border bg-good-soft'
                : 'border-danger-border bg-danger-soft',
            )}
          >
            <p className={clsx('text-xs font-medium', probe.read_only ? 'text-good' : 'text-danger')}>
              {probe.detail}
            </p>
            {probe.server_version && (
              <p className="mt-0.5 font-mono text-[10px] text-ink-muted">
                {probe.server_version}
              </p>
            )}
          </div>
        )}

        {probe?.reachable && probe.databases.length > 0 && (
          <>
            <div>
              <label className="label">
                Database
                <span className="ml-1 font-normal text-ink-faint">
                  {probe.databases.length} found on this server
                </span>
              </label>
              <Select
                value={database}
                onChange={(value) => {
                  setDatabase(value);
                  // A different database means a different set of privileges,
                  // so the read-only answer no longer applies.
                  setProbe(null);
                }}
                options={probe.databases.map((name) => ({ value: name, label: name }))}
              />
              <p className="mt-0.5 text-[10px] text-ink-faint">
                Choosing a different database re-tests it: privileges are per database.
              </p>
            </div>

            <div>
              <label className="label">Name for this connection</label>
              <input
                value={form.name}
                onChange={(event) => set('name', event.target.value)}
                placeholder={database ? `${database} (read-only)` : 'A name people will recognise'}
                className="field"
              />
            </div>

            <Checkbox
              checked={form.is_replica}
              onChange={(value) => set('is_replica', value)}
              label={<span className="text-xs">This is a read replica, not the primary</span>}
            />
          </>
        )}

        <div className="flex items-center justify-end gap-2 border-t border-line pt-3">
          {probe && !probe.read_only && (
            <p className="mr-auto text-2xs text-danger">
              This account can write, so it cannot be saved.
            </p>
          )}
          <Button onClick={onCancel}>Cancel</Button>
          <Button
            variant="primary"
            disabled={!canSave || save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending ? 'Saving…' : 'Save connection'}
          </Button>
        </div>
      </div>
    </section>
  );
}
