import type { NextConfig } from "next";

const BACKEND = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  devIndicators: false,
  // Self-contained server for the Docker image (frontend/Dockerfile). `next dev` and `next start` are unaffected.
  // ponytail: rewrites are serialised at build time, so BACKEND_URL is a build arg; a runtime proxy route if one image must serve several backends.
  output: "standalone",
  // Same-origin API: the browser only ever talks to this app, so the httpOnly session cookie just works
  // and the backend can stay on a private network in production.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND}/api/:path*` }];
  },
  // ponytail: all pages are live client-fetched views; no static shell to prerender, so Cache Components/PPR are off.
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
