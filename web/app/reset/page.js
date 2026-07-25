"use client";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { BoxIcon, EyeIcon, EyeOffIcon, Loader2Icon } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

// color-mix keeps the blueprint grid subtle in both light and dark without a new token.
const GRID = {
  backgroundImage:
    "linear-gradient(to right, color-mix(in oklch, var(--foreground) 10%, transparent) 1px, transparent 1px)," +
    "linear-gradient(to bottom, color-mix(in oklch, var(--foreground) 10%, transparent) 1px, transparent 1px)",
  backgroundSize: "34px 34px",
};

// Reached from the password-reset email. The browser client reads the recovery
// token out of the URL and turns it into a session; without one, the page is
// useless, so we gate the form on that session existing.
export default function ResetPage() {
  const router = useRouter();
  const supabase = useMemo(() => createClient(), []);
  const [ready, setReady] = useState(null); // null = checking, true = valid link, false = no session
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    const { data: sub } = supabase.auth.onAuthStateChange((_event, session) => {
      if (active && session) setReady(true);
    });
    // getSession() finishes processing the recovery token in the URL first.
    supabase.auth.getSession().then(({ data }) => {
      if (active) setReady((r) => (r === true ? r : !!data.session));
    });
    return () => {
      active = false;
      sub.subscription.unsubscribe();
    };
  }, [supabase]);

  async function submit(e) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const { error } = await supabase.auth.updateUser({ password });
      if (error) return setError(error.message);
      router.push("/");
      router.refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center px-4 py-12">
      {/* Blueprint field behind the card */}
      <div aria-hidden className="pointer-events-none absolute inset-0" style={GRID} />

      <div className="relative w-full max-w-sm rounded-xl border border-border bg-card p-8 shadow-lg">
        <div className="flex flex-col items-center text-center">
          <Link href="/" className="inline-flex items-center gap-2 text-foreground">
            <span className="grid size-7 place-items-center rounded-md border border-foreground">
              <BoxIcon className="size-4" />
            </span>
            <span className="text-sm font-semibold tracking-tight">Text2CAD AI</span>
          </Link>
          <h1 className="mt-6 text-2xl font-semibold tracking-tight">
            {ready === false ? "Link expired" : "Set a new password"}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {ready === false
              ? "This reset link is invalid or has expired. Request a new one to continue."
              : "Choose a password to finish signing in."}
          </p>
        </div>

        {ready === null && (
          <div className="mt-8 flex justify-center text-muted-foreground">
            <Loader2Icon className="size-5 animate-spin" />
          </div>
        )}

        {ready === false && (
          <Link
            href="/login"
            className="mt-6 flex h-11 w-full items-center justify-center rounded-lg bg-primary text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/80"
          >
            Back to sign in
          </Link>
        )}

        {ready === true && (
          <>
            <form onSubmit={submit} className="mt-6 space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="password">New password</Label>
                <div className="relative">
                  <Input
                    id="password"
                    type={showPassword ? "text" : "password"}
                    required
                    minLength={6}
                    autoComplete="new-password"
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

              {error && (
                <p role="alert" className="text-sm text-destructive">
                  {error}
                </p>
              )}

              <Button type="submit" disabled={busy} className="h-11 w-full">
                {busy ? (
                  <>
                    <Loader2Icon className="size-4 animate-spin" />
                    Saving…
                  </>
                ) : (
                  "Save password"
                )}
              </Button>
            </form>

            <p className="mt-6 text-center text-sm text-muted-foreground">
              <Link href="/login" className="font-medium text-foreground hover:underline">
                Back to sign in
              </Link>
            </p>
          </>
        )}
      </div>
    </div>
  );
}
