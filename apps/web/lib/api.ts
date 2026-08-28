/**
 * API client.
 *
 * The browser talks only to this backend -- it holds no database credentials
 * and has no database reachability. Errors arrive already translated into
 * plain language by the API, so they are safe to render directly.
 */

import type {
  CurrentUser,
  PreviewResult,
  ReportDefinition,
  SchemaCategory,
  SchemaTable,
  ValidationResult,
} from './types';
import type { BoardCount, BoardListing } from './board-types';
import type {
  Connector,
  ConnectorDataset,
  Discovery,
  Provider,
} from './connector-types';
import type {
  Connection,
  ConnectionInput,
  ConnectionListing,
  ProbeResult,
} from './connection-types';
import type {
  DashboardDefinition,
  DashboardOptions,
  DashboardPreview,
  DashboardSummary,
  DateField,
  PanelResult,
} from './dashboard-types';

const BASE = '/api/v1';

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly diagnostics?: unknown[],
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  });

  if (!response.ok) {
    let detail = 'Something went wrong.';
    let diagnostics: unknown[] | undefined;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
      diagnostics = body.diagnostics;
    } catch {
      /* non-JSON error body; keep the generic message */
    }
    throw new ApiError(detail, response.status, diagnostics);
  }

  return response.json() as Promise<T>;
}

const post = <T>(path: string, body: unknown) =>
  request<T>(path, { method: 'POST', body: JSON.stringify(body) });

