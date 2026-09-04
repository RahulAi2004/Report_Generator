'use client';

import clsx from 'clsx';
import { useRouter } from 'next/navigation';
import { useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Badge, Button, Checkbox, EmptyState, Select, Skeleton } from '@/components/ui/primitives';
import { api, ApiError } from '@/lib/api';
import { useSchemaTables } from '@/components/dashboards/FieldPicker';
import { useBuilder } from '@/store/builder';
import type { Suggestion } from '@/lib/ai-types';

/**
 * AI Suggestions.
 *
 * The AI proposes reports and turns questions into them. It is given the shape
 * of the database and never its contents, and everything it returns has already
 * been compiled by the server — so "will this work" is answered before anybody
 * opens it, not after.
 *
 * Nothing here runs a report. A suggestion is a proposal; accepting one opens it
 * in the builder, where the person decides.
 */
export default function AiSuggestionsPage() {
  const router = useRouter();
  const builder = useBuilder();
  const { tables } = useSchemaTables();

  const [question, setQuestion] = useState('');
  const [interest, setInterest] = useState('');
  const [focus, setFocus] = useState<string[]>([]);
  const [results, setResults] = useState<Suggestion[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showContext, setShowContext] = useState(false);
  const [showSettings, setShowSettings] = useState(false);

  const status = useQuery({ queryKey: ['ai-status'], queryFn: api.aiStatus });

  const suggest = useMutation({
    mutationFn: () => api.aiSuggest({ tables: focus, interest }),
    onSuccess: (body) => { setResults(body.suggestions); setError(null); },
    onError: (failure) =>
      setError(failure instanceof ApiError ? failure.message : String(failure)),
  });

  const ask = useMutation({
    mutationFn: () => api.aiAsk({ question, tables: focus }),
    onSuccess: (body) => { setResults([body]); setError(null); },
    onError: (failure) =>
      setError(failure instanceof ApiError ? failure.message : String(failure)),
  });

  const busy = suggest.isPending || ask.isPending;
  const available = status.data?.available ?? false;

  function open(suggestion: Suggestion) {
    // Loaded as an unsaved draft: the AI proposes, the person decides.
    builder.loadReport(null, suggestion.title, suggestion.definition);
    router.push('/reports/builder');
  }

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-canvas">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-line bg-white px-4 py-3">
        <div>
          <h1 className="text-md font-semibold text-ink">AI Suggestions</h1>
          <p className="text-xs text-ink-muted">
            Ask for a report in your own words, or let the AI propose ones worth
            building. It sees the shape of your database — never the data in it.
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          <Button size="sm" onClick={() => setShowContext((value) => !value)}>
            What the AI is sent
          </Button>
          {status.data?.can_configure && (
            <Button size="sm" onClick={() => setShowSettings((value) => !value)}>
              Settings
            </Button>
          )}
        </div>
      </header>

      {status.isSuccess && !available && (
        <p className="border-b border-warn-border bg-warn-soft px-4 py-2 text-xs text-warn">
          {status.data.can_configure
            ? 'No AI provider is configured yet. Open Settings and add an API key.'
            : 'The AI is not set up on this installation. An administrator can add a provider.'}
        </p>
      )}

      <div className="flex-1 space-y-3 p-4">
        {showSettings && status.data?.can_configure && (
          <SettingsPanel onClose={() => { setShowSettings(false); status.refetch(); }} />
        )}
        {showContext && <ContextPanel onClose={() => setShowContext(false)} />}

        <section className="panel">
          <div className="panel-header">
            <h2 className="panel-title">Ask</h2>
            {status.data?.model && (
              <span className="text-2xs text-ink-faint">{status.data.model}</span>
            )}
          </div>

          <div className="space-y-3 p-4">
            <div>
              <label className="label">What do you want to know?</label>
              <textarea
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                rows={2}
                placeholder="Which customers spent the most this year, and on what?"
                className="field resize-none"
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
                    if (question.trim() && available) ask.mutate();
                  }
                }}
              />
            </div>

            <TableFocus tables={tables} focus={focus} setFocus={setFocus} />

            <div className="flex flex-wrap items-end justify-between gap-2 border-t border-line pt-3">
              <div className="min-w-[240px] flex-1">
                <label className="label">
                  Or let it suggest
                  <span className="ml-1 font-normal text-ink-faint">
                    optionally, what you care about
                  </span>
                </label>
                <input
                  value={interest}
                  onChange={(event) => setInterest(event.target.value)}
                  placeholder="Cash flow, late deliveries, which products sell"
                  className="field"
                />
              </div>
              <div className="flex items-center gap-1.5">
                <Button
                  disabled={!available || busy}
                  onClick={() => suggest.mutate()}
                >
                  {suggest.isPending ? 'Thinking…' : 'Suggest reports'}
                </Button>
                <Button
                  variant="primary"
                  disabled={!available || busy || !question.trim()}
                  onClick={() => ask.mutate()}
                >
                  {ask.isPending ? 'Thinking…' : 'Build this report'}
                </Button>
              </div>
            </div>
          </div>
        </section>

        {error && (
          <p className="rounded-lg border border-danger-border bg-danger-soft px-4 py-2.5 text-xs text-danger">
            {error}
          </p>
        )}

        {busy && (
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, index) => (
              <Skeleton key={index} className="h-28 w-full" />
            ))}
          </div>
        )}

        {!busy && results.length === 0 && !error && (
          <div className="panel">
            <EmptyState
              title="Nothing suggested yet"
              hint="Ask a question, or press Suggest reports and see what it finds in your data."
            />
          </div>
        )}

        {!busy && results.map((suggestion, index) => (
          <SuggestionCard
            key={`${suggestion.title}-${index}`}
            suggestion={suggestion}
            onOpen={() => open(suggestion)}
          />
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
function TableFocus({
  tables,
  focus,
  setFocus,
}: {
  tables: { name: string; label: string; category: string }[];
  focus: string[];
  setFocus: (value: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const grouped = useMemo(() => {
    const map = new Map<string, typeof tables>();
    for (const table of tables) {
      map.set(table.category, [...(map.get(table.category) ?? []), table]);
    }
    return [...map.entries()].sort();
  }, [tables]);

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="text-xs font-medium text-accent hover:underline"
      >
        {focus.length === 0
          ? 'Using every table you can read'
          : `Narrowed to ${focus.length} table${focus.length === 1 ? '' : 's'}`}
        {' · '}
        {open ? 'hide' : 'change'}
      </button>
      <p className="mt-0.5 text-[10px] text-ink-faint">
        Narrowing is usually better: given sixty tables the AI suggests something
        about all of them, given three it suggests something about those three.
      </p>

      {open && (
        <div className="mt-2 max-h-52 overflow-y-auto rounded border border-line bg-white p-2">
          {focus.length > 0 && (
            <button
              type="button"
              onClick={() => setFocus([])}
              className="mb-1.5 text-2xs text-ink-muted hover:text-ink"
            >
              Clear selection
            </button>
          )}
          {grouped.map(([category, items]) => (
            <div key={category} className="mb-2">
              <p className="label mb-0.5">{category}</p>
              <div className="grid gap-x-4 sm:grid-cols-2">
                {items.map((table) => (
                  <Checkbox
                    key={table.name}
                    checked={focus.includes(table.name)}
                    onChange={(checked) =>
                      setFocus(
                        checked
                          ? [...focus, table.name]
                          : focus.filter((name) => name !== table.name),
                      )
                    }
                    label={<span className="truncate text-xs">{table.label}</span>}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function SuggestionCard({
  suggestion,
  onOpen,
}: {
  suggestion: Suggestion;
  onOpen: () => void;
}) {
  const [showDefinition, setShowDefinition] = useState(false);

  return (
    <section
      className={clsx(
        'panel p-4',
        !suggestion.runnable && 'border-warn-border',
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <h3 className="text-sm font-semibold text-ink">{suggestion.title}</h3>
            {suggestion.runnable ? (
              <Badge tone="good">Ready to run</Badge>
            ) : (
              <Badge tone="warn">Needs a change</Badge>
            )}
            {suggestion.confidence && (
              <Badge tone={suggestion.confidence === 'high' ? 'accent' : 'warn'}>
                {suggestion.confidence} confidence
              </Badge>
            )}
          </div>
          {suggestion.why && (
            <p className="mt-1 text-xs text-ink-muted">{suggestion.why}</p>
          )}
        </div>

        <Button size="sm" variant={suggestion.runnable ? 'primary' : 'default'} onClick={onOpen}>
          Open in builder
        </Button>
      </div>

      {suggestion.assumptions.length > 0 && (
        <div className="mt-2.5 rounded border border-warn-border bg-warn-soft px-3 py-2">
          <p className="text-2xs font-semibold text-warn">What it assumed</p>
          <ul className="mt-0.5 space-y-0.5">
            {suggestion.assumptions.map((assumption) => (
              <li key={assumption} className="text-2xs text-warn">
                · {assumption}
              </li>
            ))}
          </ul>
        </div>
      )}

      {!suggestion.runnable && suggestion.problems.length > 0 && (
        <div className="mt-2.5 rounded border border-danger-border bg-danger-soft px-3 py-2">
          <p className="text-2xs font-semibold text-danger">
            Why it will not run as it stands
          </p>
          <ul className="mt-0.5 space-y-0.5">
            {suggestion.problems.map((problem) => (
              <li key={problem} className="text-2xs text-danger">· {problem}</li>
            ))}
          </ul>
          <p className="mt-1 text-2xs text-ink-muted">
            Open it anyway — the builder will show the same problem next to the
            field that causes it.
          </p>
        </div>
      )}

      <div className="mt-2.5 flex flex-wrap items-center gap-3 border-t border-line pt-2">
        {Object.entries(suggestion.summary).map(([key, value]) => (
          <span key={key} className="text-2xs text-ink-faint">
            <span className="tabular font-medium text-ink-muted">{value}</span>{' '}
            {key.replace(/_/g, ' ')}
          </span>
        ))}
        <button
          type="button"
          onClick={() => setShowDefinition((value) => !value)}
          className="ml-auto text-2xs text-accent hover:underline"
        >
          {showDefinition ? 'Hide' : 'Show'} what it built
        </button>
      </div>

      {showDefinition && (
        <pre className="mt-2 max-h-64 overflow-auto rounded border border-line bg-canvas p-2.5 font-mono text-[10px] leading-relaxed text-ink-muted">
          {JSON.stringify(suggestion.definition, null, 2)}
        </pre>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
function ContextPanel({ onClose }: { onClose: () => void }) {
  const context = useQuery({ queryKey: ['ai-context'], queryFn: api.aiContext });

  return (
    <section className="panel">
      <div className="panel-header">
        <h2 className="panel-title">Everything the AI is sent</h2>
        <Button size="sm" onClick={onClose}>Close</Button>
      </div>
      <div className="p-4">
        {context.isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : context.isError ? (
          <p className="text-xs text-danger">{(context.error as Error).message}</p>
        ) : (
          <>
            <p className="mb-2 text-xs text-ink-muted">
              {context.data?.note} It is shown here rather than described, because
              &ldquo;it only sees the schema&rdquo; is a claim and this is the thing itself.
            </p>
            <p className="mb-2 text-2xs text-ink-faint">
              {context.data?.tables} tables · {context.data?.characters.toLocaleString()} characters
            </p>
            <pre className="max-h-[420px] overflow-auto rounded border border-line bg-canvas p-3 font-mono text-[10px] leading-relaxed text-ink-muted">
              {context.data?.context}
            </pre>
          </>
        )}
      </div>
    </section>
  );
}

function SettingsPanel({ onClose }: { onClose: () => void }) {
  const settings = useQuery({ queryKey: ['ai-settings'], queryFn: api.aiSettings });
  const [baseUrl, setBaseUrl] = useState('');
  const [model, setModel] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [error, setError] = useState<string | null>(null);

  const [models, setModels] = useState<string[] | null>(null);
  const current = settings.data;

  const test = useMutation({
    mutationFn: () =>
      api.aiModels({
        base_url: baseUrl || current?.base_url,
        api_key: apiKey || undefined,
      }),
    onSuccess: (body) => {
      setModels(body.models);
      setError(null);
      // Keep whatever is already chosen if the provider still offers it.
      if (!body.models.includes(model || current?.model || '')) {
        setModel(body.models[0] ?? '');
      }
    },
    onError: (failure) =>
      setError(failure instanceof ApiError ? failure.message : String(failure)),
  });

  const save = useMutation({
    mutationFn: () =>
      api.saveAiSettings({
        base_url: baseUrl || current?.base_url || '',
        model: model || current?.model || '',
        api_key: apiKey || undefined,
        enabled: true,
      }),
    onSuccess: () => { setApiKey(''); onClose(); },
    onError: (failure) =>
      setError(failure instanceof ApiError ? failure.message : String(failure)),
  });

  return (
    <section className="panel">
      <div className="panel-header">
        <h2 className="panel-title">AI provider</h2>
        <Button size="sm" onClick={onClose}>Cancel</Button>
      </div>
      <div className="space-y-3 p-4">
        {settings.isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : (
          <>
            <div className="grid gap-2.5 md:grid-cols-2">
              <div>
                <label className="label">API base URL</label>
                <input
                  value={baseUrl || current?.base_url || ''}
                  onChange={(event) => setBaseUrl(event.target.value)}
                  className="field font-mono text-xs"
                />
                <p className="mt-0.5 text-[10px] text-ink-faint">
                  Groq&rsquo;s API, or anything that speaks the same dialect.
                </p>
              </div>
              <div>
                <label className="label">Model</label>
                {models && models.length > 0 ? (
                  <Select
                    value={model || current?.model || ''}
                    onChange={setModel}
                    options={models.map((name) => ({ value: name, label: name }))}
                    className="font-mono text-xs"
                  />
                ) : (
                  <input
                    value={model || current?.model || ''}
                    onChange={(event) => setModel(event.target.value)}
                    className="field font-mono text-xs"
                  />
                )}
                <p className="mt-0.5 text-[10px] text-ink-faint">
                  {models
                    ? `${models.length} models this key can use.`
                    : 'Test the key to list the models it can actually use.'}
                </p>
              </div>
            </div>

            <div>
              <label className="label">
                API key
                {current?.has_api_key && (
                  <span className="ml-1 font-normal text-good">a key is stored</span>
                )}
              </label>
              <input
                type="password"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder={current?.has_api_key ? 'Leave blank to keep the stored key' : ''}
                autoComplete="new-password"
                className="field"
              />
              <p className="mt-0.5 text-[10px] text-ink-faint">
                Encrypted before storage. It is never sent back to this screen.
              </p>
            </div>

            {error && (
              <p className="rounded border border-danger-border bg-danger-soft px-3 py-2 text-xs text-danger">
                {error}
              </p>
            )}

            <div className="flex justify-end gap-2 border-t border-line pt-3">
              <Button onClick={onClose}>Cancel</Button>
              <Button
                disabled={test.isPending || (!current?.has_api_key && !apiKey)}
                onClick={() => test.mutate()}
              >
                {test.isPending ? 'Testing…' : 'Test key & list models'}
              </Button>
              <Button
                variant="primary"
                disabled={save.isPending || (!current?.has_api_key && !apiKey)}
                onClick={() => save.mutate()}
              >
                {save.isPending ? 'Saving…' : 'Save'}
              </Button>
            </div>
          </>
        )}
      </div>
    </section>
  );
}
