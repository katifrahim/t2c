import { updateSession } from "@/lib/supabase/middleware";

export async function middleware(request) {
  return updateSession(request);
}

export const config = {
  // Run on all routes except Next internals, the vendored viewer assets, and
  // static image files (so auth cookies refresh on pages + API routes).
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|tcv/|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)",
  ],
};
