'use client';

import { useMutation, useQueries, useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ColumnGrid } from '@/components/builder/ColumnGrid';
import { ColumnInspector } from '@/components/builder/ColumnInspector';
import { DataSourcePanel } from '@/components/builder/DataSourcePanel';
import { DiagnosticsBar } from '@/components/builder/DiagnosticsBar';
import { FieldPanel } from '@/components/builder/FieldPanel';
import { JoinCanvas } from '@/components/builder/JoinCanvas';
import { PreviewTable } from '@/components/builder/PreviewTable';
import { QueryPanels } from '@/components/builder/QueryPanels';
import { WorkflowCards } from '@/components/builder/WorkflowCards';
import { Badge, Button } from '@/components/ui/primitives';
import { api, ApiError } from '@/lib/api';
import type { PreviewResult, SchemaTable, ValidationResult } from '@/lib/types';
import { summarize, useBuilder } from '@/store/builder';

/**
 * Dynamic Report Builder.
 *
 * Owns no report state of its own -- everything lives in the builder store, and
 * this component wires the store to the backend. Two backend calls drive it:
 *
 *   /reports/validate  runs on every edit (debounced). Compiles without
 *                      executing, so problems surface while building.
 *   /reports/preview   runs on demand. Executes and returns one page.
 *
 * Separating them means the expensive call happens only when asked, while
 * correctness feedback stays immediate.
 */
