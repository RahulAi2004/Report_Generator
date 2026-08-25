'use client';

/**
 * Report builder state.
 *
 * The store holds exactly one thing: the report IR. Every panel is a view over
 * a branch of it, and every interaction is an edit to it. Keeping a single
 * source of truth is what lets the workflow counters, the diagnostics bar and
 * the preview stay consistent without any cross-panel wiring.
 */

import { create } from 'zustand';
import type {
  Aggregation,
  FilterCondition,
  FilterGroup,
  FilterNode,
  ReportColumn,
  ReportDefinition,
  SchemaColumn,
  SchemaTable,
} from '@/lib/types';
import { emptyDefinition } from '@/lib/types';

let counter = 0;
const nextId = (prefix: string) => `${prefix}${Date.now().toString(36)}${(counter++).toString(36)}`;

interface BuilderState {
  reportId: string | null;
  reportName: string;
  definition: ReportDefinition;
  selectedTable: string | null;
  selectedColumnId: string | null;
  dirty: boolean;

  setReportName: (name: string) => void;
  loadReport: (id: string | null, name: string, definition: ReportDefinition) => void;
  reset: () => void;

  selectTable: (table: string) => void;
  toggleTable: (table: SchemaTable) => void;
  setPrimaryTable: (table: string) => void;

  addColumn: (table: string, column: SchemaColumn) => void;
  removeColumn: (id: string) => void;
  toggleField: (table: string, column: SchemaColumn) => void;
  updateColumn: (id: string, patch: Partial<ReportColumn>) => void;
  moveColumn: (id: string, direction: -1 | 1) => void;
  selectColumn: (id: string | null) => void;

  addFilter: (table: string, field: string, operator: string) => void;
  updateFilter: (id: string, patch: Partial<FilterCondition>) => void;
  removeFilter: (id: string) => void;
  setFilterGroupOp: (op: 'and' | 'or') => void;

  addGroupBy: (table: string, field: string) => void;
  removeGroupBy: (index: number) => void;

  addSort: (columnId: string) => void;
  updateSort: (index: number, patch: { column_id?: string; direction?: 'asc' | 'desc' }) => void;
  removeSort: (index: number) => void;

  setRowLimit: (limit: number) => void;
  setFanoutCorrection: (enabled: boolean) => void;
}

function isCondition(node: FilterNode): node is FilterCondition {
  return node.kind === 'condition';
}

