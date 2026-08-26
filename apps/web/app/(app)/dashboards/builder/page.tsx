'use client';

import { useMutation, useQuery } from '@tanstack/react-query';
import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useEffect, useMemo, useState } from 'react';
import { Badge, Button, Select } from '@/components/ui/primitives';
import { PlacementBar, TimeRangeBar } from '@/components/dashboards/DashboardHeader';
import { FiltersPanel, SettingsPanel } from '@/components/dashboards/FiltersPanel';
import { MetricCardsPanel } from '@/components/dashboards/MetricCardsPanel';
import { MetricStrip } from '@/components/dashboards/MetricStrip';
import { ReportPanels } from '@/components/dashboards/ReportPanels';
import { api, ApiError } from '@/lib/api';
import { metricIsComplete, useDashboard } from '@/store/dashboard';

/**
 * Dashboard Builder.
 *
 * The layout is deliberate: controls on the left and right, and the live
 * preview between them. Every edit re-runs the preview, so the number a card
 * produces is visible while the card is being defined rather than after it is
 * saved -- which is the only way a mistake in an aggregation gets noticed.
 */
export default function DashboardBuilderPage() {
  return (
    <Suspense fallback={<div className="p-6 text-sm text-ink-muted">Loading…</div>}>
      <Builder />
    </Suspense>
  );
}

