'use client';

import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';

/**
 * Filter value input backed by the values the column actually holds.
 *
 * Typing a filter value from memory is how a report ends up quietly empty:
 * "delivered" instead of "Delivered" matches nothing and explains nothing.
 * Offering the real values removes the guesswork, while still allowing free
 * text for anything the list does not cover.
 */
export function ValuePicker({
  table,
  field,
  value,
  onChange,
  placeholder,
  inputType = 'text',
  className,
}: {
  table: string;
  field: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  inputType?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState(value);
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => setTyped(value), [value]);

  const values = useQuery({
    queryKey: ['column-values', table, field],
    queryFn: () => api.columnValues(table, field),
    enabled: open,
    staleTime: 120_000,
  });

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (!container.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [open]);

  const available = values.data?.values ?? [];
  const matching = typed
    ? available.filter((v) => v.toLowerCase().includes(typed.toLowerCase()))
    : available;
  const unknown =
    values.data?.supported &&
    available.length > 0 &&
    typed !== '' &&
    !available.some((v) => v === typed);

  return (
    <div ref={container} className={`relative ${className ?? ''}`}>
      <input
        type={inputType}
        value={typed}
        placeholder={placeholder}
        onChange={(event) => {
          setTyped(event.target.value);
          onChange(event.target.value);
        }}
        onFocus={() => setOpen(true)}
        className={`field py-1 pr-6 text-xs ${
          unknown ? 'border-warn bg-warn-soft' : ''
        }`}
        title={
          unknown
            ? `No row has this value. The column contains: ${available.slice(0, 6).join(', ')}`
            : undefined
        }
      />

      <button
        type="button"
        tabIndex={-1}
        aria-label="Show values from this column"
        onClick={() => setOpen((value) => !value)}
        className="absolute right-1 top-1/2 -translate-y-1/2 text-ink-faint hover:text-accent"
      >
        <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth={2.5}>
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>

      {open && (
        <div className="menu left-0 right-auto max-h-56 min-w-[180px] overflow-y-auto">
          {values.isLoading && (
            <p className="px-3 py-2 text-2xs text-ink-faint">Loading values…</p>
          )}

          {!values.isLoading && !values.data?.supported && (
            <p className="px-3 py-2 text-2xs text-ink-faint">
              {values.data?.reason === 'not a categorical field'
                ? 'Type a value — this field has too many distinct values to list.'
                : 'The values could not be listed. Type one instead.'}
            </p>
          )}

          {!values.isLoading && values.data?.supported && matching.length === 0 && (
            <p className="px-3 py-2 text-2xs text-ink-faint">
              {available.length === 0 ? 'No values found.' : 'Nothing matches what you typed.'}
            </p>
          )}

          {matching.slice(0, 100).map((option) => (
            <button
              key={option}
              type="button"
              className="menu-item text-xs"
              onClick={() => {
                setTyped(option);
                onChange(option);
                setOpen(false);
              }}
            >
              {option}
            </button>
          ))}

          {values.data?.truncated && (
            <p className="border-t border-line px-3 py-1.5 text-2xs text-ink-faint">
              Showing the first {available.length}. Type to narrow the list.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Multi-value picker, for the `in` and `not in` operators.
 *
 * Shown as removable chips rather than a comma-separated string, so it is
 * obvious what is actually being matched.
 */
export function MultiValuePicker({
  table,
  field,
  values,
  onChange,
}: {
  table: string;
  field: string;
  values: string[];
  onChange: (values: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const container = useRef<HTMLDivElement>(null);

  const available = useQuery({
    queryKey: ['column-values', table, field],
    queryFn: () => api.columnValues(table, field),
    enabled: open,
    staleTime: 120_000,
  });

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (!container.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [open]);

  const options = (available.data?.values ?? []).filter((v) =>
    search ? v.toLowerCase().includes(search.toLowerCase()) : true,
  );
  const chosen = new Set(values);

  return (
    <div ref={container} className="relative min-w-[150px] flex-1">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="field flex min-h-[26px] flex-wrap items-center gap-1 py-1 text-left text-xs"
      >
        {values.length === 0 && <span className="text-ink-faint">Choose values…</span>}
        {values.map((value) => (
          <span
            key={value}
            className="inline-flex items-center gap-1 rounded border border-accent-border
                       bg-accent-soft px-1 text-2xs text-accent"
          >
            {value}
            <span
              role="button"
              tabIndex={-1}
              onClick={(event) => {
                event.stopPropagation();
                onChange(values.filter((v) => v !== value));
              }}
              className="cursor-pointer hover:text-danger"
            >
              ×
            </span>
          </span>
        ))}
      </button>

      {open && (
        <div className="menu left-0 right-auto max-h-64 w-[220px] overflow-y-auto">
          <div className="px-2 pb-1 pt-1">
            <input
              value={search}
              autoFocus
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search values…"
              className="field py-1 text-xs"
            />
          </div>

          {available.isLoading && (
            <p className="px-3 py-2 text-2xs text-ink-faint">Loading values…</p>
          )}

          {!available.isLoading && !available.data?.supported && (
            <p className="px-3 py-2 text-2xs text-ink-faint">
              Values cannot be listed for this field.
            </p>
          )}

          {options.map((option) => (
            <label
              key={option}
              className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-xs
                         hover:bg-accent-soft"
            >
              <input
                type="checkbox"
                checked={chosen.has(option)}
                onChange={(event) =>
                  onChange(
                    event.target.checked
                      ? [...values, option]
                      : values.filter((v) => v !== option),
                  )
                }
                className="h-3.5 w-3.5 rounded-[3px] border-line-strong accent-accent"
              />
              <span className="truncate">{option}</span>
            </label>
          ))}

          {!available.isLoading && available.data?.supported && options.length === 0 && (
            <p className="px-3 py-2 text-2xs text-ink-faint">Nothing matches.</p>
          )}
        </div>
      )}
    </div>
  );
}
