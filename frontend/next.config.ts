import path from "node:path";

import type { NextConfig } from "next";

// The dashboard and the FastAPI staff API share one origin: /api/* is proxied to FastAPI, so the
// HttpOnly, SameSite=Strict session cookie works without CORS.
const apiOrigin = process.env.API_ORIGIN ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  poweredByHeader: false,
  // This folder is the app root (a package-lock.json higher up must not be picked instead).
  outputFileTracingRoot: path.join(__dirname),
  turbopack: { root: path.join(__dirname) },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiOrigin}/api/:path*` }];
  },
};

export default config;
