'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { Badge, Button, EmptyState, Skeleton, TypeGlyph } from '@/components/ui/primitives';
import { api, ApiError, type UploadedDataset } from '@/lib/api';

/**
 * Uploads.
 *
 * A spreadsheet becomes a real table in the application's own database, so it
 * can be reported on and joined with the database exactly like any other
 * source. Nothing is ever written to the operational database.
 */
export default function UploadsPage() {
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [message, setMessage] = useState<{ tone: 'good' | 'danger'; text: string } | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);

  const datasets = useQuery({ queryKey: ['uploads'], queryFn: api.listUploads });
  const me = useQuery({ queryKey: ['me'], queryFn: api.me });
  const canManage = Boolean(me.data?.permissions.includes('manage_schema'));

  const upload = useMutation({
    mutationFn: (file: File) => api.uploadFile(file),
    onSuccess: (result) => {
      setMessage({
        tone: 'good',
        text: `“${result.name}” added — ${result.row_count.toLocaleString()} rows, ${result.column_count} columns. It is now available in the report builder.`,
      });
      queryClient.invalidateQueries({ queryKey: ['uploads'] });
      queryClient.invalidateQueries({ queryKey: ['tables'] });
      queryClient.invalidateQueries({ queryKey: ['overview'] });
    },
    onError: (error) =>
      setMessage({
        tone: 'danger',
        text: error instanceof ApiError ? error.message : 'That file could not be uploaded.',
      }),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteUpload(id),
    onSuccess: () => {
      setConfirming(null);
      setMessage({ tone: 'good', text: 'Upload deleted.' });
      queryClient.invalidateQueries({ queryKey: ['uploads'] });
      queryClient.invalidateQueries({ queryKey: ['tables'] });
    },
  });

  const list = datasets.data?.datasets ?? [];

  function accept(files: FileList | null) {
    const file = files?.[0];
    if (!file) return;
    setMessage(null);
    upload.mutate(file);
  }

  return (
    <>
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-line bg-white px-5">
        <div>
          <h1 className="text-md font-semibold">Uploads</h1>
          <p className="text-xs text-ink-muted">
            Spreadsheets you can report on, and join with the database.
          </p>
        </div>
        {list.length > 0 && (
          <span className="text-xs text-ink-muted">
            {list.length} dataset{list.length === 1 ? '' : 's'}
          </span>
        )}
      </header>

      <div className="flex-1 space-y-3 overflow-y-auto p-5">
        {canManage && (
          <div
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              accept(event.dataTransfer.files);
            }}
            className={`rounded-lg border-2 border-dashed px-6 py-8 text-center transition-colors ${
              dragging
                ? 'border-accent bg-accent-soft'
                : 'border-line-strong bg-white hover:border-accent-border'
            }`}
          >
            <svg
              viewBox="0 0 24 24"
              className="mx-auto h-8 w-8 text-accent"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.5}
            >
              <path d="M12 16V4M7 9l5-5 5 5" />
              <path d="M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" />
            </svg>
            <p className="mt-2 text-sm font-medium">
              {upload.isPending ? 'Reading the file…' : 'Drop a CSV or Excel file here'}
            </p>
            <p className="mt-0.5 text-xs text-ink-muted">
              The first row is used as column headings. Types are detected automatically.
            </p>
            <input
              ref={fileInput}
              type="file"
              accept=".csv,.tsv,.txt,.xlsx,.xlsm"
              className="hidden"
              onChange={(event) => accept(event.target.files)}
            />
            <Button
              variant="primary"
              size="sm"
              className="mt-3"
              disabled={upload.isPending}
              onClick={() => fileInput.current?.click()}
            >
              {upload.isPending ? 'Uploading…' : 'Choose a file'}
            </Button>
          </div>
        )}

        {message && (
          <div
            className={`rounded-lg border px-4 py-2.5 text-sm ${
              message.tone === 'good'
                ? 'border-good-border bg-good-soft text-good'
                : 'border-danger-border bg-danger-soft text-danger'
            }`}
          >
            {message.text}
          </div>
        )}

        {datasets.isLoading && <Skeleton className="h-40 w-full" />}

        {!datasets.isLoading && list.length === 0 && (
          <div className="panel">
            <EmptyState
              title="No files uploaded yet"
              hint={
                canManage
                  ? 'Upload a spreadsheet and it becomes available in the report builder, where it can be joined with your database tables.'
                  : 'Ask an administrator to upload one.'
              }
            />
          </div>
        )}

        {list.map((dataset) => (
          <DatasetCard
            key={dataset.id}
            dataset={dataset}
            canManage={canManage}
            expanded={expanded === dataset.id}
            confirming={confirming === dataset.id}
            deleting={remove.isPending}
            onToggle={() => setExpanded(expanded === dataset.id ? null : dataset.id)}
            onAskDelete={() => setConfirming(dataset.id)}
            onCancelDelete={() => setConfirming(null)}
            onConfirmDelete={() => remove.mutate(dataset.id)}
          />
        ))}
      </div>
    </>
  );
}

