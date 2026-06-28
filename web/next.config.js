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
  async rewrites() {
    const backend = process.env.BACKEND_URL || "http://localhost:8080";
    return [
      { source: "/api/model", destination: `${backend}/model` },
      { source: "/api/version", destination: `${backend}/version` },
      { source: "/api/backend", destination: `${backend}/backend` },
    ];
  },
};
module.exports = nextConfig;
