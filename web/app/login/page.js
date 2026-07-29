"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { EyeIcon, EyeOffIcon, Loader2Icon, ArrowLeftIcon } from "lucide-react";
import { createClient, SUPABASE_CONFIGURED } from "@/lib/supabase/client";
import { track, identify, EVENTS } from "@/lib/analytics";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const COPY = {
  signin: { heading: "Welcome back", sub: "Sign in to keep building.", action: "Sign in", busy: "Signing in…" },
  signup: { heading: "Create your account", sub: "Start turning text into CAD — it's free.", action: "Create account", busy: "Creating account…" },
  forgot: { heading: "Reset your password", sub: "We'll email you a link to set a new one.", action: "Send reset link", busy: "Sending link…" },
};

// color-mix keeps the blueprint grid subtle in both light and dark without a new token.
const GRID = {
  backgroundImage:
    "linear-gradient(to right, color-mix(in oklch, var(--foreground) 10%, transparent) 1px, transparent 1px)," +
    "linear-gradient(to bottom, color-mix(in oklch, var(--foreground) 10%, transparent) 1px, transparent 1px)",
  backgroundSize: "34px 34px",
};

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState("signin"); // "signin" | "signup" | "forgot"
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [notice, setNotice] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const copy = COPY[mode];

  function switchMode(next) {
    setMode(next);
    setError(null);
    setNotice(null);
  }

  async function submit(e) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      const supabase = createClient();
      if (mode === "signin") {
        const { data, error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) return setError(error.message);
        identify(data.user?.id, email);
        track(EVENTS.SIGN_IN_SUCCEEDED);
        router.push("/");
        router.refresh();
      } else if (mode === "signup") {
        track(EVENTS.SIGN_UP_SUBMITTED);
        const { data, error } = await supabase.auth.signUp({
          email,
          password,
          // Route the confirmation link through /auth/callback so the PKCE code is
          // exchanged for a session (auto-login), landing the user in the app.
          options: { emailRedirectTo: `${window.location.origin}/auth/callback?next=/` },
        });
        if (error) return setError(error.message);
        if (!data.session) {
          setNotice("Check your email to confirm your account.");
          setMode("signin");
        } else {
          identify(data.user?.id, email);
          track(EVENTS.SIGN_UP_SUCCEEDED);
          router.push("/");
          router.refresh();
        }
      } else {
        const { error } = await supabase.auth.resetPasswordForEmail(email, {
          redirectTo: `${window.location.origin}/reset`,
        });
        if (error) return setError(error.message);
        setNotice("Check your email to reset your password.");
      }
    } finally {
      setBusy(false);
    }
  }

  async function google() {
    setError(null);
    track(EVENTS.OAUTH_CLICK, { provider: "google" });
    const supabase = createClient();
    await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: `${window.location.origin}/auth/callback` },
    });
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center px-4 py-12">
      {/* Blueprint field behind the card */}
      <div aria-hidden className="pointer-events-none absolute inset-0" style={GRID} />

      <div className="relative w-full max-w-sm rounded-xl border border-border bg-card p-8 shadow-lg">
        <div className="flex flex-col items-center text-center">
          <h1 className="text-2xl font-semibold tracking-tight">{copy.heading}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{copy.sub}</p>
        </div>

          {!SUPABASE_CONFIGURED && (
            <div className="mt-6 rounded-lg border border-border bg-muted px-3 py-2.5 text-xs text-muted-foreground">
              Auth isn’t configured yet. Add your Supabase keys to{" "}
              <code className="font-mono">web/.env.local</code> and restart.
            </div>
          )}

          <form onSubmit={submit} className="mt-6 space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                required
                autoComplete="email"
                placeholder="you@example.com"
                className="h-11"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>

            {mode !== "forgot" && (
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <Label htmlFor="password">Password</Label>
                  {mode === "signin" && (
                    <button
                      type="button"
                      onClick={() => switchMode("forgot")}
                      className="cursor-pointer text-xs text-muted-foreground transition-colors hover:text-foreground"
                    >
                      Forgot password?
                    </button>
                  )}
                </div>
                <div className="relative">
                  <Input
                    id="password"
                    type={showPassword ? "text" : "password"}
                    required
                    minLength={6}
                    autoComplete={mode === "signin" ? "current-password" : "new-password"}
                    placeholder="••••••••"
                    className="h-11 pr-10"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((s) => !s)}
                    aria-label={showPassword ? "Hide password" : "Show password"}
                    className="absolute inset-y-0 right-0 grid w-10 cursor-pointer place-items-center text-muted-foreground transition-colors hover:text-foreground"
                  >
                    {showPassword ? <EyeOffIcon className="size-4" /> : <EyeIcon className="size-4" />}
                  </button>
                </div>
              </div>
            )}

            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
            {notice && (
              <p aria-live="polite" className="text-sm text-foreground">
                {notice}
              </p>
            )}

            <Button type="submit" disabled={busy} className="h-11 w-full">
              {busy ? (
                <>
                  <Loader2Icon className="size-4 animate-spin" />
                  {copy.busy}
                </>
              ) : (
                copy.action
              )}
            </Button>
          </form>

          {mode !== "forgot" && (
            <>
              <div className="my-6 flex items-center gap-3 text-xs text-muted-foreground">
                <div className="h-px flex-1 bg-border" />
                or
                <div className="h-px flex-1 bg-border" />
              </div>
              <Button
                type="button"
                variant="outline"
                onClick={google}
                className="h-11 w-full gap-2.5 text-sm font-medium"
              >
                <GoogleG />
                Continue with Google
              </Button>
            </>
          )}

          <div className="mt-6 text-center text-sm text-muted-foreground">
            {mode === "forgot" ? (
              <button
                type="button"
                onClick={() => switchMode("signin")}
                className="inline-flex cursor-pointer items-center gap-1 hover:underline"
              >
                <ArrowLeftIcon className="size-3.5" />
                Back to sign in
              </button>
            ) : mode === "signin" ? (
              <>
                No account?{" "}
                <button
                  type="button"
                  onClick={() => switchMode("signup")}
                  className="cursor-pointer font-medium text-foreground hover:underline"
                >
                  Sign up
                </button>
              </>
            ) : (
              <>
                Already have an account?{" "}
                <button
                  type="button"
                  onClick={() => switchMode("signin")}
                  className="cursor-pointer font-medium text-foreground hover:underline"
                >
                  Sign in
                </button>
              </>
            )}
            {mode !== "forgot" && (
              <div className="mt-3">
                <Link href="/" className="inline-flex items-center gap-1 hover:underline">
                  <ArrowLeftIcon className="size-3.5" />
                  Back to home
                </Link>
              </div>
            )}
          </div>
      </div>
    </div>
  );
}

