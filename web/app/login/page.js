"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { createClient, SUPABASE_CONFIGURED } from "@/lib/supabase/client";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState("signin"); // "signin" | "signup"
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [notice, setNotice] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      const supabase = createClient();
      if (mode === "signin") {
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) return setError(error.message);
        router.push("/");
        router.refresh();
      } else {
        const { data, error } = await supabase.auth.signUp({ email, password });
        if (error) return setError(error.message);
        if (!data.session) {
          setNotice("Check your email to confirm your account, then sign in.");
          setMode("signin");
        } else {
          router.push("/");
          router.refresh();
        }
      }
    } finally {
      setBusy(false);
    }
  }

  async function google() {
    setError(null);
    const supabase = createClient();
    await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: `${window.location.origin}/auth/callback` },
    });
  }

  return (
    <div style={styles.page}>
      <div style={styles.card}>
        <div style={styles.brand}>Text2CAD AI</div>
        <div style={styles.subtitle}>
          {mode === "signin" ? "Sign in to continue" : "Create your account"}
        </div>

        {!SUPABASE_CONFIGURED && (
          <div style={styles.warn}>
            Auth is not configured yet. Add your Supabase keys to{" "}
            <code>web/.env.local</code> and restart.
          </div>
        )}

        <form onSubmit={submit} style={styles.form}>
          <input
            type="email"
            required
            placeholder="you@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            style={styles.input}
          />
          <input
            type="password"
            required
            minLength={6}
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            style={styles.input}
          />
          {error && <div style={styles.error}>{error}</div>}
          {notice && <div style={styles.notice}>{notice}</div>}
          <button
            type="submit"
            disabled={busy}
            style={styles.primary}
            onMouseEnter={(e) => { if (!busy) e.currentTarget.style.background = "#333"; }}
            onMouseLeave={(e) => (e.currentTarget.style.background = "#111")}
          >
            {busy ? "…" : mode === "signin" ? "Sign in" : "Sign up"}
          </button>
        </form>

        <div style={styles.divider}><span style={styles.dividerText}>or</span></div>

        <button
          type="button"
          onClick={google}
          style={styles.google}
          onMouseEnter={(e) => { e.currentTarget.style.background = "#f7f7f7"; e.currentTarget.style.borderColor = "#d5d5d5"; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = "#fff"; e.currentTarget.style.borderColor = "#e2e2e2"; }}
        >
          Continue with Google
        </button>

        <div style={styles.switch}>
          {mode === "signin" ? "No account?" : "Already have an account?"}{" "}
          <button
            type="button"
            onClick={() => { setMode(mode === "signin" ? "signup" : "signin"); setError(null); setNotice(null); }}
            style={styles.link}
            onMouseEnter={(e) => (e.currentTarget.style.textDecoration = "underline")}
            onMouseLeave={(e) => (e.currentTarget.style.textDecoration = "none")}
          >
            {mode === "signin" ? "Sign up" : "Sign in"}
          </button>
        </div>
      </div>
    </div>
  );
}

const styles = {
  page: { minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", background: "#fafafa", padding: 24 },
  card: { width: "100%", maxWidth: 380, background: "#fff", border: "1px solid #ececec", borderRadius: 14, padding: 32, boxShadow: "0 8px 30px -12px rgba(0,0,0,0.12)" },
  brand: { fontSize: 22, fontWeight: 600, letterSpacing: "-0.02em", color: "#111", textAlign: "center" },
  subtitle: { fontSize: 14, color: "#666", marginTop: 4, marginBottom: 22, textAlign: "center" },
  warn: { fontSize: 12.5, color: "#8a6d00", background: "#fff8e1", border: "1px solid #f2e3a8", borderRadius: 8, padding: "8px 10px", marginBottom: 16 },
  form: { display: "flex", flexDirection: "column", gap: 10 },
  input: { fontSize: 14, padding: "10px 12px", border: "1px solid #e2e2e2", borderRadius: 8, outline: "none", color: "#111" },
  primary: { marginTop: 4, fontSize: 14, fontWeight: 500, padding: "10px 12px", border: "none", borderRadius: 8, background: "#111", color: "#fff", cursor: "pointer", transition: "background 0.15s" },
  google: { width: "100%", fontSize: 14, fontWeight: 500, padding: "10px 12px", border: "1px solid #e2e2e2", borderRadius: 8, background: "#fff", color: "#333", cursor: "pointer", transition: "background 0.15s, border-color 0.15s" },
  divider: { position: "relative", textAlign: "center", margin: "18px 0", borderTop: "1px solid #eee" },
  dividerText: { position: "relative", top: -10, background: "#fff", padding: "0 10px", fontSize: 12, color: "#999" },
  error: { fontSize: 13, color: "#c02626" },
  notice: { fontSize: 13, color: "#1a7f37" },
  switch: { marginTop: 18, fontSize: 13, color: "#666", textAlign: "center" },
  link: { border: "none", background: "none", color: "#111", fontWeight: 500, cursor: "pointer", padding: 0, fontSize: 13 },
};
