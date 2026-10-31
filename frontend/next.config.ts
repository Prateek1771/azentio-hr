import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  devIndicators: false,
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
