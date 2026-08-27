'use client';

import clsx from 'clsx';

/**
 * The section tabs across the top of the board.
 *
 * Sections come from the taxonomy reports are filed into, so a tab exists
 * because somewhere a report can be filed there -- not because a list of tab
 * names was written down separately and left to drift.
 */

const stroke = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.7,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
};

const glyph = (path: React.ReactNode) => (
  <svg viewBox="0 0 24 24" className="h-[15px] w-[15px]" {...stroke}>
    {path}
  </svg>
);

/** A recognisable icon where the section name suggests one, a page otherwise. */
const ICONS: Record<string, React.ReactNode> = {
  leads: glyph(<><circle cx="9" cy="8" r="3.2" /><path d="M3 20c0-3.3 2.7-5 6-5s6 1.7 6 5" /><path d="M17 5l2 2 3-3" /></>),
  contacts: glyph(<><circle cx="9" cy="8" r="3.2" /><path d="M3 20c0-3.3 2.7-5 6-5s6 1.7 6 5" /><path d="M17 9h5M17 13h5" /></>),
  customers: glyph(<><circle cx="9" cy="8" r="3.2" /><path d="M3 20c0-3.3 2.7-5 6-5s6 1.7 6 5" /><path d="M16 6.5a3 3 0 0 1 0 6M17.5 20c0-2.4-.9-4-2.2-5" /></>),
  quotations: glyph(<><path d="M6 3h9l4 4v14H6z" /><path d="M15 3v4h4" /><path d="M9 12h6M9 16h4" /></>),
  invoices: glyph(<><rect x="4" y="3" width="16" height="18" rx="2" /><path d="M8 8h8M8 12h8M8 16h4" /></>),
  orders: glyph(<><circle cx="9" cy="19" r="1.4" /><circle cx="17" cy="19" r="1.4" /><path d="M3 4h2l2.2 10.4a1.5 1.5 0 0 0 1.5 1.2h7.9a1.5 1.5 0 0 0 1.5-1.2L20 8H6" /></>),
  payments: glyph(<><rect x="2.5" y="6" width="19" height="12" rx="2" /><path d="M2.5 10h19" /><path d="M6 14.5h3" /></>),
  receivables: glyph(<><circle cx="12" cy="12" r="9" /><path d="M12 7v10M14.5 9.5c0-1-1.1-1.6-2.5-1.6s-2.5.6-2.5 1.6 1 1.4 2.5 1.8 2.7.9 2.7 2-1.2 1.8-2.7 1.8-2.7-.7-2.7-1.7" /></>),
  shipments: glyph(<><path d="M2.5 7.5 12 3l9.5 4.5v9L12 21l-9.5-4.5z" /><path d="M2.5 7.5 12 12l9.5-4.5M12 12v9" /></>),
  products: glyph(<><rect x="3" y="7" width="18" height="13" rx="2" /><path d="M8 7V5a4 4 0 0 1 8 0v2" /></>),
  suppliers: glyph(<><rect x="2.5" y="8" width="12" height="9" rx="1.5" /><path d="M14.5 11h4l3 3v3h-7z" /><circle cx="6.5" cy="18" r="1.6" /><circle cx="17.5" cy="18" r="1.6" /></>),
  artwork: glyph(<><circle cx="12" cy="12" r="9" /><circle cx="9" cy="9.5" r="1.2" /><circle cx="15" cy="9.5" r="1.2" /><path d="M12 21a3 3 0 0 1 0-6 2 2 0 0 0 0-4" /></>),
  production: glyph(<><path d="M4 20V10l5 3V10l5 3V6l6 4v10z" /></>),
  activity: glyph(<><path d="M3 12h4l3 8 4-16 3 8h4" /></>),
  reconciliation: glyph(<><path d="M4 8h11l-3-3M20 16H9l3 3" /></>),
};

function iconFor(section: string): React.ReactNode {
  const key = section.toLowerCase();
  for (const [name, node] of Object.entries(ICONS)) {
    if (key.includes(name.replace(/s$/, ''))) return node;
  }
  return glyph(<><rect x="4" y="3" width="16" height="18" rx="2" /><path d="M8 9h8M8 13h8M8 17h5" /></>);
}

export interface Tab {
  /** Empty for the "All" tab. */
  module: string;
  section: string;
  label: string;
}

export function BoardTabs({
  tabs,
  active,
  counts,
  onSelect,
}: {
  tabs: Tab[];
  active: Tab;
  /** How many reports sit under each tab, so an empty section is visible before it is opened. */
  counts: Record<string, number>;
  onSelect: (tab: Tab) => void;
}) {
  const key = (tab: Tab) => `${tab.module}/${tab.section}`;

  return (
    <div className="overflow-x-auto border-b border-line bg-white">
      <div className="flex min-w-max items-center gap-0.5 px-4">
        {tabs.map((tab) => {
          const selected = key(tab) === key(active);
          const count = counts[key(tab)] ?? 0;
          return (
            <button
              key={key(tab)}
              type="button"
              onClick={() => onSelect(tab)}
              title={tab.module ? `${tab.module} › ${tab.section}` : 'Every section'}
              className={clsx(
                '-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2.5 text-sm font-medium transition-colors',
                selected
                  ? 'border-accent text-accent'
                  : 'border-transparent text-ink-muted hover:text-ink',
              )}
            >
              {tab.section ? iconFor(tab.section) : glyph(<><rect x="3" y="3" width="7.5" height="7.5" rx="1.5" /><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.5" /><rect x="3" y="13.5" width="7.5" height="7.5" rx="1.5" /><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.5" /></>)}
              {tab.label}
              {count > 0 && (
                <span
                  className={clsx(
                    'rounded-full px-1.5 text-[10px] font-semibold tabular',
                    selected ? 'bg-accent-soft text-accent' : 'bg-canvas text-ink-faint',
                  )}
                >
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