export const useBuilder = create<BuilderState>((set, get) => ({
  reportId: null,
  reportName: 'Untitled Report',
  definition: emptyDefinition(),
  selectedTable: null,
  selectedColumnId: null,
  dirty: false,

  setReportName: (name) => set({ reportName: name, dirty: true }),

  loadReport: (id, name, definition) =>
    set({
      reportId: id,
      reportName: name,
      definition,
      selectedTable: definition.primary_table || null,
      selectedColumnId: definition.columns[0]?.id ?? null,
      dirty: false,
    }),

  reset: () =>
    set({
      reportId: null,
      reportName: 'Untitled Report',
      definition: emptyDefinition(),
      selectedTable: null,
      selectedColumnId: null,
      dirty: false,
    }),

  selectTable: (table) => set({ selectedTable: table }),

  toggleTable: (table) =>
    set((state) => {
      const { definition } = state;
      const included = definition.tables.includes(table.name);

      if (included) {
        // Removing a table must also remove everything that referenced it,
        // otherwise the report silently fails to compile.
        const tables = definition.tables.filter((t) => t !== table.name);
        const columns = definition.columns.filter((c) => c.table !== table.name);
        const keptIds = new Set(columns.map((c) => c.id));
        return {
          selectedTable: tables[0] ?? null,
          dirty: true,
          definition: {
            ...definition,
            tables,
            primary_table:
              definition.primary_table === table.name ? (tables[0] ?? '') : definition.primary_table,
            columns,
            group_by: definition.group_by.filter((g) => g.table !== table.name),
            sort_by: definition.sort_by.filter((s) => keptIds.has(s.column_id)),
            filters: pruneFilters(definition.filters, table.name),
            joins: definition.joins.filter(
              (j) => j.left_table !== table.name && j.right_table !== table.name,
            ),
          },
        };
      }

      const tables = [...definition.tables, table.name];
      return {
        selectedTable: table.name,
        dirty: true,
        definition: {
          ...definition,
          tables,
          primary_table: definition.primary_table || table.name,
        },
      };
    }),

  setPrimaryTable: (table) =>
    set((state) => ({
      dirty: true,
      definition: { ...state.definition, primary_table: table },
    })),

  addColumn: (table, column) =>
    set((state) => {
      const id = nextId('col_');
      const next: ReportColumn = {
        id,
        table,
        field: column.name,
        display_name: column.label,
        aggregation: 'none',
        visible: true,
        conditional_formats: [],
      };
      return {
        selectedColumnId: id,
        dirty: true,
        definition: {
          ...state.definition,
          tables: state.definition.tables.includes(table)
            ? state.definition.tables
            : [...state.definition.tables, table],
          primary_table: state.definition.primary_table || table,
          columns: [...state.definition.columns, next],
        },
      };
    }),

  removeColumn: (id) =>
    set((state) => ({
      selectedColumnId: state.selectedColumnId === id ? null : state.selectedColumnId,
      dirty: true,
      definition: {
        ...state.definition,
        columns: state.definition.columns.filter((c) => c.id !== id),
        sort_by: state.definition.sort_by.filter((s) => s.column_id !== id),
      },
    })),

  toggleField: (table, column) => {
    const existing = get().definition.columns.find(
      (c) => c.table === table && c.field === column.name,
    );
    if (existing) get().removeColumn(existing.id);
    else get().addColumn(table, column);
  },

  updateColumn: (id, patch) =>
    set((state) => ({
      dirty: true,
      definition: {
        ...state.definition,
        columns: state.definition.columns.map((c) => (c.id === id ? { ...c, ...patch } : c)),
      },
    })),

  moveColumn: (id, direction) =>
    set((state) => {
      const columns = [...state.definition.columns];
      const index = columns.findIndex((c) => c.id === id);
      const target = index + direction;
      if (index < 0 || target < 0 || target >= columns.length) return state;
      [columns[index], columns[target]] = [columns[target], columns[index]];
      return { dirty: true, definition: { ...state.definition, columns } };
    }),

  selectColumn: (id) => set({ selectedColumnId: id }),

  addFilter: (table, field, operator) =>
    set((state) => {
      const condition: FilterCondition = {
        kind: 'condition',
        id: nextId('f_'),
        table,
        field,
        operator,
        values: [],
      };
      return {
        dirty: true,
        definition: {
          ...state.definition,
          filters: {
            ...state.definition.filters,
            children: [...state.definition.filters.children, condition],
          },
        },
      };
    }),

  updateFilter: (id, patch) =>
    set((state) => ({
      dirty: true,
      definition: {
        ...state.definition,
        filters: {
          ...state.definition.filters,
          children: state.definition.filters.children.map((node) =>
            isCondition(node) && node.id === id ? { ...node, ...patch } : node,
          ),
        },
      },
    })),

  removeFilter: (id) =>
    set((state) => ({
      dirty: true,
      definition: {
        ...state.definition,
        filters: {
          ...state.definition.filters,
          children: state.definition.filters.children.filter(
            (node) => !(isCondition(node) && node.id === id),
          ),
        },
      },
    })),

  setFilterGroupOp: (op) =>
    set((state) => ({
      dirty: true,
      definition: { ...state.definition, filters: { ...state.definition.filters, op } },
    })),

  addGroupBy: (table, field) =>
    set((state) => {
      const exists = state.definition.group_by.some(
        (g) => g.table === table && g.field === field,
      );
      if (exists) return state;
      return {
        dirty: true,
        definition: {
          ...state.definition,
          group_by: [...state.definition.group_by, { table, field }],
        },
      };
    }),

  removeGroupBy: (index) =>
    set((state) => ({
      dirty: true,
      definition: {
        ...state.definition,
        group_by: state.definition.group_by.filter((_, i) => i !== index),
      },
    })),

  addSort: (columnId) =>
    set((state) => ({
      dirty: true,
      definition: {
        ...state.definition,
        sort_by: [...state.definition.sort_by, { column_id: columnId, direction: 'desc' }],
      },
    })),

  updateSort: (index, patch) =>
    set((state) => ({
      dirty: true,
      definition: {
        ...state.definition,
        sort_by: state.definition.sort_by.map((s, i) => (i === index ? { ...s, ...patch } : s)),
      },
    })),

  removeSort: (index) =>
    set((state) => ({
      dirty: true,
      definition: {
        ...state.definition,
        sort_by: state.definition.sort_by.filter((_, i) => i !== index),
      },
    })),

  setRowLimit: (limit) =>
    set((state) => ({ dirty: true, definition: { ...state.definition, row_limit: limit } })),

  setFanoutCorrection: (enabled) =>
    set((state) => ({
      dirty: true,
      definition: { ...state.definition, disable_fanout_correction: !enabled },
    })),
}));

function pruneFilters(group: FilterGroup, table: string): FilterGroup {
  return {
    ...group,
    children: group.children
      .filter((node) => node.kind !== 'condition' || node.table !== table)
      .map((node) => (node.kind === 'group' ? pruneFilters(node, table) : node)),
  };
}

/** Counters shown on the workflow strip, derived rather than tracked. */
export function summarize(definition: ReportDefinition) {
  const countFilters = (group: FilterGroup): number =>
    group.children.reduce(
      (total, node) => total + (node.kind === 'group' ? countFilters(node) : 1),
      0,
    );
  return {
    data_sources: definition.tables.length,
    fields_selected: definition.columns.length + definition.calculated_columns.length,
    relationships: definition.joins.length,
    filters: countFilters(definition.filters),
    grouping: definition.group_by.length,
    sorting: definition.sort_by.length,
  };
}

export const AGGREGATION_LABELS: Record<Aggregation, string> = {
  none: 'None',
  count: 'COUNT',
  count_distinct: 'COUNT DISTINCT',
  sum: 'SUM',
  avg: 'AVG',
  min: 'MIN',
  max: 'MAX',
};
