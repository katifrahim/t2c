"use client";
import { useEffect } from "react";
import { identify, startReplay } from "@/lib/analytics";

// Stitches the anonymous PostHog session to the authenticated user on every app
// load — covers returning users whose session is restored from the cookie (no
// fresh login event fires for them). Also starts session replay here: this
// component only mounts for signed-in users (page.js returns <Landing/> for
// anonymous visitors), so replay is gated to authenticated sessions only.
// Renders nothing.
export default function AnalyticsIdentity({ userId, email }) {
  useEffect(() => {
    if (!userId) return;
    identify(userId, email);
    startReplay();
  }, [userId, email]);
  return null;
}
