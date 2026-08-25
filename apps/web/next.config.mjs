/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Emits a self-contained server bundle so the production image needs no
  // node_modules tree and no build toolchain.
  output: 'standalone',
  outputFileTracingRoot: process.cwd(),
  poweredByHeader: false,
  // The browser never talks to a database, only to this API.
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${process.env.API_ORIGIN ?? 'http://127.0.0.1:8000'}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
