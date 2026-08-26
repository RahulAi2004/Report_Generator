'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Badge, Button, EmptyState, IconButton, Skeleton } from '@/components/ui/primitives';
import { api } from '@/lib/api';
import { useDashboard } from '@/store/dashboard';

/**
 * Saved dashboards, grouped by the app they belong to.
 *
 * The same grouping the reports portal uses, for the same reason: someone
 * looking for the customer dashboard looks under CRM, not through one long
 * alphabetical list.
 */
export default function DashboardsPage() {
  const router = useRouter();
  const client = useQueryClient();
  const reset = useDashboard((state) => state.reset);

  const dashboards = useQuery({
    queryKey: ['dashboards'],
    queryFn: () => api.listDashboards(),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteDashboard(id),
    onSuccess: () => client.invalidateQueries({ queryKey: ['dashboards'] }),
  });

  const grouped = new Map<string, typeof items>();
  const items = dashboards.data?.dashboards ?? [];
  for (const item of items) {
    const key = item.app ?? 'Unfiled';
    grouped.set(key, [...(grouped.get(key) ?? []), item]);
  }

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-canvas">
      <header className="flex items-center justify-between gap-3 border-b border-line bg-white px-4 py-3">
        <div>
          <h1 className="text-md font-semibold text-ink">Dashboards</h1>
          <p className="text-xs text-ink-muted">
            Metric cards and reports on one screen, filtered together.
          </p>
        </div>
        <Button
          variant="primary"
          size="sm"
          onClick={() => {
            reset();
            router.push('/dashboards/builder');
          }}
        >
          <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2}>
            <path d="M12 5v14M5 12h14" />
          </svg>
          New Dashboard
        </Button>
      </header>

      <div className="flex-1 p-4">
        {dashboards.isLoading ? (
          <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 3 }).map((_, index) => (
              <Skeleton key={index} className="h-24 w-full" />
            ))}
          </div>
        ) : items.length === 0 ? (
          <div className="panel">
            <EmptyState
              title="No dashboards yet"
              hint="Build one and it appears here, grouped by the app it belongs to."
            />
          </div>
        ) : (
          <div className="space-y-5">
            {[...grouped.entries()].map(([app, group]) => (
              <section key={app}>
                <h2 className="panel-title mb-2">{app}</h2>
                <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
                  {group.map((item) => (
                    <div
                      key={item.id}
                      className="panel flex flex-col justify-between p-3 transition-colors hover:border-line-strong"
                    >
                      <div>
                        <div className="flex items-start justify-between gap-2">
                          <Link
                            href={`/dashboards/builder?id=${item.id}`}
                            className="min-w-0 flex-1 text-sm font-semibold text-ink hover:text-accent"
                          >
                            {item.name}
                          </Link>
                          <IconButton
                            title="Delete"
                            onClick={() => remove.mutate(item.id)}
                          >
                            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={1.8}>
                              <path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13" />
                            </svg>
                          </IconButton>
                        </div>
                        <p className="mt-0.5 line-clamp-2 text-2xs text-ink-muted">
                          {item.description || item.module || 'No description'}
                        </p>
                      </div>
                      <div className="mt-2.5 flex items-center gap-1.5">
                        {item.is_default && <Badge tone="accent">Default</Badge>}
                        <Badge>{item.visibility}</Badge>
                        <span className="ml-auto text-[10px] text-ink-faint">
                          {new Date(item.updated_at).toLocaleDateString()}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
