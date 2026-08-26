'use client';

/**
 * Dashboard builder state.
 *
 * As with the report builder, the store holds one thing -- the dashboard
 * definition -- and every panel is a view over a branch of it. The live preview
 * is then a pure function of that document, which is what keeps the numbers at
 * the top honest about the controls below them.
 */

import { create } from 'zustand';
import type {
  DashboardDefinition,
  DashboardFilter,
  DashboardReportPanel,
  MetricCard,
  Preset,
  TimeRange,
} from '@/lib/dashboard-types';
import { emptyDashboard } from '@/lib/dashboard-types';
import type { SchemaColumn } from '@/lib/types';

let counter = 0;
const nextId = (prefix: string) =>
  `${prefix}${Date.now().toString(36)}${(counter++).toString(36)}`;

interface DashboardState {
  dashboardId: string | null;
  name: string;
  description: string;
  visibility: 'private' | 'team' | 'organization';
  definition: DashboardDefinition;
  selectedMetricId: string | null;
  dirty: boolean;

  setName: (name: string) => void;
  setDescription: (value: string) => void;
  setVisibility: (value: 'private' | 'team' | 'organization') => void;
  load: (id: string | null, name: string, definition: DashboardDefinition) => void;
  reset: () => void;

  setApp: (app: string) => void;
  setModule: (module: string) => void;

  setTimeRange: (patch: Partial<TimeRange>) => void;
  setPreset: (preset: Preset) => void;

  addMetric: (card?: Partial<MetricCard>) => string;
  updateMetric: (id: string, patch: Partial<MetricCard>) => void;
  removeMetric: (id: string) => void;
  moveMetric: (id: string, direction: -1 | 1) => void;
  selectMetric: (id: string | null) => void;

  addFilter: (filter?: Partial<DashboardFilter>) => string;
  updateFilter: (id: string, patch: Partial<DashboardFilter>) => void;
  removeFilter: (id: string) => void;
  clearFilterValues: () => void;

  addReport: (reportId: string, title?: string) => string;
  updateReport: (id: string, patch: Partial<DashboardReportPanel>) => void;
  removeReport: (id: string) => void;

  setSetting: (key: keyof DashboardDefinition['settings'], value: boolean) => void;
}

/** A new card, with the defaults that make it valid the moment it appears. */
function newMetric(partial: Partial<MetricCard> = {}): MetricCard {
  return {
    id: nextId('m'),
    title: 'New Metric',
    table: '',
    field: '',
    aggregation: 'count',
    distinct: false,
    filters: { kind: 'group', op: 'and', children: [] },
    date_field: null,
    ignore_time_range: false,
    comparison: 'none',
    format: 'number',
    currency: 'EUR',
    decimals: 0,
    icon: 'hash',
    tone: 'blue',
    higher_is_better: true,
    ...partial,
  };
}

