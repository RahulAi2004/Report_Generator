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
  phase?: string;
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

const NAV: Item[] = [
  {
    href: '/dashboard',
    label: 'Dashboard',
    ready: true,
    icon: icon(<><path d="M3 10.5 12 3l9 7.5" /><path d="M5 9.5V21h14V9.5" /></>),
  },
  {
    href: '/reports',
    label: 'Reports',
    ready: true,
    icon: icon(<><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M8 9h8M8 13h8M8 17h5" /></>),
  },
  {
    href: '/query-assistant',
    label: 'Query Assistant',
    phase: 'Phase 7',
    icon: icon(<><path d="M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" /></>),
  },
  {
    href: '/anomalies',
    label: 'Anomaly Center',
    phase: 'Phase 8',
    icon: icon(<><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" /><path d="M12 9v4M12 17h.01" /></>),
  },
  {
    href: '/data-sources',
    label: 'Data Sources',
    ready: true,
    icon: icon(<><ellipse cx="12" cy="5" rx="9" ry="3" /><path d="M3 5v14c0 1.7 4 3 9 3s9-1.3 9-3V5" /><path d="M3 12c0 1.7 4 3 9 3s9-1.3 9-3" /></>),
  },
  {
    href: '/schedules',
    label: 'Schedules',
    phase: 'Phase 10',
    icon: icon(<><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>),
  },
  {
    href: '/templates',
    label: 'Templates',
    phase: 'Phase 6',
    icon: icon(<><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>),
  },
  {
    href: '/audit-logs',
    label: 'Audit Logs',
    phase: 'Phase 10',
    icon: icon(<><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /><path d="M9 15h6M9 11h3" /></>),
  },
  {
    href: '/settings',
    label: 'Settings',
    phase: 'Phase 10',
    icon: icon(<><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2V21a2 2 0 1 1-4 0v-.1A1.7 1.7 0 0 0 7 19.4a1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0-1.2-2.9H1a2 2 0 1 1 0-4h.1A1.7 1.7 0 0 0 2.6 7a1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H7a1.7 1.7 0 0 0 1-1.5V1a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 2.9 1.2 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V7a1.7 1.7 0 0 0 1.5 1H23a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></>),
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
          const active = pathname.startsWith(item.href);
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

          if (!item.ready) {
            return (
              <div
                key={item.href}
                title={`${item.label} — arrives in ${item.phase}`}
                className="mx-1.5 mb-0.5 flex cursor-not-allowed flex-col items-center rounded
                           px-1 py-2 text-rail-muted/45"
              >
                {content}
              </div>
            );
          }

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
