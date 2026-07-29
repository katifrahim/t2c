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
            // Record sessions for UX insight, but never capture proprietary prompt/model
            // text: all inputs are masked, plus any element tagged .ph-mask.
            session_recording: { maskAllInputs: true, maskTextSelector: ".ph-mask" },
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
