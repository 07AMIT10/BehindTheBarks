import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  // Phone camera + dashboard are reached through cloudflared quick tunnels during the demo.
  allowedDevOrigins: ["*.trycloudflare.com"],
};

export default nextConfig;