export const api = {
  // -- auth ---------------------------------------------------------------
  login: (email: string, password: string) =>
    post<{ token: string; user: CurrentUser }>('/auth/login', { email, password }),
  logout: () => post<{ ok: boolean }>('/auth/logout', {}),
  me: () => request<CurrentUser>('/auth/me'),

  // -- schema -------------------------------------------------------------
  tables: (search?: string) =>
    request<{ categories: SchemaCategory[]; total_tables: number }>(
      `/schema/tables${search ? `?search=${encodeURIComponent(search)}` : ''}`,
    ),
  table: (name: string) => request<SchemaTable>(`/schema/tables/${encodeURIComponent(name)}`),
  /** The values a column actually contains, for the filter editor. */
  columnValues: (table: string, column: string, search?: string) =>
    request<{ values: string[]; supported: boolean; truncated?: boolean; reason?: string }>(
      `/schema/tables/${encodeURIComponent(table)}/columns/${encodeURIComponent(column)}/values` +
        (search ? `?search=${encodeURIComponent(search)}` : ''),
    ),

  relationships: () => request<{ relationships: Relationship[] }>('/schema/relationships'),
  relationshipSuggestions: () =>
    request<{ suggestions: RelationshipSuggestion[] }>('/schema/relationships/suggestions'),
  acceptRelationships: (items: RelationshipSuggestion[]) =>
    post<{ created: number; skipped: number }>(
      '/schema/relationships',
      items.map(({ left_table, left_column, right_table, right_column, cardinality, confidence }) => ({
        left_table, left_column, right_table, right_column, cardinality, confidence,
      })),
    ),
  deleteRelationship: (id: string) =>
    request<{ ok: boolean }>(`/schema/relationships/${id}`, { method: 'DELETE' }),
  overview: () => request<Record<string, unknown>>('/schema/overview'),

  // -- report engine ------------------------------------------------------
  validate: (definition: ReportDefinition, parameters: Record<string, unknown> = {}) =>
    post<ValidationResult>('/reports/validate', { definition, parameters }),

  preview: (
    definition: ReportDefinition,
    page = 1,
    pageSize = 50,
    parameters: Record<string, unknown> = {},
  ) =>
    post<PreviewResult>('/reports/preview', {
      definition,
      parameters,
      page,
      page_size: pageSize,
    }),

  sql: (definition: ReportDefinition, parameters: Record<string, unknown> = {}) =>
    post<{ sql: string; values_included: boolean; tables_used: string[]; row_limit: number }>(
      '/reports/sql',
      { definition, parameters },
    ),

  /** Total rows the report returns, ignoring paging. Null when too costly. */
  count: (definition: ReportDefinition, parameters: Record<string, unknown> = {}) =>
    post<{ total: number | null; reason?: string }>('/reports/count', {
      definition,
      parameters,
    }),

  /**
   * Download the full report.
   *
   * The response is a file, not JSON, so this bypasses `request` and drives the
   * browser's own download rather than buffering the whole thing in a string.
   */
  export: async (
    definition: ReportDefinition,
    format: 'csv' | 'xlsx' | 'pdf',
    reportName: string,
    parameters: Record<string, unknown> = {},
  ): Promise<void> => {
    const response = await fetch(`${BASE}/reports/export`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ definition, parameters, format, report_name: reportName }),
    });

    if (!response.ok) {
      let detail = 'The export could not be produced.';
      try {
        detail = (await response.json()).detail ?? detail;
      } catch {
        /* not JSON */
      }
      throw new ApiError(detail, response.status);
    }

    const disposition = response.headers.get('content-disposition') ?? '';
    const match = disposition.match(/filename="?([^"]+)"?/);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);

    const link = document.createElement('a');
    link.href = url;
    link.download = match?.[1] ?? `report.${format}`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    // Revoking immediately can cancel the download in some browsers.
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  },

  // -- uploaded datasets --------------------------------------------------
  listUploads: () => request<{ datasets: UploadedDataset[] }>('/uploads'),

  /** Multipart, so this bypasses the JSON helper. */
  uploadFile: async (file: File, name = '', description = ''): Promise<UploadedDataset> => {
    const form = new FormData();
    form.append('file', file);
    if (name) form.append('name', name);
    if (description) form.append('description', description);

    const response = await fetch(`${BASE}/uploads`, {
      method: 'POST',
      credentials: 'include',
      body: form, // no Content-Type: the browser sets the multipart boundary
    });
    if (!response.ok) {
      let detail = 'That file could not be uploaded.';
      try {
        detail = (await response.json()).detail ?? detail;
      } catch {
        /* not JSON */
      }
      throw new ApiError(detail, response.status);
    }
    return response.json();
  },

  previewUpload: (id: string) =>
    request<{ columns: string[]; rows: (string | number | null)[][]; row_count: number }>(
      `/uploads/${id}/preview`,
    ),
  deleteUpload: (id: string) =>
    request<{ ok: boolean }>(`/uploads/${id}`, { method: 'DELETE' }),

  // -- saved reports ------------------------------------------------------
  listReports: (options: { module?: string; pinned?: boolean } = {}) => {
    const query = new URLSearchParams();
    if (options.module) query.set('module', options.module);
    if (options.pinned) query.set('pinned', 'true');
    const suffix = query.toString();
    return request<{ reports: SavedReportSummary[] }>(
      `/reports${suffix ? `?${suffix}` : ''}`,
    );
  },
  getReport: (id: string) =>
    request<{ id: string; name: string; definition: ReportDefinition }>(`/reports/${id}`),
  createReport: (name: string, definition: ReportDefinition, description?: string) =>
    post<{ id: string; name: string }>('/reports', { name, definition, description }),
  reportModules: () =>
    request<{ modules: { name: string; sections: string[] }[] }>('/reports/modules'),

  /** Save with placement, access and behaviour, as the Save Report screen collects. */
  createReportFull: (definition: ReportDefinition, options: SaveOptions) =>
    post<{ id: string; name: string }>('/reports', { ...options, definition }),
  updateReportFull: (id: string, definition: ReportDefinition, options: SaveOptions) =>
    request<{ id: string; name: string }>(`/reports/${id}`, {
      method: 'PUT',
      body: JSON.stringify({ ...options, definition }),
    }),

  // -- API connectors -----------------------------------------------------
  connectorProviders: () => request<{ providers: Provider[] }>('/connectors/providers'),
  /** Ask the provider what a credential can reach, without storing it. */
  discoverConnector: (body: {
    provider: string; token: string; api_version?: string;
    app_id?: string; app_secret?: string;
  }) =>
    post<Discovery>('/connectors/discover', body),
  connectors: () =>
    request<{ connectors: Connector[]; can_store_tokens: boolean }>('/connectors'),
  createConnector: (body: {
    provider: string; name: string; token: string;
    app_id?: string; app_secret?: string;
    api_version?: string; sync_interval_minutes?: number;
  }) => post<{ id: string; name: string; discovery: Discovery }>('/connectors', body),
  refreshDiscovery: (id: string) =>
    post<Discovery>(`/connectors/${id}/refresh-discovery`, {}),
  updateConnector: (id: string, body: Record<string, unknown>) =>
    request<{ id: string; name: string }>(`/connectors/${id}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  deleteConnector: (id: string) =>
    request<{ ok: boolean }>(`/connectors/${id}`, { method: 'DELETE' }),

  addConnectorDataset: (
    connectorId: string,
    body: {
      dataset_key: string; resource_id: string;
      resource_name?: string; display_name?: string; lookback_days?: number;
    },
  ) => post<ConnectorDataset>(`/connectors/${connectorId}/datasets`, body),
  syncDataset: (id: string) =>
    post<{ ok: boolean; already_running: boolean }>(`/connectors/datasets/${id}/sync`, {}),
  updateDataset: (id: string, body: Record<string, unknown>) =>
    request<ConnectorDataset>(`/connectors/datasets/${id}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  deleteDataset: (id: string) =>
    request<{ ok: boolean }>(`/connectors/datasets/${id}`, { method: 'DELETE' }),
  previewDataset: (id: string) =>
    request<{ columns: string[]; rows: (string | null)[][]; status: string }>(
      `/connectors/datasets/${id}/preview`,
    ),

  // -- connections --------------------------------------------------------
  connections: () => request<ConnectionListing>('/connections'),
  /** Try credentials without saving, and find out what is on the other side. */
  testConnection: (body: {
    host: string; port: number; database_name: string;
    username: string; password: string; ssl_mode: string;
  }) => post<ProbeResult>('/connections/test', body),
  createConnection: (body: ConnectionInput) =>
    post<{ id: string; name: string; probe: ProbeResult }>('/connections', body),
  updateConnection: (id: string, body: ConnectionInput) =>
    request<{ id: string; name: string; probe: ProbeResult }>(`/connections/${id}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  activateConnection: (id: string) =>
    post<{ active_id: string }>(`/connections/${id}/activate`, {}),
  deleteConnection: (id: string) =>
    request<{ ok: boolean }>(`/connections/${id}`, { method: 'DELETE' }),

  // -- report board -------------------------------------------------------
  board: (options: { module?: string; section?: string; search?: string } = {}) => {
    const query = new URLSearchParams();
    if (options.module) query.set('module', options.module);
    if (options.section) query.set('section', options.section);
    if (options.search) query.set('search', options.search);
    const suffix = query.toString();
    return request<BoardListing>(`/board/reports${suffix ? `?${suffix}` : ''}`);
  },
  /** Record counts for the rows on screen, in one request. */
  boardCounts: (reportIds: string[], refresh = false) =>
    post<{ counts: Record<string, BoardCount> }>('/board/counts', {
      report_ids: reportIds,
      refresh,
    }),
  duplicateReport: (id: string) =>
    post<{ id: string; name: string }>(`/board/reports/${id}/duplicate`, {}),

  // -- dashboards ---------------------------------------------------------
  /** Every metric card's number, with what each was actually measured over. */
  dashboardPreview: (definition: DashboardDefinition) =>
    post<DashboardPreview>('/dashboards/preview', { definition }),
  dashboardPanel: (
    definition: DashboardDefinition,
    panelId: string,
    page = 1,
    pageSize = 10,
  ) =>
    post<PanelResult>('/dashboards/panel', {
      definition,
      panel_id: panelId,
      page,
      page_size: pageSize,
    }),
  dashboardOptions: () => request<DashboardOptions>('/dashboards/options'),
  suggestDateField: (table: string) =>
    request<{ date_field: DateField | null }>(
      `/dashboards/suggest-date-field?table=${encodeURIComponent(table)}`,
    ),
  listDashboards: (app?: string) =>
    request<{ dashboards: DashboardSummary[] }>(
      `/dashboards${app ? `?app=${encodeURIComponent(app)}` : ''}`,
    ),
  getDashboard: (id: string) =>
    request<DashboardSummary & { definition: DashboardDefinition }>(`/dashboards/${id}`),
  createDashboard: (body: DashboardSaveOptions & { definition: DashboardDefinition }) =>
    post<{ id: string; name: string }>('/dashboards', body),
  updateDashboard: (
    id: string,
    body: DashboardSaveOptions & { definition: DashboardDefinition },
  ) =>
    request<{ id: string; name: string }>(`/dashboards/${id}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  deleteDashboard: (id: string) =>
    request<{ ok: boolean }>(`/dashboards/${id}`, { method: 'DELETE' }),

  deleteReport: (id: string) =>
    request<{ ok: boolean }>(`/reports/${id}`, { method: 'DELETE' }),
  updateReport: (id: string, name: string, definition: ReportDefinition) =>
    request<{ id: string; name: string }>(`/reports/${id}`, {
      method: 'PUT',
      body: JSON.stringify({ name, definition }),
    }),
};

export interface DashboardSaveOptions {
  name: string;
  description?: string;
  visibility?: 'private' | 'team' | 'organization';
  show_in_menu?: boolean;
  is_default?: boolean;
}

export interface SavedReportSummary {
  id: string;
  name: string;
  description: string | null;
  module?: string | null;
  section?: string | null;
  visibility?: 'private' | 'team' | 'organization';
  is_draft?: boolean;
  pin_to_dashboard?: boolean;
  auto_refresh?: boolean;
  show_in_menu?: boolean;
  /** Present only for pinned reports, so the dashboard can run them. */
  definition?: ReportDefinition | null;
  folder: string | null;
  is_template: boolean;
  is_favorite: boolean;
  updated_at: string;
  last_run_at: string | null;
  run_count: number;
  summary: Record<string, number>;
}


export interface Relationship {
  id: string;
  left_table: string;
  left_column: string;
  right_table: string;
  right_column: string;
  cardinality: string;
  join_type: string;
  source: 'physical' | 'manual' | 'inferred';
  confidence: number;
}

export interface RelationshipSuggestion {
  left_table: string;
  left_column: string;
  right_table: string;
  right_column: string;
  cardinality: string;
  join_type: string;
  confidence: number;
  reason: string;
}


export interface UploadedDataset {
  id: string;
  name: string;
  description: string | null;
  original_filename: string;
  table_name: string;
  row_count: number;
  column_count: number;
  columns: { name: string; label: string; data_type: string; nullable: boolean }[];
  size_bytes: number;
  created_at: string;
}


export type { PreviewResult };

export interface SaveOptions {
  name: string;
  description?: string;
  module?: string;
  section?: string;
  visibility: 'private' | 'team' | 'organization';
  allow_duplicate: boolean;
  show_in_menu: boolean;
  save_filters_and_sorting: boolean;
  pin_to_dashboard: boolean;
  auto_refresh: boolean;
  is_draft: boolean;
}
