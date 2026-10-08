import type { Metadata, Viewport } from "next";
import CameraClient from "@/components/camera/CameraClient";

export const metadata: Metadata = {
  title: "WagWatch · Camera",
  description: "Use this phone as the pet monitoring camera",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#0F1113",
};

export default function CameraPage() {
  return <CameraClient />;
}
