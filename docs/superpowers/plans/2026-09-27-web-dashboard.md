# Dashboard Implementation Plan (Person B — Plan 2 of 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The live "Claude Pet" dashboard in `frontend/` (Next.js 16 + Tailwind v4), built to the design in `ui/screens/`. It consumes Plan 1's backend (`/ws`, `/events`, `/status`, `/video`, `/treat`) and works on desktop, projector and mobile, in light and dark.

**Architecture:** All state logic lives in small **pure TypeScript modules** under `frontend/lib/` (reducer, timeline layout, signals, toasts, formatting, overlay geometry), each unit-tested with Vitest. React components in `frontend/components/` are thin views over that state. One hook (`useBackend`) owns the network: it bootstraps from `/events` + `/status`, keeps a WebSocket with backoff, and polls `/status`. The dashboard renders client-only (`next/dynamic` with `ssr: false`) because every element is live and clock-dependent, which removes all hydration-mismatch risk. TypeScript contract types are **generated** from the pydantic models, so the two sides can't drift.

**Tech Stack:** Next.js 16.3.6 (App Router, Turbopack), React 19, TypeScript 5, Tailwind CSS v4 (`@tailwindcss/postcss`), Vitest 5, `next/font/google` (Hanken Grotesk, IBM Plex Mono).

**Spec:** `docs/superpowers/specs/2026-09-27-ui-screens.md` + `ui/screens/00-dashboard-desktop.html`, `03-dashboard-mobile.html`, `09-notifications.html`, `10-system-states.html`, `11-design-tokens.html`. PROMPTS-WEB Steps 0 (frontend part), 1 (TS types), 7, 8. **When a component's look is unclear, open the matching screen file and copy its structure, sizes and copy.**

## Global Constraints

- Work only in `frontend/`, `scripts/gen_ts_types.py`, `tests/web/test_ts_types.py`, `Makefile`, `CLAUDE.md` (Commands section), `.gitignore`. Never touch Person A's files.
- **Next.js 16 is newer than your training data.** `frontend/AGENTS.md` (created by create-next-app) says to read `frontend/node_modules/next/dist/docs/` before using an unfamiliar API. Do so if an API in this plan errors.
- Node 24 / npm. All frontend commands run inside `frontend/`: `npm run test` (Vitest), `npm run typecheck`, `npm run lint`, `npm run build`.
- Emotion colours come only from the CSS variables `--{emotion}`, `--{emotion}-fg`, `--{emotion}-tint` (never hard-code hex in components). Use them via inline `style={{ color: "var(--happy-fg)" }}`, because Tailwind can't see dynamic class names.
- Every interactive element is at least 44 px tall (`h-11`) except the compact toggles the design explicitly shows smaller (overlay layer buttons 30 px, mode toggle 28 px).
- Copy strings are taken verbatim from the UI spec (curly apostrophes included).
- Backend URL: `?backend=` query param > `NEXT_PUBLIC_BACKEND_URL` > `{page protocol}//{page hostname}:8000`.
- No new runtime dependencies beyond what create-next-app installs, plus `vitest` (dev). No UI kits, no state libraries, no chart libraries.
- Offline demo note: `next/font/google` downloads fonts at **build** time. For the offline demo, run `npm run build && npm run start` while online, beforehand.

## Decisions

1. The dashboard is client-only (`ssr: false`). The server sends a static shell with a "Loading Claude Pet…" line.
2. The video uses `object-contain` (the whole frame is visible, letterboxed) rather than the design's `slice`/cover, so the canvas overlay lines up exactly with the image. The panel background is `#1B1D1F`.
3. "Ear position" shows the contract values (`Up`, `Neutral`, `Back`, `Unknown`). The design's "Forward" isn't a contract value.
4. Timeline window: from session start to now, capped at the last 30 min and at least 2 min wide.
5. The Live/Demo toggle is rendered now. **Demo** stays disabled (tooltip "Demo mode arrives with the demo clips") until `/status` lists `"demo"` in `modes` (Plan 4 adds it).
6. The "Reconnecting" banner shows the attempt number but no maximum. The dashboard retries forever with backoff capped at 5 s.
7. Source labels: `fused` → "Fused", `llm` → "AI", `rules` → "Rules".

## File Structure (all paths under `frontend/` unless noted)

| File | Responsibility |
|---|---|
| `package.json`, `next.config.ts`, `vitest.config.mts`, `tsconfig.json` | scaffold + scripts (Task 1) |
| `app/globals.css` | design tokens (light/dark), `@theme inline` mappings, base styles (Task 1) |
| `app/layout.tsx` | fonts, no-flash theme script, metadata (Task 1) |
| `app/page.tsx` | server shell → `DashboardClient` (Task 1, then Task 5) |
| `lib/config.ts` | backend URL resolution (Task 1) |
| `../scripts/gen_ts_types.py` | pydantic → `lib/contracts.ts` + `lib/skeleton.ts` (Task 2) |
| `lib/contracts.ts`, `lib/skeleton.ts` | GENERATED (Task 2) |
| `lib/emotions.ts` | emotion order, labels, icon paths, negative set, source labels (Task 3) |
| `lib/format.ts` | time/duration strings (Task 3) |
| `lib/types.ts` | Envelope, Status, Span, NotificationItem, DashboardState (Task 3) |
| `lib/store.ts` | pure reducer over envelopes/actions (Task 3) |
| `lib/timeline.ts` | window, span/audio/marker layout, ticks (Task 4) |
| `lib/signals.ts` | 6 signal rows + sparklines (Task 4) |
| `lib/toasts.ts` | toast policy: sticky negatives, 6 s fade, 2-min grouping (Task 4) |
| `lib/overlay.ts` | contain-fit geometry + canvas drawing (Task 4) |
| `lib/useBackend.ts` | network hook (Task 5) |
| `components/EmotionIcon.tsx`, `Header.tsx`, `ThemeToggle.tsx`, `StatusBar.tsx`, `EmotionCard.tsx`, `TreatButton.tsx`, `Dashboard.tsx`, `DashboardClient.tsx` | live state (Task 5) |
| `components/VideoPanel.tsx`, `SignalsPanel.tsx`, `Sparkline.tsx` | video + overlay + signals (Task 6) |
| `components/Timeline.tsx` | session timeline + selected span (Task 7) |
| `components/ToastStack.tsx`, `NotificationsBell.tsx`, `SystemNotice.tsx` | toasts, bell, system states (Task 8) |
| `lib/*.test.ts` | Vitest unit tests |

---

### Task 1: Scaffold — Next.js 16, Tailwind v4 tokens, fonts, theme, Vitest, `/health` indicator

**Files:**
- Create: `frontend/` (via create-next-app), `frontend/vitest.config.mts`, `frontend/lib/config.ts`, `frontend/lib/config.test.ts`
- Replace: `frontend/app/globals.css`, `frontend/app/layout.tsx`, `frontend/app/page.tsx`, `frontend/next.config.ts`
- Modify: `frontend/package.json` (scripts), `Makefile` (`test-frontend`), `.gitignore`

**Interfaces — Produces:**
- `backendBase(opts?: { search?: string; env?: string; protocol?: string; hostname?: string }) -> { http: string; ws: string }`. Defaults read `window.location` and `process.env.NEXT_PUBLIC_BACKEND_URL`.
- CSS: every token from `ui/screens/11-design-tokens.html` as a CSS variable, flipping with `.dark` on `<html>`. Tailwind colours available: `bg`, `surface`, `surface-2`, `border`, `track`, `text`, `text-soft`, `muted`, `accent`, `accent-fg`, `accent-soft`, `accent-ink`, and `{emotion}`, `{emotion}-fg`, `{emotion}-tint` for all 8 emotions. Fonts: `font-sans` (Hanken Grotesk), `font-mono` (IBM Plex Mono).

- [ ] **Step 1: scaffold** (from the repo root)
```bash
npx -y create-next-app@16.3.6 frontend --ts --tailwind --app --eslint --no-src-dir --import-alias "@/*" --use-npm --turbopack --yes
cd frontend && npm install -D vitest@5.0.2 @types/node@^24 && rm -rf .git public/*.svg
```
Expected: `frontend/` exists with `app/`, `AGENTS.md`, `package.json` (next 16.3.6, react 19.x, tailwindcss ^4). `@types/node@^24` is required: the scaffold pins `@types/node@^20`, which conflicts with vitest 5's peer range (`^22 || >=24`) and makes `npm install` fail with ERESOLVE. `rm -rf .git` is a safety net: create-next-app skips `git init` inside an existing repo, but if it did create a nested repo we want the frontend tracked by the outer one.

- [ ] **Step 2: scripts** — in `frontend/package.json` set `"scripts"` to:
```json
{
  "dev": "next dev",
  "build": "next build",
  "start": "next start",
  "lint": "eslint",
  "test": "vitest run",
  "typecheck": "tsc --noEmit"
}
```

`frontend/vitest.config.mts` (`.mts`, because the package isn't `"type": "module"`; a `.ts` config makes Vite 8 warn about ESM syntax in a CommonJS file, and `__dirname` doesn't exist in ESM):
```ts
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: { alias: { "@": import.meta.dirname } },
  test: { environment: "node", include: ["lib/**/*.test.ts"] },
});
```

`frontend/next.config.ts`:
```ts
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Phone camera + dashboard are reached through cloudflared quick tunnels during the demo.
  allowedDevOrigins: ["*.trycloudflare.com"],
};

export default nextConfig;
```

- [ ] **Step 3: failing test** `frontend/lib/config.test.ts`
```ts
import { describe, expect, it } from "vitest";
import { backendBase } from "./config";

describe("backendBase", () => {
  it("defaults to page host on port 8000", () => {
    expect(backendBase({ search: "", env: "", protocol: "http:", hostname: "laptop.local" })).toEqual({
      http: "http://laptop.local:8000", ws: "ws://laptop.local:8000",
    });
  });
  it("env var wins over default, https maps to wss", () => {
    expect(backendBase({ search: "", env: "https://abc.trycloudflare.com/", protocol: "http:", hostname: "x" })).toEqual({
      http: "https://abc.trycloudflare.com", ws: "wss://abc.trycloudflare.com",
    });
  });
  it("?backend= wins over everything and accepts ws urls", () => {
    expect(backendBase({ search: "?backend=wss%3A%2F%2Fb.example", env: "http://e", protocol: "http:", hostname: "x" })).toEqual({
      http: "https://b.example", ws: "wss://b.example",
    });
  });
});
```
Run: `cd frontend && npm run test` → FAIL (cannot find `./config`).

- [ ] **Step 4: implement** `frontend/lib/config.ts`
```ts
export type BackendBase = { http: string; ws: string };

type Opts = { search?: string; env?: string; protocol?: string; hostname?: string };

function normalise(raw: string): BackendBase {
  const trimmed = raw.trim().replace(/\/+$/, "");
  const http = trimmed.replace(/^ws:/, "http:").replace(/^wss:/, "https:");
  const ws = http.replace(/^http:/, "ws:").replace(/^https:/, "wss:");
  return { http, ws };
}

export function backendBase(opts: Opts = {}): BackendBase {
  const loc = typeof window !== "undefined" ? window.location : undefined;
  const search = opts.search ?? loc?.search ?? "";
  const fromQuery = new URLSearchParams(search).get("backend");
  if (fromQuery) return normalise(fromQuery);
  const env = opts.env ?? process.env.NEXT_PUBLIC_BACKEND_URL ?? "";
  if (env) return normalise(env);
  const protocol = opts.protocol ?? loc?.protocol ?? "http:";
  const hostname = opts.hostname ?? loc?.hostname ?? "localhost";
  return normalise(`${protocol}//${hostname}:8000`);
}
```
Run: `npm run test` → 3 passed.

- [ ] **Step 5: tokens** — replace `frontend/app/globals.css` entirely with:
```css
@import "tailwindcss";
@custom-variant dark (&:where(.dark, .dark *));

/* Design tokens: ui/screens/11-design-tokens.html (copied verbatim). */
:root {
  --bg: #F6F5F1;  --surface: #FFFFFF;  --surface-2: #F1EFEA;
  --border: #E4E2DB;  --track: #ECEAE4;
  --text: #17181A;  --text-soft: #3E4146;  --muted: #5E6168;
  --accent: #0B7A83;  --accent-fg: #FFFFFF;
  --accent-soft: #E0F0F1;  --accent-ink: #075960;
  --happy: #E0A100;  --happy-fg: #8C6400;  --happy-tint: #FBF1D6;
  --excited: #E4702A;  --excited-fg: #A84A10;  --excited-tint: #FCE8DA;
  --relaxed: #2F9461;  --relaxed-fg: #1E7148;  --relaxed-tint: #DDF1E6;
  --anxious: #A04FC0;  --anxious-fg: #85389F;  --anxious-tint: #F3E3F8;
  --fearful: #4C63D6;  --fearful-fg: #3A4DB8;  --fearful-tint: #E3E7FA;
  --aggressive: #D13F3F;  --aggressive-fg: #B02E2E;  --aggressive-tint: #FBE1E0;
  --disinterested: #8C8A5E;  --disinterested-fg: #66643E;  --disinterested-tint: #EEEDE0;
  --unknown: #8F949B;  --unknown-fg: #5D6168;  --unknown-tint: #ECEDEF;
  /* Video overlay (fixed, both themes) */
  --overlay-kp: #7FE3E8; --overlay-face: #F5E6B8;
  --overlay-scrim: rgb(15 17 19 / 0.72); --live: #FF5A4E;
}

.dark {
  --bg: #0F1113;  --surface: #171A1D;  --surface-2: #1E2226;
  --border: #2A2E33;  --track: #23272B;
  --text: #ECEDEE;  --text-soft: #C9CCD0;  --muted: #A0A5AD;
  --accent: #3FB8C1;  --accent-fg: #0F1113;
  --accent-soft: #16343A;  --accent-ink: #8FDDE3;
  --happy: #F0B429;  --happy-fg: #F5C451;  --happy-tint: #3A2F12;
  --excited: #F08A4B;  --excited-fg: #F6A474;  --excited-tint: #3D2618;
  --relaxed: #4CB981;  --relaxed-fg: #6FCB9B;  --relaxed-tint: #173326;
  --anxious: #C27BDD;  --anxious-fg: #D19BE6;  --anxious-tint: #33203B;
  --fearful: #7C8DF0;  --fearful-fg: #9AA7F4;  --fearful-tint: #202744;
  --aggressive: #EE6A6A;  --aggressive-fg: #F48E8E;  --aggressive-tint: #3E1D1E;
  --disinterested: #B4B282;  --disinterested-fg: #C7C59C;  --disinterested-tint: #2E2D22;
  --unknown: #8E939A;  --unknown-fg: #B1B5BB;  --unknown-tint: #26292D;
}

@theme inline {
  --font-sans: var(--font-hanken), "Hanken Grotesk", ui-sans-serif, system-ui, sans-serif;
  --font-mono: var(--font-plex-mono), "IBM Plex Mono", ui-monospace, monospace;

  --color-bg: var(--bg);  --color-surface: var(--surface);
  --color-surface-2: var(--surface-2);  --color-border: var(--border);
  --color-track: var(--track);  --color-text: var(--text);
  --color-text-soft: var(--text-soft);  --color-muted: var(--muted);
  --color-accent: var(--accent);  --color-accent-fg: var(--accent-fg);
  --color-accent-soft: var(--accent-soft);  --color-accent-ink: var(--accent-ink);

  --color-happy: var(--happy); --color-happy-fg: var(--happy-fg); --color-happy-tint: var(--happy-tint);
  --color-excited: var(--excited); --color-excited-fg: var(--excited-fg); --color-excited-tint: var(--excited-tint);
  --color-relaxed: var(--relaxed); --color-relaxed-fg: var(--relaxed-fg); --color-relaxed-tint: var(--relaxed-tint);
  --color-anxious: var(--anxious); --color-anxious-fg: var(--anxious-fg); --color-anxious-tint: var(--anxious-tint);
  --color-fearful: var(--fearful); --color-fearful-fg: var(--fearful-fg); --color-fearful-tint: var(--fearful-tint);
  --color-aggressive: var(--aggressive); --color-aggressive-fg: var(--aggressive-fg); --color-aggressive-tint: var(--aggressive-tint);
  --color-disinterested: var(--disinterested); --color-disinterested-fg: var(--disinterested-fg); --color-disinterested-tint: var(--disinterested-tint);
  --color-unknown: var(--unknown); --color-unknown-fg: var(--unknown-fg); --color-unknown-tint: var(--unknown-tint);

  --text-display-xl: 6.5rem;  --text-display-xl--line-height: 1;
  --text-display: 4rem;       --text-display--line-height: 1;
  --text-display-sm: 2.75rem; --text-display-sm--line-height: 1;
  --text-title: 1.625rem;     --text-title--line-height: 1.2;
  --text-heading: 1.0625rem;  --text-heading--line-height: 1.35;
  --text-body: 0.9375rem;     --text-body--line-height: 1.5;
  --text-small: 0.8125rem;    --text-small--line-height: 1.45;
  --text-micro: 0.6875rem;    --text-micro--line-height: 1.3;
  --tracking-display: -0.025em;  --tracking-micro: 0.08em;

  --radius-sm: 6px;  --radius-md: 10px;  --radius-lg: 14px;
  --radius-xl: 16px; --radius-2xl: 20px;

  --shadow-toast: 0 8px 28px rgb(23 24 26 / 0.12);
}

