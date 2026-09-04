/**
 * AI Suggestions types.
 *
 * A suggestion carries a report definition that has already been compiled by
 * the server, so `runnable` and `problems` are facts rather than hopes. Nothing
 * here has been executed: running a suggestion means opening it in the builder.
 */

import type { ReportDefinition } from '@/lib/types';

export interface Suggestion {
  title: string;
  why: string;
  definition: ReportDefinition;
  /** Whether the compiler accepted it. */
  runnable: boolean;
  /** Why it did not, in the compiler's own words. */
  problems: string[];
  summary: Record<string, number>;
  confidence: 'high' | 'medium' | 'low' | null;
  /** Anything the AI had to decide that the question did not say. */
  assumptions: string[];
}

export interface AiStatus {
  available: boolean;
  model: string | null;
  can_configure: boolean;
}

export interface AiSettings {
  base_url: string;
  model: string;
  has_api_key: boolean;
  enabled: boolean;
  configured: boolean;
  defaults: { base_url: string; model: string };
}

export interface AiContext {
  context: string;
  characters: number;
  /** How many tables fitted in the budget. */
  tables: number;
  /** How many exist in total. */
  tables_total: number;
  trimmed: boolean;
  note: string;
}
