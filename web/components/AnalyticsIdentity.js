"use client";
import { useEffect } from "react";
import { identify } from "@/lib/analytics";

// Stitches the anonymous PostHog session to the authenticated user on every app
// load — covers returning users whose session is restored from the cookie (no
// fresh login event fires for them). Renders nothing.
export default function AnalyticsIdentity({ userId, email }) {
  useEffect(() => {
    identify(userId, email);
  }, [userId, email]);
  return null;
}
