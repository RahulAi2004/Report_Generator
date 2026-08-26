'use client';

import { useMutation, useQuery } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useEffect, useMemo, useState } from 'react';
import { Badge, Button, Checkbox, Select, Tooltip } from '@/components/ui/primitives';
import { api, ApiError } from '@/lib/api';
import { summarize, useBuilder } from '@/store/builder';

/**
 * Save Report.
 *
 * Filing a report is a decision, not a text box: where it belongs, who may see
 * it, and whether the filters the author happened to be using travel with it.
 * Doing that on its own screen keeps those choices deliberate, and keeps the
 * builder free of a modal that would have to grow every time one is added.
 */

type Visibility = 'private' | 'team' | 'organization';

export default function SaveReportPage() {
  const router = useRouter();
  const builder = useBuilder();
  const { definition, reportName, reportId } = builder;

  const [name, setName] = useState(reportName);
  const [description, setDescription] = useState('');
  const [module, setModule] = useState('');
  const [section, setSection] = useState('');
  const [visibility, setVisibility] = useState<Visibility>('private');
  const [allowDuplicate, setAllowDuplicate] = useState(true);
  const [showInMenu, setShowInMenu] = useState(true);
  const [saveFilters, setSaveFilters] = useState(true);
  const [pinToDashboard, setPinToDashboard] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const modules = useQuery({ queryKey: ['report-modules'], queryFn: api.reportModules });
  const summary = summarize(definition);

  const validation = useQuery({
    queryKey: ['save-validate', definition],
    queryFn: () => api.validate(definition),
    enabled: definition.columns.length > 0,
  });

  const sections = useMemo(
    () => modules.data?.modules.find((m) => m.name === module)?.sections ?? [],
    [modules.data, module],
  );

  // Default to the first module once the list arrives.
  useEffect(() => {
    if (!module && modules.data?.modules.length) setModule(modules.data.modules[0].name);
  }, [modules.data, module]);

  useEffect(() => {
    if (sections.length && !sections.includes(section)) setSection(sections[0]);
  }, [sections, section]);

  const save = useMutation({
    mutationFn: (asDraft: boolean) => {
      const body = {
        name: name.trim(),
        description: description.trim() || undefined,
        module: module || undefined,
        section: section || undefined,
        visibility,
        allow_duplicate: allowDuplicate,
        show_in_menu: showInMenu,
        save_filters_and_sorting: saveFilters,
        pin_to_dashboard: pinToDashboard,
        auto_refresh: autoRefresh,
        is_draft: asDraft,
      };
      return reportId
        ? api.updateReportFull(reportId, definition, body)
        : api.createReportFull(definition, body);
    },
    onSuccess: (saved) => {
      builder.loadReport(saved.id, saved.name, definition);
      router.push('/reports');
    },
    onError: (caught) =>
      setError(caught instanceof ApiError ? caught.message : 'The report could not be saved.'),
  });

  const tables = definition.tables.length;
  const joins = validation.data?.join_plan?.steps.length ?? 0;
  const canSave = Boolean(name.trim()) && definition.columns.length > 0 && !save.isPending;

  return (
    <>
      <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-line bg-white px-5">
        <nav className="flex items-center gap-2 text-sm">
          <button
            type="button"
            onClick={() => router.push('/reports')}
            className="font-medium text-accent hover:underline"
          >
            Reports
          </button>
          <span className="text-ink-faint">/</span>
          <button
            type="button"
            onClick={() => router.push('/reports/builder')}
            className="font-medium text-accent hover:underline"
          >
            Dynamic Report Builder
          </button>
          <span className="text-ink-faint">/</span>
          <span className="font-semibold">Save Report</span>
        </nav>

        <div className="flex shrink-0 items-center gap-1.5">
          <Button size="sm" onClick={() => router.push('/reports/builder')}>
            Cancel
          </Button>
          <Button
            size="sm"
            disabled={!canSave}
            onClick={() => save.mutate(true)}
            title="Save without offering it to anyone else yet"
          >
            Save as Draft
          </Button>
          <Button size="sm" variant="primary" disabled={!canSave} onClick={() => save.mutate(false)}>
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={1.8}>
              <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
              <path d="M17 21v-8H7v8M7 3v5h8" />
            </svg>
            {save.isPending ? 'Saving…' : 'Save Report'}
          </Button>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto p-5">
        <div className="mb-4">
          <h1 className="text-lg font-semibold">Save Report</h1>
          <p className="text-sm text-ink-muted">
            Save this report to a module and section so it can be reused later.
          </p>
        </div>

        {definition.columns.length === 0 && (
          <div className="mb-3 rounded-lg border border-warn-border bg-warn-soft px-4 py-2.5 text-sm text-warn">
            This report has no columns yet, so there is nothing to save. Go back to the
            builder and pick some fields first.
          </div>
        )}

        {error && (
          <div className="mb-3 rounded-lg border border-danger-border bg-danger-soft px-4 py-2.5 text-sm text-danger">
            {error}
          </div>
        )}

        <div className="grid gap-3 xl:grid-cols-[1fr_340px]">
          <div className="space-y-3">
            <Section letter="A" title="Report Details">
              <div className="grid gap-3 md:grid-cols-[1fr_1.3fr_0.8fr]">
                <Field label="Report Name" required>
                  <input
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    className="field"
                    autoFocus
                  />
                </Field>
                <Field label="Description">
                  <textarea
                    value={description}
                    onChange={(event) => setDescription(event.target.value)}
                    rows={2}
                    placeholder="What question does this report answer?"
                    className="field resize-y"
                  />
                </Field>
                <Field label="Report Type">
                  <input value="Dynamic Report" disabled className="field" />
                </Field>
              </div>
            </Section>

            <Section
              letter="B"
              title="Module & Section"
              hint="Choose where this saved report should appear in the system."
            >
              <div className="grid gap-3 md:grid-cols-2">
                <Field label="Module" required>
                  <Select
                    value={module}
                    onChange={setModule}
                    options={(modules.data?.modules ?? []).map((m) => ({
                      value: m.name,
                      label: m.name,
                    }))}
                  />
                </Field>
                <Field label="Section" required>
                  <Select
                    value={section}
                    onChange={setSection}
                    options={sections.map((s) => ({ value: s, label: s }))}
                    placeholder={sections.length ? undefined : 'No sections'}
                  />
                </Field>
              </div>

              <p className="label mt-3">Available Modules</p>
              <div className="flex flex-wrap gap-1.5">
                {(modules.data?.modules ?? []).map((m) => (
                  <button
                    key={m.name}
                    type="button"
                    onClick={() => setModule(m.name)}
                    className={
                      module === m.name
                        ? 'inline-flex items-center gap-1.5 rounded border border-accent bg-accent-soft px-2.5 py-1.5 text-sm font-medium text-accent'
                        : 'inline-flex items-center gap-1.5 rounded border border-line bg-white px-2.5 py-1.5 text-sm text-ink-muted hover:border-accent-border hover:bg-accent-soft/50'
                    }
                  >
                    <FolderIcon />
                    {m.name}
                  </button>
                ))}
              </div>
            </Section>

            <Section letter="C" title="Database Source Summary">
              <div className="grid gap-3 rounded-lg border border-line bg-canvas/60 p-3 sm:grid-cols-2 lg:grid-cols-4">
                <Stat
                  tone="good"
                  icon={<DatabaseIcon />}
                  label="Connected Database"
                  value={validation.data ? 'Active' : 'Checking…'}
                />
                <Stat
                  tone="accent"
                  icon={<TableIcon />}
                  label="Tables used"
                  value={definition.tables.join(', ') || '—'}
                />
                <Stat
                  tone="purple"
                  icon={<LinkIcon />}
                  label="Relationships"
                  value={`${joins} join${joins === 1 ? '' : 's'}`}
                />
                <Stat
                  tone="teal"
                  icon={<span className="text-xs font-semibold">Ab</span>}
                  label="Selected fields"
                  value={String(summary.fields_selected)}
                />
              </div>
              <p className="mt-2 text-xs text-ink-muted">
                This report is stored as configuration, so it pulls live data from the
                connected database every time it runs.
              </p>
            </Section>

            <Section letter="D" title="Access & Visibility">
              <div className="grid gap-4 md:grid-cols-2">
                <div>
                  <p className="label">Visibility</p>
                  <div className="flex flex-wrap gap-1.5">
                    {(
                      [
                        ['private', 'Private', <LockIcon key="l" />],
                        ['team', 'Team', <PeopleIcon key="p" />],
                        ['organization', 'Organization', <BuildingIcon key="b" />],
                      ] as const
                    ).map(([value, label, icon]) => (
                      <button
                        key={value}
                        type="button"
                        onClick={() => setVisibility(value)}
                        className={
                          visibility === value
                            ? 'inline-flex items-center gap-2 rounded border border-accent bg-accent-soft px-3 py-1.5 text-sm font-medium text-accent'
                            : 'inline-flex items-center gap-2 rounded border border-line bg-white px-3 py-1.5 text-sm text-ink-muted hover:border-accent-border'
                        }
                      >
                        <span
                          className={
                            visibility === value
                              ? 'h-2.5 w-2.5 rounded-full border-[3px] border-accent'
                              : 'h-2.5 w-2.5 rounded-full border border-line-strong'
                          }
                        />
                        {icon}
                        {label}
                      </button>
                    ))}
                  </div>
                  <p className="mt-1.5 text-2xs text-ink-faint">
                    {visibility === 'private'
                      ? 'Only you and administrators can see this report.'
                      : visibility === 'team'
                        ? 'Everyone who can sign in will see this report.'
                        : 'Everyone in the organization will see this report.'}
                  </p>
                </div>

                <div className="space-y-2 pt-5">
                  <Checkbox
                    checked={allowDuplicate}
                    onChange={setAllowDuplicate}
                    label={
                      <WithHint hint="Others can make their own copy without changing yours.">
                        Allow other users to duplicate this report
                      </WithHint>
                    }
                  />
                  <Checkbox
                    checked={showInMenu}
                    onChange={setShowInMenu}
                    label={
                      <WithHint hint="Uncheck to keep it out of the list but still reachable by link.">
                        Show in reports menu
                      </WithHint>
                    }
                  />
                </div>
              </div>
            </Section>

            <Section letter="E" title="Save Options">
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <Checkbox
                    checked={saveFilters}
                    onChange={setSaveFilters}
                    label={
                      <WithHint hint="Off means the report opens unfiltered, rather than carrying the filters you happened to be using.">
                        Save current filters and sorting
                      </WithHint>
                    }
                  />
                  <Checkbox
                    checked={pinToDashboard}
                    onChange={setPinToDashboard}
                    label={
                      <WithHint hint="Pinned reports appear on the dashboard for this section.">
                        Pin to section dashboard
                      </WithHint>
                    }
                  />
                </div>

                <div className="flex items-center justify-between gap-3 pt-0.5">
                  <WithHint hint="Run the report as soon as it is opened, instead of waiting for Run Report.">
                    <span className="text-sm">Auto refresh when opened</span>
                  </WithHint>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={autoRefresh}
                    onClick={() => setAutoRefresh((value) => !value)}
                    className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${
                      autoRefresh ? 'bg-accent' : 'bg-line-strong'
                    }`}
                  >
                    <span
                      className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${
                        autoRefresh ? 'left-[18px]' : 'left-0.5'
                      }`}
                    />
                  </button>
                </div>
              </div>

              {!saveFilters && summary.filters > 0 && (
                <p className="mt-2 rounded border border-warn-border bg-warn-soft px-2.5 py-1.5 text-2xs text-warn">
                  The {summary.filters} filter{summary.filters === 1 ? '' : 's'} and{' '}
                  {summary.sorting} sort rule{summary.sorting === 1 ? '' : 's'} on this report
                  will not be saved.
                </p>
              )}
            </Section>
          </div>

          {/* -------- right column -------- */}
          <div className="space-y-3">
            <div className="panel">
              <div className="panel-header">
                <h2 className="text-sm font-semibold">Save Summary</h2>
              </div>
              <dl className="divide-y divide-line">
                <Row icon={<FolderIcon />} label="Module" value={module || '—'} />
                <Row icon={<FolderIcon />} label="Section" value={section || '—'} />
                <Row icon={<DocIcon />} label="Report Name" value={name || '—'} />
                <Row
                  icon={<DatabaseIcon />}
                  label="Data Source"
                  value={`${tables} table${tables === 1 ? '' : 's'}`}
                />
                <Row
                  icon={<span className="text-2xs font-semibold text-ink-faint">Ab</span>}
                  label="Fields"
                  value={`${summary.fields_selected} column${summary.fields_selected === 1 ? '' : 's'}`}
                />
                <Row icon={<FilterIcon />} label="Filters" value={String(summary.filters)} />
                <Row icon={<LayersIcon />} label="Grouping" value={String(summary.grouping)} />
                <Row icon={<SortIcon />} label="Sorting" value={String(summary.sorting)} />
              </dl>
            </div>

            <div className="panel">
              <div className="panel-header">
                <h2 className="text-sm font-semibold">Preview Placement</h2>
              </div>
              <div className="px-4 py-3 text-sm">
                <Tree label={module || 'Module'} depth={0} />
                <Tree label={section || 'Section'} depth={1} />
                <Tree label="Reports" depth={2} />
                <div className="flex items-center gap-2 pl-[54px] pt-1">
                  <span className="h-1.5 w-1.5 rounded-full bg-accent" />
                  <span className="font-medium text-accent">{name || 'Untitled Report'}</span>
                  {save.variables === true && <Badge tone="warn">Draft</Badge>}
                </div>
              </div>
            </div>

            <div className="rounded-lg border border-accent-border bg-accent-soft px-4 py-3">
              <p className="text-xs text-ink">
                Reports are saved as configuration rather than as a fixed result, so they
                show live data every time they are opened.
              </p>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
function Section({
  letter,
  title,
  hint,
  children,
}: {
  letter: string;
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="panel p-4">
      <div className="mb-3 flex items-start gap-2">
        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-accent text-2xs font-bold text-white">
          {letter}
        </span>
        <div>
          <h2 className="text-sm font-semibold leading-5">{title}</h2>
          {hint && <p className="text-xs text-ink-muted">{hint}</p>}
        </div>
      </div>
      {children}
    </section>
  );
}

function Field({
  label,
  required,
  children,
}: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="label">
        {label} {required && <span className="text-danger">*</span>}
      </label>
      {children}
    </div>
  );
}

