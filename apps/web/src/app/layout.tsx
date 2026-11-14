import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import Script from "next/script";
import Console from "@/components/console";
import "./globals.css";

// Atkinson Hyperlegible keeps 0/O and 1/l/I distinct: owners approve exact amounts and account names here.
// Self-hosted (SIL OFL 1.1, see src/fonts/OFL.txt) so no page view reaches a font CDN.
const ui = localFont({ src: "../fonts/atkinson-hyperlegible-next-latin-wght-normal.woff2", variable: "--font-ui", weight: "200 800", display: "swap" });
const mono = localFont({ src: "../fonts/atkinson-hyperlegible-mono-latin-wght-normal.woff2", variable: "--font-mono", weight: "200 800", display: "swap", preload: false });

export const metadata: Metadata = {
  title: "Gluons",
  description: "One place to discover, approve and supervise your business automations.",
  manifest: "/manifest.webmanifest",
  applicationName: "Gluons",
};
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [{ media: "(prefers-color-scheme: light)", color: "#0e3b2c" }, { media: "(prefers-color-scheme: dark)", color: "#0a1d16" }],
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  // suppressHydrationWarning: theme-init.js may set data-theme before React hydrates.
  return <html lang="en" className={`${ui.variable} ${mono.variable}`} suppressHydrationWarning><body><Script src="/theme-init.js" strategy="beforeInteractive" /><Console />{children}</body></html>;
}