function Builder() {
  const router = useRouter();
  const params = useSearchParams();
  const store = useDashboard();
  const { definition, name, dashboardId, dirty } = store;

  const [error, setError] = useState<string | null>(null);
  const [previewOnly, setPreviewOnly] = useState(false);

  const options = useQuery({
    queryKey: ['dashboard-options'],
    queryFn: api.dashboardOptions,
    staleTime: 300_000,
  });

  // Load an existing dashboard, or start a copy of one.
  const openId = params.get('id');
  const copyId = params.get('copy');
  const existing = useQuery({
    queryKey: ['dashboard', openId ?? copyId],
    queryFn: () => api.getDashboard((openId ?? copyId)!),
    enabled: Boolean(openId ?? copyId),
  });

  useEffect(() => {
    if (!existing.data) return;
    const copying = Boolean(copyId);
    store.load(
      copying ? null : existing.data.id,
      copying ? `${existing.data.name} (copy)` : existing.data.name,
      existing.data.definition,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [existing.data, copyId]);

  useEffect(() => {
    if (!openId && !copyId) store.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Only cards that name something to measure are sent: an unfinished card
  // would come back as an error the user has not made yet.
  const runnable = useMemo(
    () => ({ ...definition, metrics: definition.metrics.filter(metricIsComplete) }),
    [definition],
  );

  const preview = useQuery({
    queryKey: ['dashboard-preview', runnable],
    queryFn: () => api.dashboardPreview(runnable),
    enabled: runnable.metrics.length > 0,
    retry: false,
  });

  const save = useMutation({
    mutationFn: (close: boolean) => {
      const body = {
        name: name.trim(),
        description: store.description.trim() || undefined,
        visibility: store.visibility,
        definition,
      };
      const request = dashboardId
        ? api.updateDashboard(dashboardId, body)
        : api.createDashboard(body);
      return request.then((saved) => ({ saved, close }));
    },
    onSuccess: ({ saved, close }) => {
      setError(null);
      if (close) router.push('/dashboards');
      else if (!dashboardId) router.replace(`/dashboards/builder?id=${saved.id}`);
    },
    onError: (failure) =>
      setError(failure instanceof ApiError ? failure.message : String(failure)),
  });

  const incomplete = definition.metrics.filter((card) => !metricIsComplete(card)).length;
  const canSave = name.trim().length > 0 && Boolean(definition.app);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Top bar */}
      <header className="flex items-center justify-between gap-3 border-b border-line bg-white px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-2.5">
          <button
            type="button"
            onClick={() => router.push('/dashboards')}
            className="rounded p-1 text-ink-muted hover:bg-canvas hover:text-ink"
            aria-label="Back to dashboards"
          >
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2}>
              <path d="M19 12H5M11 18l-6-6 6-6" />
            </svg>
          </button>
          <div className="min-w-0">
            <h1 className="truncate text-md font-semibold text-ink">Dashboard Builder</h1>
            <p className="truncate text-2xs text-ink-muted">
              Create and customize your dashboard
            </p>
          </div>
          {dirty && <Badge tone="warn">Unsaved</Badge>}
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          <Button size="sm" onClick={() => router.push('/dashboards')}>
            Cancel
          </Button>
          <Button
            size="sm"
            disabled={!canSave || save.isPending}
            onClick={() => save.mutate(false)}
          >
            Save
          </Button>
          <Button
            size="sm"
            disabled={!canSave || save.isPending}
            onClick={() => {
              store.load(null, `${name} (copy)`, definition);
              save.mutate(false);
            }}
          >
            Save As
          </Button>
          <Button size="sm" onClick={() => setPreviewOnly((value) => !value)}>
            {previewOnly ? 'Edit' : 'Preview'}
          </Button>
          <Button
            size="sm"
            variant="primary"
            disabled={!canSave || save.isPending}
            onClick={() => save.mutate(true)}
          >
            Save &amp; Close
          </Button>
        </div>
      </header>

      {error && (
        <p className="border-b border-danger-border bg-danger-soft px-4 py-1.5 text-xs text-danger">
          {error}
        </p>
      )}
      {!canSave && (
        <p className="border-b border-line bg-canvas px-4 py-1.5 text-2xs text-ink-muted">
          A name and an app are needed before this dashboard can be saved.
        </p>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto bg-canvas p-3">
        <div className="space-y-2.5">
          {!previewOnly && (
            <>
              <PlacementBar options={options.data} />
              <TimeRangeBar options={options.data} />
            </>
          )}
          {previewOnly && definition.settings.show_time_range && (
            <TimeRangeBar options={options.data} />
          )}

          <div
            className={
              previewOnly
                ? 'space-y-2.5'
                : 'grid gap-2.5 xl:grid-cols-[300px_minmax(0,1fr)_260px]'
            }
          >
            {!previewOnly && (
              <div className="min-h-0">
                <MetricCardsPanel />
              </div>
            )}

            <div className="min-w-0 space-y-2.5">
              <section className="panel">
                <div className="panel-header">
                  <h2 className="panel-title">Live Preview</h2>
                  <div className="flex items-center gap-2">
                    {incomplete > 0 && (
                      <span className="text-2xs text-warn">
                        {incomplete} card{incomplete === 1 ? '' : 's'} not configured
                      </span>
                    )}
                    {preview.data && (
                      <span className="text-2xs text-ink-faint">
                        {preview.data.time_range.label} · {preview.data.duration_ms}ms
                      </span>
                    )}
                    {definition.settings.show_refresh && (
                      <Button size="sm" onClick={() => preview.refetch()}>
                        <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth={2}>
                          <path d="M21 12a9 9 0 1 1-2.6-6.4M21 3v6h-6" />
                        </svg>
                        Refresh
                      </Button>
                    )}
                  </div>
                </div>
                <div className="p-3">
                  {preview.isError ? (
                    <p className="text-xs text-danger">
                      {(preview.error as Error).message}
                    </p>
                  ) : (
                    <MetricStrip
                      metrics={preview.data?.metrics ?? []}
                      loading={preview.isFetching}
                      placeholderCount={runnable.metrics.length || 4}
                      selectedId={store.selectedMetricId}
                      onSelect={store.selectMetric}
                    />
                  )}
                </div>
              </section>

              <ReportPanels
                definition={definition}
                options={options.data}
                editable={!previewOnly}
              />
            </div>

            {!previewOnly && (
              <div className="space-y-2.5">
                <FiltersPanel />
                <SettingsPanel />
                <VisibilityPanel />
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function VisibilityPanel() {
  const { visibility, setVisibility, description, setDescription } = useDashboard();
  return (
    <section className="panel">
      <div className="panel-header">
        <h2 className="panel-title">Access</h2>
      </div>
      <div className="space-y-2 p-3">
        <div>
          <label className="label">Who can see it</label>
          <Select
            value={visibility}
            onChange={(value) => setVisibility(value as 'private' | 'team' | 'organization')}
            options={[
              { value: 'private', label: 'Only me' },
              { value: 'team', label: 'My team' },
              { value: 'organization', label: 'Everyone in the organisation' },
            ]}
          />
        </div>
        <div>
          <label className="label">Description</label>
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            rows={2}
            placeholder="What this dashboard is for"
            className="field resize-none"
          />
        </div>
      </div>
    </section>
  );
}
