import type { NextConfig } from "next";

const apiOrigin = process.env.API_ORIGIN?.replace(/\/$/, "");

// Docker consumes the standalone bundle; Vercel manages its own Next.js output
// tracing and must not receive the standalone override.
const config: NextConfig = {
  ...(process.env.VERCEL ? {} : { output: "standalone" as const }),
  async rewrites() {
    if (!apiOrigin) return [];
    return [
      { source: "/api/:path*", destination: `${apiOrigin}/api/:path*` },
      { source: "/health", destination: `${apiOrigin}/health` },
    ];
  },
};

export default config;
