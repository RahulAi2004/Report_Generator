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
  relationships: () => request<{ relationships: unknown[] }>('/schema/relationships'),
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

  // -- saved reports ------------------------------------------------------
  listReports: () => request<{ reports: SavedReportSummary[] }>('/reports'),
  getReport: (id: string) =>
    request<{ id: string; name: string; definition: ReportDefinition }>(`/reports/${id}`),
  createReport: (name: string, definition: ReportDefinition, description?: string) =>
    post<{ id: string; name: string }>('/reports', { name, definition, description }),
  updateReport: (id: string, name: string, definition: ReportDefinition) =>
    request<{ id: string; name: string }>(`/reports/${id}`, {
      method: 'PUT',
      body: JSON.stringify({ name, definition }),
    }),
};

export interface SavedReportSummary {
  id: string;
  name: string;
  description: string | null;
  folder: string | null;
  is_template: boolean;
  is_favorite: boolean;
  updated_at: string;
  last_run_at: string | null;
  run_count: number;
  summary: Record<string, number>;
}
