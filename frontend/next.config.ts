import type { NextConfig } from "next";

const buildId = process.env.BUILD_ID || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;

const nextConfig: NextConfig = {
  output: "export",
  generateBuildId: () => buildId,
  env: {
    NEXT_PUBLIC_BUILD_ID: buildId,
  },
  images: { unoptimized: true },
  // Phone camera + dashboard are reached through cloudflared quick tunnels during the demo.
  allowedDevOrigins: ["*.trycloudflare.com"],
};

export default nextConfig;
