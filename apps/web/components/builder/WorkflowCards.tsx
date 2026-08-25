'use client';

import clsx from 'clsx';

/**
 * The workflow strip from the reference.
 *
 * Counts are derived from the report definition rather than tracked separately,
 * so they cannot drift. Each card scrolls its section into view, which turns a
 * decorative header into navigation.
 */

const CARDS = [
  { key: 'data_sources', label: 'Data Sources', unit: ['Table', 'Tables'], target: 'sources' },
  { key: 'fields_selected', label: 'Fields Selected', unit: ['Field', 'Fields'], target: 'columns' },
  { key: 'relationships', label: 'Relationships', unit: ['Join', 'Joins'], target: 'joins' },
  { key: 'filters', label: 'Filters', unit: ['Filter', 'Filters'], target: 'filters' },
  { key: 'grouping', label: 'Grouping', unit: ['Group', 'Groups'], target: 'grouping' },
  { key: 'sorting', label: 'Sorting', unit: ['Sort', 'Sorts'], target: 'sorting' },
] as const;

const ICONS: Record<string, React.ReactNode> = {
  data_sources: <><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5" /></>,
  fields_selected: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 10h18M9 10v10" /></>,
  relationships: <><circle cx="6" cy="6" r="3" /><circle cx="18" cy="18" r="3" /><path d="M9 6h6a3 3 0 0 1 3 3v6" /></>,
  filters: <path d="M3 4h18l-7 8v7l-4 2v-9z" />,
  grouping: <><path d="M4 6h16M7 12h13M10 18h10" /></>,
  sorting: <><path d="M7 4v16M7 20l-3-3M7 20l3-3" /><path d="M14 7h6M14 12h5M14 17h4" /></>,
};

export function WorkflowCards({
  summary,
  activeSection,
  onNavigate,
}: {
  summary: Record<string, number>;
  activeSection?: string;
  onNavigate: (target: string) => void;
}) {
  return (
    <div className="grid grid-cols-2 gap-2 border-b border-line bg-white px-4 py-2.5
                    sm:grid-cols-3 lg:grid-cols-6">
      {CARDS.map((card) => {
        const count = summary[card.key] ?? 0;
        const unit = count === 1 ? card.unit[0] : card.unit[1];
        const empty = count === 0;
        return (
          <button
            key={card.key}
            type="button"
            onClick={() => onNavigate(card.target)}
            className={clsx(
              'flex items-center gap-2.5 rounded-lg border px-3 py-2 text-left transition-colors',
              activeSection === card.target
                ? 'border-accent-border bg-accent-soft'
                : 'border-line bg-white hover:border-line-strong hover:bg-canvas',
            )}
          >
            <svg
              viewBox="0 0 24 24"
              className={clsx('h-4 w-4 shrink-0', empty ? 'text-ink-faint' : 'text-accent')}
              fill="none"
              stroke="currentColor"
              strokeWidth={1.6}
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              {ICONS[card.key]}
            </svg>
            <span className="min-w-0">
              <span className="block truncate text-xs font-medium text-ink-muted">
                {card.label}
              </span>
              <span
                className={clsx(
                  'block text-sm font-semibold tabular',
                  empty ? 'text-ink-faint' : 'text-ink',
                )}
              >
                {count} {unit}
              </span>
            </span>
          </button>
        );
      })}
    </div>
  );
}
