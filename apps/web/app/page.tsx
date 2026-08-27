import { redirect } from 'next/navigation';

/**
 * Land where the navigation starts.
 *
 * This used to open the report builder, from when that was the only screen
 * there was. Opening straight into a half-built report is now the wrong first
 * thing to see.
 */
export default function Home() {
  redirect('/dashboards');
}
