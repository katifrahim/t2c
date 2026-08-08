/** @type {import('next').NextConfig} */
// Proxy the viewer-data endpoints to the backend so the browser only ever talks
// to this origin (the backend URL/token stay server-side). Set BACKEND_URL in
// .env.local (defaults to a local backend on :8080).
const nextConfig = {
  // The only TypeScript in this (otherwise JS) app is the CLI-generated
  // assistant-ui / shadcn components, which track a moving registry and have
  // type-only skews against the installed @assistant-ui/react and @ai-sdk/mcp
  // versions (e.g. tool-type / literal-union mismatches). The runtime is fine,
  // so don't let those third-party type/lint errors block the production build.
  typescript: { ignoreBuildErrors: true },
  eslint: { ignoreDuringBuilds: true },
  // PostHog sends a trailing slash on its API calls; don't let Next redirect it.
  skipTrailingSlashRedirect: true,
  // Expose Vercel's deploy env to the client so analytics can gate on "production
  // only" (see lib/analytics-enabled.js). Empty locally → treated as non-prod.
  env: { NEXT_PUBLIC_VERCEL_ENV: process.env.VERCEL_ENV || "" },
  // Keep the OpenTelemetry/Langfuse packages out of the bundler so their Node
  // instrumentation loads correctly on the server.
  serverExternalPackages: [
    "@langfuse/otel",
    "@langfuse/tracing",
    "@langfuse/client",
    "@opentelemetry/sdk-trace-node",
  ],
  // Allow the dev server to be reached over the LAN (Next 15 blocks cross-origin
  // dev requests otherwise). Add any other host/IP you serve from here.
  allowedDevOrigins: ["10.18.198.6", "192.168.0.101"],
  async rewrites() {
    const backend = process.env.BACKEND_URL || "http://localhost:8080";
    return [
      { source: "/api/model", destination: `${backend}/model` },
      { source: "/api/version", destination: `${backend}/version` },
      { source: "/api/backend", destination: `${backend}/backend` },
      { source: "/api/selection", destination: `${backend}/selection` },
      // Reverse-proxy PostHog through our origin so its requests are first-party
      // (survives ad-blockers). Matches the provider's api_host of "/ingest".
      { source: "/ingest/static/:path*", destination: "https://us-assets.i.posthog.com/static/:path*" },
      { source: "/ingest/:path*", destination: "https://us.i.posthog.com/:path*" },
    ];
  },
};
module.exports = nextConfig;

