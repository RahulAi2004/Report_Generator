/**
 * Cell formatting.
 *
 * The API returns raw values; presentation happens here so the same number can
 * be shown as currency in one report and a plain number in another without
 * re-running the query.
 */

import type { ColumnFormat, DataType } from './types';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const CURRENCY_SYMBOLS: Record<string, string> = {
  USD: '$', EUR: '€', GBP: '£', JPY: '¥', INR: '₹',
  AUD: 'A$', CAD: 'C$', PKR: 'Rs ', AED: 'AED ',
};

export function formatValue(
  value: unknown,
  format: ColumnFormat | null,
  dataType: DataType,
): string {
  if (value === null || value === undefined || value === '') {
    return format?.null_display ?? '—';
  }

  const kind = format?.kind ?? inferKind(dataType);
  const decimals = format?.decimals ?? (kind === 'currency' ? 2 : 0);
  const separator = format?.thousands_separator ?? true;

  let text: string;
  switch (kind) {
    case 'currency': {
      const symbol = CURRENCY_SYMBOLS[format?.currency ?? 'USD'] ?? `${format?.currency ?? ''} `;
      text = `${symbol}${formatNumber(value, decimals, separator)}`;
      break;
    }
    case 'percent':
      text = `${formatNumber(value, decimals, separator)}%`;
      break;
    case 'number':
      text = formatNumber(value, decimals, separator);
      break;
    case 'date':
      text = formatDate(value, false);
      break;
    case 'datetime':
      text = formatDate(value, true);
      break;
    case 'boolean':
      text = value === true || value === 1 || value === 'true' ? 'Yes' : 'No';
      break;
    default:
      text = String(value);
  }

  return `${format?.prefix ?? ''}${text}${format?.suffix ?? ''}`;
}

function inferKind(dataType: DataType): ColumnFormat['kind'] {
  switch (dataType) {
    case 'integer': return 'number';
    case 'decimal': return 'number';
    case 'date': return 'date';
    case 'datetime': return 'datetime';
    case 'boolean': return 'boolean';
    default: return 'text';
  }
}

export function formatNumber(value: unknown, decimals: number, separator = true): string {
  const numeric = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  const fixed = numeric.toFixed(decimals);
  if (!separator) return fixed;
  const [whole, fraction] = fixed.split('.');
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return fraction ? `${grouped}.${fraction}` : grouped;
}

function formatDate(value: unknown, withTime: boolean): string {
  const date = new Date(String(value));
  if (Number.isNaN(date.getTime())) return String(value);

  const base = `${MONTHS[date.getMonth()]} ${date.getDate()}, ${date.getFullYear()}`;
  if (!withTime) return base;

  const hours = date.getHours();
  const suffix = hours >= 12 ? 'PM' : 'AM';
  const hour12 = hours % 12 === 0 ? 12 : hours % 12;
  return `${base} ${hour12}:${String(date.getMinutes()).padStart(2, '0')} ${suffix}`;
}

export function compactNumber(value: number): string {
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (Math.abs(value) >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(value);
}

/** `sales_order_items` -> `Sales Order Items`, mirroring the backend's humanize(). */
export function humanize(identifier: string): string {
  const acronyms: Record<string, string> = {
    id: 'ID', no: 'No.', qty: 'Qty', url: 'URL', sku: 'SKU', po: 'PO', so: 'SO',
  };
  return identifier
    .split(/[_-]/)
    .filter(Boolean)
    .map((word) => acronyms[word.toLowerCase()] ?? word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

export const OPERATOR_LABELS: Record<string, string> = {
  equals: 'Equals', not_equals: 'Not equals', contains: 'Contains',
  not_contains: 'Does not contain', starts_with: 'Starts with', ends_with: 'Ends with',
  in: 'In', not_in: 'Not in', is_empty: 'Is empty', is_not_empty: 'Is not empty',
  greater_than: 'Greater than', greater_or_equal: 'Greater or equal',
  less_than: 'Less than', less_or_equal: 'Less or equal', between: 'Between',
  on: 'On', before: 'Before', after: 'After', today: 'Today', yesterday: 'Yesterday',
  this_week: 'This week', this_month: 'This month', this_year: 'This year',
  last_7_days: 'Last 7 days', last_30_days: 'Last 30 days', last_n_days: 'Last N days',
  year_to_date: 'Year to date', is_null: 'Is empty (null)', is_not_null: 'Is not null',
  is_true: 'Is true', is_false: 'Is false',
};

/** Operators that need no operand -- the value input is hidden for these. */
export const NO_VALUE_OPERATORS = new Set([
  'is_null', 'is_not_null', 'is_empty', 'is_not_empty', 'is_true', 'is_false',
  'today', 'yesterday', 'this_week', 'this_month', 'this_year',
  'last_7_days', 'last_30_days', 'year_to_date',
]);

export const TWO_VALUE_OPERATORS = new Set(['between']);
export const LIST_VALUE_OPERATORS = new Set(['in', 'not_in']);
