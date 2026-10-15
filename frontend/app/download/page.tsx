"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";

export default function DownloadPage() {
  const [downloadStarted, setDownloadStarted] = useState(false);

  const handleDownload = useCallback(() => {
    setDownloadStarted(true);
    const link = document.createElement("a");
    link.href = "/wagwatch.apk";
    link.download = "WagWatch.apk";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }, []);

  useEffect(() => {
    // If user arrived with ?auto=1 or ?start=1, automatically initiate download
    const params = new URLSearchParams(window.location.search);
    if (params.get("auto") === "1" || params.get("start") === "1") {
      const timer = setTimeout(() => {
        handleDownload();
      }, 800);
      return () => clearTimeout(timer);
    }
  }, [handleDownload]);

  return (
    <main className="min-h-screen bg-[#0B0F19] text-white flex flex-col justify-between px-4 py-8 sm:py-12">
      <div className="max-w-md mx-auto w-full">
        {/* Brand Header */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-20 h-20 rounded-2xl bg-gradient-to-br from-emerald-500 to-teal-700 shadow-xl shadow-emerald-500/20 mb-4 border border-emerald-400/30">
            <span className="text-4xl" role="img" aria-label="dog">🐾</span>
          </div>
          <h1 className="text-2xl sm:text-3xl font-extrabold tracking-tight">WagWatch Station</h1>
          <p className="text-sm text-neutral-400 mt-1">Dedicated Android Camera & Perception App</p>
          <div className="inline-flex items-center gap-1.5 mt-3 px-3 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-xs font-semibold">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
            Latest Build · 24/7 Verified
          </div>
        </div>

        {/* Primary Download Card */}
        <div className="rounded-2xl border border-neutral-800 bg-neutral-900/90 backdrop-blur-md p-6 shadow-2xl">
          <button
            onClick={handleDownload}
            className="w-full flex items-center justify-center gap-3 py-4 px-6 rounded-xl bg-gradient-to-r from-emerald-500 to-teal-600 hover:from-emerald-400 hover:to-teal-500 text-black font-bold text-base transition-all shadow-lg shadow-emerald-500/25 active:scale-[0.98]"
          >
            <span className="text-xl">📲</span>
            <span>Download WagWatch APK</span>
          </button>

          {downloadStarted && (
            <div className="mt-3.5 p-3 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 text-xs text-center flex items-center justify-center gap-2">
              <span className="text-base">✓</span>
              <span>Downloading <strong>WagWatch.apk</strong>… Check your notification drawer!</span>
            </div>
          )}

          <div className="flex items-center justify-between text-[11px] text-neutral-500 mt-4 px-1">
            <span>Size: ~66 MB</span>
            <span>Android 8.0+ (Oreo or higher)</span>
            <span>Direct Safe APK</span>
          </div>
        </div>

        {/* 3-Step Install Guide */}
        <div className="mt-8 rounded-2xl border border-neutral-800/80 bg-neutral-900/50 p-6">
          <h2 className="text-sm font-semibold text-neutral-200 uppercase tracking-wider mb-4 flex items-center gap-2">
            <span>📋</span> Quick Installation Guide
          </h2>

          <div className="space-y-4 text-xs text-neutral-300">
            <div className="flex items-start gap-3">
              <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-neutral-800 text-emerald-400 font-bold border border-neutral-700">
                1
              </div>
              <div>
                <p className="font-semibold text-neutral-100">Tap Download Above</p>
                <p className="text-neutral-400 mt-0.5">
                  If Chrome shows a warning saying <em>&quot;File might be harmful&quot;</em>, tap <strong>&quot;Download anyway&quot;</strong>. (This standard prompt appears for all direct APK installs).
                </p>
              </div>
            </div>

            <div className="flex items-start gap-3">
              <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-neutral-800 text-emerald-400 font-bold border border-neutral-700">
                2
              </div>
              <div>
                <p className="font-semibold text-neutral-100">Open &amp; Install</p>
                <p className="text-neutral-400 mt-0.5">
                  Pull down your phone&apos;s notification drawer and tap <strong>WagWatch.apk</strong>. When prompted, tap <strong>&quot;Install&quot;</strong>.
                </p>
              </div>
            </div>

            <div className="flex items-start gap-3">
              <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-neutral-800 text-emerald-400 font-bold border border-neutral-700">
                3
              </div>
              <div>
                <p className="font-semibold text-neutral-100">Scan QR to Pair</p>
                <p className="text-neutral-400 mt-0.5">
                  Open WagWatch on your phone, tap <strong>&quot;🔗 Pair by QR&quot;</strong>, and scan the QR code from your dashboard. That&apos;s it!
                </p>
              </div>
            </div>
          </div>
        </div>

        {/* Secondary Links */}
        <div className="mt-8 flex flex-col sm:flex-row gap-3">
          <Link
            href="/"
            className="flex-1 py-3 px-4 rounded-xl border border-neutral-800 bg-neutral-900/60 hover:bg-neutral-800 text-neutral-200 text-xs font-semibold text-center transition flex items-center justify-center gap-2"
          >
            <span>🖥️</span> Open Live Dashboard
          </Link>
          <Link
            href="/admin"
            className="flex-1 py-3 px-4 rounded-xl border border-neutral-800 bg-neutral-900/60 hover:bg-neutral-800 text-neutral-200 text-xs font-semibold text-center transition flex items-center justify-center gap-2"
          >
            <span>⚙️</span> Server &amp; Health Admin
          </Link>
        </div>
      </div>

      {/* Footer */}
      <footer className="text-center text-[11px] text-neutral-600 mt-8">
        WagWatch 24/7 Pet Monitoring · Self-Hosted &amp; Privacy-First
      </footer>
    </main>
  );
}