html, body { background: var(--bg); color: var(--text); }
body { font-family: var(--font-sans); -webkit-font-smoothing: antialiased; }
.micro { font-size: 0.6875rem; line-height: 1.3; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; }
@keyframes spin { to { transform: rotate(360deg); } }
```

- [ ] **Step 6: layout** — replace `frontend/app/layout.tsx`:
```tsx
import type { Metadata, Viewport } from "next";
import { Hanken_Grotesk, IBM_Plex_Mono } from "next/font/google";
import "./globals.css";

const hanken = Hanken_Grotesk({ variable: "--font-hanken", subsets: ["latin"], weight: ["400", "500", "600", "700"] });
const plexMono = IBM_Plex_Mono({ variable: "--font-plex-mono", subsets: ["latin"], weight: ["400", "500"] });

export const metadata: Metadata = {
  title: "Claude Pet",
  description: "Live emotion monitor for your dog's feeding area",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, viewportFit: "cover" };

// Runs before first paint: apply the saved (or system) theme so there is no light->dark flash.
const THEME_SCRIPT = `(function(){try{var t=localStorage.getItem('theme');if(!t){t=matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'}if(t==='dark'){document.documentElement.classList.add('dark')}}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={`${hanken.variable} ${plexMono.variable}`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="min-h-dvh bg-bg font-sans text-text antialiased">{children}</body>
    </html>
  );
}
```

- [ ] **Step 7: temporary page with a health indicator** — replace `frontend/app/page.tsx` (Task 5 replaces it again):
```tsx
import HealthBadge from "@/components/HealthBadge";

export default function Page() {
  return (
    <main className="flex min-h-dvh items-center justify-center gap-3 p-8">
      <span className="text-title font-bold">Claude Pet</span>
      <HealthBadge />
    </main>
  );
}
```
`frontend/components/HealthBadge.tsx`:
```tsx
"use client";

import { useEffect, useState } from "react";
import { backendBase } from "@/lib/config";

export default function HealthBadge() {
  const [ok, setOk] = useState<boolean | null>(null);
  useEffect(() => {
    let alive = true;
    const check = () =>
      fetch(`${backendBase().http}/health`).then((r) => r.ok).catch(() => false).then((v) => alive && setOk(v));
    check();
    const id = setInterval(check, 3000);
    return () => { alive = false; clearInterval(id); };
  }, []);
  const label = ok === null ? "backend: checking" : ok ? "backend: connected" : "backend: offline";
  return (
    <span role="status" className="rounded-md border border-border bg-surface px-3 py-1 text-small text-muted">
      {label}
    </span>
  );
}
```

- [ ] **Step 8: Makefile + gitignore** — append to the repo `Makefile` (TAB-indented recipe; add `test-frontend` to `.PHONY`):
```make
test-frontend:
	cd frontend && npm run test && npm run typecheck && npm run lint
```
Append to the repo `.gitignore`:
```
frontend/node_modules/
frontend/.next/
frontend/next-env.d.ts
```

- [ ] **Step 9: verify**
Run inside `frontend/`: `npm run test && npm run typecheck && npm run lint && npm run build`. Expected: all green; the build lists route `/`.
Manual: in terminal A `make dev-backend`; in terminal B `cd frontend && npm run dev`; open http://localhost:3000. You should see "Claude Pet backend: connected" (it switches to "offline" when the backend stops). Toggle the OS dark mode and reload to check the page follows it.

- [ ] **Step 10: commit**
```bash
git add frontend Makefile .gitignore
git commit -m "Web step 0b: Next.js 16 + Tailwind v4 scaffold with design tokens, fonts, theme, health badge"
```
(`frontend/AGENTS.md` and `frontend/CLAUDE.md` are created by create-next-app; commit them as they are.)

---

### Task 2: Generated TypeScript contracts + skeleton

**Files:**
- Create: `scripts/gen_ts_types.py`, `tests/web/test_ts_types.py`, `frontend/lib/contracts.ts` (generated), `frontend/lib/skeleton.ts` (generated)

**Interfaces — Produces** (`frontend/lib/contracts.ts`):
- `export const EMOTIONS = [...] as const; export type Emotion = (typeof EMOTIONS)[number];`, and likewise `AUDIO_LABELS`/`AudioLabel`, `EAR_POSITIONS`/`EarPosition`
- `export interface Features`, `FrameEvent`, `AudioEvent`, `RulesLabel`, `EmotionState`, `LLMResult`. Every property is required (the backend always serialises nulls). Tuples become TS tuples, `dict[str, X]` becomes `Record<string, X>`, and `dict[Emotion, float]` becomes `Partial<Record<Emotion, number>>`.
- `frontend/lib/skeleton.ts`: `export const CANONICAL_NAMES: readonly string[]; export const SKELETON: readonly (readonly [string, string])[];` copied from `backend/vision/keypoint_map.py`. The generator imports that module: it's pure constants, and this build-time script is the only web code allowed to import from `backend.vision`.

- [ ] **Step 1: failing test** `tests/web/test_ts_types.py`
```python
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _gen(tmp_path: Path) -> tuple[str, str]:
    subprocess.run([sys.executable, "scripts/gen_ts_types.py", "--out", str(tmp_path)], cwd=ROOT, check=True)
    return (tmp_path / "contracts.ts").read_text(), (tmp_path / "skeleton.ts").read_text()


def test_generated_shapes(tmp_path):
    ts, sk = _gen(tmp_path)
    assert "// GENERATED by scripts/gen_ts_types.py" in ts
    assert 'export const EMOTIONS = ["happy", "excited", "relaxed", "anxious", "fearful", "aggressive", "disinterested", "unknown"] as const;' in ts
    assert "export type Emotion = (typeof EMOTIONS)[number];" in ts
    assert "export interface FrameEvent {" in ts
    assert "  bbox: [number, number, number, number] | null;" in ts
    assert "  body_keypoints: Record<string, [number, number, number] | null>;" in ts
    assert "  face_landmarks: [number, number][] | null;" in ts
    assert "  features: Features;" in ts
    assert "  ear_position: EarPosition | null;" in ts
    assert "  label: AudioLabel;" in ts
    assert "  scores: Partial<Record<Emotion, number>>;" in ts
    assert '  source: "rules" | "llm" | "fused";' in ts
    assert "export interface LLMResult {" in ts
    assert "export const SKELETON" in sk and '["tail_base", "tail_tip"]' in sk


def test_committed_files_are_up_to_date(tmp_path):
    ts, sk = _gen(tmp_path)
    assert (ROOT / "frontend/lib/contracts.ts").read_text() == ts, "run: python scripts/gen_ts_types.py"
    assert (ROOT / "frontend/lib/skeleton.ts").read_text() == sk, "run: python scripts/gen_ts_types.py"
```
Run: `.venv/bin/python -m pytest tests/web/test_ts_types.py -q` → FAIL (script missing).

- [ ] **Step 2: implement** `scripts/gen_ts_types.py`
```python
"""Generate frontend/lib/contracts.ts and frontend/lib/skeleton.ts from the pydantic contracts.

    python scripts/gen_ts_types.py            # writes into frontend/lib/
    python scripts/gen_ts_types.py --out DIR  # writes into DIR (used by tests)

tests/web/test_ts_types.py fails if the committed files are stale, so run this after changing contracts.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import contracts as c  # noqa: E402
from backend.vision import keypoint_map as km  # noqa: E402  (pure constants; build-time only)

HEADER = "// GENERATED by scripts/gen_ts_types.py from backend/contracts.py. Do not edit.\n"
MODELS = [c.Features, c.FrameEvent, c.AudioEvent, c.RulesLabel, c.EmotionState, c.LLMResult]
ALIASES = {
    "Emotion": ("EMOTIONS", list(c.EMOTIONS)),
    "AudioLabel": ("AUDIO_LABELS", list(c.AudioLabel.__args__)),  # type: ignore[attr-defined]
    "EarPosition": ("EAR_POSITIONS", list(c.EarPosition.__args__)),  # type: ignore[attr-defined]
}


def _lit(values: list) -> str:
    for alias, (_, vals) in ALIASES.items():
        if sorted(map(str, values)) == sorted(vals):
            return alias
    return " | ".join(json.dumps(v) for v in values)


def ts_type(s: dict) -> str:
    if "$ref" in s:
        return s["$ref"].split("/")[-1]
    if "anyOf" in s:
        return " | ".join(dict.fromkeys(ts_type(x) for x in s["anyOf"]))
    if "enum" in s:
        return _lit(s["enum"])
    if "const" in s:
        return json.dumps(s["const"])
    t = s.get("type")
    if t in ("number", "integer"):
        return "number"
    if t == "string":
        return "string"
    if t == "boolean":
        return "boolean"
    if t == "null":
        return "null"
    if t == "array":
        if "prefixItems" in s:
            return "[" + ", ".join(ts_type(x) for x in s["prefixItems"]) + "]"
        inner = ts_type(s.get("items", {}))
        return f"({inner})[]" if "|" in inner else f"{inner}[]"
    if t == "object":
        val = ts_type(s["additionalProperties"]) if isinstance(s.get("additionalProperties"), dict) else "unknown"
        if "propertyNames" in s and "enum" in s["propertyNames"]:
            return f"Partial<Record<{_lit(s['propertyNames']['enum'])}, {val}>>"
        return f"Record<string, {val}>"
    return "unknown"


def interface(model) -> str:
    schema = model.model_json_schema(ref_template="#/$defs/{model}")
    lines = [f"export interface {model.__name__} {{"]
    for name, prop in schema["properties"].items():
        lines.append(f"  {name}: {ts_type(prop)};")
    lines.append("}")
    return "\n".join(lines)


def contracts_ts() -> str:
    parts = [HEADER]
    for alias, (const, vals) in ALIASES.items():
        parts.append(f"export const {const} = {json.dumps(vals)} as const;")
        parts.append(f"export type {alias} = (typeof {const})[number];")
    parts.append("")
    parts.extend(interface(m) + "\n" for m in MODELS)
    return "\n".join(parts).rstrip() + "\n"


def skeleton_ts() -> str:
    names = ", ".join(json.dumps(n) for n in km.CANONICAL_NAMES)
    edges = ",\n  ".join(f"[{json.dumps(a)}, {json.dumps(b)}]" for a, b in km.SKELETON)
    return (f"{HEADER}export const CANONICAL_NAMES: readonly string[] = [{names}];\n\n"
            f"export const SKELETON: readonly (readonly [string, string])[] = [\n  {edges},\n];\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "frontend" / "lib"))
    out = Path(ap.parse_args().out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "contracts.ts").write_text(contracts_ts())
    (out / "skeleton.ts").write_text(skeleton_ts())
    print(f"wrote {out / 'contracts.ts'} and {out / 'skeleton.ts'}")


if __name__ == "__main__":
    main()
```
- [ ] **Step 3: generate + verify**
```bash
.venv/bin/python scripts/gen_ts_types.py
.venv/bin/python -m pytest tests/web/test_ts_types.py -q      # 2 passed
cd frontend && npm run typecheck                              # generated TS compiles
```
Open `frontend/lib/contracts.ts` and check that `FrameEvent`, `EmotionState` and `RulesLabel` read naturally. If pydantic emits `"items": {...}` for tuples instead of `prefixItems` (older pydantic), extend `ts_type` rather than editing the output by hand.

- [ ] **Step 4: commit**
```bash
git add scripts/gen_ts_types.py tests/web/test_ts_types.py frontend/lib/contracts.ts frontend/lib/skeleton.ts
git commit -m "Web step 1b: generate TS contracts and skeleton from pydantic, staleness test"
```

---

### Task 3: Pure state core — emotions, formatting, types, reducer

**Files:**
- Create: `frontend/lib/emotions.ts`, `frontend/lib/format.ts`, `frontend/lib/types.ts`, `frontend/lib/store.ts`
- Test: `frontend/lib/format.test.ts`, `frontend/lib/store.test.ts`

**Interfaces — Produces:**
- `emotions.ts`: `EMOTION_ORDER: Emotion[]`; `EMOTION_META: Record<Emotion, { label: string; paths: [string, string, string] }>`; `FACE_CIRCLE: string`; `NEGATIVE: ReadonlySet<Emotion>`; `sourceLabel(s: "rules"|"llm"|"fused"): "Rules"|"AI"|"Fused"`; `emotionVars(e: Emotion) -> { solid: string; fg: string; tint: string }` (CSS `var(...)` strings).
- `format.ts`: `hhmm(tsSec)`, `hhmmss(tsSec)`, `agoLabel(seconds)`, `durationLabel(seconds)`, `sinceLabel(sinceSec, nowSec)`, `pct(x)`.
- `types.ts`: `Envelope`, `EnvelopeType`, `Status`, `Span`, `NotificationItem`, `AudioMark`, `DashboardState`, `Action`.
- `store.ts`: `initialState: DashboardState`; `reduce(state, action): DashboardState` (pure); `serverNow(state, clientMs) -> number` (seconds, server clock).

- [ ] **Step 1: failing tests**

`frontend/lib/format.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import { agoLabel, durationLabel, hhmm, hhmmss, pct, sinceLabel } from "./format";

const at = (h: number, m: number, s = 0) => new Date(2026, 8, 27, h, m, s).getTime() / 1000;

describe("format", () => {
  it("clock strings are local time, zero padded", () => {
    expect(hhmm(at(8, 5))).toBe("08:05");
    expect(hhmmss(at(18, 30, 4))).toBe("18:30:04");
  });
  it("ago label", () => {
    expect(agoLabel(0.4)).toBe("just now");
    expect(agoLabel(3.2)).toBe("3s ago");
    expect(agoLabel(125)).toBe("2m ago");
  });
  it("duration label", () => {
    expect(durationLabel(45)).toBe("45 s");
    expect(durationLabel(180)).toBe("3 min");
    expect(durationLabel(3900)).toBe("1 h 5 min");
  });
  it("since label", () => {
    expect(sinceLabel(at(18, 27), at(18, 30))).toBe("since 18:27 · 3 min");
  });
  it("pct rounds and clamps", () => {
    expect(pct(0.864)).toBe("86%");
    expect(pct(1.3)).toBe("100%");
  });
});
```

