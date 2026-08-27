/**
 * Database connection types.
 *
 * No field here ever carries a password. It goes to the server when a
 * connection is created or its credentials change, and nothing sends one back.
 */

export interface Connection {
  id: string;
  name: string;
  database_type: string;
  /** null for the connection configured on the server: infrastructure, not content. */
  host: string | null;
  port: number | null;
  database_name: string;
  username: string | null;
  ssl_mode: string | null;
  is_read_only: boolean;
  is_replica: boolean;
  is_active: boolean;
  is_builtin: boolean;
  /** Whether the application is currently reading from this one. */
  is_selected: boolean;
  schemas: string[];
  last_scanned_at: string | null;
  created_at: string | null;
}

export interface ConnectionListing {
  connections: Connection[];
  active_id: string;
  can_store_passwords: boolean;
}

export interface ProbeResult {
  reachable: boolean;
  read_only: boolean;
  detail: string;
  server_version: string | null;
  database_name: string | null;
  /** Every database on that server the credentials can see. */
  databases: string[];
  schemas: string[];
}

export interface ConnectionInput {
  name: string;
  host: string;
  port: number;
  database_name: string;
  username: string;
  password?: string;
  ssl_mode: 'disable' | 'allow' | 'prefer' | 'require';
  is_replica: boolean;
}