// Google's brand mark keeps its own colors per the Sign-In button guidelines.
function GoogleG() {
  return (
    <svg viewBox="0 0 24 24" className="size-4" aria-hidden focusable="false">
      <path fill="#4285F4" d="M23.52 12.27c0-.79-.07-1.54-.2-2.27H12v4.51h6.47a5.53 5.53 0 0 1-2.4 3.63v3h3.88c2.27-2.09 3.57-5.17 3.57-8.87Z" />
      <path fill="#34A853" d="M12 24c3.24 0 5.96-1.08 7.95-2.91l-3.88-3c-1.08.72-2.45 1.16-4.07 1.16-3.13 0-5.78-2.11-6.73-4.96H1.29v3.09A12 12 0 0 0 12 24Z" />
      <path fill="#FBBC05" d="M5.27 14.29a7.21 7.21 0 0 1 0-4.58V6.62H1.29a12 12 0 0 0 0 10.76l3.98-3.09Z" />
      <path fill="#EA4335" d="M12 4.75c1.77 0 3.35.61 4.6 1.8l3.44-3.44A11.98 11.98 0 0 0 12 0 12 12 0 0 0 1.29 6.62l3.98 3.09C6.22 6.86 8.87 4.75 12 4.75Z" />
    </svg>
  );
}
