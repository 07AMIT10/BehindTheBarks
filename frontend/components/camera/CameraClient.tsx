"use client";

import dynamic from "next/dynamic";

// Client-only: the page needs window, getUserMedia and WebSocket from the first render.
const CameraApp = dynamic(() => import("./CameraApp"), {
  ssr: false,
  loading: () => <div className="p-8 text-small text-muted">Loading camera…</div>,
});

export default function CameraClient() {
  return <CameraApp />;
}