`frontend/lib/store.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import { initialState, reduce, serverNow } from "./store";
import type { Envelope, Span } from "./types";

let seq = 0;
const env = (type: Envelope["type"], data: unknown, ts: number, meta?: Record<string, unknown>): Envelope =>
  ({ type, seq: ++seq, ts, data, ...(meta ? { meta } : {}) }) as Envelope;
const emotion = (e: string, ts: number, changed: boolean, conf = 0.8) =>
  env("emotion", { ts, emotion: e, confidence: conf, source: "fused", reason: `${e} reason`, snapshot: null }, ts, { changed });
const frame = (ts: number, dog = true, tail = 0.5) =>
  env("frame", {
    ts, source: "mock", dog_detected: dog, bbox: dog ? [0, 0, 10, 10] : null, bbox_conf: 0.9, body_keypoints: {},
    face_landmarks: null,
    features: { tail_height: tail, tail_wag_hz: 2, ear_position: "up", mouth_open: 0.5, body_lowering: 0.1, motion_energy: 0.4, in_feeding_zone: true },
  }, ts);
const apply = (actions: Parameters<typeof reduce>[1][]) => actions.reduce(reduce, initialState);

describe("reduce", () => {
  it("connection flags", () => {
    const s = apply([{ kind: "connected", value: true }, { kind: "connected", value: false }]);
    expect(s.connected).toBe(false);
    expect(s.everConnected).toBe(true);
    expect(s.reconnectAttempt).toBe(1);
  });

  it("first emotion sets current and since; changed opens a new span", () => {
    const s = apply([
      { kind: "envelope", env: emotion("relaxed", 100, true), at: 1_000 },
      { kind: "envelope", env: emotion("relaxed", 101, false), at: 2_000 },
      { kind: "envelope", env: emotion("excited", 104, true), at: 5_000 },
    ]);
    expect(s.current?.emotion).toBe("excited");
    expect(s.currentSince).toBe(104);
    expect(s.lastEmotionAt).toBe(5_000);
    expect(s.spans.map((x: Span) => [x.emotion, x.start, x.end])).toEqual([["relaxed", 100, 104], ["excited", 104, 104]]);
  });

  it("unchanged emotion extends the last span", () => {
    const s = apply([
      { kind: "envelope", env: emotion("happy", 10, true), at: 0 },
      { kind: "envelope", env: emotion("happy", 15, false, 0.6), at: 0 },
    ]);
    expect(s.spans).toHaveLength(1);
    expect(s.spans[0].end).toBe(15);
    expect(s.spans[0].confidence).toBe(0.6);
  });

  it("frames update latest frame, dog-last-seen and a sampled feature history", () => {
    const actions = [0, 0.1, 0.2, 0.6, 1.2].map((t) => ({ kind: "envelope" as const, env: frame(50 + t), at: 0 }));
    const s = apply([...actions, { kind: "envelope", env: frame(52, false), at: 0 }]);
    expect(s.frame?.dog_detected).toBe(false);
    expect(s.lastDogTs).toBeCloseTo(51.2);
    expect(s.history).toHaveLength(3); // samples >= 0.5 s apart: 50, 50.6, 51.2
  });

  it("history is capped at 24 samples", () => {
    const actions = Array.from({ length: 40 }, (_, i) => ({ kind: "envelope" as const, env: frame(i), at: 0 }));
    expect(apply(actions).history).toHaveLength(24);
  });

  it("audio keeps dog sounds only; treats and notifications recorded", () => {
    const s = apply([
      { kind: "envelope", env: env("audio", { ts: 1, label: "silence", score: 0.9 }, 1), at: 0 },
      { kind: "envelope", env: env("audio", { ts: 2, label: "yip", score: 0.8 }, 2), at: 0 },
      { kind: "envelope", env: env("treat", { ts: 3 }, 3), at: 0 },
      { kind: "envelope", env: env("notification", { state: { ts: 4, emotion: "anxious", confidence: 0.7, source: "rules", reason: "r", snapshot: null }, status: "dashboard_only", channel: "dashboard", detail: "would send to owner" }, 4), at: 0 },
    ]);
    expect(s.audio.map((a) => a.label)).toEqual(["yip"]);
    expect(s.treats).toEqual([3]);
    expect(s.notifications[0]).toMatchObject({ status: "dashboard_only", read: false });
    expect(s.notifications[0].state.emotion).toBe("anxious");
    expect(reduce(s, { kind: "readAll" }).notifications[0].read).toBe(true);
  });

  it("status envelope replaces or merges status", () => {
    const full = { pipeline: "mock", fps: 8, profile: { dog_name: "Bruno", location: "Kitchen", zone_label: "feeding area" } };
    let s = apply([{ kind: "envelope", env: env("status", full, 1), at: 0 }]);
    s = reduce(s, { kind: "envelope", env: env("status", { phone: { connected: true, device: "Pixel 7", facing: "back", fps: 12 } }, 2), at: 0 });
    expect(s.status?.pipeline).toBe("mock");
    expect(s.status?.phone?.device).toBe("Pixel 7");
  });

  it("bootstrap uses server timeline for spans and replays the rest", () => {
    const timeline: Span[] = [{ emotion: "relaxed", start: 1, end: 9, confidence: 0.7, reason: "r", source: "rules" }];
    const s = reduce(initialState, {
      kind: "bootstrap", timeline, at: 20_000,
      events: [emotion("relaxed", 9, false), env("treat", { ts: 5 }, 5)],
    });
    expect(s.spans).toEqual(timeline);
    expect(s.current?.emotion).toBe("relaxed");
    expect(s.currentSince).toBe(1);
    expect(s.treats).toEqual([5]);
    expect(s.lastSeq).toBeGreaterThan(0);
  });

  it("server clock offset", () => {
    const s = apply([{ kind: "envelope", env: emotion("happy", 1000, true), at: 990_000 }]);
    expect(serverNow(s, 995_000)).toBeCloseTo(1005);
  });
});
```
Run: `cd frontend && npm run test` → FAIL (modules missing).

- [ ] **Step 2: implement**

`frontend/lib/emotions.ts`:
```ts
import type { Emotion } from "./contracts";

export const EMOTION_ORDER: Emotion[] = ["happy", "excited", "relaxed", "anxious", "fearful", "aggressive", "disinterested", "unknown"];

export const FACE_CIRCLE = "M21 12a9 9 0 1 1-18 0a9 9 0 0 1 18 0z";
const EYE_L = "M8.6 10a.4.4 0 1 0 .8 0a.4.4 0 1 0 -.8 0";
const EYE_R = "M14.6 10a.4.4 0 1 0 .8 0a.4.4 0 1 0 -.8 0";

// Icon paths copied from ui/screens/00-dashboard-desktop.html (EMO table).
export const EMOTION_META: Record<Emotion, { label: string; paths: [string, string, string] }> = {
  happy: { label: "Happy", paths: [EYE_L, EYE_R, "M8 14c1 1.6 2.4 2.4 4 2.4s3-.8 4-2.4"] },
  excited: { label: "Excited", paths: ["M7.5 10c.5-1 2.5-1 3 0", "M13.5 10c.5-1 2.5-1 3 0", "M8 13.5h8c0 2.2-1.8 4-4 4s-4-1.8-4-4z"] },
  relaxed: { label: "Relaxed", paths: ["M7.5 10c.5 1 2.5 1 3 0", "M13.5 10c.5 1 2.5 1 3 0", "M9 15c.8.8 1.8 1.2 3 1.2s2.2-.4 3-1.2"] },
  anxious: { label: "Anxious", paths: [`${EYE_L} ${EYE_R}`, "M7.5 8.5l2.5-1M16.5 8.5l-2.5-1", "M8 16c.7-.8 1.3-.8 2 0s1.3.8 2 0 1.3-.8 2 0 1.3.8 2 0"] },
  fearful: { label: "Fearful", paths: ["M8.6 10.5a.4.4 0 1 0 .8 0a.4.4 0 1 0 -.8 0M14.6 10.5a.4.4 0 1 0 .8 0a.4.4 0 1 0 -.8 0", "M7.5 7.5c.7-.7 2-.7 2.5 0M14 7.5c.5-.7 1.8-.7 2.5 0", "M13.5 16a1.5 1.5 0 1 1-3 0a1.5 1.5 0 0 1 3 0z"] },
  aggressive: { label: "Aggressive", paths: ["M8.6 11a.4.4 0 1 0 .8 0a.4.4 0 1 0 -.8 0M14.6 11a.4.4 0 1 0 .8 0a.4.4 0 1 0 -.8 0", "M7.5 8l2.5 1.5M16.5 8l-2.5 1.5", "M8.5 16.2c1-.8 2.2-1.2 3.5-1.2s2.5.4 3.5 1.2"] },
  disinterested: { label: "Disinterested", paths: ["M8 10.5h2.5M13.5 10.5H16", "", "M9.5 15.5h5"] },
  unknown: { label: "Unknown", paths: ["M9.8 9.5a2.3 2.3 0 1 1 3.3 2.1c-.7.3-1.1.9-1.1 1.6v.3", "M12 16.5h.01", ""] },
};

export const NEGATIVE: ReadonlySet<Emotion> = new Set<Emotion>(["anxious", "fearful", "aggressive", "disinterested"]);

export function sourceLabel(s: "rules" | "llm" | "fused"): "Rules" | "AI" | "Fused" {
  return s === "llm" ? "AI" : s === "fused" ? "Fused" : "Rules";
}

export function emotionVars(e: Emotion): { solid: string; fg: string; tint: string } {
  return { solid: `var(--${e})`, fg: `var(--${e}-fg)`, tint: `var(--${e}-tint)` };
}
```

`frontend/lib/format.ts`:
```ts
const pad = (n: number) => String(n).padStart(2, "0");

export function hhmm(tsSec: number): string {
  const d = new Date(tsSec * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function hhmmss(tsSec: number): string {
  const d = new Date(tsSec * 1000);
  return `${hhmm(tsSec)}:${pad(d.getSeconds())}`;
}

export function agoLabel(seconds: number): string {
  if (seconds < 1) return "just now";
  if (seconds < 60) return `${Math.floor(seconds)}s ago`;
  return `${Math.floor(seconds / 60)}m ago`;
}

export function durationLabel(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}

export function sinceLabel(sinceSec: number, nowSec: number): string {
  return `since ${hhmm(sinceSec)} · ${durationLabel(nowSec - sinceSec)}`;
}

export function pct(x: number): string {
  return `${Math.round(Math.min(1, Math.max(0, x)) * 100)}%`;
}
```

`frontend/lib/types.ts`:
```ts
import type { AudioLabel, Emotion, EmotionState, Features, FrameEvent } from "./contracts";

export type EnvelopeType = "frame" | "audio" | "rules" | "emotion" | "llm" | "notification" | "treat" | "status";

export type Envelope = {
  type: EnvelopeType;
  seq: number;
  ts: number; // server clock, seconds
  data: any; // eslint-disable-line @typescript-eslint/no-explicit-any -- narrowed per type in store.ts
  meta?: Record<string, unknown>;
};

export type PipelineStatus = {
  source: string;
  state: "running" | "stalled" | "stopped" | string;
  fps: number;
  last_frame_age_s: number | null;
  audio_ok: boolean;
};

export type Status = {
  pipeline?: string;
  pipeline_status?: PipelineStatus | null;
  demo_mode?: boolean;
  llm?: {
    enabled: boolean;
    online: boolean;
    provider: string | null;
    model: string | null;
    vision: boolean | null;
    last_call: { latency_ms?: number; outcome?: string } | null;
  };
  notify_mode?: string;
  clients?: number;
  fps?: number;
  profile?: { dog_name: string; location: string; zone_label: string };
  phone?: { connected: boolean; device: string | null; facing: string | null; fps: number } | null; // Plan 3
  modes?: string[]; // Plan 4: ["live", "demo"] when demo clips exist
  mode?: "live" | "demo"; // Plan 4
};

export type Span = {
  emotion: Emotion;
  start: number;
  end: number;
  confidence: number;
  reason: string;
  source: "rules" | "llm" | "fused";
};

export type NotificationItem = {
  id: number;
  ts: number;
  state: EmotionState;
  status: "sent" | "failed" | "dashboard_only";
  detail: string;
  read: boolean;
};

export type AudioMark = { ts: number; label: AudioLabel; score: number };

export type DashboardState = {
  connected: boolean;
  everConnected: boolean;
  reconnectAttempt: number;
  status: Status | null;
  current: EmotionState | null;
  currentSince: number | null; // server seconds when the current state began
  lastEmotionAt: number | null; // client ms when the last emotion message arrived
  frame: FrameEvent | null;
  lastDogTs: number | null;
  history: Features[];
  lastHistoryTs: number | null;
  audio: AudioMark[];
  spans: Span[];
  treats: number[];
  notifications: NotificationItem[];
  lastSeq: number;
  clockOffset: number | null; // server seconds - client seconds
};

export type Action =
  | { kind: "connected"; value: boolean }
  | { kind: "envelope"; env: Envelope; at: number }
  | { kind: "bootstrap"; events: Envelope[]; timeline: Span[]; at: number }
  | { kind: "status"; status: Status }
  | { kind: "readAll" };
```

`frontend/lib/store.ts`:
```ts
import type { AudioEvent, EmotionState, FrameEvent } from "./contracts";
import type { Action, DashboardState, Envelope, NotificationItem, Span, Status } from "./types";

export const HISTORY_MAX = 24;
export const HISTORY_EVERY_S = 0.5;
const AUDIO_MAX = 400;
const NOTIFICATIONS_MAX = 50;
const DOG_SOUNDS = new Set(["bark", "yip", "growl", "whimper", "howl"]);

export const initialState: DashboardState = {
  connected: false,
  everConnected: false,
  reconnectAttempt: 0,
  status: null,
  current: null,
  currentSince: null,
  lastEmotionAt: null,
  frame: null,
  lastDogTs: null,
  history: [],
  lastHistoryTs: null,
  audio: [],
  spans: [],
  treats: [],
  notifications: [],
  lastSeq: 0,
  clockOffset: null,
};

export function serverNow(s: DashboardState, clientMs: number): number {
  return clientMs / 1000 + (s.clockOffset ?? 0);
}

function applyEmotion(s: DashboardState, e: EmotionState, changed: boolean, at: number, touchSpans: boolean): DashboardState {
  const first = s.current === null;
  const isNew = first || changed || s.current?.emotion !== e.emotion;
  let spans = s.spans;
  if (touchSpans) {
    const last = spans[spans.length - 1];
    if (!last || (isNew && last.emotion !== e.emotion) || changed) {
      const closed = last ? [...spans.slice(0, -1), { ...last, end: e.ts }] : spans;
      spans = [...closed, { emotion: e.emotion, start: e.ts, end: e.ts, confidence: e.confidence, reason: e.reason, source: e.source }];
    } else {
      spans = [...spans.slice(0, -1), { ...last, end: e.ts, confidence: e.confidence, reason: e.reason, source: e.source }];
    }
  }
  return {
    ...s,
    current: e,
    currentSince: isNew ? e.ts : s.currentSince,
    lastEmotionAt: at,
    spans,
  };
}

function applyFrame(s: DashboardState, f: FrameEvent): DashboardState {
  let { history, lastHistoryTs } = s;
  if (f.dog_detected && (lastHistoryTs === null || f.ts - lastHistoryTs >= HISTORY_EVERY_S - 1e-9)) {
    history = [...history, f.features].slice(-HISTORY_MAX);
    lastHistoryTs = f.ts;
  }
  return { ...s, frame: f, lastDogTs: f.dog_detected ? f.ts : s.lastDogTs, history, lastHistoryTs };
}

function applyEnvelope(s: DashboardState, env: Envelope, at: number, touchSpans: boolean): DashboardState {
  const base: DashboardState = {
    ...s,
    lastSeq: Math.max(s.lastSeq, env.seq ?? 0),
    clockOffset: typeof env.ts === "number" && at > 0 ? env.ts - at / 1000 : s.clockOffset,
  };
  switch (env.type) {
    case "emotion":
      return applyEmotion(base, env.data as EmotionState, Boolean(env.meta?.changed), at, touchSpans);
    case "frame":
      return applyFrame(base, env.data as FrameEvent);
    case "audio": {
      const a = env.data as AudioEvent;
      if (!DOG_SOUNDS.has(a.label)) return base;
      return { ...base, audio: [...base.audio, { ts: a.ts, label: a.label, score: a.score }].slice(-AUDIO_MAX) };
    }
    case "treat":
      return { ...base, treats: [...base.treats, (env.data as { ts: number }).ts] };
    case "notification": {
      const d = env.data as { state: EmotionState; status: NotificationItem["status"]; detail: string };
      const item: NotificationItem = { id: env.seq, ts: d.state.ts, state: d.state, status: d.status, detail: d.detail ?? "", read: false };
      return { ...base, notifications: [...base.notifications, item].slice(-NOTIFICATIONS_MAX) };
    }
    case "status": {
      const d = env.data as Status;
      const isFull = "pipeline" in d && "profile" in d;
      return { ...base, status: isFull ? d : { ...(base.status ?? {}), ...d } };
    }
    default:
      return base; // rules, llm: not stored (status poll carries LLM latency)
  }
}

export function reduce(s: DashboardState, a: Action): DashboardState {
  switch (a.kind) {
    case "connected":
      return a.value
        ? { ...s, connected: true, everConnected: true, reconnectAttempt: 0 }
        : { ...s, connected: false, reconnectAttempt: s.everConnected || s.connected ? s.reconnectAttempt + 1 : s.reconnectAttempt };
    case "envelope":
      return applyEnvelope(s, a.env, a.at, true);
    case "bootstrap": {
      const reset: DashboardState = { ...s, spans: [], treats: [], audio: [], notifications: [], current: null, currentSince: null };
      let next = a.events.reduce((acc, e) => applyEnvelope(acc, e, a.at, false), reset);
      next = { ...next, spans: a.timeline };
      const last: Span | undefined = a.timeline[a.timeline.length - 1];
      if (last && next.current && last.emotion === next.current.emotion) next = { ...next, currentSince: last.start };
      return next;
    }
    case "status":
      return { ...s, status: a.status };
    case "readAll":
      return { ...s, notifications: s.notifications.map((n) => ({ ...n, read: true })) };
  }
}
```
Note on the `connected` test: `[connected true, connected false]` → attempt 1. A first-ever failure (never connected) doesn't count as a reconnect.

- [ ] **Step 3: run** — `npm run test` → format 5, store 9, config 3 all pass. `npm run typecheck && npm run lint` clean.

- [ ] **Step 4: commit**
```bash
git add frontend/lib/emotions.ts frontend/lib/format.ts frontend/lib/types.ts frontend/lib/store.ts frontend/lib/format.test.ts frontend/lib/store.test.ts
git commit -m "Web step 7a: pure dashboard state core (emotions, formatting, reducer)"
```

