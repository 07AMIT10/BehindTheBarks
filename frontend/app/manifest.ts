import type { MetadataRoute } from "next";

export const dynamic = "force-static";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "WagWatch · Behind The Barks",
    short_name: "WagWatch",
    description: "Real-time multimodal dog perception and well-being monitor",
    start_url: "/",
    display: "standalone",
    orientation: "any",
    background_color: "#0B0F19",
    theme_color: "#0B0F19",
    icons: [
      {
        src: "/icon.svg",
        sizes: "any",
        type: "image/svg+xml",
        purpose: "any",
      },
    ],
  };
}
