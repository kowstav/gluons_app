import type { Metadata, Viewport } from "next";
import Console from "@/components/console";
import "./globals.css";

export const metadata: Metadata = {
  title: "Gluons — Your business, in motion",
  description: "One place to discover, approve and supervise your business automations.",
  manifest: "/manifest.webmanifest",
  applicationName: "Gluons",
};
export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#10231c" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body><Console />{children}</body></html>;
}