---

### Task 4: Pure layout modules — timeline, signals, toasts, overlay

**Files:**
- Create: `frontend/lib/timeline.ts`, `frontend/lib/signals.ts`, `frontend/lib/toasts.ts`, `frontend/lib/overlay.ts`
- Test: `frontend/lib/timeline.test.ts`, `frontend/lib/signals.test.ts`, `frontend/lib/toasts.test.ts`, `frontend/lib/overlay.test.ts`

**Interfaces — Produces:**
- `timeline.ts`: `type TimeWindow = { start: number; end: number }`; `timelineWindow(spans: Span[], now: number, maxS = 1800, minS = 120): TimeWindow`; `leftPct(t, w): number` (0..100, clamped); `layoutSpans(spans, w, now): { span: Span; index: number; left: number; width: number }[]`; `groupAudio(audio: AudioMark[], w, gapS = 2): { ts: number; label: string; count: number; left: number }[]`; `ticks(w, n = 6): { left: number; label: string; align: "start" | "center" | "end" }[]`; `spanAt(spans, t): number` (index or -1).
- `signals.ts`: `type SignalRow = { key: keyof Features; name: string; value: string; unit: string; points: string; live: boolean }`; `signalRows(history: Features[]): SignalRow[]` (6 rows in design order); `sparkPoints(values: (number | null)[]): string`.
- `toasts.ts`: `type Toast = { id: string; kind: "negative" | "positive" | "system"; emotion?: Emotion; title: string; body: string; ts: number; status?: NotificationItem["status"]; detail?: string; count: number; sticky: boolean; createdAt: number }`; `toastFromNotification(list, item, dogName, nowMs): Toast[]`; `systemToast(list, title, nowMs): Toast[]`; `expireToasts(list, nowMs): Toast[]`; `dismissToast(list, id): Toast[]`; constants `FADE_MS = 6000`, `GROUP_MS = 120000`, `MAX_TOASTS = 4`.
- `overlay.ts`: `type Fit = { scale: number; dx: number; dy: number }`; `fitContain(srcW, srcH, boxW, boxH): Fit`; `type Layers = { box: boolean; skeleton: boolean; face: boolean }`; `drawOverlay(ctx: CanvasRenderingContext2D, frame: FrameEvent | null, fit: Fit, layers: Layers, dpr: number): void`.

- [ ] **Step 1: failing tests**

`frontend/lib/timeline.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import { groupAudio, layoutSpans, leftPct, spanAt, ticks, timelineWindow } from "./timeline";
import type { Span } from "./types";

const S = (emotion: Span["emotion"], start: number, end: number): Span => ({ emotion, start, end, confidence: 0.8, reason: "r", source: "fused" });

describe("timeline", () => {
  it("window: session start to now, min 2 min, max 30 min", () => {
    expect(timelineWindow([], 1000)).toEqual({ start: 880, end: 1000 });
    expect(timelineWindow([S("happy", 500, 600)], 1000)).toEqual({ start: 500, end: 1000 });
    expect(timelineWindow([S("happy", 0, 10)], 5000)).toEqual({ start: 3200, end: 5000 });
  });
  it("leftPct clamps", () => {
    const w = { start: 0, end: 100 };
    expect(leftPct(25, w)).toBe(25);
    expect(leftPct(-5, w)).toBe(0);
    expect(leftPct(150, w)).toBe(100);
  });
  it("layoutSpans stretches the last span to now and drops spans outside the window", () => {
    const w = { start: 100, end: 200 };
    const out = layoutSpans([S("relaxed", 0, 50), S("happy", 50, 150), S("excited", 150, 160)], w, 200);
    expect(out.map((o) => [o.span.emotion, o.index, o.left, o.width])).toEqual([["happy", 1, 0, 50], ["excited", 2, 50, 50]]);
  });
  it("groupAudio merges repeats of the same label within the gap", () => {
    const w = { start: 0, end: 100 };
    const g = groupAudio([{ ts: 10, label: "yip", score: 1 }, { ts: 11.5, label: "yip", score: 1 }, { ts: 20, label: "bark", score: 1 }], w);
    expect(g.map((x) => [x.label, x.count, x.left])).toEqual([["yip", 2, 10], ["bark", 1, 20]]);
  });
  it("ticks: n+1 labels, last is now", () => {
    const t = ticks({ start: 0, end: 600 }, 6);
    expect(t).toHaveLength(7);
    expect(t[0].align).toBe("start");
    expect(t[6]).toMatchObject({ left: 100, label: "now", align: "end" });
    expect(t[1].label).toMatch(/^\d\d:\d\d$/);
    expect(ticks({ start: 0, end: 120 }, 6)[1].label).toMatch(/^\d\d:\d\d:\d\d$/); // < 1 min apart: add seconds so labels don't repeat
  });
  it("spanAt", () => {
    const spans = [S("relaxed", 0, 10), S("happy", 10, 20)];
    expect(spanAt(spans, 12)).toBe(1);
    expect(spanAt(spans, 99)).toBe(1); // after the end -> last span
    expect(spanAt([], 5)).toBe(-1);
  });
});
```

`frontend/lib/signals.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import type { Features } from "./contracts";
import { signalRows, sparkPoints } from "./signals";

const F = (o: Partial<Features>): Features => ({
  tail_height: null, tail_wag_hz: null, ear_position: null, mouth_open: null, body_lowering: null,
  motion_energy: null, in_feeding_zone: null, ...o,
});

describe("signals", () => {
  it("six rows in design order with formatted latest values", () => {
    const rows = signalRows([F({ tail_height: 0.1 }), F({ tail_height: 0.82, tail_wag_hz: 4.23, ear_position: "up", mouth_open: 0.64, body_lowering: 0.08, motion_energy: 0.712 })]);
    expect(rows.map((r) => r.name)).toEqual(["Tail height", "Tail wag", "Ear position", "Mouth open", "Body lowering", "Motion energy"]);
    expect(rows.map((r) => [r.value, r.unit])).toEqual([["High", "0.82"], ["4.2", "Hz"], ["Up", ""], ["64", "%"], ["8", "%"], ["0.71", ""]]);
    expect(rows.every((r) => r.live)).toBe(true);
  });
  it("null latest value shows a dash and is not live", () => {
    const rows = signalRows([F({ tail_height: -0.7 })]);
    expect(rows[0]).toMatchObject({ value: "Tucked", live: true });
    expect(rows[1]).toMatchObject({ value: "—", unit: "", live: false });
  });
  it("tail height labels", () => {
    const label = (v: number) => signalRows([F({ tail_height: v })])[0].value;
    expect([label(0.5), label(0), label(-0.4), label(-0.8)]).toEqual(["High", "Mid", "Low", "Tucked"]);
  });
  it("sparkPoints maps 0..1 to y 22..2 across x 0..100 and skips nulls", () => {
    expect(sparkPoints([0, 1])).toBe("0.0,22.0 100.0,2.0");
    expect(sparkPoints([0.5, null, 0.5])).toBe("0.0,12.0 100.0,12.0");
    expect(sparkPoints([null])).toBe("");
  });
});
```

`frontend/lib/toasts.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import { dismissToast, expireToasts, FADE_MS, GROUP_MS, MAX_TOASTS, systemToast, toastFromNotification } from "./toasts";
import type { NotificationItem } from "./types";

const N = (id: number, emotion: NotificationItem["state"]["emotion"], reason = "r"): NotificationItem => ({
  id, ts: 100 + id, read: false, status: "dashboard_only", detail: "would send to owner",
  state: { ts: 100 + id, emotion, confidence: 0.8, source: "fused", reason, snapshot: null },
});

describe("toasts", () => {
  it("negative is sticky with 'seems', positive fades with 'is'", () => {
    let t = toastFromNotification([], N(1, "anxious"), "Bruno", 0);
    t = toastFromNotification(t, N(2, "excited"), "Bruno", 0);
    expect(t[0]).toMatchObject({ kind: "positive", title: "Bruno is excited", sticky: false });
    expect(t[1]).toMatchObject({ kind: "negative", title: "Bruno seems anxious", sticky: true, status: "dashboard_only" });
  });
  it("repeat of the same emotion within 2 min is grouped and moved to top", () => {
    let t = toastFromNotification([], N(1, "anxious", "first"), "Bruno", 0);
    t = toastFromNotification(t, N(2, "excited"), "Bruno", 1000);
    t = toastFromNotification(t, N(3, "anxious", "second"), "Bruno", GROUP_MS - 1);
    expect(t).toHaveLength(2);
    expect(t[0]).toMatchObject({ emotion: "anxious", count: 2, body: "second" });
    t = toastFromNotification(t, N(4, "anxious"), "Bruno", 3 * GROUP_MS);
    expect(t.filter((x) => x.emotion === "anxious")).toHaveLength(2);
  });
  it("expire removes non-sticky after 6 s; dismiss removes by id", () => {
    let t = toastFromNotification([], N(1, "fearful"), "Bruno", 0);
    t = systemToast(t, "AI back online · fused readings resumed", 0);
    expect(expireToasts(t, FADE_MS - 1)).toHaveLength(2);
    const left = expireToasts(t, FADE_MS + 1);
    expect(left.map((x) => x.kind)).toEqual(["negative"]);
    expect(dismissToast(left, left[0].id)).toHaveLength(0);
  });
  it("keeps at most MAX_TOASTS", () => {
    let t: ReturnType<typeof systemToast> = [];
    for (let i = 0; i < 10; i++) t = systemToast(t, `s${i}`, i);
    expect(t).toHaveLength(MAX_TOASTS);
    expect(t[0].title).toBe("s9");
  });
});
```

`frontend/lib/overlay.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import { fitContain } from "./overlay";

describe("fitContain", () => {
  it("letterboxes a 4:3 frame in a 16:9 box", () => {
    const f = fitContain(640, 480, 1600, 900);
    expect(f.scale).toBeCloseTo(1.875);
    expect(f.dx).toBeCloseTo(200);
    expect(f.dy).toBeCloseTo(0);
  });
  it("pillarboxes a portrait frame", () => {
    const f = fitContain(360, 640, 800, 800);
    expect(f.scale).toBeCloseTo(1.25);
    expect(f.dx).toBeCloseTo(175);
    expect(f.dy).toBeCloseTo(0);
  });
  it("zero sizes give an identity-safe fit", () => {
    expect(fitContain(0, 0, 100, 100)).toEqual({ scale: 1, dx: 0, dy: 0 });
  });
});
```
Run: `npm run test` → FAIL (modules missing).

- [ ] **Step 2: implement**

`frontend/lib/timeline.ts`:
```ts
import { hhmm, hhmmss } from "./format";
import type { AudioMark, Span } from "./types";

export type TimeWindow = { start: number; end: number };

export function timelineWindow(spans: Span[], now: number, maxS = 1800, minS = 120): TimeWindow {
  const first = spans.length ? spans[0].start : now;
  let start = Math.max(first, now - maxS);
  if (now - start < minS) start = now - minS;
  return { start, end: now };
}

export function leftPct(t: number, w: TimeWindow): number {
  const span = w.end - w.start;
  if (span <= 0) return 0;
  return Math.min(100, Math.max(0, ((t - w.start) / span) * 100));
}

export function layoutSpans(spans: Span[], w: TimeWindow, now: number) {
  const out: { span: Span; index: number; left: number; width: number }[] = [];
  spans.forEach((span, index) => {
    const end = index === spans.length - 1 ? Math.max(span.end, now) : span.end;
    if (end <= w.start || span.start >= w.end) return;
    const left = leftPct(span.start, w);
    const width = leftPct(end, w) - left;
    if (width > 0) out.push({ span, index, left, width });
  });
  return out;
}

export function groupAudio(audio: AudioMark[], w: TimeWindow, gapS = 2) {
  const out: { ts: number; label: string; count: number; left: number }[] = [];
  let lastTs = -Infinity;
  for (const a of audio) {
    if (a.ts < w.start || a.ts > w.end) continue;
    const prev = out[out.length - 1];
    if (prev && prev.label === a.label && a.ts - lastTs <= gapS) {
      prev.count += 1;
    } else {
      out.push({ ts: a.ts, label: a.label, count: 1, left: leftPct(a.ts, w) });
    }
    lastTs = a.ts;
  }
  return out;
}

export function ticks(w: TimeWindow, n = 6) {
  const fmt = (w.end - w.start) / n < 60 ? hhmmss : hhmm; // the 2-min minimum window would repeat HH:MM labels
  return Array.from({ length: n + 1 }, (_, i) => {
    const t = w.start + ((w.end - w.start) * i) / n;
    return {
      left: (i / n) * 100,
      label: i === n ? "now" : fmt(t),
      align: (i === 0 ? "start" : i === n ? "end" : "center") as "start" | "center" | "end",
    };
  });
}

export function spanAt(spans: Span[], t: number): number {
  if (!spans.length) return -1;
  for (let i = spans.length - 1; i >= 0; i--) if (spans[i].start <= t) return i;
  return 0;
}
```

`frontend/lib/signals.ts`:
```ts
import type { Features } from "./contracts";

export type SignalRow = { key: keyof Features; name: string; value: string; unit: string; points: string; live: boolean };

const clamp01 = (x: number) => Math.min(1, Math.max(0, x));

export function sparkPoints(values: (number | null)[]): string {
  const pts: string[] = [];
  const n = values.length;
  values.forEach((v, i) => {
    if (v === null || Number.isNaN(v)) return;
    const x = n > 1 ? (i / (n - 1)) * 100 : 0;
    pts.push(`${x.toFixed(1)},${(22 - clamp01(v) * 20).toFixed(1)}`);
  });
  return pts.length ? pts.join(" ") : "";
}

function tailLabel(v: number): string {
  if (v <= -0.6) return "Tucked";
  if (v < -0.3) return "Low";
  if (v > 0.3) return "High";
  return "Mid";
}

const EAR_NUM: Record<string, number> = { up: 1, neutral: 0.5, back: 0, unknown: 0.5 };

type Spec = {
  key: keyof Features;
  name: string;
  norm: (v: Features[keyof Features]) => number | null;
  show: (v: NonNullable<Features[keyof Features]>) => [string, string];
};

const SPECS: Spec[] = [
  { key: "tail_height", name: "Tail height", norm: (v) => (v === null ? null : ((v as number) + 1) / 2), show: (v) => [tailLabel(v as number), (v as number).toFixed(2)] },
  { key: "tail_wag_hz", name: "Tail wag", norm: (v) => (v === null ? null : (v as number) / 6), show: (v) => [(v as number).toFixed(1), "Hz"] },
  { key: "ear_position", name: "Ear position", norm: (v) => (v === null ? null : EAR_NUM[v as string] ?? 0.5), show: (v) => [String(v).charAt(0).toUpperCase() + String(v).slice(1), ""] },
  { key: "mouth_open", name: "Mouth open", norm: (v) => (v === null ? null : (v as number)), show: (v) => [String(Math.round((v as number) * 100)), "%"] },
  { key: "body_lowering", name: "Body lowering", norm: (v) => (v === null ? null : (v as number)), show: (v) => [String(Math.round((v as number) * 100)), "%"] },
  { key: "motion_energy", name: "Motion energy", norm: (v) => (v === null ? null : (v as number)), show: (v) => [(v as number).toFixed(2), ""] },
];

export function signalRows(history: Features[]): SignalRow[] {
  const latest = history[history.length - 1];
  return SPECS.map((spec) => {
    const raw = latest ? latest[spec.key] : null;
    const live = raw !== null && raw !== undefined;
    const [value, unit] = live ? spec.show(raw as NonNullable<Features[keyof Features]>) : ["—", ""];
    return { key: spec.key, name: spec.name, value, unit, live, points: sparkPoints(history.map((f) => spec.norm(f[spec.key]))) };
  });
}
```

