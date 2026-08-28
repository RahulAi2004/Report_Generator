'use client';

import clsx from 'clsx';
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Badge, Button, EmptyState, Select, Skeleton } from '@/components/ui/primitives';
import { api, ApiError } from '@/lib/api';
import { RESOURCE_LABELS } from '@/lib/connector-types';
import type {
  Connector,
  ConnectorDataset,
  ConnectorResource,
  Discovery,
  Provider,
  ProviderDataset,
} from '@/lib/connector-types';

/**
 * API Connections.
 *
 * The screen follows the only order that works: paste a credential, find out
 * what it can reach, choose from that. Nobody remembers which ad accounts a
 * token was generated for, and a mistyped account id produces an empty table
 * rather than an error — which looks like a business with no spend.
 *
 * Once a dataset is syncing it becomes an ordinary table in the report builder,
 * so this page's job ends where the builder's begins.
 */
export default function ConnectorsPage() {
  const client = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [notice, setNotice] = useState<{ tone: 'good' | 'bad'; text: string } | null>(null);

  const providers = useQuery({
    queryKey: ['connector-providers'],
    queryFn: api.connectorProviders,
    staleTime: 600_000,
  });

  const listing = useQuery({
    queryKey: ['connectors'],
    queryFn: api.connectors,
    // While anything is syncing, keep the row's status honest.
    refetchInterval: (query) =>
      query.state.data?.connectors.some((c) =>
        c.datasets.some((d) => d.status === 'syncing' || d.status === 'pending'),
      )
        ? 4000
        : false,
  });

  const say = (tone: 'good' | 'bad', text: string) => setNotice({ tone, text });
  const fail = (error: unknown) =>
    say('bad', error instanceof ApiError ? error.message : String(error));

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteConnector(id),
    onSuccess: () => {
      say('good', 'Connector removed, along with the tables it produced.');
      client.invalidateQueries();
    },
    onError: fail,
  });

  const connectors = listing.data?.connectors ?? [];
  const meta = providers.data?.providers.find((p) => p.key === 'meta');

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-canvas">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-line bg-white px-4 py-3">
        <div>
          <h1 className="text-md font-semibold text-ink">API Connections</h1>
          <p className="text-xs text-ink-muted">
            Data pulled from other companies&rsquo; APIs, refreshed on a schedule and
            reportable like any other table.
          </p>
        </div>
        <Button variant="primary" onClick={() => setAdding(true)} disabled={adding}>
          <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2}>
            <path d="M12 5v14M5 12h14" />
          </svg>
          Connect Meta
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

      {listing.data && !listing.data.can_store_tokens && (
        <p className="border-b border-warn-border bg-warn-soft px-4 py-2 text-xs text-warn">
          APP_SECRET is not configured on the server, so access tokens cannot be stored
          safely. Connectors cannot be added until it is set.
        </p>
      )}

      <div className="flex-1 space-y-3 p-4">
        {adding && meta && (
          <AddConnector
            provider={meta}
            onCancel={() => setAdding(false)}
            onSaved={(name) => {
              setAdding(false);
              say('good', `"${name}" connected. Choose what to sync below.`);
              client.invalidateQueries({ queryKey: ['connectors'] });
            }}
          />
        )}

        {listing.isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : listing.isError ? (
          <div className="panel p-4">
            <p className="text-sm text-danger">{(listing.error as Error).message}</p>
            <p className="mt-1 text-xs text-ink-muted">
              Managing API connections needs an administrator account.
            </p>
          </div>
        ) : connectors.length === 0 && !adding ? (
          <div className="panel">
            <EmptyState
              title="Nothing connected yet"
              hint="Connect Meta to pull ad spend, page and Instagram data into the report builder."
            />
          </div>
        ) : (
          connectors.map((connector) => (
            <ConnectorCard
              key={connector.id}
              connector={connector}
              provider={providers.data?.providers.find((p) => p.key === connector.provider)}
              onNotice={say}
              onError={fail}
              onDelete={() => {
                if (
                  window.confirm(
                    `Remove "${connector.name}" and every table it produced? Reports built on them will stop working.`,
                  )
                ) {
                  remove.mutate(connector.id);
                }
              }}
            />
          ))
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
function AddConnector({
  provider,
  onCancel,
  onSaved,
}: {
  provider: Provider;
  onCancel: () => void;
  onSaved: (name: string) => void;
}) {
  const [appId, setAppId] = useState('');
  const [appSecret, setAppSecret] = useState('');
  const [token, setToken] = useState('');
  const [name, setName] = useState('');
  const [version, setVersion] = useState(provider.default_api_version);
  const [found, setFound] = useState<Discovery | null>(null);
  const [error, setError] = useState<string | null>(null);

  const discover = useMutation({
    mutationFn: () =>
      api.discoverConnector({
        provider: provider.key,
        token,
        api_version: version,
        app_id: appId.trim(),
        app_secret: appSecret.trim(),
      }),
    onSuccess: (result) => {
      setFound(result);
      setError(null);
      if (!name && result.account_name) setName(`Meta — ${result.account_name}`);
    },
    onError: (failure) => {
      setFound(null);
      setError(failure instanceof ApiError ? failure.message : String(failure));
    },
  });

  const save = useMutation({
    mutationFn: () =>
      api.createConnector({
        provider: provider.key,
        name: name.trim(),
        token,
        app_id: appId.trim(),
        app_secret: appSecret.trim(),
        api_version: version,
        sync_interval_minutes: 60,
      }),
    onSuccess: (created) => onSaved(created.name),
    onError: (failure) =>
      setError(failure instanceof ApiError ? failure.message : String(failure)),
  });

  return (
    <section className="panel">
      <div className="panel-header">
        <h2 className="panel-title">Connect {provider.label}</h2>
        <Button size="sm" onClick={onCancel}>Cancel</Button>
      </div>

      <div className="space-y-3 p-4">
        <div className="grid gap-2.5 md:grid-cols-2">
          <div>
            <label className="label">App ID</label>
            <input
              value={appId}
              onChange={(event) => setAppId(event.target.value)}
              placeholder="From Meta for Developers → your app"
              className="field font-mono"
              autoComplete="off"
            />
          </div>
          <div>
            <label className="label">App Secret</label>
            <input
              type="password"
              value={appSecret}
              onChange={(event) => setAppSecret(event.target.value)}
              className="field"
              autoComplete="new-password"
            />
          </div>
        </div>

        <p className="rounded border border-line bg-canvas px-3 py-2 text-[11px] leading-relaxed text-ink-muted">
          The App ID and Secret are optional, but three things need them.
          Apps with <strong>Require App Secret</strong> turned on reject every call
          without a signature. Reading which permissions a token has needs an app
          token. And a token from the Graph API Explorer expires in an hour or two —
          with these, it is exchanged for one that lasts about sixty days, which is
          what keeps the hourly refresh working tomorrow.
        </p>

        <div>
          <label className="label">Access token</label>
          <textarea
            value={token}
            onChange={(event) => setToken(event.target.value)}
            rows={3}
            placeholder="Paste the token from Meta Business settings"
            className="field resize-none font-mono text-xs"
            autoComplete="off"
            spellCheck={false}
          />
          <p className="mt-0.5 text-[10px] text-ink-faint">
            Encrypted before storage. It is never sent back to this screen and never
            written to a log.
          </p>
        </div>

        <div className="grid gap-2.5 md:grid-cols-[160px_1fr]">
          <div>
            <label className="label">API version</label>
            <input
              value={version}
              onChange={(event) => setVersion(event.target.value)}
              className="field font-mono"
            />
          </div>
          <div className="flex items-end">
            <Button
              disabled={token.trim().length < 8 || discover.isPending}
              onClick={() => discover.mutate()}
            >
              {discover.isPending ? 'Asking Meta…' : 'Check what this token can reach'}
            </Button>
          </div>
        </div>

        {error && (
          <p className="rounded border border-danger-border bg-danger-soft px-3 py-2 text-xs text-danger">
            {error}
          </p>
        )}

        {found && <DiscoveryPanel found={found} />}

        {found && found.resources.length > 0 && (
          <>
            <div>
              <label className="label">Name for this connection</label>
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                className="field"
              />
            </div>
            <div className="flex justify-end gap-2 border-t border-line pt-3">
              <Button onClick={onCancel}>Cancel</Button>
              <Button
                variant="primary"
                disabled={!name.trim() || save.isPending}
                onClick={() => save.mutate()}
              >
                {save.isPending ? 'Saving…' : 'Save connection'}
              </Button>
            </div>
          </>
        )}
      </div>
    </section>
  );
}

function DiscoveryPanel({ found }: { found: Discovery }) {
  const byKind = new Map<string, ConnectorResource[]>();
  for (const resource of found.resources) {
    byKind.set(resource.kind, [...(byKind.get(resource.kind) ?? []), resource]);
  }

  const expiring = found.expires_at ? new Date(found.expires_at) : null;
  const soon =
    expiring && expiring.getTime() - Date.now() < 14 * 24 * 60 * 60 * 1000;

  return (
    <div className="rounded-lg border border-good-border bg-good-soft/50 p-3">
      <p className="text-xs font-medium text-good">
        Connected as {found.account_name ?? 'an unnamed account'} — {found.detail}
      </p>

      {/*
        One sentence about the token's life, not two. Saying "exchanged for a
        sixty-day token" and "does not expire" on consecutive lines is a
        contradiction the reader has to resolve, and the whole point of this
        panel is that they should not have to work anything out.
      */}
      <p
        className={clsx(
          'mt-1 text-2xs',
          soon ? 'text-warn' : found.exchanged_for_long_lived ? 'text-good' : 'text-ink-muted',
        )}
      >
        {expiring
          ? found.exchanged_for_long_lived
            ? `Exchanged for a long-lived token, valid until ${expiring.toLocaleDateString()}.`
            : `This token expires ${expiring.toLocaleDateString()}.`
          : found.exchanged_for_long_lived
            ? 'Exchanged for a long-lived token, and Meta reports no expiry on it.'
            : 'Meta reports no expiry on this token.'}
        {soon && ' Syncing will stop then unless it is replaced.'}
      </p>

      {!found.has_app_credentials && !found.expires_at && (
        <p className="mt-1 text-2xs text-warn">
          No App ID and Secret were given, so the token is stored exactly as pasted.
          Meta reports no expiry, but a token from the Graph API Explorer usually has
          one it does not declare here.
        </p>
      )}
      {!found.has_app_credentials && found.expires_at && (
        <p className="mt-1 text-2xs text-warn">
          Stored exactly as pasted. Adding the App ID and Secret would let it be
          exchanged for a longer-lived one.
        </p>
      )}

      {found.permissions.length > 0 && (
        <p className="mt-1.5 font-mono text-[10px] leading-relaxed text-ink-muted">
          {found.permissions.join(' · ')}
        </p>
      )}

      <div className="mt-2 space-y-1.5">
        {[...byKind.entries()].map(([kind, items]) => (
          <div key={kind}>
            <p className="text-2xs font-semibold uppercase tracking-wide text-ink-muted">
              {RESOURCE_LABELS[kind] ?? kind} ({items.length})
            </p>
            <ul className="mt-0.5 space-y-0.5">
              {items.map((resource) => (
                <li key={resource.id} className="text-xs text-ink">
                  {resource.name}
                  <span className="ml-1.5 font-mono text-[10px] text-ink-faint">
                    {resource.id}
                  </span>
                  {typeof resource.detail.status === 'string' &&
                    resource.detail.status !== 'Active' && (
                      <Badge tone="warn">{resource.detail.status}</Badge>
                    )}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      {found.resources.length === 0 && (
        <p className="mt-2 text-xs text-warn">
          This token works, but reaches no ad accounts, pages or Instagram profiles.
          It may be missing permissions, or belong to a user with no access.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
function ConnectorCard({
  connector,
  provider,
  onNotice,
  onError,
  onDelete,
}: {
  connector: Connector;
  provider?: Provider;
  onNotice: (tone: 'good' | 'bad', text: string) => void;
  onError: (error: unknown) => void;
  onDelete: () => void;
}) {
  const client = useQueryClient();
  const [addingDataset, setAddingDataset] = useState(false);

  const refreshDiscovery = useMutation({
    mutationFn: () => api.refreshDiscovery(connector.id),
    onSuccess: () => {
      onNotice('good', 'Checked again — accounts and permissions are up to date.');
      client.invalidateQueries({ queryKey: ['connectors'] });
    },
    onError,
  });

  return (
    <section className="panel">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <h2 className="text-sm font-semibold text-ink">{connector.name}</h2>
            <Badge tone="accent">{connector.provider_label}</Badge>
            {!connector.is_active && <Badge tone="warn">Paused</Badge>}
            {connector.last_error && <Badge tone="danger">Problem</Badge>}
          </div>
          <p className="mt-0.5 text-2xs text-ink-muted">
            {connector.discovery?.detail ?? 'Not checked yet'} · refreshed every{' '}
            {connector.sync_interval_minutes} minutes · API {connector.api_version}
            {connector.has_app_secret ? ' · app secret stored' : ' · no app secret'}
          </p>
          {connector.token_expires_at && (
            <p
              className={clsx(
                'mt-0.5 text-2xs',
                new Date(connector.token_expires_at).getTime() - Date.now()
                  < 7 * 24 * 60 * 60 * 1000
                  ? 'text-warn'
                  : 'text-ink-faint',
              )}
            >
              Token expires {new Date(connector.token_expires_at).toLocaleDateString()}
            </p>
          )}
          {connector.last_error && (
            <p className="mt-1 text-2xs text-danger">{connector.last_error}</p>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          <Button size="sm" disabled={refreshDiscovery.isPending}
                  onClick={() => refreshDiscovery.mutate()}>
            Re-check token
          </Button>
          <Button size="sm" variant="primary" onClick={() => setAddingDataset((v) => !v)}>
            Add data
          </Button>
          <Button size="sm" onClick={onDelete}>Remove</Button>
        </div>
      </div>

      {addingDataset && provider && connector.discovery && (
        <AddDataset
          connector={connector}
          provider={provider}
          onDone={(message) => {
            setAddingDataset(false);
            onNotice('good', message);
            client.invalidateQueries({ queryKey: ['connectors'] });
          }}
          onError={onError}
        />
      )}

      <div className="p-3">
        {connector.datasets.length === 0 ? (
          <p className="rounded border border-dashed border-line-strong px-3 py-5 text-center text-xs text-ink-muted">
            Nothing syncing yet. Use <strong>Add data</strong> to pick what to pull in.
          </p>
        ) : (
          <div className="space-y-2">
            {connector.datasets.map((dataset) => (
              <DatasetRow
                key={dataset.id}
                dataset={dataset}
                onNotice={onNotice}
                onError={onError}
              />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function AddDataset({
  connector,
  provider,
  onDone,
  onError,
}: {
  connector: Connector;
  provider: Provider;
  onDone: (message: string) => void;
  onError: (error: unknown) => void;
}) {
  const discovery = connector.discovery!;
  const [datasetKey, setDatasetKey] = useState(provider.datasets[0]?.key ?? '');
  const [resourceId, setResourceId] = useState('');
  const [lookback, setLookback] = useState(30);

  const chosen: ProviderDataset | undefined = provider.datasets.find(
    (d) => d.key === datasetKey,
  );
  // Only resources of the kind this dataset reads: an Ads Insights table cannot
  // be built from a Page, and offering one would only produce an error later.
  const candidates = discovery.resources.filter(
    (resource) => resource.kind === chosen?.resource_kind,
  );
  const missing = discovery.missing_permissions[datasetKey] ?? [];

  const add = useMutation({
    mutationFn: () => {
      const resource = candidates.find((r) => r.id === resourceId);
      return api.addConnectorDataset(connector.id, {
        dataset_key: datasetKey,
        resource_id: resourceId,
        resource_name: resource?.name ?? '',
        lookback_days: lookback,
      });
    },
    onSuccess: () => onDone('Added. The first sync is running now.'),
    onError,
  });

  return (
    <div className="space-y-2.5 border-b border-line bg-canvas/60 px-4 py-3">
      <div className="grid gap-2.5 md:grid-cols-2">
        <div>
          <label className="label">What to pull in</label>
          <Select
            value={datasetKey}
            onChange={(value) => {
              setDatasetKey(value);
              setResourceId('');
            }}
            options={provider.datasets.map((dataset) => ({
              value: dataset.key,
              label: dataset.label,
            }))}
          />
          {chosen && (
            <p className="mt-0.5 text-[10px] text-ink-faint">{chosen.description}</p>
          )}
        </div>

        <div>
          <label className="label">
            From which {RESOURCE_LABELS[chosen?.resource_kind ?? ''] ?? 'account'}
          </label>
          <Select
            value={resourceId}
            onChange={setResourceId}
            placeholder={
              candidates.length ? 'Choose one' : 'This token reaches none of these'
            }
            options={candidates.map((resource) => ({
              value: resource.id,
              label: resource.detail.currency
                ? `${resource.name} (${resource.detail.currency})`
                : resource.name,
            }))}
          />
        </div>
      </div>

      {chosen?.time_series && (
        <div className="max-w-[240px]">
          <label className="label">How far back to fetch, each sync</label>
          <Select
            value={String(lookback)}
            onChange={(value) => setLookback(Number(value))}
            options={[7, 30, 90, 180, 365].map((days) => ({
              value: String(days),
              label: `${days} days`,
            }))}
          />
          <p className="mt-0.5 text-[10px] text-ink-faint">
            Meta restates recent days, so this window is re-fetched and replaced every
            time rather than added to.
          </p>
        </div>
      )}

      {missing.length > 0 && (
        <p className="rounded border border-warn-border bg-warn-soft px-3 py-2 text-xs text-warn">
          This token is missing {missing.join(', ')}. Meta will refuse the request —
          add the permission and re-check the token first.
        </p>
      )}

      <div className="flex justify-end gap-2">
        <Button
          variant="primary"
          size="sm"
          disabled={!resourceId || add.isPending}
          onClick={() => add.mutate()}
        >
          {add.isPending ? 'Starting…' : 'Add and sync now'}
        </Button>
      </div>
    </div>
  );
}

function DatasetRow({
  dataset,
  onNotice,
  onError,
}: {
  dataset: ConnectorDataset;
  onNotice: (tone: 'good' | 'bad', text: string) => void;
  onError: (error: unknown) => void;
}) {
  const client = useQueryClient();
  const [showColumns, setShowColumns] = useState(false);

  const sync = useMutation({
    mutationFn: () => api.syncDataset(dataset.id),
    onSuccess: (result) => {
      onNotice(
        'good',
        result.already_running ? 'Already refreshing.' : 'Refreshing now.',
      );
      client.invalidateQueries({ queryKey: ['connectors'] });
    },
    onError,
  });

  const remove = useMutation({
    mutationFn: () => api.deleteDataset(dataset.id),
    onSuccess: () => {
      onNotice('good', 'Removed, along with its table.');
      client.invalidateQueries({ queryKey: ['connectors'] });
    },
    onError,
  });

  const busy = dataset.status === 'syncing' || dataset.status === 'pending';

  return (
    <div className="rounded-lg border border-line p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-sm font-medium text-ink">{dataset.display_name}</span>
            {dataset.status === 'ready' && <Badge tone="good">Ready</Badge>}
            {busy && <Badge tone="accent">Refreshing…</Badge>}
            {dataset.status === 'error' && <Badge tone="danger">Failed</Badge>}
          </div>
          <p className="mt-0.5 text-2xs text-ink-muted">
            {dataset.row_count.toLocaleString()} rows ·{' '}
            <button
              type="button"
              onClick={() => setShowColumns((value) => !value)}
              className="underline decoration-dotted hover:text-accent"
            >
              {dataset.column_count} fields
            </button>{' '}
            ·{' '}
            {dataset.last_synced_at
              ? `refreshed ${new Date(dataset.last_synced_at).toLocaleString()}`
              : 'never refreshed'}
            {dataset.last_duration_ms > 0 && ` in ${dataset.last_duration_ms}ms`}
          </p>
          {dataset.last_error && (
            <p className="mt-1 text-2xs text-danger">{dataset.last_error}</p>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          <Button size="sm" disabled={busy || sync.isPending} onClick={() => sync.mutate()}>
            Refresh
          </Button>
          <Button
            size="sm"
            disabled={remove.isPending}
            onClick={() => {
              if (window.confirm(`Remove "${dataset.display_name}" and its table?`)) {
                remove.mutate();
              }
            }}
          >
            Remove
          </Button>
        </div>
      </div>

      {showColumns && dataset.columns.length > 0 && (
        <div className="mt-2 border-t border-line pt-2">
          <p className="mb-1 text-2xs text-ink-muted">
            These are the fields the report builder offers for this table.
          </p>
          <div className="flex max-h-40 flex-wrap gap-1 overflow-y-auto">
            {dataset.columns.map((column) => (
              <span
                key={column.name}
                title={`${column.name} · ${column.data_type}`}
                className="rounded border border-line bg-canvas px-1.5 py-0.5 font-mono text-[10px] text-ink-muted"
              >
                {column.name}
                <span className="ml-1 text-ink-faint">{column.data_type}</span>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
