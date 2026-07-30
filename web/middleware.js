import { updateSession } from "@/lib/supabase/middleware";

export async function middleware(request) {
  return updateSession(request);
}

export const config = {
  // Run on all routes except Next internals, the vendored viewer assets, static
  // image files, and telemetry proxy paths (so auth cookies refresh on pages +
  // API routes). ingest/ = PostHog reverse proxy, _vercel/ = Vercel Analytics —
  // both must bypass the auth gate or their scripts get redirected to /login.
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|tcv/|ingest/|_vercel/|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)",
  ],
};