`frontend/lib/toasts.ts`:
```ts
import type { Emotion } from "./contracts";
import { NEGATIVE } from "./emotions";
import type { NotificationItem } from "./types";

export const FADE_MS = 6000;
export const GROUP_MS = 120000;
export const MAX_TOASTS = 4;

export type Toast = {
  id: string;
  kind: "negative" | "positive" | "system";
  emotion?: Emotion;
  title: string;
  body: string;
  ts: number;
  status?: NotificationItem["status"];
  detail?: string;
  count: number;
  sticky: boolean;
  createdAt: number;
};

let counter = 0;
const nextId = () => `t${Date.now().toString(36)}${(counter++).toString(36)}`;

export function toastFromNotification(list: Toast[], item: NotificationItem, dogName: string, nowMs: number): Toast[] {
  const e = item.state.emotion;
  const negative = NEGATIVE.has(e);
  const existing = list.find((t) => t.emotion === e && t.kind !== "system" && nowMs - t.createdAt < GROUP_MS);
  const base: Toast = {
    id: existing?.id ?? nextId(),
    kind: negative ? "negative" : "positive",
    emotion: e,
    title: `${dogName} ${negative ? "seems" : "is"} ${e}`,
    body: item.state.reason,
    ts: item.state.ts,
    status: item.status,
    detail: item.detail,
    count: existing ? existing.count + 1 : 1,
    sticky: negative,
    createdAt: existing ? existing.createdAt : nowMs,
  };
  const rest = list.filter((t) => t.id !== base.id);
  return [base, ...rest].slice(0, MAX_TOASTS);
}

export function systemToast(list: Toast[], title: string, nowMs: number): Toast[] {
  const t: Toast = { id: nextId(), kind: "system", title, body: "", ts: nowMs / 1000, count: 1, sticky: false, createdAt: nowMs };
  return [t, ...list].slice(0, MAX_TOASTS);
}

export function expireToasts(list: Toast[], nowMs: number): Toast[] {
  const next = list.filter((t) => t.sticky || nowMs - t.createdAt <= FADE_MS);
  return next.length === list.length ? list : next;
}

export function dismissToast(list: Toast[], id: string): Toast[] {
  return list.filter((t) => t.id !== id);
}
```
Grouped toasts keep their original `createdAt`, so a grouped positive toast still fades 6 s after it first appeared. The fade is counted from creation.

`frontend/lib/overlay.ts`:
```ts
import type { FrameEvent } from "./contracts";
import { SKELETON } from "./skeleton";

export type Fit = { scale: number; dx: number; dy: number };
export type Layers = { box: boolean; skeleton: boolean; face: boolean };

export function fitContain(srcW: number, srcH: number, boxW: number, boxH: number): Fit {
  if (srcW <= 0 || srcH <= 0 || boxW <= 0 || boxH <= 0) return { scale: 1, dx: 0, dy: 0 };
  const scale = Math.min(boxW / srcW, boxH / srcH);
  return { scale, dx: (boxW - srcW * scale) / 2, dy: (boxH - srcH * scale) / 2 };
}

const KP = "#7FE3E8";
const FACE = "#F5E6B8";

export function drawOverlay(ctx: CanvasRenderingContext2D, frame: FrameEvent | null, fit: Fit, layers: Layers, dpr: number): void {
  const { width, height } = ctx.canvas;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, width, height);
  if (!frame || !frame.dog_detected) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const X = (x: number) => fit.dx + x * fit.scale;
  const Y = (y: number) => fit.dy + y * fit.scale;

  if (layers.box && frame.bbox) {
    const [x1, y1, x2, y2] = frame.bbox;
    const l = X(x1), t = Y(y1), r = X(x2), b = Y(y2);
    ctx.strokeStyle = KP;
    ctx.globalAlpha = 0.9;
    ctx.lineWidth = 1.5;
    ctx.strokeRect(l, t, r - l, b - t);
    ctx.globalAlpha = 1;
    ctx.lineWidth = 3;
    const c = Math.min(20, (r - l) / 4, (b - t) / 4);
    ctx.beginPath();
    ctx.moveTo(l, t + c); ctx.lineTo(l, t); ctx.lineTo(l + c, t);
    ctx.moveTo(r - c, t); ctx.lineTo(r, t); ctx.lineTo(r, t + c);
    ctx.moveTo(r, b - c); ctx.lineTo(r, b); ctx.lineTo(r - c, b);
    ctx.moveTo(l + c, b); ctx.lineTo(l, b); ctx.lineTo(l, b - c);
    ctx.stroke();
    const label = `dog ${(frame.bbox_conf ?? 0).toFixed(2)}`;
    // Canvas fonts can't use var(): resolve next/font's family name from the CSS variable instead.
    const mono = getComputedStyle(ctx.canvas).getPropertyValue("--font-plex-mono").trim();
    ctx.font = `700 11px ${mono ? `${mono}, ` : ""}ui-monospace, monospace`;
    const tw = ctx.measureText(label).width + 16;
    ctx.fillStyle = KP;
    ctx.fillRect(l, t - 24, tw, 22);
    ctx.fillStyle = "#0F1113";
    ctx.fillText(label, l + 8, t - 9);
  }

  if (layers.skeleton) {
    const kp = frame.body_keypoints;
    ctx.strokeStyle = KP;
    ctx.globalAlpha = 0.85;
    ctx.lineWidth = 2;
    ctx.lineCap = "round";
    ctx.beginPath();
    for (const [a, b] of SKELETON) {
      const p = kp[a], q = kp[b];
      if (!p || !q) continue;
      ctx.moveTo(X(p[0]), Y(p[1]));
      ctx.lineTo(X(q[0]), Y(q[1]));
    }
    ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.fillStyle = KP;
    for (const p of Object.values(kp)) {
      if (!p) continue;
      ctx.beginPath();
      ctx.arc(X(p[0]), Y(p[1]), 4, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  if (layers.face && frame.face_landmarks) {
    ctx.fillStyle = FACE;
    for (const [x, y] of frame.face_landmarks) {
      ctx.beginPath();
      ctx.arc(X(x), Y(y), 2.5, 0, Math.PI * 2);
      ctx.fill();
    }
  }
}
```
Note: the mock pipeline's keypoint names (`withers`, `hip`, `tail_base`…) are canonical names, so `SKELETON` edges between them draw. Edges involving points the mock doesn't produce are skipped, because missing keys read as `undefined`.

- [ ] **Step 3: run** — `npm run test` → all suites pass (timeline 6, signals 4, toasts 4, overlay 3 + earlier). `npm run typecheck && npm run lint` clean.

- [ ] **Step 4: commit**
```bash
git add frontend/lib/timeline.ts frontend/lib/signals.ts frontend/lib/toasts.ts frontend/lib/overlay.ts frontend/lib/*.test.ts
git commit -m "Web step 8a: pure timeline, signals, toast policy and overlay geometry"
```

---

### Task 5: Network hook + live state — header, status bar, emotion card, treat button, dashboard shell

**Files:**
- Create: `frontend/lib/useBackend.ts`, `frontend/lib/useNow.ts`, `frontend/components/EmotionIcon.tsx`, `frontend/components/ThemeToggle.tsx`, `frontend/components/Header.tsx`, `frontend/components/StatusBar.tsx`, `frontend/components/EmotionCard.tsx`, `frontend/components/TreatButton.tsx`, `frontend/components/Dashboard.tsx`, `frontend/components/DashboardClient.tsx`
- Replace: `frontend/app/page.tsx`
- Delete: `frontend/components/HealthBadge.tsx` (its job moves to the status bar)

**Interfaces:**
- Consumes: `backendBase` (Task 1), `reduce`/`initialState`/`serverNow` + types (Task 3), `EMOTION_META`/`FACE_CIRCLE`/`sourceLabel`/`emotionVars` + formatters (Task 3).
- Produces:
  - `useBackend() -> { state: DashboardState; treat(): Promise<void>; readAll(): void; setMode(mode: "live" | "demo"): Promise<void>; http: string }`
  - `useNow(intervalMs = 1000) -> number` (client ms, re-renders on the interval)
  - `<EmotionIcon emotion size? strokeWidth? color? />`
  - `<Header dogName location>{right-side children}</Header>`
  - `<StatusBar connected everConnected reconnectAttempt status onMode? />`
  - `<EmotionCard current currentSince lastEmotionAt nowMs nowSec paused projector dogName lastDogTs lastSeenEmotion />`
  - `<TreatButton onTreat flash? />`
  - `Dashboard` contains the marker comments `{/* SLOT:bell */}`, `{/* SLOT:notices */}`, `{/* SLOT:video */}`, `{/* SLOT:signals */}`, `{/* SLOT:timeline */}` and `{/* SLOT:toasts */}`. Tasks 6–8 replace each marker **and the placeholder element right after it** with a real component.

- [ ] **Step 1: hooks**

`frontend/lib/useNow.ts`:
```ts
"use client";

import { useEffect, useState } from "react";

export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}
```

`frontend/lib/useBackend.ts`:
```ts
"use client";

import { useCallback, useEffect, useMemo, useReducer } from "react";
import { backendBase } from "./config";
import { initialState, reduce } from "./store";
import type { Envelope, Span, Status } from "./types";

const STATUS_POLL_MS = 2000;
const MAX_BACKOFF_MS = 5000;

export function useBackend() {
  const [state, dispatch] = useReducer(reduce, initialState);
  const base = useMemo(() => backendBase(), []);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let stopped = false;
    let attempt = 0;
    let retry: ReturnType<typeof setTimeout> | undefined;

    const getJson = async <T,>(path: string): Promise<T> => {
      const r = await fetch(`${base.http}${path}`, { cache: "no-store" });
      if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
      return (await r.json()) as T;
    };

    const bootstrap = async () => {
      try {
        const [ev, st] = await Promise.all([
          getJson<{ events: Envelope[]; timeline: Span[] }>("/events"),
          getJson<Status>("/status"),
        ]);
        if (stopped) return;
        dispatch({ kind: "bootstrap", events: ev.events, timeline: ev.timeline, at: Date.now() });
        dispatch({ kind: "status", status: st });
      } catch {
        /* backend unreachable: the socket retry loop covers it */
      }
    };

    const connect = () => {
      if (stopped) return;
      ws = new WebSocket(`${base.ws}/ws`);
      ws.onopen = () => {
        attempt = 0;
        dispatch({ kind: "connected", value: true });
        void bootstrap();
      };
      ws.onmessage = (m) => {
        try {
          dispatch({ kind: "envelope", env: JSON.parse(String(m.data)) as Envelope, at: Date.now() });
        } catch {
          /* ignore malformed message */
        }
      };
      ws.onerror = () => ws?.close();
      ws.onclose = () => {
        dispatch({ kind: "connected", value: false });
        if (stopped) return;
        attempt += 1;
        retry = setTimeout(connect, Math.min(MAX_BACKOFF_MS, 500 * 2 ** (attempt - 1)));
      };
    };

    connect();
    const poll = setInterval(() => {
      getJson<Status>("/status")
        .then((st) => !stopped && dispatch({ kind: "status", status: st }))
        .catch(() => undefined);
    }, STATUS_POLL_MS);

    return () => {
      stopped = true;
      clearTimeout(retry);
      clearInterval(poll);
      if (ws) {
        ws.onclose = null;
        ws.close();
      }
    };
  }, [base]);

  const treat = useCallback(async () => {
    await fetch(`${base.http}/treat`, { method: "POST" }).catch(() => undefined);
  }, [base]);

  const setMode = useCallback(async (mode: "live" | "demo") => {
    await fetch(`${base.http}/mode`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ mode }),
    }).catch(() => undefined);
  }, [base]);

  const readAll = useCallback(() => dispatch({ kind: "readAll" }), []);

  return { state, treat, readAll, setMode, http: base.http };
}
```

- [ ] **Step 2: small components**

`frontend/components/EmotionIcon.tsx`:
```tsx
import type { Emotion } from "@/lib/contracts";
import { EMOTION_META, FACE_CIRCLE } from "@/lib/emotions";

type Props = { emotion: Emotion; size?: number; strokeWidth?: number; color?: string };

export default function EmotionIcon({ emotion, size = 24, strokeWidth = 1.8, color }: Props) {
  const paths = EMOTION_META[emotion].paths.filter(Boolean);
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color ?? `var(--${emotion}-fg)`}
      strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={FACE_CIRCLE} strokeDasharray={emotion === "unknown" ? "2.2 2.4" : undefined} />
      {paths.map((d, i) => <path key={i} d={d} />)}
    </svg>
  );
}
```

`frontend/components/ThemeToggle.tsx`:
```tsx
"use client";

import { useState } from "react";

export default function ThemeToggle() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  const toggle = () => {
    const next = !dark;
    document.documentElement.classList.toggle("dark", next);
    try { localStorage.setItem("theme", next ? "dark" : "light"); } catch { /* private mode */ }
    setDark(next);
  };
  return (
    <button type="button" onClick={toggle} aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      className="flex h-11 w-11 items-center justify-center rounded-md border border-border bg-surface text-text">
      {dark ? (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round"><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>
      ) : (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" /></svg>
      )}
    </button>
  );
}
```

`frontend/components/Header.tsx`:
```tsx
type Props = { dogName: string; location: string; children?: React.ReactNode };

function Logo({ size }: { size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <rect x="1" y="1" width="30" height="30" rx="9" stroke="var(--accent)" strokeWidth="2" />
      <path d="M7 17h4l2.5-6 4 11 2.5-5H25" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export default function Header({ dogName, location, children }: Props) {
  return (
    <header className="flex h-[52px] items-center gap-3 lg:h-12 lg:gap-6">
      <div className="hidden items-center gap-3 lg:flex">
        <Logo size={32} />
        <div className="text-[20px] font-bold tracking-[-0.01em]">Claude Pet</div>
      </div>
      <div className="hidden h-7 w-px bg-border lg:block" />
      <div className="flex items-center gap-3 lg:hidden"><Logo size={28} /></div>
      <div className="flex min-w-0 flex-col gap-0.5">
        <div className="truncate text-heading font-bold lg:text-body lg:font-semibold">{dogName}</div>
        <div className="truncate text-[12px] text-muted lg:text-small">
          <span className="lg:hidden">Claude Pet · {location}</span>
          <span className="hidden lg:inline">Feeding area · {location}</span>
        </div>
      </div>
      <div className="grow" />
      <div className="flex items-center gap-2">{children}</div>
    </header>
  );
}
```

`frontend/components/StatusBar.tsx`:
```tsx
import type { Status } from "@/lib/types";

type Props = {
  connected: boolean;
  everConnected: boolean;
  reconnectAttempt: number;
  status: Status | null;
  onMode?: (mode: "live" | "demo") => void;
};

function deviceLabel(s: Status | null): string {
  if (s?.phone?.connected) return `${s.phone.device ?? "Phone"} · ${s.phone.facing ?? "back"} camera`;
  const src = s?.pipeline_status?.source;
  if (src === "mock") return "Mock camera";
  if (src === "browser") return "Phone camera · waiting";
  return src ? `${src} source` : "No camera yet";
}

export default function StatusBar({ connected, everConnected, reconnectAttempt, status, onMode }: Props) {
  const llm = status?.llm;
  const aiOk = Boolean(llm?.enabled && llm.online);
  const latency = llm?.last_call?.latency_ms;
  const mode = status?.mode ?? (status?.demo_mode ? "demo" : "live");
  const demoAvailable = Boolean(status?.modes?.includes("demo")) && Boolean(onMode);
  const conn = connected ? "Connected" : everConnected ? `Reconnecting · attempt ${reconnectAttempt}` : "Connecting…";
  return (
    <div role="status" className="flex min-h-10 flex-wrap items-center gap-x-5 gap-y-2 rounded-md border border-border bg-surface px-4 py-2 text-small text-muted">
      <div className="flex items-center gap-2 font-semibold text-text">
        <span className="h-2 w-2 rounded-full"
          style={{ background: connected ? "var(--accent)" : "var(--unknown)", boxShadow: connected ? "0 0 0 3px var(--accent-soft)" : "none" }} />
        {conn}
      </div>
      <div className="flex items-center gap-1.5">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="7" y="2.5" width="10" height="19" rx="2.5" /><path d="M11 18.5h2" /></svg>
        {deviceLabel(status)}
      </div>
      <div className="font-mono">{Math.round(status?.fps ?? 0)} fps</div>
      <div className="flex items-center gap-1.5">
        {aiOk ? (
          <>AI · {llm?.model || "vision"}{latency !== undefined && <span className="font-mono">{Math.round(latency)} ms</span>}</>
        ) : (
          <><span className="inline-block h-2 w-2 rounded-full border-[1.5px] border-muted" />{llm?.enabled ? "AI offline · rules only" : "Rules only"}</>
        )}
      </div>
      <div className="grow" />
      <div role="group" aria-label="Mode" className="flex gap-0.5 rounded-lg bg-surface-2 p-[3px]">
        {(["live", "demo"] as const).map((m) => {
          const active = mode === m;
          const disabled = m === "demo" ? !demoAvailable : !onMode && !active;
          return (
            <button key={m} type="button" aria-pressed={active} disabled={disabled || active}
              title={m === "demo" && !demoAvailable ? "Demo mode arrives with the demo clips" : undefined}
              onClick={() => onMode?.(m)}
              className={`flex h-7 items-center gap-1.5 rounded-sm px-3 text-[12px] font-semibold ${active ? "bg-surface text-text shadow-sm" : "text-muted"} disabled:cursor-default ${disabled && !active ? "opacity-50" : ""}`}>
              {m === "live" && <span className="h-1.5 w-1.5 rounded-full" style={{ background: "var(--accent)" }} />}
              {m === "live" ? "Live" : "Demo"}
            </button>
          );
        })}
      </div>
    </div>
  );
}
```

