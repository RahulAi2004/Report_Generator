'use client';

import clsx from 'clsx';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useState } from 'react';

/**
 * Persistent navigation rail (spec 4).
 *
 * Sections that are not built yet are shown, disabled, with the phase they
 * arrive in -- an honest roadmap in the product beats a menu of dead links.
 */

interface Item {
  href: string;
  label: string;
  icon: React.ReactNode;
  ready?: boolean;
}

const stroke = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.6,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
};

const icon = (path: React.ReactNode) => (
  <svg viewBox="0 0 24 24" className="h-[18px] w-[18px]" {...stroke}>
    {path}
  </svg>
);

/**
 * Only destinations that exist.
 *
 * Sections still to be built are omitted entirely rather than shown disabled:
 * a sidebar full of things that do nothing trains people to ignore it.
 */
const NAV: Item[] = [
  {
    href: '/dashboards',
    label: 'Dashboards',
    ready: true,
    icon: icon(<><rect x="3" y="3" width="7.5" height="7.5" rx="1.5" /><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.5" /><rect x="3" y="13.5" width="7.5" height="7.5" rx="1.5" /><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.5" /></>),
  },
  {
    href: '/dashboard',
    label: 'Pinned',
    ready: true,
    icon: icon(<><path d="M3 10.5 12 3l9 7.5" /><path d="M5 9.5V21h14V9.5" /></>),
  },
  {
    href: '/report-board',
    label: 'Report Board',
    ready: true,
    icon: icon(<><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 9h18M9 9v11" /><path d="M12 13h6M12 16h4" /></>),
  },
  {
    href: '/ai-suggestions',
    label: 'AI Suggestions',
    ready: true,
    icon: icon(<><path d="M12 3l1.9 4.6L18.5 9.5l-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9z" /><path d="M18 15l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z" /></>),
  },
  {
    href: '/reports',
    label: 'Reports',
    ready: true,
    icon: icon(<><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M8 9h8M8 13h8M8 17h5" /></>),
  },
  {
    href: '/uploads',
    label: 'Uploads',
    ready: true,
    icon: icon(<><path d="M12 16V4M7 9l5-5 5 5" /><path d="M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" /></>),
  },
  {
    href: '/connectors',
    label: 'API Connections',
    ready: true,
    icon: icon(<><path d="M9 2v6M15 2v6" /><rect x="6" y="8" width="12" height="6" rx="2" /><path d="M12 14v4a3 3 0 0 0 3 3h1" /></>),
  },
  {
    href: '/connections',
    label: 'Connections',
    ready: true,
    icon: icon(<><rect x="2.5" y="4" width="8" height="6.5" rx="1.5" /><rect x="13.5" y="13.5" width="8" height="6.5" rx="1.5" /><path d="M6.5 10.5v4a2 2 0 0 0 2 2h5" /></>),
  },
  {
    href: '/data-sources',
    label: 'Data Sources',
    ready: true,
    icon: icon(<><ellipse cx="12" cy="5" rx="9" ry="3" /><path d="M3 5v14c0 1.7 4 3 9 3s9-1.3 9-3V5" /><path d="M3 12c0 1.7 4 3 9 3s9-1.3 9-3" /></>),
  },
];

export function Sidebar() {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);

  return (
    <nav
      className={clsx(
        'flex shrink-0 flex-col bg-rail text-white transition-[width] duration-200',
        collapsed ? 'w-[58px]' : 'w-[82px]',
      )}
      aria-label="Main"
    >
      <div className="flex h-14 items-center justify-center border-b border-white/8">
        <div className="flex h-8 w-8 items-end justify-center gap-[3px] rounded bg-accent/90 px-1.5 pb-1.5">
          <span className="h-2.5 w-[3px] rounded-sm bg-white/70" />
          <span className="h-4 w-[3px] rounded-sm bg-white" />
          <span className="h-3 w-[3px] rounded-sm bg-white/70" />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto py-2">
        {NAV.map((item) => {
          // Matched on a path boundary, not a prefix: /dashboards would
          // otherwise light up /dashboard as well, and two highlighted rows
          // say nothing about where you are.
          const active =
            pathname === item.href || pathname.startsWith(`${item.href}/`);
          const content = (
            <>
              <span className={clsx(active && 'text-white')}>{item.icon}</span>
              {!collapsed && (
                <span className="mt-1 w-full px-1 text-center text-[10px] leading-tight">
                  {item.label}
                </span>
              )}
            </>
          );


          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? 'page' : undefined}
              className={clsx(
                'mx-1.5 mb-0.5 flex flex-col items-center rounded px-1 py-2 transition-colors',
                active
                  ? 'bg-accent text-white'
                  : 'text-rail-muted hover:bg-rail-hover hover:text-white',
              )}
            >
              {content}
            </Link>
          );
        })}
      </div>

      <button
        type="button"
        onClick={() => setCollapsed((value) => !value)}
        title={collapsed ? 'Expand navigation' : 'Collapse navigation'}
        className="flex h-10 items-center justify-center border-t border-white/8
                   text-rail-muted hover:bg-rail-hover hover:text-white"
      >
        <svg viewBox="0 0 24 24" className="h-4 w-4" {...stroke}>
          <path d={collapsed ? 'M9 6l6 6-6 6' : 'M15 6l-6 6 6 6'} />
        </svg>
      </button>
    </nav>
  );
}
