import "./globals.css";

export const metadata = { title: "t2c" };

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <head>
        {/* vendored three-cad-viewer styles (matches the backend tessellator) */}
        <link rel="stylesheet" href="/tcv/three-cad-viewer.css" />
      </head>
      <body>{children}</body>
    </html>
  );
}
