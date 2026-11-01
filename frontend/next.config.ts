import type { NextConfig } from "next";

const BACKEND = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  devIndicators: false,
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