`frontend/components/TreatButton.tsx` (the parent owns the 1.5 s "Treat marked" flash, set in the click handler, so no effect needs to set state):
```tsx
type Props = { onTreat: () => void; flash?: boolean };

export default function TreatButton({ onTreat, flash = false }: Props) {
  return (
    <button type="button" onClick={onTreat}
      className="flex h-[60px] w-full items-center justify-center gap-3 rounded-lg bg-accent text-[17px] font-bold text-accent-fg transition-transform active:scale-[0.98]">
      {flash ? (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
      ) : (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 13h18a9 5 0 0 1-18 0z" /><path d="M12 3v6" /><path d="M9 6.5l3 3 3-3" /></svg>
      )}
      {flash ? "Treat marked" : "Treat dropped"}
      <span className="hidden rounded-[4px] border border-current px-1.5 py-0.5 font-mono text-[12px] font-medium opacity-70 lg:inline">T</span>
    </button>
  );
}
```

- [ ] **Step 3: emotion card** — `frontend/components/EmotionCard.tsx` (states: first load, paused, no dog in view, normal; copy from `ui/screens/10-system-states.html`):
```tsx
import type { Emotion, EmotionState } from "@/lib/contracts";
import { EMOTION_META, emotionVars, sourceLabel } from "@/lib/emotions";
import { agoLabel, hhmm, hhmmss, pct, sinceLabel } from "@/lib/format";
import EmotionIcon from "./EmotionIcon";

type Props = {
  current: EmotionState | null;
  currentSince: number | null;
  lastEmotionAt: number | null;
  nowMs: number;
  nowSec: number;
  paused: boolean;
  projector: boolean;
  dogName: string;
  lastDogTs: number | null;
  lastSeenEmotion: Emotion | null;
};

function SourceBadge({ source }: { source: EmotionState["source"] }) {
  const label = sourceLabel(source);
  const rules = source === "rules";
  return (
    <div className={`flex h-[26px] items-center gap-1.5 rounded-full px-2.5 text-[12px] font-bold ${rules ? "bg-surface-2 text-text-soft" : "bg-accent-soft text-accent-ink"}`}>
      {label}
    </div>
  );
}

export default function EmotionCard(p: Props) {
  const sizes = p.projector
    ? { label: "text-display-xl", tile: 128, glyph: 80, reason: "text-[22px]" }
    : { label: "text-display-sm lg:text-display", tile: 88, glyph: 56, reason: "text-body lg:text-[17px]" };

  if (!p.current) {
    return (
      <section aria-label="Current emotion" className="relative flex flex-col gap-4 overflow-hidden rounded-xl border border-border bg-surface p-7">
        <div className="flex items-center gap-5">
          <div className="flex shrink-0 items-center justify-center rounded-[18px]" style={{ width: 72, height: 72, background: "var(--unknown-tint)" }}>
            <EmotionIcon emotion="unknown" size={44} strokeWidth={1.6} />
          </div>
          <div className="flex flex-col gap-1">
            <div className="text-title font-bold tracking-[-0.015em]">Waiting for {p.dogName}</div>
            <div className="text-small text-muted">Readings start when a dog enters the feeding zone.</div>
          </div>
        </div>
      </section>
    );
  }

  const c = p.current;
  const noDog = c.emotion === "unknown" && c.reason.startsWith("No dog");
  const v = emotionVars(c.emotion);
  const updated = p.lastEmotionAt ? agoLabel((p.nowMs - p.lastEmotionAt) / 1000) : "";

  return (
    <section aria-label="Current emotion" className="relative flex flex-col gap-[18px] overflow-hidden rounded-xl border border-border bg-surface p-6 lg:p-7">
      <div className="absolute inset-x-0 top-0 h-1" style={{ background: p.paused ? "var(--unknown)" : v.solid }} />
      <div className="flex items-center gap-2.5">
        <div className="micro text-muted">{p.paused ? "Paused · last reading" : "Right now"}</div>
        <div className="grow" />
        {!p.paused && !noDog && <SourceBadge source={c.source} />}
        {!p.paused && <div className="font-mono text-[12px] text-muted">updated {updated}</div>}
      </div>

      <div className={`flex items-center gap-5 ${p.paused ? "opacity-55" : ""}`}>
        <div className="flex shrink-0 items-center justify-center rounded-[18px]"
          style={{ width: sizes.tile, height: sizes.tile, background: noDog ? "var(--unknown-tint)" : v.tint }}>
          <EmotionIcon emotion={c.emotion} size={sizes.glyph} strokeWidth={1.6} />
        </div>
        <div className="flex min-w-0 flex-col gap-1">
          <div className={`${noDog ? "text-title" : sizes.label} font-bold tracking-[-0.025em]`} style={{ color: p.paused ? "var(--muted)" : v.fg }}>
            {noDog ? "No dog in view" : EMOTION_META[c.emotion].label}
          </div>
          <div className="flex items-center gap-1.5 text-[14px] text-muted">
            {p.paused ? (
              <>Held since {hhmmss(c.ts)}</>
            ) : noDog ? (
              p.lastDogTs ? (
                <>Last seen {hhmm(p.lastDogTs)}{p.lastSeenEmotion && (
                  <> · <EmotionIcon emotion={p.lastSeenEmotion} size={16} /> <span style={{ color: `var(--${p.lastSeenEmotion}-fg)` }}>{EMOTION_META[p.lastSeenEmotion].label}</span></>
                )}</>
              ) : <>Not seen yet this session</>
            ) : (
              p.currentSince !== null && sinceLabel(p.currentSince, p.nowSec)
            )}
          </div>
        </div>
      </div>

      {!noDog && (
        <div className={`flex flex-col gap-2 ${p.paused ? "opacity-55" : ""}`}>
          <div className="flex justify-between text-small">
            <span className="font-semibold text-muted">Confidence</span>
            <span className="font-mono font-medium">{pct(c.confidence)}</span>
          </div>
          <div role="meter" aria-label="Confidence" aria-valuenow={Math.round(c.confidence * 100)} aria-valuemin={0} aria-valuemax={100}
            className="h-2 overflow-hidden rounded bg-track">
            <div className="h-full rounded" style={{ width: pct(c.confidence), background: p.paused ? "#BDBFC3" : v.solid }} />
          </div>
        </div>
      )}

      {!noDog && <p className={`m-0 leading-[1.45] text-pretty ${sizes.reason} ${p.paused ? "opacity-55" : ""}`}>{c.reason}</p>}
    </section>
  );
}
```

- [ ] **Step 4: dashboard shell + client wrapper + page**

`frontend/components/Dashboard.tsx`:
```tsx
"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { serverNow } from "@/lib/store";
import { useBackend } from "@/lib/useBackend";
import { useNow } from "@/lib/useNow";
import EmotionCard from "./EmotionCard";
import Header from "./Header";
import StatusBar from "./StatusBar";
import ThemeToggle from "./ThemeToggle";
import TreatButton from "./TreatButton";

export default function Dashboard() {
  const backend = useBackend(); // backend.readAll / backend.http are used by Tasks 6 and 8
  const { state, treat, setMode } = backend;
  const nowMs = useNow(1000);
  const nowSec = serverNow(state, nowMs);
  const projector = useMemo(() => new URLSearchParams(window.location.search).get("size") === "projector", []);
  const profile = state.status?.profile ?? { dog_name: "your dog", location: "Kitchen", zone_label: "feeding area" };
  const stalled = state.status?.pipeline_status?.state === "stalled";
  const paused = (!state.connected && state.everConnected) || stalled;
  const lastSeenEmotion = useMemo(() => [...state.spans].reverse().find((s) => s.emotion !== "unknown")?.emotion ?? null, [state.spans]);

  const [treatFlash, setTreatFlash] = useState(false);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const onTreat = useCallback(() => {
    setTreatFlash(true);
    clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => setTreatFlash(false), 1500);
    void treat();
  }, [treat]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() !== "t" || e.metaKey || e.ctrlKey || e.altKey || e.repeat) return;
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      onTreat();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onTreat]);

  return (
    <div className={projector ? "projector" : undefined}>
      <div className="mx-auto flex w-full max-w-[1920px] flex-col gap-4 px-4 pb-28 pt-3 lg:gap-5 lg:p-8">
        <Header dogName={profile.dog_name} location={profile.location}>
          <ThemeToggle />
          {/* SLOT:bell */}
          <span />
        </Header>
        <StatusBar connected={state.connected} everConnected={state.everConnected} reconnectAttempt={state.reconnectAttempt}
          status={state.status} onMode={(m) => void setMode(m)} />
        {/* SLOT:notices */}
        <span className="hidden" />
        <div className="grid gap-4 [grid-template-areas:'card'_'video'_'signals'] lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] lg:grid-rows-[auto_auto_1fr] lg:gap-6 lg:[grid-template-areas:'video_card'_'video_treat'_'video_signals']">
          <div className="min-h-[216px] [grid-area:video] lg:min-h-[480px]">
            {/* SLOT:video */}
            <div className="h-full rounded-xl bg-[#1B1D1F]" />
          </div>
          <div className="[grid-area:card]">
            <EmotionCard current={state.current} currentSince={state.currentSince} lastEmotionAt={state.lastEmotionAt}
              nowMs={nowMs} nowSec={nowSec} paused={paused} projector={projector} dogName={profile.dog_name}
              lastDogTs={state.lastDogTs} lastSeenEmotion={lastSeenEmotion} />
          </div>
          <div className="hidden [grid-area:treat] lg:block">
            <TreatButton onTreat={onTreat} flash={treatFlash} />
          </div>
          <div className="[grid-area:signals]">
            {/* SLOT:signals */}
            <span />
          </div>
        </div>
        {/* SLOT:timeline */}
        <span className="hidden" />
      </div>
      <div className="fixed inset-x-0 bottom-0 z-20 border-t border-border bg-bg/90 p-3 backdrop-blur lg:hidden"
        style={{ paddingBottom: "max(12px, env(safe-area-inset-bottom))" }}>
        <TreatButton onTreat={onTreat} flash={treatFlash} />
      </div>
      {/* SLOT:toasts */}
      <span className="hidden" />
    </div>
  );
}
```

`frontend/components/DashboardClient.tsx`:
```tsx
"use client";

import dynamic from "next/dynamic";

const Dashboard = dynamic(() => import("./Dashboard"), {
  ssr: false,
  loading: () => <div className="p-8 text-small text-muted">Loading Claude Pet…</div>,
});

export default function DashboardClient() {
  return <Dashboard />;
}
```

Replace `frontend/app/page.tsx`:
```tsx
import DashboardClient from "@/components/DashboardClient";

export default function Page() {
  return <DashboardClient />;
}
```
Delete `frontend/components/HealthBadge.tsx`.

