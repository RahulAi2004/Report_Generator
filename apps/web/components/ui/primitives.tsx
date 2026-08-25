'use client';

/**
 * Small shared primitives.
 *
 * Deliberately minimal: an enterprise BI screen is mostly tables, selects and
 * checkboxes at high density, and a heavyweight component kit would fight the
 * compact layout rather than help it.
 */

import clsx from 'clsx';
import type { ReactNode } from 'react';

export function Button({
  variant = 'default',
  size = 'md',
  className,
  children,
  ...props
}: {
  variant?: 'default' | 'primary' | 'ghost';
  size?: 'sm' | 'md';
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      className={clsx('btn', `btn-${variant}`, size === 'sm' && 'btn-sm', className)}
      {...props}
    >
      {children}
    </button>
  );
}

export function Checkbox({
  checked,
  onChange,
  disabled,
  label,
  id,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  label?: ReactNode;
  id?: string;
}) {
  return (
    <label
      className={clsx(
        'flex items-center gap-2',
        disabled ? 'cursor-not-allowed opacity-50' : 'cursor-pointer',
      )}
    >
      <input
        id={id}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="h-3.5 w-3.5 shrink-0 cursor-pointer rounded-[3px] border-line-strong
                   text-accent accent-accent focus:ring-1 focus:ring-accent/30"
      />
      {label != null && <span className="min-w-0 truncate">{label}</span>}
    </label>
  );
}

export function Select({
  value,
  onChange,
  options,
  className,
  disabled,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string; disabled?: boolean }[];
  className?: string;
  disabled?: boolean;
  placeholder?: string;
}) {
  return (
    <select
      className={clsx('field', className)}
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
    >
      {placeholder && <option value="">{placeholder}</option>}
      {options.map((option) => (
        <option key={option.value} value={option.value} disabled={option.disabled}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

export function Badge({
  tone = 'neutral',
  children,
  className,
}: {
  tone?: 'neutral' | 'accent' | 'good' | 'warn' | 'danger';
  children: ReactNode;
  className?: string;
}) {
  const tones = {
    neutral: 'border-line bg-canvas text-ink-muted',
    accent: 'border-accent-border bg-accent-soft text-accent',
    good: 'border-good-border bg-good-soft text-good',
    warn: 'border-warn-border bg-warn-soft text-warn',
    danger: 'border-danger-border bg-danger-soft text-danger',
  } as const;
  return <span className={clsx('chip', tones[tone], className)}>{children}</span>;
}

export function IconButton({
  title,
  onClick,
  children,
  disabled,
  className,
}: {
  title: string;
  onClick?: () => void;
  children: ReactNode;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      disabled={disabled}
      onClick={onClick}
      className={clsx(
        'inline-flex h-6 w-6 items-center justify-center rounded text-ink-faint transition-colors',
        'hover:bg-canvas hover:text-ink disabled:cursor-not-allowed disabled:opacity-40',
        className,
      )}
    >
      {children}
    </button>
  );
}

export function EmptyState({
  title,
  hint,
  icon,
}: {
  title: string;
  hint?: string;
  icon?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-1.5 px-6 py-10 text-center">
      {icon && <div className="mb-1 text-line-strong">{icon}</div>}
      <p className="text-sm font-medium text-ink-muted">{title}</p>
      {hint && <p className="max-w-xs text-xs text-ink-faint">{hint}</p>}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx('skeleton', className)} />;
}

export function Tooltip({ text, children }: { text: string; children: ReactNode }) {
  return (
    <span className="group relative inline-flex">
      {children}
      <span
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-1.5 hidden
                   -translate-x-1/2 whitespace-nowrap rounded bg-rail px-2 py-1 text-2xs
                   text-white shadow-pop group-hover:block"
      >
        {text}
      </span>
    </span>
  );
}

/** Type glyph shown beside every field, so the type is readable at a glance. */
export function TypeGlyph({ dataType }: { dataType: string }) {
  const map: Record<string, { symbol: string; tone: string; title: string }> = {
    text: { symbol: 'Ab', tone: 'text-ink-faint', title: 'Text' },
    integer: { symbol: '#', tone: 'text-accent', title: 'Whole number' },
    decimal: { symbol: '#', tone: 'text-accent', title: 'Decimal number' },
    boolean: { symbol: '◑', tone: 'text-ink-faint', title: 'True / false' },
    date: { symbol: '▤', tone: 'text-good', title: 'Date' },
    datetime: { symbol: '▤', tone: 'text-good', title: 'Date and time' },
    time: { symbol: '◷', tone: 'text-good', title: 'Time' },
    uuid: { symbol: 'ID', tone: 'text-ink-faint', title: 'Identifier' },
    json: { symbol: '{}', tone: 'text-ink-faint', title: 'JSON' },
  };
  const glyph = map[dataType] ?? { symbol: '?', tone: 'text-ink-faint', title: dataType };
  return (
    <span
      title={glyph.title}
      className={clsx('w-5 shrink-0 text-center font-mono text-2xs', glyph.tone)}
    >
      {glyph.symbol}
    </span>
  );
}
