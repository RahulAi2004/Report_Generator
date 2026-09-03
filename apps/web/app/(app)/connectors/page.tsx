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
  CredentialField,
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
  //: Which provider's form is open, by key. Empty means none.
  const [adding, setAdding] = useState('');
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
  const available = providers.data?.providers ?? [];
  const chosen = available.find((p) => p.key === adding);

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
        <div className="flex flex-wrap items-center gap-1.5">
          {available.map((provider) => (
            <Button
              key={provider.key}
              variant={provider.key === available[0]?.key ? 'primary' : 'default'}
              onClick={() => setAdding(provider.key)}
              disabled={adding === provider.key}
            >
              <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2}>
                <path d="M12 5v14M5 12h14" />
              </svg>
              {provider.label.replace(/ \(.*\)$/, '')}
            </Button>
          ))}
        </div>
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
        {chosen && (
          <AddConnector
            key={chosen.key}
            provider={chosen}
            onCancel={() => setAdding('')}
            onSaved={(name) => {
              setAdding('');
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
        ) : connectors.length === 0 && !chosen ? (
          <div className="panel">
            <EmptyState
              title="Nothing connected yet"
              hint="Connect an API above to pull its data into the report builder."
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
  //: Keyed by the provider's own field names, so this form does not know what
  //: any particular API calls its credentials.
  const [values, setValues] = useState<Record<string, string>>({});
  const [name, setName] = useState('');
  const [version, setVersion] = useState(provider.default_api_version);
  const [found, setFound] = useState<Discovery | null>(null);
  const [error, setError] = useState<string | null>(null);

  const value = (key: string) => values[key] ?? '';
  const set = (key: string, next: string) =>
    setValues((current) => ({ ...current, [key]: next }));

  const complete = provider.credentials
    .filter((field) => field.required)
    .every((field) => value(field.key).trim().length > 0);

  const discover = useMutation({
    mutationFn: () =>
      api.discoverConnector({
        provider: provider.key,
        token: value('token').trim(),
        api_version: version,
        app_id: value('app_id').trim(),
        app_secret: value('app_secret').trim(),
      }),
    onSuccess: (result) => {
      setFound(result);
      setError(null);
      if (!name && result.account_name) setName(result.account_name);
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
        token: value('token').trim(),
        app_id: value('app_id').trim(),
        app_secret: value('app_secret').trim(),
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
        {provider.where_to_find && (
          <p className="rounded border border-line bg-canvas px-3 py-2 text-[11px] leading-relaxed text-ink-muted">
            <strong className="font-semibold text-ink">Where to find these:</strong>{' '}
            {provider.where_to_find}
          </p>
        )}

        {provider.credentials.map((field) => (
          <CredentialInput
            key={field.key}
            field={field}
            value={value(field.key)}
            onChange={(next) => set(field.key, next)}
          />
        ))}

        <div className="grid gap-2.5 md:grid-cols-[160px_1fr]">
          {provider.default_api_version ? (
            <div>
              <label className="label">API version</label>
              <input
                value={version}
                onChange={(event) => setVersion(event.target.value)}
                className="field font-mono"
              />
            </div>
          ) : (
            <div />
          )}
          <div className="flex items-end">
            <Button disabled={!complete || discover.isPending} onClick={() => discover.mutate()}>
              {discover.isPending
                ? `Asking ${provider.label}…`
                : 'Check what these credentials can reach'}
            </Button>
          </div>
        </div>

        {error && (
          <p className="rounded border border-danger-border bg-danger-soft px-3 py-2 text-xs text-danger">
            {error}
          </p>
        )}

        {found && (
          <DiscoveryPanel
            found={found}
            provider_supports_exchange={provider.supports_token_exchange}
          />
        )}

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

function DiscoveryPanel({
  found,
  provider_supports_exchange,
}: {
  found: Discovery;
  provider_supports_exchange: boolean;
}) {
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
            : 'This credential does not appear to expire.'}
        {soon && ' Syncing will stop then unless it is replaced.'}
      </p>

      {provider_supports_exchange && !found.has_app_credentials && !found.expires_at && (
        <p className="mt-1 text-2xs text-warn">
          No App ID and Secret were given, so the token is stored exactly as pasted.
          Meta reports no expiry, but a token from the Graph API Explorer usually has
          one it does not declare here.
        </p>
      )}
      {provider_supports_exchange && !found.has_app_credentials && found.expires_at && (
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
          {/* The provider's own sentence, which discovery already wrote. Saying
              "no ad accounts or Instagram profiles" to somebody connecting a
              supplier API is how a screen stops being believed. */}
          {found.detail || 'These credentials work, but reach nothing this connector can sync.'}
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
            {connector.sync_interval_minutes} minutes
            {connector.api_version && ` · API ${connector.api_version}`}
            {/* Only where the provider has a second credential at all: telling
                somebody their Shippo connection has "no app secret" invites
                them to go and look for one that does not exist. */}
            {provider?.credentials.some((field) => field.key === 'app_secret') &&
              (connector.has_app_secret ? ' · app secret stored' : ' · no app secret')}
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


function CredentialInput({
  field,
  value,
  onChange,
}: {
  field: CredentialField;
  value: string;
  onChange: (value: string) => void;
}) {
  //: Declared by the provider, not guessed. A 400-character Meta token needs a
  //: box you can read; a key short enough to fit on one line is short enough to
  //: be read over a shoulder, or to be legible in a screenshot.
  const long = field.multiline;

  return (
    <div>
      <label className="label">
        {field.label}
        {!field.required && <span className="ml-1 font-normal text-ink-faint">optional</span>}
      </label>
      {long ? (
        <textarea
          value={value}
          onChange={(event) => onChange(event.target.value)}
          rows={3}
          placeholder={field.placeholder}
          className="field resize-none font-mono text-xs"
          autoComplete="off"
          spellCheck={false}
        />
      ) : (
        <input
          type={field.secret ? 'password' : 'text'}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={field.placeholder}
          className={clsx('field', !field.secret && 'font-mono')}
          autoComplete={field.secret ? 'new-password' : 'off'}
        />
      )}
      {field.help && (
        <p className="mt-0.5 text-[10px] leading-relaxed text-ink-faint">{field.help}</p>
      )}
      {field.secret && !field.help && (
        <p className="mt-0.5 text-[10px] text-ink-faint">
          Encrypted before storage. It is never sent back to this screen.
        </p>
      )}
    </div>
  );
}
