'use client';

import clsx from 'clsx';
import { useEffect, useRef, useState } from 'react';

/**
 * Dropdown menu.
 *
 * Closes on outside click and on Escape, and returns focus to the trigger --
 * the small behaviours that make a menu feel finished rather than fiddly.
 */
export function Menu({
  trigger,
  children,
  align = 'right',
  className,
}: {
  trigger: (props: { open: boolean; toggle: () => void }) => React.ReactNode;
  children: (props: { close: () => void }) => React.ReactNode;
  align?: 'left' | 'right';
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;

    function onPointerDown(event: MouseEvent) {
      if (!container.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setOpen(false);
        container.current?.querySelector('button')?.focus();
      }
    }

    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  return (
    <div ref={container} className={clsx('relative', className)}>
      {trigger({ open, toggle: () => setOpen((value) => !value) })}
      {open && (
        <div role="menu" className={clsx('menu', align === 'left' && 'left-0 right-auto')}>
          {children({ close: () => setOpen(false) })}
        </div>
      )}
    </div>
  );
}

export function MenuItem({
  icon,
  children,
  onClick,
  disabled,
  hint,
}: {
  icon?: React.ReactNode;
  children: React.ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  hint?: string;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      className="menu-item"
      disabled={disabled}
      title={hint}
      onClick={onClick}
    >
      {icon && <span className="shrink-0">{icon}</span>}
      <span className="flex-1">{children}</span>
    </button>
  );
}

export function MenuSeparator() {
  return <div className="menu-sep" />;
}

/** File-type glyphs for the download menu, coloured by format. */
export const FileIcon = {
  pdf: (
    <svg viewBox="0 0 24 24" className="h-4 w-4 text-danger" fill="none" stroke="currentColor" strokeWidth={1.6}>
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6" />
      <path d="M8 16h1.5a1.5 1.5 0 0 0 0-3H8v5" />
    </svg>
  ),
  csv: (
    <svg viewBox="0 0 24 24" className="h-4 w-4 text-good" fill="none" stroke="currentColor" strokeWidth={1.6}>
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6" />
      <path d="M9 13h6M9 17h4" />
    </svg>
  ),
  xlsx: (
    <svg viewBox="0 0 24 24" className="h-4 w-4 text-good" fill="none" stroke="currentColor" strokeWidth={1.6}>
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6" />
      <path d="m9 13 4 5M13 13l-4 5" />
    </svg>
  ),
};