export default function BuilderPage() {
  const builder = useBuilder();
  const { definition, selectedTable, selectedColumnId, reportName } = builder;

  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [fullscreen, setFullscreen] = useState(false);
  const [sqlOpen, setSqlOpen] = useState(false);
  const [activeSection, setActiveSection] = useState<string>();
  const [toast, setToast] = useState<string | null>(null);

  const { data: me } = useQuery({ queryKey: ['me'], queryFn: api.me });
  const can = (permission: string) => me?.permissions.includes(permission) ?? false;

  // -- schema ---------------------------------------------------------------
  const { data: catalog, isLoading: catalogLoading } = useQuery({
    queryKey: ['tables'],
    queryFn: () => api.tables(),
  });

  // Column metadata for every selected table, fetched in parallel.
  const tableQueries = useQueries({
    queries: definition.tables.map((name) => ({
      queryKey: ['table', name],
      queryFn: () => api.table(name),
      staleTime: 300_000,
    })),
  });

  const tables = useMemo(() => {
    const map: Record<string, SchemaTable> = {};
    for (const query of tableQueries) if (query.data) map[query.data.name] = query.data;
    return map;
  }, [tableQueries]);

  const activeTableQuery = useQuery({
    queryKey: ['table', selectedTable],
    queryFn: () => api.table(selectedTable!),
    enabled: Boolean(selectedTable),
    staleTime: 300_000,
  });

  const labels = useMemo(() => {
    const map: Record<string, string> = {};
    for (const category of catalog?.categories ?? []) {
      for (const table of category.tables) map[table.name] = table.label;
    }
    return map;
  }, [catalog]);

  // -- live validation (debounced) -----------------------------------------
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (definition.columns.length === 0) {
      setValidation(null);
      return;
    }
    if (debounce.current) clearTimeout(debounce.current);
    debounce.current = setTimeout(() => {
      api
        .validate(definition)
        .then(setValidation)
        .catch(() => setValidation(null));
    }, 350);
    return () => {
      if (debounce.current) clearTimeout(debounce.current);
    };
  }, [definition]);

  // -- preview --------------------------------------------------------------
  const runPreview = useCallback(
    async (targetPage = page, size = pageSize) => {
      if (definition.columns.length === 0) {
        setPreview(null);
        setPreviewError('Add at least one column first.');
        return;
      }
      setPreviewError(null);
      try {
        const result = await api.preview(definition, targetPage, size);
        setPreview(result);
        if (!result.ok) {
          setPreviewError('This report is not valid yet — see the issues above.');
        }
      } catch (caught) {
        setPreview(null);
        setPreviewError(
          caught instanceof ApiError ? caught.message : 'Could not run this report.',
        );
      }
    },
    [definition, page, pageSize],
  );

  const previewMutation = useMutation({ mutationFn: () => runPreview(page, pageSize) });

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (builder.reportId) {
        return api.updateReport(builder.reportId, reportName, definition);
      }
      return api.createReport(reportName, definition);
    },
    onSuccess: (saved) => {
      builder.loadReport(saved.id, saved.name, definition);
      setToast(`Saved “${saved.name}”`);
      setTimeout(() => setToast(null), 2600);
    },
  });

  const sqlQuery = useQuery({
    queryKey: ['sql', definition],
    queryFn: () => api.sql(definition),
    enabled: sqlOpen && can('view_sql'),
  });

  const scrollTo = (section: string) => {
    setActiveSection(section);
    document
      .getElementById(`section-${section}`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  const summary = summarize(definition);
  const selectedFields = useMemo(
    () => new Set(definition.columns.map((column) => `${column.table}.${column.field}`)),
    [definition.columns],
  );
  const selectedColumn =
    definition.columns.find((column) => column.id === selectedColumnId) ?? null;
  const diagnostics = validation?.diagnostics ?? [];
  const hasErrors = diagnostics.some((item) => item.severity === 'error');

  return (
    <>
      {/* ---- header ---- */}
      <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-white px-4">
        <h1 className="shrink-0 text-md font-semibold">Dynamic Report Builder</h1>

        <div className="ml-2 flex min-w-0 flex-1 items-center gap-2">
          <label htmlFor="report-name" className="shrink-0 text-xs text-ink-muted">
            Report Name <span className="text-danger">*</span>
          </label>
          <div className="relative min-w-0 max-w-[420px] flex-1">
            <input
              id="report-name"
              value={reportName}
              onChange={(event) => builder.setReportName(event.target.value)}
              className="field pr-7 font-medium"
            />
            <svg
              viewBox="0 0 24 24"
              className="pointer-events-none absolute right-2 top-1/2 h-3.5 w-3.5
                         -translate-y-1/2 text-ink-faint"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.8}
            >
              <path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />
            </svg>
          </div>
          {builder.dirty && <Badge tone="warn">Unsaved</Badge>}
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          <Button
            size="sm"
            onClick={() => saveMutation.mutate()}
            disabled={!can('save_report') || saveMutation.isPending || !reportName.trim()}
            title={can('save_report') ? undefined : 'Your role cannot save reports'}
          >
            {saveMutation.isPending ? 'Saving…' : 'Save'}
          </Button>
          <Button
            size="sm"
            onClick={() => {
              builder.loadReport(null, `${reportName} (copy)`, definition);
              setToast('Now editing a copy — press Save to store it');
              setTimeout(() => setToast(null), 2600);
            }}
            disabled={!can('save_report')}
          >
            Save As
          </Button>
          <Button
            size="sm"
            onClick={() => setSqlOpen(true)}
            disabled={!can('view_sql') || hasErrors}
            title={can('view_sql') ? 'Inspect generated SQL' : 'Your role cannot view SQL'}
          >
            View SQL
          </Button>
          <Button
            size="sm"
            variant="primary"
            onClick={() => previewMutation.mutate()}
            disabled={previewMutation.isPending || definition.columns.length === 0}
          >
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="currentColor">
              <path d="M8 5v14l11-7z" />
            </svg>
            {previewMutation.isPending ? 'Running…' : 'Run Report'}
          </Button>
        </div>
      </header>

      {/* ---- workflow strip ---- */}
      <WorkflowCards summary={summary} activeSection={activeSection} onNavigate={scrollTo} />

      <DiagnosticsBar diagnostics={diagnostics} onNavigate={scrollTo} />

      {/* ---- work area ---- */}
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <div id="section-sources" className="flex">
          <DataSourcePanel
            categories={catalog?.categories ?? []}
            loading={catalogLoading}
            selectedTables={definition.tables}
            primaryTable={definition.primary_table}
            activeTable={selectedTable}
            onToggleTable={builder.toggleTable}
            onSelectTable={builder.selectTable}
            onSetPrimary={builder.setPrimaryTable}
          />
          <FieldPanel
            table={activeTableQuery.data ?? null}
            loading={activeTableQuery.isLoading}
            selectedFields={selectedFields}
            onToggleField={builder.toggleField}
          />
        </div>

        <main className="min-w-0 flex-1 space-y-3 overflow-y-auto bg-canvas p-3">
          <ColumnGrid
            columns={definition.columns}
            tables={tables}
            selectedColumnId={selectedColumnId}
            onSelect={builder.selectColumn}
            onUpdate={builder.updateColumn}
            onRemove={builder.removeColumn}
            onMove={builder.moveColumn}
          />

          <JoinCanvas
            validation={validation}
            primaryTable={definition.primary_table}
            labels={labels}
          />

          <QueryPanels
            definition={definition}
            tables={tables}
            onAddFilter={builder.addFilter}
            onUpdateFilter={builder.updateFilter}
            onRemoveFilter={builder.removeFilter}
            onSetGroupOp={builder.setFilterGroupOp}
            onAddGroupBy={builder.addGroupBy}
            onRemoveGroupBy={builder.removeGroupBy}
            onAddSort={builder.addSort}
            onUpdateSort={builder.updateSort}
            onRemoveSort={builder.removeSort}
          />

          <PreviewTable
            result={preview}
            loading={previewMutation.isPending}
            error={previewError}
            page={page}
            pageSize={pageSize}
            onPageChange={(next) => {
              setPage(next);
              runPreview(next, pageSize);
            }}
            onPageSizeChange={(size) => {
              setPageSize(size);
              setPage(1);
              runPreview(1, size);
            }}
            onRefresh={() => previewMutation.mutate()}
            fullscreen={fullscreen}
            onToggleFullscreen={() => setFullscreen((value) => !value)}
          />
        </main>

        <ColumnInspector
          column={selectedColumn}
          tables={tables}
          onUpdate={builder.updateColumn}
        />
      </div>

      {sqlOpen && (
        <SqlDialog
          sql={sqlQuery.data?.sql}
          tables={sqlQuery.data?.tables_used ?? []}
          valuesIncluded={sqlQuery.data?.values_included ?? false}
          loading={sqlQuery.isLoading}
          onClose={() => setSqlOpen(false)}
        />
      )}

      {toast && (
        <div className="fixed bottom-4 right-4 z-50 rounded border border-good-border
                        bg-good-soft px-3 py-2 text-sm text-good shadow-pop">
          {toast}
        </div>
      )}
    </>
  );
}

/* -------------------------------------------------------------------------- */
function SqlDialog({
  sql,
  tables,
  valuesIncluded,
  loading,
  onClose,
}: {
  sql?: string;
  tables: string[];
  valuesIncluded: boolean;
  loading: boolean;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-rail/40 p-6"
      onClick={onClose}
    >
      <div
        className="flex max-h-[80vh] w-full max-w-3xl flex-col overflow-hidden rounded-lg
                   border border-line bg-white shadow-pop"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="panel-header">
          <div>
            <h2 className="panel-title">Generated SQL</h2>
            <p className="mt-0.5 text-2xs text-ink-faint">
              Read-only. {valuesIncluded ? 'Filter values shown.' : 'Filter values hidden by policy.'}
            </p>
          </div>
          <div className="flex items-center gap-1.5">
            <Button
              size="sm"
              onClick={() => {
                if (sql) {
                  navigator.clipboard?.writeText(sql);
                  setCopied(true);
                  setTimeout(() => setCopied(false), 1600);
                }
              }}
              disabled={!sql}
            >
              {copied ? 'Copied' : 'Copy'}
            </Button>
            <Button size="sm" onClick={onClose}>
              Close
            </Button>
          </div>
        </div>

        <div className="flex-1 overflow-auto bg-[#0F2137] p-4">
          {loading ? (
            <p className="text-xs text-white/60">Compiling…</p>
          ) : (
            <pre className="whitespace-pre font-mono text-xs leading-relaxed text-[#D6E4FF]">
              {sql ?? 'No SQL available.'}
            </pre>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-1.5 border-t border-line px-4 py-2">
          <span className="text-2xs text-ink-muted">Tables used:</span>
          {tables.map((table) => (
            <Badge key={table}>{table}</Badge>
          ))}
        </div>
      </div>
    </div>
  );
}
