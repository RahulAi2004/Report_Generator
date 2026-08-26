'use client';

import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Select } from '@/components/ui/primitives';
import { api } from '@/lib/api';
import type { SchemaColumn, SchemaTable } from '@/lib/types';

/**
 * Table-and-column chooser, used everywhere a dashboard names a field.
 *
 * Both lists come from the schema the signed-in user may actually read, so a
 * dashboard can never be built against a table that does not exist or that the
 * builder is not allowed to see.
 */
export function useSchemaTables() {
  const query = useQuery({
    queryKey: ['schema-tables'],
    queryFn: () => api.tables(),
    staleTime: 300_000,
  });

  const tables = useMemo(() => {
    const flat: SchemaTable[] = [];
    for (const category of query.data?.categories ?? []) flat.push(...category.tables);
    return flat.sort((a, b) => a.label.localeCompare(b.label));
  }, [query.data]);

  return { tables, isLoading: query.isLoading };
}

export function useTableColumns(table: string | undefined) {
  const query = useQuery({
    queryKey: ['schema-table', table],
    queryFn: () => api.table(table!),
    enabled: Boolean(table),
    staleTime: 300_000,
  });
  return { columns: query.data?.columns ?? [], isLoading: query.isLoading };
}

export function TableSelect({
  value,
  onChange,
  className,
}: {
  value: string;
  onChange: (table: string) => void;
  className?: string;
}) {
  const { tables, isLoading } = useSchemaTables();
  return (
    <Select
      value={value}
      onChange={onChange}
      disabled={isLoading}
      placeholder={isLoading ? 'Loading tables…' : 'Select a table'}
      options={tables.map((table) => ({ value: table.name, label: table.label }))}
      className={className}
    />
  );
}

export function FieldSelect({
  table,
  value,
  onChange,
  className,
  filter,
  placeholder = 'Select a field',
}: {
  table: string;
  value: string;
  onChange: (field: string) => void;
  className?: string;
  /** Narrow the list, e.g. to date columns for a time range. */
  filter?: (column: SchemaColumn) => boolean;
  placeholder?: string;
}) {
  const { columns, isLoading } = useTableColumns(table);
  const options = (filter ? columns.filter(filter) : columns).map((column) => ({
    value: column.name,
    label: column.is_masked ? `${column.label} (masked)` : column.label,
  }));

  return (
    <Select
      value={value}
      onChange={onChange}
      disabled={!table || isLoading}
      placeholder={!table ? 'Choose a table first' : isLoading ? 'Loading…' : placeholder}
      options={options}
      className={className}
    />
  );
}

export const isDateColumn = (column: SchemaColumn) =>
  column.data_type === 'date' || column.data_type === 'datetime';