- [ ] **Step 5: verify**
- `npm run test && npm run typecheck && npm run lint && npm run build` → green.
- Manual (backend `make dev-backend`, frontend `npm run dev`, open http://localhost:3000):
  - Status bar: "Connected", "Mock camera", ~8 fps, "Rules only" (no LLM key), Live pressed and Demo disabled with a tooltip.
  - Header: "Bruno", "Feeding area · Kitchen" (desktop) or "Claude Pet · Kitchen" (narrow window).
  - The emotion card starts at "Waiting for Bruno", then shows the mock's emotions (Relaxed → Excited → …) with the colour bar, the source badge "Rules", "updated Ns ago", the "since HH:MM · N min" line and the confidence bar.
  - Press **T** (or click Treat dropped): the button shows "Treat marked" for 1.5 s, and within ~3 s the card turns Excited (the mock jumps phase).
  - During the mock's "absent" phase (66–72 s into the loop): "No dog in view" with "Last seen …".
  - Stop the backend: the status bar reads "Reconnecting · attempt n" and the card greys to "Paused · last reading". Restart the backend and it recovers without a reload.
  - The theme toggle flips light/dark, and a reload keeps the choice.
  - `http://localhost:3000/?size=projector` makes the label much larger.

- [ ] **Step 6: commit**
```bash
git add -A frontend/lib frontend/components frontend/app/page.tsx
git commit -m "Web step 7b: backend hook, header, status bar, emotion card, treat button + T hotkey"
```

---

### Task 6: Video panel with canvas overlay + signals panel

**Files:**
- Create: `frontend/components/VideoPanel.tsx`, `frontend/components/SignalsPanel.tsx`, `frontend/components/Sparkline.tsx`
- Modify: `frontend/components/Dashboard.tsx` (fill `SLOT:video` and `SLOT:signals`)

**Interfaces:**
- Consumes: `fitContain`, `drawOverlay`, `Layers` (Task 4); `signalRows` (Task 4); `hhmmss`, `durationLabel` (Task 3).
- Produces: `<VideoPanel http frame nowSec location paused lastDogTs />`, `<SignalsPanel history defaultOpen />`, `<Sparkline points />`.

- [ ] **Step 1: components**

`frontend/components/Sparkline.tsx`:
```tsx
export default function Sparkline({ points }: { points: string }) {
  return (
    <svg viewBox="0 0 100 24" preserveAspectRatio="none" className="h-6 w-full" aria-hidden="true">
      {points && (
        <polyline points={points} fill="none" stroke="var(--accent)" strokeWidth="1.75"
          vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
      )}
    </svg>
  );
}
```

`frontend/components/VideoPanel.tsx`:
```tsx
"use client";

import { useEffect, useRef, useState } from "react";
import type { FrameEvent } from "@/lib/contracts";
import { durationLabel, hhmmss } from "@/lib/format";
import { drawOverlay, fitContain, type Layers } from "@/lib/overlay";

type Props = {
  http: string;
  frame: FrameEvent | null;
  nowSec: number;
  location: string;
  paused: boolean;
  lastDogTs: number | null;
};

const chip = "flex h-[30px] items-center rounded-lg px-3 text-[12px]";
const scrim = { background: "var(--overlay-scrim)" };

export default function VideoPanel({ http, frame, nowSec, location, paused, lastDogTs }: Props) {
  const boxRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [layers, setLayers] = useState<Layers>({ box: true, skeleton: true, face: true });
  const [retryKey, setRetryKey] = useState(0);
  const [videoOk, setVideoOk] = useState(false);
  const [size, setSize] = useState({ w: 0, h: 0 });

  useEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    const ro = new ResizeObserver(([entry]) => setSize({ w: entry.contentRect.width, h: entry.contentRect.height }));
    ro.observe(box);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    const img = imgRef.current;
    if (!canvas || !img || size.w === 0) return;
    const dpr = window.devicePixelRatio || 1;
    const cw = Math.round(size.w * dpr), ch = Math.round(size.h * dpr);
    if (canvas.width !== cw || canvas.height !== ch) {
      canvas.width = cw;
      canvas.height = ch;
    }
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    drawOverlay(ctx, paused || !videoOk ? null : frame, fitContain(img.naturalWidth, img.naturalHeight, size.w, size.h), layers, dpr);
  }, [frame, layers, paused, size, videoOk]);

  const onError = () => {
    setVideoOk(false);
    setTimeout(() => setRetryKey((k) => k + 1), 2000);
  };

  const kp = frame ? Object.values(frame.body_keypoints).filter(Boolean).length : 0;
  const facePts = frame?.face_landmarks?.length ?? 0;
  const noDog = frame !== null && !frame.dog_detected;
  const toggle = (k: keyof Layers) => setLayers((l) => ({ ...l, [k]: !l[k] }));
  // When the backend restarts, Chrome ends the MJPEG stream without an error event, so the <img> would
  // freeze on its last frame. Keying the stream on `paused` opens a fresh stream once we're live again.
  const streamId = `${retryKey}${paused ? "p" : ""}`;

  return (
    <section aria-label="Live video of the feeding area" ref={boxRef}
      className="relative h-full min-h-[216px] overflow-hidden rounded-xl bg-[#1B1D1F]">
      {/* eslint-disable-next-line @next/next/no-img-element -- MJPEG stream, next/image can't handle it */}
      <img ref={imgRef} key={streamId} src={`${http}/video?k=${streamId}`} alt=""
        onLoad={() => setVideoOk(true)} onError={onError}
        className="absolute inset-0 h-full w-full object-contain" />
      <canvas ref={canvasRef} className="pointer-events-none absolute inset-0 h-full w-full" />

      {(paused || !videoOk) && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 p-6 text-center text-[#D6D8DB]" style={scrim}>
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#7FE3E8" strokeWidth="2" strokeLinecap="round"
            style={{ animation: "spin 1.2s linear infinite" }} aria-hidden="true"><path d="M12 3a9 9 0 1 0 9 9" /></svg>
          <div className="text-heading font-semibold text-white">{paused ? "Reconnecting…" : "Waiting for video…"}</div>
          {paused && <div className="max-w-sm text-small">The camera phone dropped off Wi‑Fi. Keep it on and near the router.</div>}
        </div>
      )}

      <div className="absolute left-4 top-4 flex flex-wrap gap-2">
        <div className={`${chip} gap-2 font-bold tracking-[0.06em] text-white`} style={scrim}>
          <span className="h-2 w-2 rounded-full" style={{ background: "var(--live)" }} />LIVE
          <span className="font-mono font-normal tracking-normal text-[#D6D8DB]">{hhmmss(nowSec)}</span>
        </div>
        <div className={`${chip} text-[#D6D8DB]`} style={scrim}>{location} · bowl cam</div>
        {noDog && (
          <div className={`${chip} gap-1 text-white`} style={scrim}>
            No dog in view{lastDogTs && <span className="font-mono text-[#A0A5AD]">· {durationLabel(nowSec - lastDogTs)}</span>}
          </div>
        )}
      </div>

      <div className="absolute inset-x-4 bottom-4 flex flex-wrap items-center gap-2">
        <div className={`${chip} gap-3 text-[#D6D8DB]`} style={scrim}><span>Pose {kp} kp</span><span>Face {facePts} pts</span></div>
        <div className="grow" />
        <div role="group" aria-label="Overlay layers" className="flex gap-1 rounded-md p-1" style={scrim}>
          {(["box", "skeleton", "face"] as const).map((k) => (
            <button key={k} type="button" aria-pressed={layers[k]} onClick={() => toggle(k)}
              className="h-[30px] rounded-sm px-2.5 text-[12px] font-semibold"
              style={layers[k] ? { background: "rgba(127,227,232,.18)", color: "#BFF2F4" } : { color: "#A0A5AD" }}>
              {k === "box" ? "Box" : k === "skeleton" ? "Skeleton" : "Face"}
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
```

`frontend/components/SignalsPanel.tsx`:
```tsx
"use client";

import { useState } from "react";
import type { Features } from "@/lib/contracts";
import { signalRows } from "@/lib/signals";
import Sparkline from "./Sparkline";

export default function SignalsPanel({ history, defaultOpen }: { history: Features[]; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const rows = signalRows(history);
  const live = rows.filter((r) => r.live).length;
  return (
    <section aria-label="Signals" className="flex flex-col rounded-xl border border-border bg-surface">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
        className="flex h-[52px] items-center gap-2.5 px-5 text-left text-body font-semibold text-text">
        Signals<span className="text-[12px] font-medium text-muted">{live} live · from pose, face and audio</span>
        <span className="grow" />
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
          strokeLinejoin="round" style={{ transform: `rotate(${open ? 180 : 0}deg)` }} aria-hidden="true"><path d="M6 9l6 6 6-6" /></svg>
      </button>
      {open && (
        <div className="grid grid-cols-2 gap-px overflow-hidden rounded-b-xl border-t border-border bg-border sm:grid-cols-3">
          {rows.map((r) => (
            <div key={r.key} className="flex flex-col gap-1.5 bg-surface px-4 py-3.5">
              <div className="text-[11px] font-bold uppercase tracking-[0.06em] text-muted">{r.name}</div>
              <div className="flex items-baseline gap-1">
                <span className="font-mono text-[20px] font-medium">{r.value}</span>
                <span className="text-[12px] text-muted">{r.unit}</span>
              </div>
              <Sparkline points={r.points} />
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
```

- [ ] **Step 2: wire into the dashboard** — in `frontend/components/Dashboard.tsx`:
  - Add imports: `import SignalsPanel from "./SignalsPanel";` and `import VideoPanel from "./VideoPanel";`.
  - Replace
    ```tsx
            {/* SLOT:video */}
            <div className="h-full rounded-xl bg-[#1B1D1F]" />
    ```
    with
    ```tsx
            <VideoPanel http={backend.http} frame={state.frame} nowSec={nowSec} location={profile.location}
              paused={paused} lastDogTs={state.lastDogTs} />
    ```
  - Replace
    ```tsx
            {/* SLOT:signals */}
            <span />
    ```
    with
    ```tsx
            <SignalsPanel history={state.history} defaultOpen={!projector} />
    ```

- [ ] **Step 3: verify**
- `npm run test && npm run typecheck && npm run lint && npm run build` → green.
- Manual (backend + frontend running): the grey mock frame with the white stick figure shows in the video panel. The cyan overlay (bbox with corner brackets, "dog 0.90" chip, skeleton lines and dots) sits **on top of** the white figure, which confirms the scaling. Resize the window: the overlay stays aligned. Box/Skeleton/Face toggle their layers. The clock ticks. In the mock's absent phase the "No dog in view · N s" chip appears and the overlay clears. Signals show 6 tiles with moving sparklines, and some values flip to "—" now and then (the mock's null features). Stop the backend: the video shows "Reconnecting…".

- [ ] **Step 4: commit**
```bash
git add frontend/components
git commit -m "Web step 8b: MJPEG video with canvas overlay and layer toggles, signals panel"
```

---

### Task 7: Session timeline (spans, markers, audio lane, ticks, selected-span detail)

**Files:**
- Create: `frontend/components/Timeline.tsx`
- Modify: `frontend/components/Dashboard.tsx` (fill `SLOT:timeline`, add selection state)

**Interfaces:**
- Consumes: `timelineWindow`, `layoutSpans`, `groupAudio`, `ticks`, `leftPct`, `spanAt` (Task 4); `EMOTION_ORDER`, `EMOTION_META`, `emotionVars`, `sourceLabel` (Task 3); `hhmm`, `durationLabel`, `pct` (Task 3).
- Produces: `<Timeline spans audio treats notifications nowSec selected onSelect />`, where `selected: number | null` (null = last span) and `onSelect(index: number)`.

- [ ] **Step 1: component** — `frontend/components/Timeline.tsx` (structure mirrors `ui/screens/00-dashboard-desktop.html` "Timeline + audio"; the empty state copies `10-system-states.html` "First load"):
```tsx
"use client";

import { EMOTION_META, EMOTION_ORDER, emotionVars, sourceLabel } from "@/lib/emotions";
import { durationLabel, hhmm, pct } from "@/lib/format";
import { groupAudio, layoutSpans, leftPct, ticks, timelineWindow } from "@/lib/timeline";
import type { AudioMark, NotificationItem, Span } from "@/lib/types";
import EmotionIcon from "./EmotionIcon";

type Props = {
  spans: Span[];
  audio: AudioMark[];
  treats: number[];
  notifications: NotificationItem[];
  nowSec: number;
  selected: number | null;
  onSelect: (index: number) => void;
};

const TREAT_ICON = "M3 13h18a9 5 0 0 1-18 0zM12 3v6";
const BELL_ICON = "M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15zM10 20.5a2 2 0 0 0 4 0";

export default function Timeline({ spans, audio, treats, notifications, nowSec, selected, onSelect }: Props) {
  const w = timelineWindow(spans, nowSec);
  const laid = layoutSpans(spans, w, nowSec);
  const sounds = groupAudio(audio, w);
  const tickList = ticks(w, 6);
  const markers = [
    ...treats.filter((t) => t >= w.start).map((t) => ({ t, kind: "treat" as const, title: `Treat dropped ${hhmm(t)}` })),
    ...notifications.filter((n) => n.ts >= w.start).map((n) => ({ t: n.ts, kind: "bell" as const, title: `Owner notified ${hhmm(n.ts)}` })),
  ];
  const selIndex = selected ?? spans.length - 1;
  const sel = spans[selIndex];

  return (
    <section aria-label="Session timeline" className="flex flex-col gap-3.5 rounded-xl border border-border bg-surface px-4 py-5 lg:px-6">
      <div className="flex flex-wrap items-center gap-4">
        <div className="text-heading font-semibold"><span className="lg:hidden">Timeline</span><span className="hidden lg:inline">Session timeline</span></div>
        <div className="font-mono text-small text-muted">{hhmm(w.start)} – now · {durationLabel(w.end - w.start)}</div>
        <div className="grow" />
        <div className="text-[12px] text-muted lg:hidden">Swipe</div>
        <div className="hidden flex-wrap gap-3.5 lg:flex">
          {EMOTION_ORDER.map((e) => (
            <div key={e} className="flex items-center gap-1.5 text-[12px] text-muted">
              <EmotionIcon emotion={e} size={16} strokeWidth={1.9} />{EMOTION_META[e].label}
            </div>
          ))}
        </div>
      </div>

      {spans.length === 0 ? (
        <div className="flex flex-col items-center gap-1 rounded-lg border border-dashed border-border px-6 py-8 text-center">
          <div className="text-body font-semibold">The timeline fills in as readings arrive</div>
          <div className="text-small text-muted">
            Press <span className="font-bold text-accent-ink">Treat dropped</span> to mark a moment and watch the response.
          </div>
        </div>
      ) : (
        <div className="-mx-4 overflow-x-auto px-4 lg:mx-0 lg:px-0">
          <div className="grid min-w-[640px] grid-cols-[72px_minmax(0,1fr)] gap-x-3 lg:min-w-0">
            <div className="flex flex-col text-[12px] font-semibold text-muted">
              <div className="h-6" />
              <div className="flex h-11 items-center">Emotion</div>
              <div className="h-2" />
              <div className="flex h-10 items-center">Audio</div>
            </div>
            <div className="relative">
              <div className="pointer-events-none absolute inset-x-0 bottom-5 top-6">
                {markers.map((m, i) => (
                  <div key={`l${i}`} className="absolute bottom-0 top-0 border-l border-dashed opacity-70"
                    style={{ left: `${leftPct(m.t, w)}%`, borderColor: m.kind === "treat" ? "var(--accent)" : "var(--muted)" }} />
                ))}
              </div>
              <div className="relative h-6">
                {markers.map((m, i) => (
                  <div key={`m${i}`} title={m.title}
                    className="absolute top-0 flex h-5 w-5 -translate-x-1/2 items-center justify-center rounded-full"
                    style={{ left: `${leftPct(m.t, w)}%`, background: m.kind === "treat" ? "var(--accent-soft)" : "var(--surface-2)", color: m.kind === "treat" ? "var(--accent)" : "var(--muted)" }}>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d={m.kind === "treat" ? TREAT_ICON : BELL_ICON} />
                    </svg>
                  </div>
                ))}
              </div>
              <div className="relative h-11 overflow-hidden rounded-lg bg-track">
                {laid.map(({ span, index, left, width }) => {
                  const v = emotionVars(span.emotion);
                  const end = index === spans.length - 1 ? "now" : hhmm(span.end);
                  return (
                    <button key={`${span.start}-${index}`} type="button" onClick={() => onSelect(index)}
                      aria-label={`${EMOTION_META[span.emotion].label}, ${hhmm(span.start)} to ${end}`}
                      className="absolute bottom-0 top-0 flex items-center justify-center overflow-hidden border-r-2 border-surface p-0"
                      style={{ left: `${left}%`, width: `${width}%`, background: v.tint, boxShadow: index === selIndex ? `inset 0 0 0 2px ${v.fg}` : "none" }}>
                      <span className="absolute inset-x-0 top-0 h-1" style={{ background: v.solid }} />
                      <EmotionIcon emotion={span.emotion} size={18} strokeWidth={1.9} />
                    </button>
                  );
                })}
              </div>
              <div className="h-2" />
              <div className="relative h-10 rounded-lg bg-surface-2">
                <div className="absolute inset-x-0 top-1/2 h-px bg-border" />
                {sounds.map((a, i) => (
                  <div key={`a${i}`}
                    className="absolute top-1/2 flex h-6 -translate-x-1/2 -translate-y-1/2 items-center gap-1 whitespace-nowrap rounded-full border border-border bg-surface px-2 text-[11px] font-semibold"
                    style={{ left: `${a.left}%` }}>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--muted)" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true"><path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 10v4" /></svg>
                    {a.label}{a.count > 1 ? ` ×${a.count}` : ""}
                  </div>
                ))}
              </div>
              <div className="relative mt-1.5 h-5">
                {tickList.map((k, i) => (
                  <div key={`t${i}`} className="absolute top-0 font-mono text-[11px] text-muted"
                    style={{ left: `${k.left}%`, transform: k.align === "start" ? "none" : k.align === "end" ? "translateX(-100%)" : "translateX(-50%)" }}>
                    {k.label}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {sel && (
        <div className="flex flex-wrap items-center gap-x-3.5 gap-y-1 rounded-[12px] px-4 py-3" style={{ background: emotionVars(sel.emotion).tint }}>
          <EmotionIcon emotion={sel.emotion} size={24} />
          <div className="min-w-24 text-body font-bold" style={{ color: emotionVars(sel.emotion).fg }}>{EMOTION_META[sel.emotion].label}</div>
          <div className="whitespace-nowrap font-mono text-small text-text-soft">
            {hhmm(sel.start)} – {selIndex === spans.length - 1 ? "now" : hhmm(sel.end)}
          </div>
          <div className="min-w-0 grow text-[14px] text-text">{sel.reason}</div>
          <div className="whitespace-nowrap text-[12px] text-text-soft">{sourceLabel(sel.source)} · {pct(sel.confidence)}</div>
        </div>
      )}
    </section>
  );
}
```

- [ ] **Step 2: wire into the dashboard** — in `frontend/components/Dashboard.tsx`:
  - Add `import Timeline from "./Timeline";`.
  - Below the `treatFlash` state add: `const [selectedSpan, setSelectedSpan] = useState<number | null>(null);`
  - Replace
    ```tsx
        {/* SLOT:timeline */}
        <span className="hidden" />
    ```
    with
    ```tsx
        <div id="timeline">
          <Timeline spans={state.spans} audio={state.audio} treats={state.treats} notifications={state.notifications}
            nowSec={nowSec} selected={selectedSpan} onSelect={setSelectedSpan} />
        </div>
    ```

- [ ] **Step 3: verify**
- `npm run test && npm run typecheck && npm run lint && npm run build` → green.
- Manual: with a fresh backend the timeline first shows the empty state. Within seconds coloured spans appear and grow to "now". Press **T**: a treat marker with a dashed line appears. Yip chips show in the Audio lane during Excited, grouped as "yip ×2" when close together. Clicking a span shows its detail row (range, reason, "Rules · 64%"). With nothing selected, the detail row follows the latest span ("– now"). On a narrow window the timeline scrolls sideways and shows "Swipe".

- [ ] **Step 4: commit**
```bash
git add frontend/components
git commit -m "Web step 8c: session timeline with markers, audio lane, ticks and span detail"
```

---

### Task 8: Toasts, notifications bell, system-state notices

Toasts live **in the reducer**, not in component effects. Notification envelopes create or group toasts, a 1 s `tick` action expires them, and status updates detect the AI going offline or coming back. Keeping this pure and tested also avoids `setState`-in-effect patterns, which React 19 lint rules reject.

**Files:**
- Modify: `frontend/lib/types.ts` (state + actions), `frontend/lib/store.ts` (toast handling), `frontend/lib/useBackend.ts` (tick + dismiss), `frontend/components/Dashboard.tsx` (fill `SLOT:bell`, `SLOT:notices`, `SLOT:toasts`)
- Create: `frontend/components/ToastStack.tsx`, `frontend/components/NotificationsBell.tsx`, `frontend/components/SystemNotice.tsx`
- Test: `frontend/lib/store.test.ts` (append)

**Interfaces:**
- Consumes: `toastFromNotification`, `systemToast`, `expireToasts`, `dismissToast`, `Toast` (Task 4).
- Produces:
  - `DashboardState.toasts: Toast[]`; new actions `{ kind: "tick"; at: number }` and `{ kind: "dismissToast"; id: string }`.
  - `useBackend()` additionally returns `dismissToast(id: string): void`, and dispatches `tick` every second.
  - `<ToastStack toasts onDismiss onView />`, `<NotificationsBell notifications dogName onOpen />`, `<SystemNotice connected everConnected reconnectAttempt status lastFrameTs />`.

- [ ] **Step 1: failing tests** — append to `frontend/lib/store.test.ts`:
```ts
describe("toasts in the reducer", () => {
  const notif = (e: string, ts: number) =>
    env("notification", { state: { ts, emotion: e, confidence: 0.8, source: "fused", reason: `${e}!`, snapshot: null }, status: "dashboard_only", channel: "dashboard", detail: "would send to owner" }, ts);
  const withProfile = reduce(initialState, { kind: "status", status: { pipeline: "mock", profile: { dog_name: "Bruno", location: "Kitchen", zone_label: "feeding area" } } });

  it("notification creates a toast using the dog name", () => {
    const s = reduce(withProfile, { kind: "envelope", env: notif("anxious", 10), at: 1_000 });
    expect(s.toasts[0]).toMatchObject({ title: "Bruno seems anxious", sticky: true });
  });
  it("tick expires positive toasts after 6 s; dismiss removes", () => {
    let s = reduce(withProfile, { kind: "envelope", env: notif("excited", 10), at: 1_000 });
    s = reduce(s, { kind: "tick", at: 7_500 });
    expect(s.toasts).toHaveLength(0);
    s = reduce(s, { kind: "envelope", env: notif("fearful", 11), at: 8_000 });
    s = reduce(s, { kind: "dismissToast", id: s.toasts[0].id });
    expect(s.toasts).toHaveLength(0);
  });
  it("AI going offline and back produces system toasts", () => {
    const llm = (online: boolean) => ({ enabled: true, online, provider: "p", model: "m", vision: true, last_call: null });
    let s = reduce(withProfile, { kind: "status", status: { pipeline: "mock", llm: llm(true) } });
    expect(s.toasts).toHaveLength(0);
    s = reduce(s, { kind: "status", status: { pipeline: "mock", llm: llm(false) } });
    expect(s.toasts[0]).toMatchObject({ kind: "system", title: "AI offline · rules only" });
    s = reduce(s, { kind: "status", status: { pipeline: "mock", llm: llm(true) } });
    expect(s.toasts[0].title).toBe("AI back online · fused readings resumed");
  });
  it("bootstrap does not toast old notifications", () => {
    const s = reduce(withProfile, { kind: "bootstrap", events: [notif("anxious", 5)], timeline: [], at: 1 });
    expect(s.notifications).toHaveLength(1);
    expect(s.toasts).toHaveLength(0);
  });
});
```
Run: `npm run test` → FAIL (no `toasts` in state).

- [ ] **Step 2: types** — in `frontend/lib/types.ts`:
  - Add at the top: `import type { Toast } from "./toasts";`
  - Add the field `toasts: Toast[];` to `DashboardState` (after `notifications`).
  - Add two members to the `Action` union:
    ```ts
      | { kind: "tick"; at: number }
      | { kind: "dismissToast"; id: string };
    ```
    (Move the final `;` from `{ kind: "readAll" }` to the new last member.)

- [ ] **Step 3: store** — in `frontend/lib/store.ts`:
  - Add the import: `import { dismissToast, expireToasts, systemToast, toastFromNotification } from "./toasts";`
  - Add `toasts: [],` to `initialState` (after `notifications: []`).
  - Change the signature of `applyEnvelope` to take one more parameter, `live: boolean` (true for real-time envelopes, false during bootstrap replay): `function applyEnvelope(s: DashboardState, env: Envelope, at: number, touchSpans: boolean, live: boolean): DashboardState`.
  - In its `"notification"` case, replace the `return` with:
    ```ts
      const notifications = [...base.notifications, item].slice(-NOTIFICATIONS_MAX);
      const dog = base.status?.profile?.dog_name ?? "Your dog";
      return { ...base, notifications, toasts: live ? toastFromNotification(base.toasts, item, dog, at) : base.toasts };
    ```
  - Add this helper above `reduce`:
    ```ts
    function withStatus(s: DashboardState, next: Status, at: number): DashboardState {
      const was = s.status?.llm;
      const now = next.llm;
      let toasts = s.toasts;
      if (was?.enabled && now?.enabled && was.online !== now.online) {
        toasts = systemToast(toasts, now.online ? "AI back online · fused readings resumed" : "AI offline · rules only", at);
      }
      return { ...s, status: next, toasts };
    }
    ```
  - In `applyEnvelope`'s `"status"` case, replace the return with:
    `return withStatus(base, isFull ? d : { ...(base.status ?? {}), ...d }, at);`
  - In `reduce`:
    - `"envelope"` → `return applyEnvelope(s, a.env, a.at, true, true);`
    - `"bootstrap"` → in the `a.events.reduce(...)` call pass `false, false`: `applyEnvelope(acc, e, a.at, false, false)`. Also keep existing toasts, i.e. don't reset `toasts` in `reset`.
    - `"status"` → `return withStatus(s, a.status, Date.now());`
    - Add:
      ```ts
          case "tick":
            return { ...s, toasts: expireToasts(s.toasts, a.at) };
          case "dismissToast":
            return { ...s, toasts: dismissToast(s.toasts, a.id) };
      ```
  Run: `npm run test` → all store tests pass (including the 4 new ones). `expireToasts` returns the same array when nothing expired, so the 1 s tick doesn't cause needless re-renders.

- [ ] **Step 4: hook** — in `frontend/lib/useBackend.ts`:
  - Inside the effect, after `const poll = setInterval(...)`, add: `const tick = setInterval(() => dispatch({ kind: "tick", at: Date.now() }), 1000);`, and in the cleanup add `clearInterval(tick);`.
  - Add `const dismissToast = useCallback((id: string) => dispatch({ kind: "dismissToast", id }), []);` and include `dismissToast` in the returned object.

- [ ] **Step 5: components**

`frontend/components/ToastStack.tsx` (design: `ui/screens/09-notifications.html`):
```tsx
"use client";

import { hhmm } from "@/lib/format";
import type { Toast } from "@/lib/toasts";
import EmotionIcon from "./EmotionIcon";

type Props = { toasts: Toast[]; onDismiss: (id: string) => void; onView: (ts: number) => void };

function statusLine(t: Toast): string | null {
  if (t.status === "dashboard_only") return "Would send to owner";
  if (t.status === "failed") return "Telegram failed";
  if (t.status === "sent") return "Sent to owner";
  return null;
}

export default function ToastStack({ toasts, onDismiss, onView }: Props) {
  if (!toasts.length) return null;
  return (
    <div className="pointer-events-none fixed inset-x-3 top-3 z-30 flex flex-col gap-3 lg:inset-x-auto lg:right-8 lg:top-8 lg:w-[380px]">
      {toasts.map((t) => {
        const words = t.title.split(" ");
        const last = words.pop();
        const line = statusLine(t);
        return (
          <div key={t.id} role={t.kind === "negative" ? "alert" : "status"}
            className="pointer-events-auto relative overflow-hidden rounded-lg border border-border bg-surface shadow-[var(--shadow-toast)]">
            {t.emotion && <div className="absolute inset-x-0 top-0 h-[3px]" style={{ background: `var(--${t.emotion})` }} />}
            <div className="flex gap-3 p-4">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md"
                style={{ background: t.emotion ? `var(--${t.emotion}-tint)` : "var(--accent-soft)" }}>
                {t.emotion ? <EmotionIcon emotion={t.emotion} size={24} /> : (
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 3l2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z" /></svg>
                )}
              </div>
              <div className="flex min-w-0 grow flex-col gap-1">
                <div className="flex items-baseline gap-2">
                  <div className="min-w-0 grow truncate text-[14px] font-bold">
                    {t.emotion ? <>{words.join(" ")} <span style={{ color: `var(--${t.emotion}-fg)` }}>{last}</span></> : t.title}
                    {t.count > 1 && <span className="ml-1.5 rounded-full bg-surface-2 px-1.5 text-[11px] font-semibold text-muted">×{t.count}</span>}
                  </div>
                  <div className="font-mono text-[12px] text-muted">{hhmm(t.ts)}</div>
                </div>
                {t.body && <div className="line-clamp-2 text-small text-text-soft">{t.body}</div>}
                {line && <div className="text-[12px] text-muted" title={t.detail}>{line}</div>}
                {t.kind === "negative" && (
                  <div className="mt-1.5 flex gap-2">
                    <button type="button" onClick={() => onView(t.ts)} className="h-8 rounded-sm bg-accent px-3 text-[12px] font-semibold text-accent-fg">View moment</button>
                    <button type="button" onClick={() => onDismiss(t.id)} className="h-8 rounded-sm border border-border px-3 text-[12px] font-semibold text-text">Dismiss</button>
                  </div>
                )}
              </div>
              {t.kind !== "negative" && (
                <button type="button" aria-label="Dismiss" onClick={() => onDismiss(t.id)} className="-m-2 flex h-11 w-11 shrink-0 items-center justify-center text-muted">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
                </button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
```

`frontend/components/NotificationsBell.tsx`:
```tsx
"use client";

import { useState } from "react";
import { NEGATIVE } from "@/lib/emotions";
import { hhmm } from "@/lib/format";
import type { NotificationItem } from "@/lib/types";
import EmotionIcon from "./EmotionIcon";

type Props = { notifications: NotificationItem[]; dogName: string; onOpen: () => void };

export default function NotificationsBell({ notifications, dogName, onOpen }: Props) {
  const [open, setOpen] = useState(false);
  const unread = notifications.filter((n) => !n.read).length;
  const toggle = () => {
    if (!open) onOpen();
    setOpen(!open);
  };
  return (
    <div className="relative">
      <button type="button" onClick={toggle} aria-expanded={open}
        aria-label={unread ? `Notifications, ${unread} new` : "Notifications"}
        className="relative flex h-11 w-11 items-center justify-center rounded-md border border-border bg-surface text-text">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z" /><path d="M10 20.5a2 2 0 0 0 4 0" /></svg>
        {unread > 0 && (
          <span className="absolute right-1.5 top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-accent px-1 text-[10px] font-bold text-accent-fg">{unread}</span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 top-12 z-40 flex max-h-96 w-80 flex-col overflow-y-auto rounded-lg border border-border bg-surface p-2 shadow-[var(--shadow-toast)]">
          {notifications.length === 0 && <div className="p-4 text-small text-muted">No notifications yet.</div>}
          {[...notifications].reverse().map((n) => (
            <div key={n.id} className="flex gap-3 rounded-md p-2 hover:bg-surface-2">
              <EmotionIcon emotion={n.state.emotion} size={22} />
              <div className="flex min-w-0 grow flex-col">
                <div className="flex items-baseline gap-2">
                  <div className="grow truncate text-small font-bold">{dogName} {NEGATIVE.has(n.state.emotion) ? "seems" : "is"} {n.state.emotion}</div>
                  <div className="font-mono text-[11px] text-muted">{hhmm(n.ts)}</div>
                </div>
                <div className="truncate text-[12px] text-text-soft">{n.state.reason}</div>
                <div className="text-[11px] text-muted">{n.status === "dashboard_only" ? "Would send to owner" : n.status === "failed" ? "Telegram failed" : "Sent to owner"}</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

`frontend/components/SystemNotice.tsx` (copy from `ui/screens/10-system-states.html`; the demo-mode and camera-blocked states arrive in Plans 3 and 4):
```tsx
import { hhmmss } from "@/lib/format";
import type { Status } from "@/lib/types";

type Props = { connected: boolean; everConnected: boolean; reconnectAttempt: number; status: Status | null; lastFrameTs: number | null };

function Banner({ children }: { children: React.ReactNode }) {
  return <div role="status" className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-border bg-surface-2 px-4 py-2.5 text-small text-text-soft">{children}</div>;
}

export default function SystemNotice({ connected, everConnected, reconnectAttempt, status, lastFrameTs }: Props) {
  if (!connected && everConnected) {
    return (
      <Banner>
        <span className="font-bold text-text">Reconnecting to Claude Pet</span>
        <span>attempt {reconnectAttempt}</span>
        {lastFrameTs && <span className="font-mono">last frame {hhmmss(lastFrameTs)}</span>}
      </Banner>
    );
  }
  if (status?.pipeline_status?.state === "stalled") {
    return (
      <Banner>
        <span className="font-bold text-text">Reconnecting to camera</span>
        {lastFrameTs && <span className="font-mono">last frame {hhmmss(lastFrameTs)}</span>}
        <span>The camera phone dropped off Wi‑Fi. Keep it on and near the router.</span>
      </Banner>
    );
  }
  if (status?.llm?.enabled && !status.llm.online) {
    return (
      <Banner>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M12 8h.01M11 12h1v4h1" /></svg>
        <span>AI is unreachable, so readings come from rules and may be less nuanced. Retrying automatically.</span>
      </Banner>
    );
  }
  return null;
}
```

- [ ] **Step 6: wire into the dashboard** — in `frontend/components/Dashboard.tsx`:
  - Imports: `import NotificationsBell from "./NotificationsBell";`, `import SystemNotice from "./SystemNotice";`, `import ToastStack from "./ToastStack";`, and add `spanAt` via `import { spanAt } from "@/lib/timeline";`.
  - Add a handler below `setSelectedSpan`:
    ```tsx
      const viewMoment = useCallback((ts: number) => {
        const i = spanAt(state.spans, ts);
        if (i >= 0) setSelectedSpan(i);
        document.getElementById("timeline")?.scrollIntoView({ behavior: "smooth", block: "center" });
      }, [state.spans]);
    ```
  - Replace `{/* SLOT:bell */}` and the `<span />` after it with:
    ```tsx
          <NotificationsBell notifications={state.notifications} dogName={profile.dog_name} onOpen={backend.readAll} />
    ```
  - Replace `{/* SLOT:notices */}` and the `<span className="hidden" />` after it with:
    ```tsx
        <SystemNotice connected={state.connected} everConnected={state.everConnected} reconnectAttempt={state.reconnectAttempt}
          status={state.status} lastFrameTs={state.frame?.ts ?? null} />
    ```
  - Replace `{/* SLOT:toasts */}` and the `<span className="hidden" />` after it with:
    ```tsx
      <ToastStack toasts={state.toasts} onDismiss={backend.dismissToast} onView={viewMoment} />
    ```

- [ ] **Step 7: verify**
- `npm run test && npm run typecheck && npm run lint && npm run build` → green.
- Manual (backend mock, dashboard-only notifications):
  - When the mock reaches Excited (or you press T), a positive toast "Bruno is excited" with "Would send to owner" appears top-right and fades after ~6 s. The bell badge counts up; opening the bell lists the notifications and clears the badge.
  - The mock's Disinterested and Anxious phases produce **sticky** negative toasts ("Bruno seems disinterested") with **View moment** / **Dismiss**. View moment scrolls to the timeline and selects the matching span. Dismiss removes the toast.
  - Stop the backend: the banner "Reconnecting to Claude Pet · attempt n · last frame …" appears; it clears when the backend restarts.
  - With a bad key (`LLM_BASE_URL=http://127.0.0.1:9/v1 LLM_API_KEY=x LLM_MODEL=x make dev-backend`), after the first failed call the AI-unreachable banner and "AI offline · rules only" appear in the status bar.

- [ ] **Step 8: commit**
```bash
git add frontend/lib frontend/components
git commit -m "Web step 8d: toasts (grouped, sticky negatives), notifications bell, system-state notices"
```

---

### Task 9: Whole-dashboard verification + docs

**Files:**
- Modify: `CLAUDE.md` ("Commands"), `Makefile` (`dev` already runs both)

- [ ] **Step 1: full checks**
```bash
.venv/bin/python -m pytest -q                              # backend + ts-types staleness
cd frontend && npm run test && npm run typecheck && npm run lint && npm run build
```
Expected: all green.

- [ ] **Step 2: visual check against the design** — `make dev`, then open http://localhost:3000 next to `ui/screens/00-dashboard-desktop.html` (open that file in a browser; the design renders from its template). Check at 1440 px wide (light and dark), at 1920 with `?size=projector`, and at 390 px in the device toolbar (mobile order: card → video → signals → timeline, plus the sticky Treat bar). Fix spacing or colour differences in the components, not in the tokens.

- [ ] **Step 3: CLAUDE.md** — in the "Commands" block, replace the two frontend lines (`# frontend` / `cd frontend && npm run dev`) with:
```
# frontend (Next.js 16; first time: cd frontend && npm install)
make dev-frontend           # http://localhost:3000 (backend URL: ?backend=… or NEXT_PUBLIC_BACKEND_URL)
make test-frontend          # vitest + typecheck + lint
python scripts/gen_ts_types.py   # after changing backend/contracts.py
```

- [ ] **Step 4: commit**
```bash
git add CLAUDE.md
git commit -m "Docs: frontend commands"
```

---

## Self-review (done while writing)

- **Spec coverage:** desktop layout (header, status bar, video + overlay + layer toggles, emotion card, treat + T, signals, timeline, span detail) → Tasks 5–7. Mobile order + sticky treat → Task 5 grid areas. Projector → `?size=projector` (Task 5/6). Light/dark + no flash → Task 1. Toasts (sticky negatives, 6 s fade, 2-min grouping, "would send to owner") + bell → Task 8. System states: reconnecting, camera stalled, no dog, AI offline, first load → Tasks 5, 7, 8. Demo banner/clip list → Plan 4. Camera blocked → Plan 3. Generated TS contracts → Task 2.
- **Type consistency:** `DashboardState` fields are used identically in the store, hook and components. `Toast` is defined in `toasts.ts` and imported by `types.ts`, which is type-only, so there's no runtime cycle. `useBackend` returns `{ state, treat, readAll, setMode, dismissToast, http }` after Task 8.
- **Known risk (checked: Tailwind 4 compiles these classes, so this fallback isn't needed with the pinned versions):** Tailwind arbitrary `grid-template-areas` with quotes. If a later build rejects it, move those two rules into `globals.css` as `.dash-grid { grid-template-areas: "card" "video" "signals"; } @media (min-width: 1024px) { .dash-grid { grid-template-areas: "video card" "video treat" "video signals"; } }` and use `className="dash-grid …"`.
