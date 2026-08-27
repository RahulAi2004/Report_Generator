/**
 * Report Board types.
 *
 * `records` and `empty_records` are deliberately not part of a board row. The
 * listing is metadata and renders without touching the operational database;
 * counts arrive separately, and `null` means "we could not find out", which is
 * not the same fact as zero.
 */

export interface BoardDashboardLink {
  id: string;
  name: string;
}

export interface BoardReport {
  id: string;
  name: string;
  description: string | null;
  module: string | null;
  section: string | null;
  visibility: 'private' | 'team' | 'organization';
  is_draft: boolean;
  is_favorite: boolean;
  show_in_menu: boolean;
  owner_id: string;
  owner_name: string | null;
  is_mine: boolean;
  field_count: number;
  table_count: number;
  dashboards: BoardDashboardLink[];
  updated_at: string;
  last_run_at: string | null;
  run_count: number;
}

export interface BoardListing {
  reports: BoardReport[];
  total: number;
  can_delete: boolean;
  can_edit: boolean;
}

export interface BoardCount {
  /** null when the count could not be produced. Never render this as 0. */
  records: number | null;
  empty_records: number | null;
  error: string | null;
  cached?: boolean;
}

export const VISIBILITY_LABEL: Record<BoardReport['visibility'], string> = {
  private: 'Private',
  team: 'Team',
  organization: 'Public',
};
