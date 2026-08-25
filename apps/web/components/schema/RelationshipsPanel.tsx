'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Badge, Button, Skeleton } from '@/components/ui/primitives';
import { api, type RelationshipSuggestion } from '@/lib/api';

/**
 * Relationships (spec 1, 36).
 *
 * Without these the report builder can only ever use one table at a time, which
 * is why a schema with none gets a prominent call to action rather than a quiet
 * empty list.
 *
 * Suggestions are never applied automatically. A wrong relationship does not
 * produce an error -- it produces a plausible, wrong number -- so accepting one
 * is a decision a person makes.
 */
export function RelationshipsPanel({ canManage }: { canManage: boolean }) {
  const queryClient = useQueryClient();
  const [discovering, setDiscovering] = useState(false);
  const [chosen, setChosen] = useState<Set<string>>(new Set());

  const existing = useQuery({
    queryKey: ['relationships'],
    queryFn: api.relationships,
  });

  const suggestions = useQuery({
    queryKey: ['relationship-suggestions'],
    queryFn: api.relationshipSuggestions,
    enabled: discovering,
  });

  const accept = useMutation({
    mutationFn: (items: RelationshipSuggestion[]) => api.acceptRelationships(items),
    onSuccess: () => {
      setDiscovering(false);
      setChosen(new Set());
      queryClient.invalidateQueries({ queryKey: ['relationships'] });
      queryClient.invalidateQueries({ queryKey: ['overview'] });
      queryClient.invalidateQueries({ queryKey: ['relationship-suggestions'] });
    },
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteRelationship(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['relationships'] });
      queryClient.invalidateQueries({ queryKey: ['overview'] });
    },
  });

  const relationships = existing.data?.relationships ?? [];
  const found = suggestions.data?.suggestions ?? [];
  const key = (s: RelationshipSuggestion) =>
    `${s.right_table}.${s.right_column}->${s.left_table}.${s.left_column}`;

  return (
    <section className="panel">
      <div className="panel-header">
        <div className="flex items-center gap-2">
          <h2 className="panel-title">Relationships</h2>
          <span className="text-2xs text-ink-faint">
            {existing.isLoading ? '…' : `${relationships.length} in use`}
          </span>
        </div>
        {canManage && !discovering && (
          <Button size="sm" onClick={() => setDiscovering(true)}>
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={1.8}>
              <circle cx="11" cy="11" r="7" />
              <path d="m20 20-3.5-3.5" />
            </svg>
            Discover relationships
          </Button>
        )}
      </div>

      {/* A schema with no relationships cannot join anything at all. */}
      {!existing.isLoading && relationships.length === 0 && !discovering && (
        <div className="m-4 rounded-lg border border-warn-border bg-warn-soft px-4 py-3">
          <p className="text-sm font-semibold text-warn">
            No relationships are defined, so reports can only use one table at a time
          </p>
          <p className="mt-1 text-xs text-ink-muted">
            This schema declares no foreign keys — PostgreSQL views cannot carry them.
            Relationships have to be defined here instead. They are stored by this
            application and never written to your database.
          </p>
          {canManage ? (
            <Button size="sm" variant="primary" className="mt-2.5"
                    onClick={() => setDiscovering(true)}>
              Discover relationships
            </Button>
          ) : (
            <p className="mt-2 text-xs text-ink-muted">
              Ask an administrator to define them.
            </p>
          )}
        </div>
      )}

      {discovering && (
        <div className="border-b border-line bg-accent-soft/40 px-4 py-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <p className="text-sm font-semibold">
                {suggestions.isLoading
                  ? 'Looking for relationships…'
                  : `${found.length} relationship${found.length === 1 ? '' : 's'} found`}
              </p>
              <p className="text-xs text-ink-muted">
                Matched by naming convention and column type. Review before accepting —
                a wrong link produces a plausible wrong number, not an error.
              </p>
            </div>
            <div className="flex items-center gap-1.5">
              <Button size="sm" onClick={() => { setDiscovering(false); setChosen(new Set()); }}>
                Cancel
              </Button>
              <Button size="sm"
                      onClick={() => setChosen(new Set(found.map(key)))}
                      disabled={found.length === 0}>
                Select all
              </Button>
              <Button
                size="sm"
                variant="primary"
                disabled={chosen.size === 0 || accept.isPending}
                onClick={() => accept.mutate(found.filter((s) => chosen.has(key(s))))}
              >
                {accept.isPending ? 'Saving…' : `Accept ${chosen.size || ''}`.trim()}
              </Button>
            </div>
          </div>

          {suggestions.isLoading && <Skeleton className="mt-3 h-40 w-full" />}

          {!suggestions.isLoading && found.length === 0 && (
            <p className="mt-3 text-xs text-ink-muted">
              Nothing matched the naming convention. Relationships can still be defined by
              hand once that screen exists.
            </p>
          )}

          {found.length > 0 && (
            <div className="mt-3 max-h-[320px] overflow-y-auto rounded-lg border border-line bg-white">
              {found.map((suggestion) => {
                const id = key(suggestion);
                return (
                  <label
                    key={id}
                    className="flex cursor-pointer items-start gap-2.5 border-b border-line/70
                               px-3 py-2 last:border-0 hover:bg-accent-soft/60"
                  >
                    <input
                      type="checkbox"
                      checked={chosen.has(id)}
                      onChange={(event) =>
                        setChosen((current) => {
                          const next = new Set(current);
                          if (event.target.checked) next.add(id);
                          else next.delete(id);
                          return next;
                        })
                      }
                      className="mt-0.5 h-3.5 w-3.5 shrink-0 rounded-[3px] border-line-strong accent-accent"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block font-mono text-xs text-ink">
                        {suggestion.right_table}.{suggestion.right_column}
                        <span className="mx-1.5 text-ink-faint">→</span>
                        {suggestion.left_table}.{suggestion.left_column}
                      </span>
                      <span className="mt-0.5 block text-2xs text-ink-faint">
                        {suggestion.reason}
                      </span>
                    </span>
                    <Badge tone={suggestion.confidence >= 0.85 ? 'good' : 'warn'}>
                      {Math.round(suggestion.confidence * 100)}%
                    </Badge>
                  </label>
                );
              })}
            </div>
          )}
        </div>
      )}

      {relationships.length > 0 && (
        <div className="max-h-[420px] overflow-y-auto">
          <table className="striped w-full border-collapse">
            <thead className="sticky top-0 z-10 bg-canvas">
              <tr className="border-b border-line text-left text-2xs uppercase text-ink-faint">
                <th className="px-4 py-2 font-medium">From</th>
                <th className="px-4 py-2 font-medium">To</th>
                <th className="px-4 py-2 font-medium">Cardinality</th>
                <th className="px-4 py-2 font-medium">Source</th>
                <th className="w-10 px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {relationships.map((relationship) => (
                <tr key={relationship.id} className="border-b border-line/60 last:border-0">
                  <td className="px-4 py-1.5 font-mono text-xs">
                    {relationship.right_table}.{relationship.right_column}
                  </td>
                  <td className="px-4 py-1.5 font-mono text-xs">
                    {relationship.left_table}.{relationship.left_column}
                  </td>
                  <td className="px-4 py-1.5 text-xs tabular">{relationship.cardinality}</td>
                  <td className="px-4 py-1.5">
                    {relationship.source === 'physical' ? (
                      <Badge tone="good">Foreign key</Badge>
                    ) : (
                      <Badge tone="warn">
                        {relationship.source === 'inferred' ? 'Discovered' : 'Manual'}
                      </Badge>
                    )}
                  </td>
                  <td className="px-4 py-1.5 text-right">
                    {canManage && relationship.source !== 'physical' && (
                      <button
                        type="button"
                        title="Remove this relationship"
                        onClick={() => remove.mutate(relationship.id)}
                        className="text-ink-faint transition-colors hover:text-danger"
                      >
                        <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none"
                             stroke="currentColor" strokeWidth={1.8}>
                          <path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" />
                        </svg>
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
