import { Geist, Space_Mono } from "next/font/google";
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
      <body>{children}</body>
    </html>
  );
}
