/**
 * Dashboard IR, mirroring the server's.
 *
 * Kept beside the report types rather than inside them: a dashboard is a
 * different document that happens to compile down to reports, and merging the
 * two would blur which fields the report engine actually reads.
 */

import type { Aggregation, FilterGroup } from '@/lib/types';

export type Preset =
  | 'daily' | 'weekly' | 'monthly' | 'quarterly' | 'yearly' | 'all_time' | 'custom';
export type RangeMode = 'last' | 'this' | 'previous';
export type MetricFormat = 'number' | 'currency' | 'percent' | 'duration';
export type Comparison = 'none' | 'previous_period' | 'share_of_total';
export type FilterControl =
  | 'select' | 'multi_select' | 'text' | 'date' | 'date_range' | 'boolean';
export type Tone = 'blue' | 'green' | 'amber' | 'violet' | 'rose' | 'slate';

export interface DateField {
  table: string;
  field: string;
}

export interface TimeRange {
  preset: Preset;
  mode: RangeMode;
  periods: number;
  start?: string | null;
  end?: string | null;
  date_field?: DateField | null;
}

export interface MetricCard {
  id: string;
  title: string;
  table: string;
  field: string;
  aggregation: Aggregation;
  distinct: boolean;
  filters: FilterGroup;
  date_field?: DateField | null;
  ignore_time_range: boolean;
  comparison: Comparison;
  format: MetricFormat;
  currency: string;
  decimals: number;
  icon: string;
  tone: Tone;
  higher_is_better: boolean;
}

export interface DashboardFilter {
  id: string;
  label: string;
  table: string;
  field: string;
  control: FilterControl;
  operator: string;
  values: unknown[];
  choices: string[];
  required: boolean;
}

export interface DashboardReportPanel {
  id: string;
  report_id: string;
  title?: string | null;
  columns: string[];
  page_size: number;
  date_field?: DateField | null;
  ignore_time_range: boolean;
  ignore_dashboard_filters: boolean;
}

export interface DashboardSettings {
  show_time_range: boolean;
  show_refresh: boolean;
  allow_export: boolean;
  allow_viewers_to_save: boolean;
}

export interface DashboardDefinition {
  version: number;
  app?: string | null;
  module?: string | null;
  time_range: TimeRange;
  metrics: MetricCard[];
  filters: DashboardFilter[];
  reports: DashboardReportPanel[];
  settings: DashboardSettings;
}

// ---------------------------------------------------------------------------
// Responses
// ---------------------------------------------------------------------------
export interface AppliedFilters {
  applied: string[];
  not_applicable: string[];
  time_range_applied: boolean;
}

export interface MetricResult {
  id: string;
  title: string;
  value: number | string | null;
  format?: MetricFormat;
  currency?: string;
  decimals?: number;
  icon?: string;
  tone?: Tone;
  errors: string[];
  filters: Partial<AppliedFilters>;
  window: [string, string] | null;
  caption?: string;
  delta?: {
    previous: number;
    difference: number;
    percent: number | null;
    direction: 'up' | 'down' | 'flat';
  } | null;
  share?: number | null;
  higher_is_better?: boolean;
}

export interface DashboardPreview {
  ok: boolean;
  metrics: MetricResult[];
  time_range: {
    label: string;
    window: [string, string] | null;
    previous: [string, string] | null;
  };
  summary: Record<string, number>;
  duration_ms: number;
}

export interface PanelResult {
  ok: boolean;
  title: string;
  source?: string;
  report_id?: string;
  columns: { key: string; label: string; data_type: string; align?: string | null }[];
  rows: Record<string, unknown>[];
  page: number;
  page_size: number;
  has_more: boolean;
  duration_ms: number;
  filters: Partial<AppliedFilters>;
  diagnostics?: { severity: string; message: string }[];
}

export interface DashboardSummary {
  id: string;
  name: string;
  description: string | null;
  app: string | null;
  module: string | null;
  visibility: string;
  show_in_menu: boolean;
  is_default: boolean;
  updated_at: string;
  view_count: number;
}

export interface DashboardOptions {
  apps: { name: string; modules: string[] }[];
  reports: { id: string; name: string; module: string | null; section: string | null; tables: string[] }[];
  period_choices: Record<string, number[]>;
}

// ---------------------------------------------------------------------------
export function emptyDashboard(): DashboardDefinition {
  return {
    version: 1,
    app: null,
    module: null,
    time_range: { preset: 'daily', mode: 'last', periods: 7, date_field: null },
    metrics: [],
    filters: [],
    reports: [],
    settings: {
      show_time_range: true,
      show_refresh: true,
      allow_export: true,
      allow_viewers_to_save: false,
    },
  };
}

/** The window dropdown's label, matching what the server will report back. */
export function rangeLabel(range: TimeRange): string {
  if (range.preset === 'all_time') return 'All Time';
  if (range.preset === 'custom') {
    return range.start && range.end ? `${range.start} to ${range.end}` : 'Custom';
  }
  const unit = {
    daily: 'Day', weekly: 'Week', monthly: 'Month',
    quarterly: 'Quarter', yearly: 'Year',
  }[range.preset];
  if (range.mode === 'this') return `This ${unit}`;
  const plural = range.periods === 1 ? unit : `${unit}s`;
  const prefix = range.mode === 'last' ? 'Last' : 'Previous';
  return `${prefix} ${range.periods} ${plural}`;
}
