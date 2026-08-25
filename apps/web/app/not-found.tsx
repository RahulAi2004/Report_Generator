import Link from 'next/link';

export default function NotFound() {
  return (
    <div className="flex min-h-full items-center justify-center bg-canvas p-6">
      <div className="panel max-w-md p-6 text-center">
        <p className="text-2xl font-semibold text-ink-faint">404</p>
        <h1 className="mt-1 text-md font-semibold">Page not found</h1>
        <p className="mt-1 text-sm text-ink-muted">
          That screen does not exist, or has not been built yet.
        </p>
        <Link
          href="/reports/builder"
          className="btn btn-primary btn-sm mt-4 inline-flex"
        >
          Go to the report builder
        </Link>
      </div>
    </div>
  );
}
