import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // /api/* is handled by Route Handlers in src/app/api/
  output: "standalone",
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://backend:8000/api/:path*", // Proxy to the backend service
      },
    ];
  },
};

export default nextConfig;
