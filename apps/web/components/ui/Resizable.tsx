'use client';

import clsx from 'clsx';
import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * A panel the user can drag wider or narrower.
 *
 * The width is remembered per panel, because someone who widens the table list
 * to read long names has done so for a reason and should not have to do it
 * again on the next report.
 *
 * Stored in localStorage rather than on the server: it is a preference of this
 * browser at this screen size, not of the person, and syncing it would make a
 * laptop and a wide monitor fight over one number.
 */
export function useResizableWidth(key: string, initial: number, min = 160, max = 560) {
  const [width, setWidth] = useState(initial);
  const dragging = useRef(false);

  // Read once on mount. Reading during render would differ between the server
  // and the browser and produce a hydration mismatch.
  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(`panel:${key}`);
      if (stored) {
        const value = Number(stored);
        if (Number.isFinite(value)) setWidth(Math.min(max, Math.max(min, value)));
      }
    } catch {
      /* private windows and blocked storage: the default is fine */
    }
  }, [key, min, max]);

  const persist = useCallback(
    (value: number) => {
      try {
        window.localStorage.setItem(`panel:${key}`, String(value));
      } catch {
        /* nothing here is worth failing a resize over */
      }
    },
    [key],
  );

  const onPointerDown = useCallback(
    (event: React.PointerEvent) => {
      event.preventDefault();
      dragging.current = true;
      const startX = event.clientX;
      const startWidth = width;

      const move = (moveEvent: PointerEvent) => {
        if (!dragging.current) return;
        const next = Math.min(max, Math.max(min, startWidth + moveEvent.clientX - startX));
        setWidth(next);
      };
      const up = () => {
        dragging.current = false;
        document.removeEventListener('pointermove', move);
        document.removeEventListener('pointerup', up);
        document.body.style.removeProperty('cursor');
        document.body.style.removeProperty('user-select');
        setWidth((current) => {
          persist(current);
          return current;
        });
      };

      // While dragging, the cursor should stay a resize cursor even as it
      // passes over text, and text should not select under it.
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
      document.addEventListener('pointermove', move);
      document.addEventListener('pointerup', up);
    },
    [width, min, max, persist],
  );

  const reset = useCallback(() => {
    setWidth(initial);
    persist(initial);
  }, [initial, persist]);

  return { width, onPointerDown, reset };
}

/**
 * The grab strip itself.
 *
 * Wider than it looks: a two-pixel target is a target people miss, so the hit
 * area is eight pixels with a one-pixel line drawn inside it.
 */
export function ResizeHandle({
  onPointerDown,
  onReset,
  label,
}: {
  onPointerDown: (event: React.PointerEvent) => void;
  onReset?: () => void;
  label: string;
}) {
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={`Resize ${label}`}
      title={`Drag to resize${onReset ? ' · double-click to reset' : ''}`}
      onPointerDown={onPointerDown}
      onDoubleClick={onReset}
      className={clsx(
        'group relative w-2 shrink-0 cursor-col-resize select-none',
        'bg-transparent hover:bg-accent-soft/60 active:bg-accent-soft',
      )}
    >
      <span
        className="pointer-events-none absolute inset-y-0 left-1/2 w-px -translate-x-1/2
                   bg-line group-hover:bg-accent-border"
      />
    </div>
  );
}