function DatasetCard({
  dataset,
  canManage,
  expanded,
  confirming,
  deleting,
  onToggle,
  onAskDelete,
  onCancelDelete,
  onConfirmDelete,
}: {
  dataset: UploadedDataset;
  canManage: boolean;
  expanded: boolean;
  confirming: boolean;
  deleting: boolean;
  onToggle: () => void;
  onAskDelete: () => void;
  onCancelDelete: () => void;
  onConfirmDelete: () => void;
}) {
  const preview = useQuery({
    queryKey: ['upload-preview', dataset.id],
    queryFn: () => api.previewUpload(dataset.id),
    enabled: expanded,
  });

  return (
    <section className="panel">
      <div className="panel-header">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="truncate text-sm font-semibold">{dataset.name}</h2>
            <Badge tone="accent">{dataset.row_count.toLocaleString()} rows</Badge>
            <Badge>{dataset.column_count} columns</Badge>
          </div>
          <p className="mt-0.5 truncate text-2xs text-ink-faint">
            {dataset.original_filename} · {(dataset.size_bytes / 1024).toFixed(0)} KB ·
            uploaded {new Date(dataset.created_at).toLocaleDateString()}
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          <Button size="sm" onClick={onToggle}>
            {expanded ? 'Hide' : 'Preview'}
          </Button>
          {canManage &&
            (confirming ? (
              <>
                <Button size="sm" onClick={onCancelDelete}>
                  Cancel
                </Button>
                <Button
                  size="sm"
                  className="border-danger bg-danger text-white hover:bg-danger/90"
                  disabled={deleting}
                  onClick={onConfirmDelete}
                >
                  {deleting ? 'Deleting…' : 'Delete for good'}
                </Button>
              </>
            ) : (
              <Button size="sm" onClick={onAskDelete}>
                Delete
              </Button>
            ))}
        </div>
      </div>

      {confirming && (
        <p className="border-b border-danger-border bg-danger-soft px-4 py-2 text-xs text-danger">
          This drops the data permanently. Any saved report built on it will stop working
          and say which table is missing.
        </p>
      )}

      <div className="flex flex-wrap gap-1.5 px-4 py-2.5">
        {dataset.columns.map((column) => (
          <span
            key={column.name}
            className="inline-flex items-center gap-1 rounded border border-line bg-canvas px-1.5 py-0.5"
            title={`${column.name} · ${column.data_type}`}
          >
            <TypeGlyph dataType={column.data_type} />
            <span className="text-2xs">{column.label}</span>
          </span>
        ))}
      </div>

      {expanded && (
        <div className="border-t border-line">
          {preview.isLoading && <Skeleton className="m-4 h-32" />}
          {preview.data && (
            <div className="overflow-x-auto">
              <table className="striped w-full border-collapse">
                <thead>
                  <tr className="border-b border-line text-left text-2xs uppercase text-ink-faint">
                    {preview.data.columns.map((name) => (
                      <th key={name} className="whitespace-nowrap px-3 py-2 font-medium">
                        {name}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {preview.data.rows.map((row, index) => (
                    <tr key={index} className="border-b border-line/60 last:border-0">
                      {row.map((value, cell) => (
                        <td
                          key={cell}
                          className={`whitespace-nowrap px-3 py-1.5 text-sm ${
                            value === null ? 'text-ink-faint' : ''
                          }`}
                        >
                          {value === null ? '—' : String(value)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="px-4 py-2 text-2xs text-ink-faint">
                Showing {preview.data.rows.length} of{' '}
                {preview.data.row_count.toLocaleString()} rows. Use this dataset in the
                report builder under “Uploaded Files”.
              </p>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
