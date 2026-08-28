/**
 * API connector types.
 *
 * No field carries a token. It goes to the server when a connector is created
 * or its credentials change, and nothing sends one back.
 */

export interface ConnectorResource {
  id: string;
  name: string;
  /** "ad_account" | "page" | "instagram" */
  kind: string;
  detail: Record<string, unknown>;
}

export interface Discovery {
  account_name: string | null;
  account_id: string | null;
  permissions: string[];
  /** Scopes a dataset needs that this token does not have, keyed by dataset. */
  missing_permissions: Record<string, string[]>;
  resources: ConnectorResource[];
  expires_at: string | null;
  detail: string;
}

export interface ConnectorColumn {
  name: string;
  label: string;
  data_type: string;
  nullable: boolean;
  source?: string;
}

export interface ConnectorDataset {
  id: string;
  connector_id: string;
  dataset_key: string;
  resource_id: string;
  resource_name: string;
  display_name: string;
  table_name: string;
  columns: ConnectorColumn[];
  column_count: number;
  row_count: number;
  lookback_days: number;
  is_enabled: boolean;
  /** pending | syncing | ready | error */
  status: string;
  last_error: string | null;
  last_synced_at: string | null;
  last_duration_ms: number;
}

export interface Connector {
  id: string;
  provider: string;
  provider_label: string;
  name: string;
  api_version: string;
  is_active: boolean;
  sync_interval_minutes: number;
  last_checked_at: string | null;
  last_error: string | null;
  discovery: Discovery | null;
  created_at: string;
  datasets: ConnectorDataset[];
}

export interface ProviderDataset {
  key: string;
  label: string;
  description: string;
  resource_kind: string;
  required_permissions: string[];
  time_series: boolean;
}

export interface Provider {
  key: string;
  label: string;
  default_api_version: string;
  datasets: ProviderDataset[];
}

export const RESOURCE_LABELS: Record<string, string> = {
  ad_account: 'Ad account',
  page: 'Facebook Page',
  instagram: 'Instagram profile',
};
