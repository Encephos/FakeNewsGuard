import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    // Only proxy known backend routes – unknown paths (bot probes,
    // Next.js-internal like /api/rsc) get a Next.js 404 instead of
    // being forwarded to FastAPI where they'd also 404.
    // Routes with their own Next.js Route Handlers (analyze, jobs,
    // archive, extract) are handled there and never reach these rewrites.
    const backend = "http://backend:8000";
    return [
      { source: "/api/auth/:path*", destination: `${backend}/api/auth/:path*` },
      { source: "/api/admin/:path*", destination: `${backend}/api/admin/:path*` },
      { source: "/api/export/:path*", destination: `${backend}/api/export/:path*` },
      { source: "/api/graph/:path*", destination: `${backend}/api/graph/:path*` },
      { source: "/api/health", destination: `${backend}/api/health` },
      { source: "/api/locales", destination: `${backend}/api/locales` },
      { source: "/api/archive-stats", destination: `${backend}/api/archive-stats` },
    ];
  },
};

export default nextConfig;
