import { Geist, Space_Mono } from "next/font/google";
import { PostHogProvider, PostHogPageView } from "@posthog/next";
import { Analytics } from "@vercel/analytics/next";
import { SpeedInsights } from "@vercel/speed-insights/next";
import { ANALYTICS_ENABLED } from "@/lib/analytics-enabled";
import "./globals.css";

// Geist for all UI/body text; Space Mono as the characterful technical face used
// only for the wordmark. Exposed as CSS variables that globals.css maps to
// --font-sans / --font-wordmark.
const geistSans = Geist({ subsets: ["latin"], variable: "--font-sans" });
const spaceMono = Space_Mono({ subsets: ["latin"], weight: ["400", "700"], variable: "--font-wordmark" });

// default = homepage tab title; template gives sub-pages "<Page> · Cadopy".
export const metadata = {
  title: {
    default: "Cadopy - Text2CAD AI",
    template: "%s · Cadopy",
  },
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" className={`${geistSans.variable} ${spaceMono.variable}`}>
      <body>
        <PostHogProvider
          clientOptions={{
            api_host: "/ingest",
            ui_host: "https://us.posthog.com",
            // Only build person profiles for logged-in users (keeps event costs down).
            person_profiles: "identified_only",
            // PostHogPageView captures SPA pageviews; disable the built-in to avoid dupes.
            capture_pageview: false,
            // Record everything for maximum UX/debugging insight — replays are only
            // accessed by us. Mask just password inputs so credentials never land in
            // a recording (PostHog masks type=password when maskInputOptions says so).
            // captureCanvas records the WebGL 3D viewer (off by default); WebGL frames
            // may still capture blank unless the canvas keeps its drawing buffer.
            session_recording: {
              maskAllInputs: false,
              maskInputOptions: { password: true },
              // fps/quality kept low on purpose: capture runs on the USER's machine
              // (framebuffer readback + JPEG encode), so higher values add CPU/GPU +
              // upload bandwidth load to their session. Quota is per-recording, not size.
              captureCanvas: { canvasFps: 4, canvasQuality: "0.6" },
            },
            // Outside production the SDK still loads (so feature flags work in dev),
            // but captures nothing — no events, no replays — to protect the free-tier
            // quota. Flip on locally with NEXT_PUBLIC_ANALYTICS_DEV=true.
            opt_out_capturing_by_default: !ANALYTICS_ENABLED,
            // Never auto-start replay: anonymous landing traffic would burn the
            // free-tier quota fast. AnalyticsIdentity starts it (startReplay) only
            // for signed-in users; sign-out stops it (resetIdentity).
            disable_session_recording: true,
            autocapture: ANALYTICS_ENABLED,
          }}
          bootstrapFlags
        >
          <PostHogPageView />
          {children}
        </PostHogProvider>
        {ANALYTICS_ENABLED && (
          <>
            <Analytics />
            <SpeedInsights />
          </>
        )}
      </body>
    </html>
  );
}
