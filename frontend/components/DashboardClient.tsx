"use client";

import dynamic from "next/dynamic";

const Dashboard = dynamic(() => import("./Dashboard"), {
  ssr: false,
  loading: () => <div className="p-8 text-small text-muted">Loading WagWatch…</div>,
});

export default function DashboardClient() {
  return <Dashboard />;
}