export const useDashboard = create<DashboardState>((set, get) => ({
  dashboardId: null,
  name: 'Untitled Dashboard',
  description: '',
  visibility: 'private',
  definition: emptyDashboard(),
  selectedMetricId: null,
  dirty: false,

  setName: (name) => set({ name, dirty: true }),
  setDescription: (description) => set({ description, dirty: true }),
  setVisibility: (visibility) => set({ visibility, dirty: true }),

  load: (dashboardId, name, definition) =>
    set({
      dashboardId,
      name,
      definition,
      selectedMetricId: definition.metrics[0]?.id ?? null,
      dirty: false,
    }),

  reset: () =>
    set({
      dashboardId: null,
      name: 'Untitled Dashboard',
      description: '',
      visibility: 'private',
      definition: emptyDashboard(),
      selectedMetricId: null,
      dirty: false,
    }),

  setApp: (app) =>
    set((state) => ({
      // Changing app clears the module: a module belongs to one app, and
      // silently keeping a stale one files the dashboard somewhere that no
      // longer exists.
      definition: { ...state.definition, app, module: null },
      dirty: true,
    })),

  setModule: (module) =>
    set((state) => ({ definition: { ...state.definition, module }, dirty: true })),

  setTimeRange: (patch) =>
    set((state) => ({
      definition: {
        ...state.definition,
        time_range: { ...state.definition.time_range, ...patch },
      },
      dirty: true,
    })),

  setPreset: (preset) =>
    set((state) => {
      const range = state.definition.time_range;
      // Each granularity offers its own window sizes, so a period count carried
      // over from another one would be a window nobody chose.
      const defaults: Record<string, number> = {
        daily: 7, weekly: 4, monthly: 3, quarterly: 2, yearly: 1,
      };
      return {
        definition: {
          ...state.definition,
          time_range: {
            ...range,
            preset,
            periods: defaults[preset] ?? range.periods,
          },
        },
        dirty: true,
      };
    }),

  addMetric: (card) => {
    const created = newMetric(card);
    set((state) => ({
      definition: {
        ...state.definition,
        metrics: [...state.definition.metrics, created],
      },
      selectedMetricId: created.id,
      dirty: true,
    }));
    return created.id;
  },

  updateMetric: (id, patch) =>
    set((state) => ({
      definition: {
        ...state.definition,
        metrics: state.definition.metrics.map((card) =>
          card.id === id ? { ...card, ...patch } : card,
        ),
      },
      dirty: true,
    })),

  removeMetric: (id) =>
    set((state) => ({
      definition: {
        ...state.definition,
        metrics: state.definition.metrics.filter((card) => card.id !== id),
      },
      selectedMetricId: state.selectedMetricId === id ? null : state.selectedMetricId,
      dirty: true,
    })),

  moveMetric: (id, direction) =>
    set((state) => {
      const cards = [...state.definition.metrics];
      const index = cards.findIndex((card) => card.id === id);
      const target = index + direction;
      if (index < 0 || target < 0 || target >= cards.length) return state;
      [cards[index], cards[target]] = [cards[target], cards[index]];
      return { definition: { ...state.definition, metrics: cards }, dirty: true };
    }),

  selectMetric: (selectedMetricId) => set({ selectedMetricId }),

  addFilter: (filter) => {
    const created: DashboardFilter = {
      id: nextId('f'),
      label: 'New Filter',
      table: '',
      field: '',
      control: 'select',
      operator: 'equals',
      values: [],
      choices: [],
      required: false,
      ...filter,
    };
    set((state) => ({
      definition: { ...state.definition, filters: [...state.definition.filters, created] },
      dirty: true,
    }));
    return created.id;
  },

  updateFilter: (id, patch) =>
    set((state) => ({
      definition: {
        ...state.definition,
        filters: state.definition.filters.map((filter) =>
          filter.id === id ? { ...filter, ...patch } : filter,
        ),
      },
      dirty: true,
    })),

  removeFilter: (id) =>
    set((state) => ({
      definition: {
        ...state.definition,
        filters: state.definition.filters.filter((filter) => filter.id !== id),
      },
      dirty: true,
    })),

  clearFilterValues: () =>
    set((state) => ({
      definition: {
        ...state.definition,
        // Clearing empties the values but keeps the controls: "Clear All"
        // resets the dashboard, it does not dismantle it.
        filters: state.definition.filters.map((filter) => ({ ...filter, values: [] })),
      },
      dirty: true,
    })),

  addReport: (reportId, title) => {
    const created: DashboardReportPanel = {
      id: nextId('p'),
      report_id: reportId,
      title: title ?? null,
      columns: [],
      page_size: 10,
      date_field: null,
      ignore_time_range: false,
      ignore_dashboard_filters: false,
    };
    set((state) => ({
      definition: { ...state.definition, reports: [...state.definition.reports, created] },
      dirty: true,
    }));
    return created.id;
  },

  updateReport: (id, patch) =>
    set((state) => ({
      definition: {
        ...state.definition,
        reports: state.definition.reports.map((panel) =>
          panel.id === id ? { ...panel, ...patch } : panel,
        ),
      },
      dirty: true,
    })),

  removeReport: (id) =>
    set((state) => ({
      definition: {
        ...state.definition,
        reports: state.definition.reports.filter((panel) => panel.id !== id),
      },
      dirty: true,
    })),

  setSetting: (key, value) =>
    set((state) => ({
      definition: {
        ...state.definition,
        settings: { ...state.definition.settings, [key]: value },
      },
      dirty: true,
    })),
}));

/**
 * Which aggregations make sense for a column.
 *
 * Sourced from the schema rather than assumed: the backend rejects SUM on a
 * text column, and offering it here would only produce an error later.
 */
export function metricAggregations(column: SchemaColumn | undefined): string[] {
  return column?.aggregations?.filter((a) => a !== 'none') ?? ['count'];
}

/** A card is only worth running once it names something to measure. */
export function metricIsComplete(card: MetricCard): boolean {
  return Boolean(card.table && card.field && card.title.trim());
}
