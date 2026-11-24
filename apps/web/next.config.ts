import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  async rewrites() {
    if (process.env.NEXT_PUBLIC_SAME_ORIGIN_API !== "true") return [];
    const upstream = new URL(process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000");
    return [{ source: "/api/v1/:path*", destination: `${upstream.origin}/v1/:path*` }];
  },
  async headers() {
    return [{ source: "/(.*)", headers: [
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "Referrer-Policy", value: "no-referrer" },
      { key: "X-Frame-Options", value: "DENY" },
      { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
    ] }];
  },
};
export default nextConfig;