function Stat({
  icon,
  label,
  value,
  tone,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  tone: 'good' | 'accent' | 'purple' | 'teal';
}) {
  const tones = {
    good: 'bg-good-soft text-good',
    accent: 'bg-accent-soft text-accent',
    purple: 'bg-[#F3EEFF] text-[#7C3AED]',
    teal: 'bg-info-soft text-info',
  }[tone];
  return (
    <div className="flex items-center gap-2.5">
      <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${tones}`}>
        {icon}
      </span>
      <span className="min-w-0">
        <span className="block text-2xs text-ink-muted">{label}</span>
        <span className="block truncate text-xs font-semibold" title={value}>
          {value}
        </span>
      </span>
    </div>
  );
}

function Row({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-center gap-2.5 px-4 py-2">
      <span className="flex h-4 w-4 shrink-0 items-center justify-center text-ink-faint">
        {icon}
      </span>
      <dt className="flex-1 text-sm text-ink-muted">{label}</dt>
      <dd className="max-w-[52%] truncate text-sm font-medium" title={value}>
        {value}
      </dd>
    </div>
  );
}

function Tree({ label, depth }: { label: string; depth: number }) {
  return (
    <div className="flex items-center gap-2 py-0.5" style={{ paddingLeft: depth * 18 }}>
      {depth > 0 && <span className="text-line-strong">└</span>}
      <FolderIcon />
      <span className={depth === 0 ? 'font-medium' : 'text-ink-muted'}>{label}</span>
    </div>
  );
}

function WithHint({ hint, children }: { hint: string; children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      {children}
      <Tooltip text={hint}>
        <span className="flex h-3.5 w-3.5 items-center justify-center rounded-full border border-line-strong text-[9px] text-ink-faint">
          i
        </span>
      </Tooltip>
    </span>
  );
}

// -- icons ------------------------------------------------------------------
const stroke = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.7 } as const;
const FolderIcon = () => (
  <svg viewBox="0 0 24 24" className="h-4 w-4 text-ink-faint" {...stroke}>
    <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
  </svg>
);
const DatabaseIcon = () => (
  <svg viewBox="0 0 24 24" className="h-4 w-4" {...stroke}>
    <ellipse cx="12" cy="5" rx="8" ry="3" />
    <path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5" />
  </svg>
);
const TableIcon = () => (
  <svg viewBox="0 0 24 24" className="h-4 w-4" {...stroke}>
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <path d="M3 10h18M9 10v10" />
  </svg>
);
const LinkIcon = () => (
  <svg viewBox="0 0 24 24" className="h-4 w-4" {...stroke}>
    <path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1" />
    <path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1" />
  </svg>
);
const DocIcon = () => (
  <svg viewBox="0 0 24 24" className="h-4 w-4" {...stroke}>
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
    <path d="M14 2v6h6" />
  </svg>
);
const FilterIcon = () => (
  <svg viewBox="0 0 24 24" className="h-4 w-4" {...stroke}>
    <path d="M3 4h18l-7 8v7l-4 2v-9z" />
  </svg>
);
const LayersIcon = () => (
  <svg viewBox="0 0 24 24" className="h-4 w-4" {...stroke}>
    <path d="m12 3 9 5-9 5-9-5z" />
    <path d="m3 14 9 5 9-5" />
  </svg>
);
const SortIcon = () => (
  <svg viewBox="0 0 24 24" className="h-4 w-4" {...stroke}>
    <path d="M7 4v16M7 20l-3-3M7 20l3-3M14 7h6M14 12h5M14 17h4" />
  </svg>
);
const LockIcon = () => (
  <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" {...stroke}>
    <rect x="4" y="11" width="16" height="10" rx="2" />
    <path d="M8 11V7a4 4 0 0 1 8 0v4" />
  </svg>
);
const PeopleIcon = () => (
  <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" {...stroke}>
    <circle cx="9" cy="8" r="3" />
    <path d="M2 20a7 7 0 0 1 14 0M17 11a3 3 0 1 0 0-6M18 20a6 6 0 0 0-2-4" />
  </svg>
);
const BuildingIcon = () => (
  <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" {...stroke}>
    <rect x="4" y="3" width="16" height="18" rx="2" />
    <path d="M9 7h2M13 7h2M9 11h2M13 11h2M9 15h6" />
  </svg>
);
