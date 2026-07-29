import { Geist, Space_Mono } from "next/font/google";
import { PostHogProvider, PostHogPageView } from "@posthog/next";
import { Analytics } from "@vercel/analytics/next";
import { SpeedInsights } from "@vercel/speed-insights/next";
import "./globals.css";

// Geist for all UI/body text; Space Mono as the characterful technical face used
// only for the wordmark. Exposed as CSS variables that globals.css maps to
// --font-sans / --font-wordmark.
const geistSans = Geist({ subsets: ["latin"], variable: "--font-sans" });
const spaceMono = Space_Mono({ subsets: ["latin"], weight: ["400", "700"], variable: "--font-wordmark" });

export const metadata = { title: "T2C" };

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
              captureCanvas: { canvasFps: 4, canvasQuality: "0.6" },
            },
          }}
          bootstrapFlags
        >
          <PostHogPageView />
          {children}
        </PostHogProvider>
        <Analytics />
        <SpeedInsights />
      </body>
    </html>
  );
}
