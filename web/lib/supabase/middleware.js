import { createServerClient } from "@supabase/ssr";
import { NextResponse } from "next/server";

// Refresh the Supabase session cookie on every request and gate the app behind
// login. Until the project keys are set, this is a pass-through so the app runs.
export async function updateSession(request) {
  if (!process.env.NEXT_PUBLIC_SUPABASE_URL) {
    return NextResponse.next({ request });
  }

  let supabaseResponse = NextResponse.next({ request });

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL,
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value));
          supabaseResponse = NextResponse.next({ request });
          cookiesToSet.forEach(({ name, value, options }) =>
            supabaseResponse.cookies.set(name, value, options));
        },
      },
    },
  );

  // getClaims verifies the session JWT locally (no network round-trip → fast) and
  // refreshes the cookie when needed. IMPORTANT: nothing between createServerClient
  // and this call.
  const { data } = await supabase.auth.getClaims();
  const loggedIn = !!data?.claims;

  const path = request.nextUrl.pathname;
  const onLogin = path.startsWith("/login");
  const onAuth = path.startsWith("/auth");
  const onReset = path.startsWith("/reset"); // recovery link lands here before a session exists
  const onRoot = path === "/"; // public landing page for logged-out visitors

  // Unauthenticated → gate everything except the landing, login, auth + reset routes.
  if (!loggedIn && !onLogin && !onAuth && !onReset && !onRoot) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    return NextResponse.redirect(url);
  }
  // Already signed in but on /login → send to the app.
  if (loggedIn && onLogin) {
    const url = request.nextUrl.clone();
    url.pathname = "/";
    return NextResponse.redirect(url);
  }

  return supabaseResponse;
}
