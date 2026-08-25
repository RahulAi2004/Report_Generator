'use client';

import { useQuery } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useEffect } from 'react';
import { Sidebar } from '@/components/shell/Sidebar';
import { api, ApiError } from '@/lib/api';

/**
 * Authenticated shell.
 *
 * Redirects to sign-in on 401 rather than rendering an empty app. The server
 * enforces access on every request regardless -- this is convenience, not
 * security.
 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { data: user, isLoading, error } = useQuery({
    queryKey: ['me'],
    queryFn: api.me,
    retry: false,
  });

  useEffect(() => {
    if (error instanceof ApiError && error.status === 401) router.replace('/login');
  }, [error, router]);

  if (isLoading) {
    return (
      <div className="flex h-full">
        <div className="w-[82px] bg-rail" />
        <div className="flex-1 space-y-3 p-6">
          <div className="skeleton h-14 w-full" />
          <div className="skeleton h-20 w-full" />
          <div className="skeleton h-[420px] w-full" />
        </div>
      </div>
    );
  }

  if (!user) return null;

  return (
    <div className="flex h-full overflow-hidden">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">{children}</div>
    </div>
  );
}
