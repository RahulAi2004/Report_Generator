/**
 * Report IR types -- the TypeScript mirror of `app/domain/report/ir.py`.
 *
 * This shape is the contract between the builder UI and the compiler. The
 * builder's entire job is editing one of these documents; the backend's job is
 * turning it into validated SQL. Nothing here names a business table: every
 * value is discovered from the connected database at runtime.
 */

export type Aggregation =
  | 'none' | 'count' | 'count_distinct' | 'sum' | 'avg' | 'min' | 'max';

export type DataType =
  | 'text' | 'integer' | 'decimal' | 'boolean' | 'date' | 'datetime'
  | 'time' | 'uuid' | 'json' | 'binary' | 'unknown';

export type JoinType = 'inner' | 'left' | 'right' | 'full';

export interface ColumnFormat {
  kind: 'text' | 'number' | 'currency' | 'percent' | 'date' | 'datetime' | 'boolean';
  decimals: number;
  currency: string;
  thousands_separator: boolean;
  date_pattern: string;
  null_display: string;
  prefix: string;
  suffix: string;
}

export interface ReportColumn {
  id: string;
  table: string;
  field: string;
  display_name?: string | null;
  aggregation: Aggregation;
  format?: ColumnFormat | null;
  align?: 'left' | 'center' | 'right' | null;
  width?: number | null;
  visible: boolean;
  conditional_formats: unknown[];
}

export interface FilterCondition {
  kind: 'condition';
  id?: string | null;
  table: string;
  field: string;
  operator: string;
  values: unknown[];
  parameter?: { name: string; prompt: string; required: boolean; default?: unknown } | null;
}

export interface FilterGroup {
  kind: 'group';
  id?: string | null;
  op: 'and' | 'or';
  children: FilterNode[];
}

export type FilterNode = FilterCondition | FilterGroup;

export interface ReportJoin {
  left_table: string;
  left_column: string;
  right_table: string;
  right_column: string;
  join_type: JoinType;
  relationship_id?: string | null;
}

export interface ReportDefinition {
  version: number;
  connection_id?: string | null;
  primary_table: string;
  tables: string[];
  joins: ReportJoin[];
  columns: ReportColumn[];
  calculated_columns: unknown[];
  filters: FilterGroup;
  group_by: { table: string; field: string }[];
  sort_by: { column_id: string; direction: 'asc' | 'desc' }[];
  visualization: { type: string; dimension_column_id?: string | null; metric_column_id?: string | null; stacked: boolean };
  row_limit: number;
  disable_fanout_correction: boolean;
}

// ---------------------------------------------------------------------------
// API payloads
// ---------------------------------------------------------------------------
export interface SchemaColumn {
  name: string;
  label: string;
  table: string;
  data_type: DataType;
  physical_type: string;
  nullable: boolean;
  is_primary_key: boolean;
  is_foreign_key: boolean;
  is_sensitive: boolean;
  is_masked: boolean;
  /** Legal aggregations for this type. The UI renders only what the API allows. */
  aggregations: Aggregation[];
  operators: string[];
}

export interface SchemaTable {
  name: string;
  label: string;
  schema: string;
  kind: string;
  category: string;
  description: string | null;
  /** null when the engine has no cheap estimate, e.g. for a view. */
  estimated_rows: number | null;
  column_count: number;
  primary_key: string[];
  is_sensitive: boolean;
  columns?: SchemaColumn[];
}

export interface SchemaCategory {
  name: string;
  tables: SchemaTable[];
}

export interface Diagnostic {
  code: string;
  severity: 'error' | 'warning' | 'info';
  message: string;
  section: string;
  target: string | null;
  fix: Record<string, unknown> | null;
}

export interface JoinStep {
  from_table: string;
  from_column: string;
  to_table: string;
  to_column: string;
  join_type: JoinType;
  cardinality: string;
  relationship_id: string;
  relationship_source: string;
  multiplies_rows: boolean;
}

export interface ValidationResult {
  ok: boolean;
  summary: Record<string, number>;
  diagnostics: Diagnostic[];
  join_plan: { root: string; steps: JoinStep[]; bridge_tables: string[]; fans_out: Record<string, boolean> } | null;
  fanout: {
    corrected: boolean;
    inflation_detected: boolean;
    branches: { table: string; strategy: string; multiplies_rows: boolean }[];
  };
  parameters: { name: string; prompt: string; required: boolean }[];
  columns: PreviewColumn[];
}

export interface PreviewColumn {
  id: string;
  key: string;
  label: string;
  table: string;
  field: string;
  data_type: DataType;
  aggregation: Aggregation;
  align: 'left' | 'center' | 'right';
  format: ColumnFormat | null;
  is_masked: boolean;
}

export interface PreviewResult {
  ok: boolean;
  columns: PreviewColumn[];
  rows: Record<string, unknown>[];
  page: number;
  page_size: number;
  has_more: boolean;
  duration_ms: number;
  truncated: boolean;
  diagnostics: Diagnostic[];
  summary: Record<string, number>;
  fanout_corrected: boolean;
}

export interface CurrentUser {
  id: string;
  email: string;
  full_name: string;
  role: string;
  permissions: string[];
}

export function emptyDefinition(primaryTable = ''): ReportDefinition {
  return {
    version: 1,
    primary_table: primaryTable,
    tables: primaryTable ? [primaryTable] : [],
    joins: [],
    columns: [],
    calculated_columns: [],
    filters: { kind: 'group', op: 'and', children: [] },
    group_by: [],
    sort_by: [],
    visualization: { type: 'table', stacked: false },
    row_limit: 50,
    disable_fanout_correction: false,
  };
}
