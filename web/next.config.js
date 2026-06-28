/** @type {import('next').NextConfig} */
// Proxy the viewer-data endpoints to the backend so the browser only ever talks
// to this origin (the backend URL/token stay server-side). Set BACKEND_URL in
// .env.local (defaults to a local backend on :8080).
const nextConfig = {
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
