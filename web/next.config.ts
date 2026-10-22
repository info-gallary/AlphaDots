import type { NextConfig } from "next";

const config: NextConfig = {
  // The container image (web/Dockerfile) sets NEXT_OUTPUT=standalone for a self-contained
  // server bundle. Vercel builds with its own output format, so it is left unset there.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  poweredByHeader: false,
  reactStrictMode: true,
  // The snapshot is read from disk at runtime, so ship it with every API function.
  outputFileTracingIncludes: {
    "/api/**": ["./src/data/snapshot.json"],
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
          { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" },
        ],
      },
    ];
  },
};

export default config;
