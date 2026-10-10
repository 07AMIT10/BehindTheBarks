// Adaptive-UI matrix for the WagWatch dashboard (pin and households modes), driven by playwright-core
// on the system Chrome (no browsers are bundled; same setup as scripts/shot.mjs).
//
//   node scripts/adaptive_ui_dashboard.mjs [--pin http://127.0.0.1:8010] [--hh http://127.0.0.1:8011]
//        [--states idle,live,...] [--viewports 390x844,...] [--out-dir docs/screenshots/adaptive-dashboard]
//        [--report docs/ADAPTIVE_UI_DASHBOARD_2026-10-11.md] [--json /tmp/adaptive.json] [--compare before.json]
//
// It drives the states of the matrix against two already running backends (see the report for the
// launch commands) and starts/stops scripts/sim_phone.py itself. Each cell is one (state, viewport,
// device scale, theme) combination; every assertion is recorded per cell with offender CSS selectors.
// Exit code 1 when any assertion FAILs.

import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PW = process.env.BTB_PLAYWRIGHT || "/tmp/pptr/node_modules/playwright-core/index.mjs";
const { chromium } = await import(pathToFileURL(PW).href);
const CHROME = process.env.BTB_CHROME || "/home/amit/.local/bin/google-chrome";
const PY = process.env.BTB_PY || path.join(REPO, ".venv/bin/python");
const SIM = path.join(REPO, "scripts/sim_phone.py");

const arg = (name, dflt) => {
  const i = process.argv.indexOf(`--${name}`);
  return i === -1 ? dflt : process.argv[i + 1];
};
const PIN = arg("pin", "http://127.0.0.1:8010");
const HH = arg("hh", "http://127.0.0.1:8011");
const ONLY_STATES = arg("states", null)?.split(",") ?? null;
const ONLY_VP = arg("viewports", null)?.split(",") ?? null;
const OUT_DIR = path.resolve(REPO, arg("out-dir", "docs/screenshots/adaptive-dashboard"));
const REPORT = path.resolve(REPO, arg("report", "docs/ADAPTIVE_UI_DASHBOARD_2026-10-11.md"));
const JSON_OUT = arg("json", "/tmp/btb-adapt/adaptive-results.json");
const COMPARE = arg("compare", null);
const RENDER = arg("render", null); // re-render the report from a saved run JSON (no browser)
const SIM_DIR = arg("sim-dir", path.join(os.tmpdir(), "btb-adapt-sims"));
const SHOT_BUDGET = 5.5 * 1024 * 1024; // the screenshot folder must stay under 6 MB

const VIEWPORTS = [
  { w: 360, h: 740, dpr: 2 }, { w: 390, h: 844, dpr: 2 }, { w: 430, h: 932, dpr: 2 },
  { w: 600, h: 960, dpr: 2 }, { w: 768, h: 1024, dpr: 2 }, { w: 844, h: 390, dpr: 2 },
  { w: 1024, h: 768, dpr: 1 }, { w: 1024, h: 768, dpr: 2 }, { w: 1280, h: 800, dpr: 1 },
  { w: 1280, h: 800, dpr: 2 }, { w: 1920, h: 1080, dpr: 1 }, { w: 1920, h: 1080, dpr: 2 },
];
const ASSERTS = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l"];
const ASSERT_NAMES = {
  a: "no horizontal scroll", b: "touch targets", c: "no overlapping controls", d: "text not clipped",
  e: "font >= 12px", f: "bottom bar clears content", g: "modals fit / close reachable", h: "video aspect",
  i: "contrast >= 4.5", j: "reduced motion", k: "keyboard focus + dialog trap/Escape", l: "accessible names",
};

// ---------------------------------------------------------------------------------------------
// In-page library (injected once per page). Plain JS only: it is serialised into the page.
// ---------------------------------------------------------------------------------------------
function ADAPT_LIB() {
  const INTERACTIVE = "button, a[href], input:not([type=hidden]), select, textarea, summary, [role=button], [role=switch], [role=checkbox], [role=tab], [role=link], [role=menuitem]";
  const cv = document.createElement("canvas");
  cv.width = 1; cv.height = 1;
  const cx = cv.getContext("2d", { willReadFrequently: true });
  const cssSel = (el) => {
    if (!el || el.nodeType !== 1) return "?";
    const one = (e) => {
      let s = e.tagName.toLowerCase();
      if (e.id) s += "#" + e.id;
      else if (e.classList.length) s += "." + [...e.classList].slice(0, 2).join(".");
      return s;
    };
    const txt = (el.getAttribute("aria-label") || el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 28);
    return one(el) + (txt ? ` "${txt}"` : "");
  };
  const hiddenAncestor = (el) => {
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      if (e.hasAttribute("inert") || e.getAttribute("aria-hidden") === "true") return true;
    }
    return false;
  };
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.display === "none" || cs.visibility === "hidden") return false;
    }
    return parseFloat(getComputedStyle(el).opacity) > 0;
  };
  const interactive = () => [...document.querySelectorAll(INTERACTIVE)].filter((el) => visible(el) && !hiddenAncestor(el) && !el.disabled);
  const rgba = (c) => {
    cx.clearRect(0, 0, 1, 1);
    cx.fillStyle = "#000000";
    cx.fillStyle = c;
    cx.fillRect(0, 0, 1, 1);
    const d = cx.getImageData(0, 0, 1, 1).data;
    return [d[0], d[1], d[2], d[3] / 255];
  };
  const over = (top, bottom) => {
    const a = top[3];
    return [top[0] * a + bottom[0] * (1 - a), top[1] * a + bottom[1] * (1 - a), top[2] * a + bottom[2] * (1 - a), 1];
  };
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  };
  const ratio = (x, y) => {
    const a = lum(x), b = lum(y);
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
  };
  const accName = (el) => {
    const t = (s) => (s || "").replace(/\s+/g, " ").trim();
    if (t(el.getAttribute("aria-label"))) return true;
    const lb = el.getAttribute("aria-labelledby");
    if (lb && lb.split(/\s+/).some((id) => document.getElementById(id) && t(document.getElementById(id).textContent))) return true;
    if (t(el.textContent)) return true;
    if (t(el.getAttribute("title"))) return true;
    if (el.labels && [...el.labels].some((l) => t(l.textContent))) return true;
    const named = el.querySelector("img[alt], svg[aria-label]");
    return !!(named && (named.getAttribute("alt") || named.getAttribute("aria-label")));
  };
  const focusSig = (el) => {
    const cs = getComputedStyle(el);
    return [cs.outlineStyle, cs.outlineWidth, cs.outlineColor, cs.boxShadow, cs.borderColor].join("|");
  };

  // Tab-walk helpers
  const markInteractive = () => {
    document.querySelectorAll("[data-adapt-id]").forEach((e) => e.removeAttribute("data-adapt-id"));
    const els = interactive();
    els.forEach((el, i) => el.setAttribute("data-adapt-id", String(i)));
    return els.length;
  };
  const dialogRoot = () => {
    const roots = [...document.querySelectorAll("[role=dialog], [aria-modal=true], dialog[open]")].filter((e) => visible(e));
    return roots.length ? roots[roots.length - 1] : null;
  };
  const markDialog = () => {
    document.querySelectorAll("[data-adapt-dialog]").forEach((e) => e.removeAttribute("data-adapt-dialog"));
    const d = dialogRoot();
    if (d) d.setAttribute("data-adapt-dialog", "1");
    return !!d;
  };
  const focusProbe = () => {
    const el = document.activeElement;
    if (!el || el === document.body || el === document.documentElement) return null;
    const on = focusSig(el);
    el.blur();
    const off = focusSig(el);
    el.focus({ preventScroll: true });
    const d = document.querySelector("[data-adapt-dialog]");
    return {
      id: el.getAttribute("data-adapt-id"), sel: cssSel(el), ring: on !== off,
      inDialog: d ? d.contains(el) : null,
    };
  };
  const dialogOpen = () => !!dialogRoot();

  // Full audit of the current viewport.
  const audit = async (opts) => {
    const iw = innerWidth, ih = innerHeight;
    const phone = iw < 768;
    const dark = document.documentElement.classList.contains("dark");
    const base = dark ? [18, 20, 22, 1] : [255, 255, 255, 1];
    const out = { vw: iw, vh: ih, phone, dark, interactive: 0 };

    // (a) horizontal scroll
    const sw = document.documentElement.scrollWidth;
    out.a = { sw, iw, ok: sw <= iw };

    // (b), (c), (l) interactive elements
    const els = interactive();
    out.interactive = els.length;
    const minSize = phone ? 44 : 32;
    out.b = [];
    out.l = [];
    const boxes = [];
    for (const el of els) {
      const label = el.tagName === "INPUT" && el.closest("label") ? el.closest("label") : el;
      const r = label.getBoundingClientRect();
      if (r.width < minSize || r.height < minSize) {
        out.b.push({ sel: cssSel(el), w: Math.round(r.width), h: Math.round(r.height), min: minSize });
      }
      if (!accName(el)) out.l.push({ sel: cssSel(el) });
      boxes.push({ el, r: el.getBoundingClientRect() });
    }
    // Fixed layers (the treat bar, toasts, sheets) and absolute inset-0 backdrops overlap the content
    // by design; they are not counted as overlapping controls.
    const overlayLayer = (el) => {
      for (let e = el; e && e.nodeType === 1; e = e.parentElement) if (getComputedStyle(e).position === "fixed") return true;
      const par = el.offsetParent;
      if (getComputedStyle(el).position === "absolute" && par) {
        const a = el.getBoundingClientRect(), b = par.getBoundingClientRect();
        if (Math.abs(a.width - b.width) <= 2 && Math.abs(a.height - b.height) <= 2) return true;
      }
      return false;
    };
    out.c = [];
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        const A = boxes[i], B = boxes[j];
        if (A.el.contains(B.el) || B.el.contains(A.el)) continue;
        if (overlayLayer(A.el) || overlayLayer(B.el)) continue;
        const x = Math.min(A.r.right, B.r.right) - Math.max(A.r.left, B.r.left);
        const y = Math.min(A.r.bottom, B.r.bottom) - Math.max(A.r.top, B.r.top);
        if (x > 1 && y > 1) out.c.push({ a: cssSel(A.el), b: cssSel(B.el), px: Math.round(x * y) });
      }
    }

    // (d) clipped text (intentional truncation = `truncate` class or ellipsis with a title/aria-label)
    out.d = { offenders: [], intentional: 0 };
    // (e) font size of visible text, (i) contrast of visible text
    out.e = [];
    out.i = { offenders: [], checked: 0, unknown: 0 };
    const textEls = [...document.querySelectorAll("body *")].filter((el) =>
      [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim()) && visible(el) && !hiddenAncestor(el));
    for (const el of textEls) {
      const cs = getComputedStyle(el);
      if (cs.display === "inline") continue;
      const fs = parseFloat(cs.fontSize);
      if (fs < 12) out.e.push({ sel: cssSel(el), fs });
      const clipped = el.scrollWidth > el.clientWidth + 1 && (/hidden|clip/.test(cs.overflowX) || cs.textOverflow === "ellipsis");
      if (clipped) {
        if (el.classList.contains("truncate") && (el.title || el.getAttribute("aria-label"))) out.d.intentional++;
        else out.d.offenders.push({ sel: cssSel(el), sw: el.scrollWidth, cw: el.clientWidth });
      }
      // contrast
      if (el.closest(":disabled, [aria-disabled=true]") || cs.color === "transparent") continue;
      // decorative glyphs (emoji, icons as text) carry no words: not text for WCAG purposes
      if (!/[\p{L}\p{N}]/u.test(el.textContent)) continue;
      const chain = [];
      for (let e = el; e && e.nodeType === 1; e = e.parentElement) chain.push(e);
      let bg = base, unknown = false, opacity = 1;
      for (let k = chain.length - 1; k >= 0; k--) {
        const c2 = getComputedStyle(chain[k]);
        if (c2.backgroundImage && c2.backgroundImage !== "none") { unknown = true; break; }
        const bc = rgba(c2.backgroundColor);
        if (bc[3] > 0) bg = over([bc[0], bc[1], bc[2], bc[3]], bg);
      }
      for (const e of chain) opacity *= parseFloat(getComputedStyle(e).opacity);
      if (unknown) { out.i.unknown++; continue; }
      const f = rgba(cs.color);
      const fg = over([f[0], f[1], f[2], f[3] * opacity], bg);
      const cr = ratio(fg, bg);
      out.i.checked++;
      if (cr < 4.5) out.i.offenders.push({ sel: cssSel(el), ratio: Math.round(cr * 100) / 100, text: el.textContent.trim().slice(0, 30) });
    }

    // (f) bottom treat bar must not cover the last content at max scroll
    const bar = [...document.querySelectorAll("body *")].find((e) => {
      if (getComputedStyle(e).position !== "fixed") return false;
      const r = e.getBoundingClientRect();
      return r.height > 20 && r.width >= iw * 0.5 && Math.abs(r.bottom - ih) <= 2 && visible(e)
        && [...e.querySelectorAll("button")].some((b) => /treat/i.test(b.textContent));
    });
    out.f = { bar: !!bar, ok: true };
    if (bar) {
      const barRect = bar.getBoundingClientRect();
      const y0 = scrollY;
      window.scrollTo(0, 1e9);
      await new Promise((res) => requestAnimationFrame(() => requestAnimationFrame(res)));
      let maxBottom = -1, last = null;
      for (const e of document.querySelectorAll("body *")) {
        if (bar.contains(e) || e.closest("[data-adapt-skip]")) continue;
        let fixed = false;
        for (let x = e; x && x.nodeType === 1; x = x.parentElement) if (getComputedStyle(x).position === "fixed") { fixed = true; break; }
        if (fixed || !visible(e)) continue;
        if ([...e.children].some((c) => visible(c))) continue; // leaves only: wrappers stretch to the page bottom
        const r = e.getBoundingClientRect();
        if (r.bottom > maxBottom) { maxBottom = r.bottom; last = e; }
      }
      window.scrollTo(0, y0);
      out.f = { bar: true, ok: maxBottom <= barRect.top + 1, barTop: Math.round(barRect.top), barH: Math.round(barRect.height),
        maxBottom: Math.round(maxBottom), last: last ? cssSel(last) : null };
    }

    // (g) dialogs and sheets: fit the viewport or scroll internally, close control reachable
    out.g = { dialogs: 0, offenders: [] };
    const closeBtns = [...document.querySelectorAll("button, [role=button]")].filter((b) => {
      const n = (b.getAttribute("aria-label") || b.getAttribute("title") || b.textContent || "").trim();
      return visible(b) && /^(close|dismiss|×|✕|x|done|cancel)\b/i.test(n);
    });
    const roots = new Set();
    for (const d of document.querySelectorAll("[role=dialog], [aria-modal=true], dialog[open]")) if (visible(d)) roots.add(d);
    for (const b of closeBtns) {
      let root = null;
      for (let x = b; x && x.nodeType === 1; x = x.parentElement) {
        if (x.getAttribute("role") === "dialog" || x.getAttribute("aria-modal") === "true") { root = x; break; }
        if (getComputedStyle(x).position === "fixed" && x.getBoundingClientRect().height > ih * 0.3) root = x;
      }
      if (root) roots.add(root);
    }
    out.g.dialogs = roots.size;
    for (const root of roots) {
      const r = root.getBoundingClientRect();
      const fits = r.top >= -1 && r.left >= -1 && r.bottom <= ih + 1 && r.right <= iw + 1;
      const scroller = [root, ...root.querySelectorAll("*")].some((e) => {
        const cs = getComputedStyle(e);
        return /auto|scroll/.test(cs.overflowY) && e.scrollHeight > e.clientHeight + 1;
      });
      const close = closeBtns.find((b) => root.contains(b));
      const cr = close ? close.getBoundingClientRect() : null;
      const reachable = !!cr && cr.top >= -1 && cr.bottom <= ih + 1 && cr.left >= -1 && cr.right <= iw + 1;
      if (!close) out.g.offenders.push({ sel: cssSel(root), why: "no close control found" });
      else if (!reachable) out.g.offenders.push({ sel: cssSel(close), why: "close control outside the viewport" });
      if (!fits && !scroller) out.g.offenders.push({ sel: cssSel(root), why: "overflows the viewport with no internal scroll" });
    }

    // (h) video box aspect (phones: not taller than 70% of the viewport)
    const v = document.querySelector('section[aria-label="Live video of the feeding area"]');
    out.h = { present: false, ok: true };
    if (v && visible(v)) {
      const r = v.getBoundingClientRect();
      out.h = { present: true, w: Math.round(r.width), hgt: Math.round(r.height), ratio: Math.round((r.height / r.width) * 100) / 100,
        vhPct: Math.round((r.height / ih) * 100), ok: !phone || r.height <= 0.7 * ih };
    }

    // (j) reduced motion: running CSS animations (spinners, pulses) in the reduce cell
    out.j = [];
    if (opts.reduce) {
      for (const an of document.getAnimations()) {
        if (!(an instanceof CSSAnimation) || an.playState !== "running") continue;
        const t = an.effect.getComputedTiming();
        const target = an.effect.target;
        if (!target || !visible(target)) continue;
        if (t.iterations === Infinity || t.duration >= 200) out.j.push({ sel: cssSel(target), name: an.animationName });
      }
    }
    out.reduceCell = !!opts.reduce;
    return out;
  };

  return { INTERACTIVE, cssSel, interactive, markInteractive, markDialog, dialogOpen, focusProbe, audit, visible };
}

// Injected once per page: window.__adapt = ADAPT_LIB().
// ---------------------------------------------------------------------------------------------

const PRE_INIT = (theme) => {
  try { localStorage.setItem("theme", theme); } catch { /* storage blocked */ }
};

const shotName = (st, c) => `${st.id}-${c.w}x${c.h}@${c.dpr}-${c.theme}.jpg`;

async function status(base, cookie) {
  const headers = cookie ? { cookie } : {};
  const r = await fetch(`${base}/status`, { headers }).catch(() => null);
  return r && r.ok ? r.json() : null;
}

async function waitFor(fn, timeoutMs, stepMs = 500) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    const v = await fn().catch(() => null);
    if (v) return v;
    await new Promise((r) => setTimeout(r, stepMs));
  }
  return null;
}

function cookieHeader(cookies) {
  return cookies ? cookies.map((c) => `${c.name}=${c.value}`).join("; ") : "";
}

function startSim(name, port, extra = [], seconds = 3600) {
  fs.mkdirSync(SIM_DIR, { recursive: true });
  const log = fs.openSync(path.join(SIM_DIR, `${name}.log`), "w");
  const p = spawn(PY, [SIM, "--url", `ws://127.0.0.1:${port}/ingest-events`, "--seconds", String(seconds), "--period", "0.2", ...extra],
    { stdio: ["ignore", log, log] });
  p.on("exit", () => fs.closeSync(log));
  return p;
}

async function settlePage(page) {
  await page.waitForSelector("main, section, h1, [role=dialog], button", { timeout: 8000 }).catch(() => {});
  await page.waitForTimeout(900);
}

async function clickOpen(page, what) {
  const sels = {
    pairing: ['[title="Pair Phone Station"]'],
    "mode-sheet": ['[title^="Viewing mode"]', 'button:has-text("Controls")'],
    notifications: ['button[aria-label^="Notifications"]'],
    members: ['[aria-label="Household members"]'],
    settings: ['[aria-label="Edit pet profile"]'],
  }[what] ?? [];
  for (const s of sels) {
    const loc = page.locator(s).first();
    if (await loc.isVisible().catch(() => false)) {
      await loc.click({ timeout: 4000 }).catch(() => {});
      await page.waitForTimeout(700);
      return true;
    }
  }
  if (what === "mode-sheet") {
    const ctl = page.locator('button:has-text("Controls")').first();
    if (await ctl.isVisible().catch(() => false)) {
      await ctl.click().catch(() => {});
      await page.waitForTimeout(400);
      const m = page.locator('[title^="Viewing mode"]').first();
      if (await m.isVisible().catch(() => false)) { await m.click().catch(() => {}); await page.waitForTimeout(700); return true; }
    }
  }
  return false;
}

// Keyboard pass: tab through the page, check focus rings, check a dialog keeps focus and closes on Escape.
async function keyboardPass(page, cell) {
  const count = await page.evaluate(() => window.__adapt.markInteractive());
  const hasDialog = await page.evaluate(() => window.__adapt.markDialog());
  await page.evaluate(() => { document.activeElement?.blur?.(); window.scrollTo(0, 0); });
  const reached = new Set();
  const ringFails = [];
  let trapFails = 0;
  const steps = count + 6 + (hasDialog ? 20 : 0);
  for (let i = 0; i < steps; i++) {
    await page.keyboard.press("Tab");
    const p = await page.evaluate(() => window.__adapt.focusProbe());
    if (!p) continue;
    if (p.id) reached.add(p.id);
    if (!p.ring) ringFails.push({ sel: p.sel });
    if (hasDialog && p.inDialog === false) trapFails++;
  }
  // with a dialog open, Tab is trapped inside it: only the dialog's own controls must be reachable
  const unreached = await page.evaluate((ids) => {
    const dlg = document.querySelector("[data-adapt-dialog]");
    return window.__adapt.interactive()
      .filter((el) => (!dlg || dlg.contains(el)) && !ids.includes(el.getAttribute("data-adapt-id")))
      .map((el) => window.__adapt.cssSel(el)).slice(0, 25);
  }, [...reached]);
  const out = { count, reached: reached.size, ringFails: ringFails.slice(0, 40), unreached, dialog: hasDialog, trapFails, escape: null };
  if (hasDialog) {
    await page.keyboard.press("Escape");
    await page.waitForTimeout(400);
    out.escape = !(await page.evaluate(() => window.__adapt.dialogOpen()));
  }
  return out;
}

function verdicts(audit, kb, cell) {
  const v = {};
  v.a = audit.a.ok ? { s: "PASS" } : { s: "FAIL", off: [`scrollWidth ${audit.a.sw} > innerWidth ${audit.a.iw}`] };
  v.b = audit.b.length ? { s: "FAIL", off: audit.b.map((o) => `${o.sel} ${o.w}x${o.h} (min ${o.min})`) } : { s: "PASS" };
  v.c = audit.c.length ? { s: "FAIL", off: audit.c.map((o) => `${o.a} overlaps ${o.b} (${o.px}px2)`) } : { s: "PASS" };
  v.d = audit.d.offenders.length ? { s: "FAIL", off: audit.d.offenders.map((o) => `${o.sel} scrollWidth ${o.sw} > ${o.cw}`) } : { s: "PASS", note: audit.d.intentional ? `${audit.d.intentional} intentional truncate` : "" };
  v.e = audit.e.length ? { s: "FAIL", off: audit.e.map((o) => `${o.sel} ${o.fs}px`) } : { s: "PASS" };
  v.f = audit.f.bar ? (audit.f.ok ? { s: "PASS" } : { s: "FAIL", off: [`last content ${audit.f.last} bottom ${audit.f.maxBottom} > bar top ${audit.f.barTop} (bar ${audit.f.barH}px)`] }) : { s: "SKIP", note: "no fixed bottom bar" };
  v.g = audit.g.offenders.length ? { s: "FAIL", off: audit.g.offenders.map((o) => `${o.sel}: ${o.why}`) } : { s: audit.g.dialogs ? "PASS" : "SKIP", note: audit.g.dialogs ? "" : "no dialog open" };
  v.h = !audit.h.present ? { s: "SKIP", note: "no video box" } : audit.h.ok ? { s: "PASS", note: `${audit.h.w}x${audit.h.hgt} (${audit.h.vhPct}% vh)` } : { s: "FAIL", off: [`video ${audit.h.w}x${audit.h.hgt} is ${audit.h.vhPct}% of the viewport height`] };
  v.i = audit.i.offenders.length ? { s: "FAIL", off: audit.i.offenders.map((o) => `${o.sel} ratio ${o.ratio}: "${o.text}"`) } : { s: "PASS", note: `${audit.i.checked} checked, ${audit.i.unknown} gradient-skipped` };
  if (audit.reduceCell) v.j = audit.j.length ? { s: "FAIL", off: audit.j.map((o) => `${o.sel} animation ${o.name}`) } : { s: "PASS" };
  else v.j = { s: "N/A" };
  if (kb) {
    const off = [...kb.ringFails.map((o) => `no focus indicator: ${o.sel}`), ...kb.unreached.map((s) => `not reached by Tab: ${s}`)];
    if (kb.trapFails) off.push(`${kb.trapFails} Tab steps left the open dialog`);
    if (kb.dialog && kb.escape === false) off.push("dialog not closed by Escape");
    v.k = off.length ? { s: "FAIL", off } : { s: "PASS", note: `${kb.reached}/${kb.count} tab stops` };
  } else v.k = { s: "N/A" };
  v.l = audit.l.length ? { s: "FAIL", off: audit.l.map((o) => o.sel) } : { s: "PASS" };
  return v;
}

async function main() {
  if (RENDER) {
    const data = JSON.parse(fs.readFileSync(RENDER, "utf8"));
    const before = COMPARE && fs.existsSync(COMPARE) ? JSON.parse(fs.readFileSync(COMPARE, "utf8")) : null;
    fs.writeFileSync(REPORT, renderReport(data, before));
    console.log(JSON.stringify({ rendered: REPORT, from: RENDER }));
    return;
  }
  const states = STATES.filter((s) => !ONLY_STATES || ONLY_STATES.includes(s.id));
  fs.mkdirSync(OUT_DIR, { recursive: true });
  // clear only this run's screenshots, so a pin-only run and a households-only run can share the folder
  for (const f of fs.readdirSync(OUT_DIR)) if (f.endsWith(".jpg") && states.some((st) => f.startsWith(`${st.id}-`))) fs.unlinkSync(path.join(OUT_DIR, f));
  const browser = await chromium.launch({ executablePath: CHROME, args: ["--no-sandbox"] });
  const results = [];
  const sims = new Map();
  const ctxState = { cookies: null };
  let shotBytes = 0;
  const savedPass = new Set();

  const pinStatus = () => status(PIN);
  try {
    for (const st of states) {
      console.error(`== state ${st.id}`);
      await st.setup({ browser, sims, ctxState, startSim, waitFor, status, cookieHeader, PIN, HH });
      if (st.skipAll) { results.push({ state: st.id, skipped: st.skipAll }); continue; }
      for (const c of cellsFor(st)) {
        const ctx = await browser.newContext({
          viewport: { width: c.w, height: c.h }, deviceScaleFactor: c.dpr,
          colorScheme: c.theme, reducedMotion: c.reduce ? "reduce" : "no-preference",
        });
        if (st.base === "hh" && ctxState.cookies && !st.noCookie) await ctx.addCookies(ctxState.cookies);
        await ctx.addInitScript(PRE_INIT, c.theme);
        const page = await ctx.newPage();
        const pageErrors = [];
        page.on("pageerror", (e) => pageErrors.push(String(e).slice(0, 200)));
        await page.goto(`${st.base === "hh" ? HH : PIN}/`, { waitUntil: "load", timeout: 30000 }).catch((e) => pageErrors.push(String(e).slice(0, 200)));
        await settlePage(page);
        await page.addScriptTag({ content: `window.__adapt = (${ADAPT_LIB.toString()})();` });
        let opened = true;
        if (st.open) opened = await clickOpen(page, st.open);
        const htmlDark = await page.evaluate(() => document.documentElement.classList.contains("dark"));
        const audit = opened ? await page.evaluate((opts) => window.__adapt.audit(opts), { reduce: !!c.reduce }) : null;
        let kb = null;
        if (opened && c.keyboard) kb = await keyboardPass(page, c);
        const name = shotName(st, c);
        const cell = { state: st.id, w: c.w, h: c.h, dpr: c.dpr, theme: c.theme, reduce: !!c.reduce, keyboard: !!c.keyboard, opened, htmlDark, pageErrors };
        if (!opened) {
          cell.skip = `could not open ${st.open}`;
          results.push(cell);
          await ctx.close();
          continue;
        }
        cell.v = verdicts(audit, kb, c);
        cell.interactive = audit.interactive;
        cell.failed = ASSERTS.filter((k) => cell.v[k]?.s === "FAIL");
        cell.shot = `docs/screenshots/adaptive-dashboard/${name}`;
        const key = `${st.id}|${c.w}x${c.h}`;
        // failures always keep their screenshot; a pass keeps one per viewport x state (light, not reduce)
        const passShot = !cell.failed.length && !savedPass.has(key) && c.theme === "light" && !c.reduce && shotBytes < SHOT_BUDGET;
        if (cell.failed.length || passShot) {
          const file = path.join(OUT_DIR, name);
          await page.screenshot({ path: file, type: "jpeg", quality: 70 });
          shotBytes += fs.statSync(file).size;
          if (!cell.failed.length) savedPass.add(key);
        } else cell.shot = null;
        if (kb) cell.kb = kb;
        results.push(cell);
        console.error(`   ${name}: ${cell.failed.length ? "FAIL " + cell.failed.join(",") : "PASS"}`);
        await ctx.close();
      }
      if (st.teardown) await st.teardown({ sims, ctxState });
    }
  } finally {
    for (const [, p] of sims) { try { p.kill("SIGKILL"); } catch { /* gone */ } }
    await browser.close();
  }
  const data = { ranAt: new Date().toISOString(), shotBytes, cells: results, backends: { PIN, HH } };
  fs.mkdirSync(path.dirname(JSON_OUT), { recursive: true });
  fs.writeFileSync(JSON_OUT, JSON.stringify(data, null, 1));
  const before = COMPARE && fs.existsSync(COMPARE) ? JSON.parse(fs.readFileSync(COMPARE, "utf8")) : null;
  fs.writeFileSync(REPORT, renderReport(data, before));
  const failCells = results.filter((r) => r.failed?.length).length;
  const failTotal = results.reduce((n, r) => n + (r.failed?.length ?? 0), 0);
  console.log(JSON.stringify({ ok: failTotal === 0, cells: results.length, cellsWithFailures: failCells, failedAssertions: failTotal, report: REPORT, json: JSON_OUT }));
  process.exit(failTotal === 0 ? 0 : 1);
}

function renderReport(data, before) {
  const L = [];
  const cells = data.cells.filter((c) => c.v);
  const counts = Object.fromEntries(ASSERTS.map((k) => [k, { fail: 0, pass: 0 }]));
  for (const c of cells) for (const k of ASSERTS) { const s = c.v[k]?.s; if (s === "FAIL") counts[k].fail++; else if (s === "PASS") counts[k].pass++; }
  const bcounts = before ? Object.fromEntries(ASSERTS.map((k) => [k, 0])) : null;
  if (before) for (const c of before.cells.filter((x) => x.v)) for (const k of ASSERTS) if (c.v[k]?.s === "FAIL") bcounts[k]++;
  L.push("# Adaptive UI matrix: WagWatch dashboard (2026-10-11)", "");
  L.push("Generated by `scripts/adaptive_ui_dashboard.mjs` (playwright-core on the system Chrome). Run: " +
    `${data.ranAt}. Backends: pin ${data.backends.PIN} (remote pipeline), households ${data.backends.HH}.`, "");
  L.push("Cells are (state, viewport, device scale, theme). Assertions: a no horizontal scroll; b touch targets (44px below 768 CSS px, 32px at and above); " +
    "c no overlapping interactive boxes; d no clipped text (intentional `truncate` with title/aria-label is counted, not failed); e visible text >= 12px; " +
    "f fixed bottom bar does not cover the last content at max scroll; g dialogs fit or scroll and their close control is reachable; h video box not taller than 70% of a phone viewport; " +
    "i text contrast >= 4.5:1 (WCAG AA, approximated by compositing ancestor backgrounds; gradient backgrounds are skipped); j running CSS animations under prefers-reduced-motion; " +
    "k focus ring differs on focus for every Tab stop, Tab keeps focus inside open dialogs, Escape closes them (run on two cells per state); l every control has an accessible name.", "");
  L.push("## Method and limits", "");
  for (const line of [
    "Contrast (i) composites each text element over its opaque ancestor backgrounds (white or dark base) and skips elements on gradient backgrounds, disabled controls and glyphs without letters or digits (emoji). It is an approximation of WCAG, not a browser-accessibility audit.",
    "Overlap (c) ignores fixed layers (the Drop Treat bar, toasts, sheets) and absolute inset-0 backdrops, which overlay scrolling content by design. Dialog states also count only the dialog's own controls.",
    "Bottom bar (f) runs only where the fixed Drop Treat bar is shown (phone and tablet widths); desktop cells are skipped on purpose.",
    "Touch size (b) measures a label-wrapped checkbox by its label row. Desktop cells require 32 px, phone and tablet cells 44 px.",
    "Keyboard (k) tabs from the top of the page on two cells per state (390x844 at 2x with reduced motion, 1280x800 at 1x) and checks the focus indicator with the same computed-style comparison (outline or box-shadow differs on focus).",
    "Paused is a real Blackout (POST /mode) with the simulated phone still connected; dropped is a SIGKILL of that phone. Pairing is a households-mode state (the pin backend answers /households/me/pairing with 404).",
    "Reduced motion (j) only sees animations that are on screen in a cell. The matrix states show none of the spinners and pulses (mode-change pending, reconnecting spinner, battery stale pulse, auth loading), so j passing is not evidence for them; the global prefers-reduced-motion rule in globals.css covers them by construction.",
    "Toasts were not covered: the simulated phone produces no toast on demand. Notifications (bell panel) are covered in the live state.",
    "In households mode the header toolbar scrolls horizontally inside its own box on the narrowest phone (360 px), so the page itself does not overflow; the controls there are reached by swiping, not all visible at once.",
  ]) L.push(`- ${line}`);
  L.push("");
  L.push("## Summary", "");
  L.push(`Cells run: ${cells.length} (${data.cells.filter((c) => c.skip).length} skipped with a reason). Screenshots kept: ${(data.shotBytes / 1024).toFixed(0)} KB.`, "");
  L.push("| assertion | description | cells FAIL" + (before ? " before" : "") + " | cells FAIL after | cells PASS |");
  L.push("|---|---|---|---|---|");
  for (const k of ASSERTS) L.push(`| ${k} | ${ASSERT_NAMES[k]} | ${before ? bcounts[k] + " | " : ""}${counts[k].fail} | ${counts[k].pass} |`);
  L.push("");
  L.push("## Matrix", "");
  L.push("| state | viewport | dpr | theme | " + ASSERTS.join(" | ") + " |");
  L.push("|---|---|---|---|" + ASSERTS.map(() => ":-:").join("|") + "|");
  const mark = (v) => (!v ? "-" : v.s === "PASS" ? "PASS" : v.s === "FAIL" ? "**FAIL**" : v.s === "SKIP" ? "skip" : "n/a");
  for (const r of data.cells) {
    if (r.skip) { L.push(`| ${r.state} | ${r.w}x${r.h} | ${r.dpr} | ${r.theme} | ${r.skip} |`); continue; }
    L.push(`| ${r.state} | ${r.w}x${r.h}${r.reduce ? " reduce" : ""}${r.keyboard ? " kb" : ""} | ${r.dpr} | ${r.theme}${r.htmlDark === (r.theme === "dark") ? "" : " (html class mismatch)"} | ` +
      ASSERTS.map((k) => mark(r.v[k])).join(" | ") + " |");
  }
  L.push("");
  L.push("## Offenders (cells with a FAIL)", "");
  for (const r of data.cells.filter((c) => c.failed?.length)) {
    L.push(`### ${r.state} ${r.w}x${r.h} @${r.dpr} ${r.theme}${r.reduce ? " reduce" : ""}${r.keyboard ? " kb" : ""}`, "");
    if (r.shot) L.push(`![${r.state} ${r.w}x${r.h}](${r.shot.replace(/^docs\//, "")})`, "");
    for (const k of r.failed) {
      L.push(`- **${k}** ${ASSERT_NAMES[k]}:`);
      for (const o of (r.v[k].off || []).slice(0, 12)) L.push(`  - \`${o.replace(/`/g, "'")}\``);
      if ((r.v[k].off || []).length > 12) L.push(`  - ... ${r.v[k].off.length - 12} more`);
    }
    L.push("");
  }
  L.push("## Screenshots", "");
  L.push("One JPEG per viewport and state (light theme, first cell kept). Paths are relative to this file.", "");
  const byState = new Map();
  for (const r of data.cells) if (r.shot) byState.set(r.state, [...(byState.get(r.state) ?? []), r]);
  for (const [state, list] of byState) {
    L.push(`- **${state}**: ` + list.map((r) => `[${r.w}x${r.h}](${r.shot.replace(/^docs\//, "")})`).join(", "));
  }
  L.push("");
  const errs = data.cells.filter((c) => c.pageErrors?.length);
  if (errs.length) {
    L.push("## Page errors", "");
    for (const r of errs) L.push(`- ${r.state} ${r.w}x${r.h}: ${r.pageErrors[0]}`);
    L.push("");
  }
  return L.join("\n") + "\n";
}

// ---------------------------------------------------------------------------------------------
// Cells and states
// ---------------------------------------------------------------------------------------------
function cellsFor(st) {
  const out = [];
  const vps = VIEWPORTS.filter((v) => !ONLY_VP || ONLY_VP.includes(`${v.w}x${v.h}`));
  for (const v of vps) {
    const c = { ...v, theme: "light" };
    if (v.w === 390 && v.h === 844 && v.dpr === 2) { c.keyboard = true; c.reduce = true; }
    if (v.w === 1280 && v.h === 800 && v.dpr === 1) c.keyboard = true;
    out.push(c);
  }
  for (const v of vps.filter((x) => (x.w === 390 && x.dpr === 2) || (x.w === 1280 && x.dpr === 1))) out.push({ ...v, theme: "dark" });
  return out;
}

const STATES = [
  {
    id: "idle", base: "pin",
    setup: async ({ sims, waitFor, status, PIN }) => {
      for (const [, p] of sims) { p.kill("SIGKILL"); }
      sims.clear();
      const ok = await waitFor(async () => { const s = await status(PIN); return s?.pipeline_status?.state === "idle" && !s?.phone?.connected; }, 15000);
      if (!ok) throw new Error("pin backend is not idle: restart it (no sim phone may have connected)");
    },
  },
  {
    id: "live", base: "pin",
    setup: async ({ sims, startSim, waitFor, status, PIN }) => {
      sims.set("pin-live", startSim("pin-live", 8010, [], 3600));
      const ok = await waitFor(async () => { const s = await status(PIN); return s?.phone?.connected && s?.pipeline_status?.state === "running" && s.pipeline_status.events > 0 ? s : null; }, 25000);
      if (!ok) throw new Error("sim phone did not reach the running state on the pin backend");
      await new Promise((r) => setTimeout(r, 3000));
    },
  },
  { id: "mode-sheet", base: "pin", open: "mode-sheet", setup: async () => {} },
  { id: "notifications", base: "pin", open: "notifications", setup: async () => {} },
  {
    id: "paused", base: "pin",
    setup: async ({ sims, waitFor, status, PIN }) => {
      const r = await fetch(`${PIN}/mode`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ mode: "blackout" }) });
      if (!r.ok) throw new Error(`POST /mode blackout -> ${r.status}`);
      const ok = await waitFor(async () => (await status(PIN))?.pipeline_status?.state === "paused", 10000);
      if (!ok) throw new Error("blackout did not reach the paused state");
      if (!sims.has("pin-live")) return;
    },
    teardown: async () => {},
  },
  {
    id: "dropped", base: "pin",
    setup: async ({ sims, waitFor, status, PIN }) => {
      const p = sims.get("pin-live");
      if (p) { p.kill("SIGKILL"); sims.delete("pin-live"); }
      const ok = await waitFor(async () => (await status(PIN))?.phone?.connected === false, 15000);
      if (!ok) throw new Error("phone did not drop on the pin backend");
      await new Promise((r) => setTimeout(r, 1500));
    },
  },
  {
    id: "hh-welcome", base: "hh", noCookie: true,
    setup: async () => {},
  },
  {
    id: "hh-empty", base: "hh",
    setup: async ({ browser, ctxState, HH }) => {
      const ctx = await browser.newContext({});
      const r = await ctx.request.post(`${HH}/households`, { data: { dog_name: "Rex", location: "Kitchen", name: "Adaptive test home" } });
      if (!r.ok()) throw new Error(`POST /households -> ${r.status()}`);
      ctxState.cookies = await ctx.cookies(HH);
      ctxState.ctx = ctx;
      await ctx.close().catch(() => {});
      ctxState.cookies = ctxState.cookies.map((c) => ({ ...c }));
    },
  },
  { id: "hh-pairing", base: "hh", open: "pairing", setup: async () => {} },
  {
    id: "hh-1", base: "hh",
    setup: async ({ sims, ctxState, startSim, waitFor, status, cookieHeader, HH, browser }) => {
      const code = await mintCode(browser, ctxState, HH);
      fs.rmSync(path.join(SIM_DIR, "hh-1.json"), { force: true }); // fresh station key for this household
      sims.set("hh-1", startSim("hh-1", 8011, [`--pair-code=${code}`, "--pair-url", `${HH}/pair`, "--keyfile", path.join(SIM_DIR, "hh-1.json")], 3600));
      const ok = await waitFor(async () => (await status(HH, cookieHeader(ctxState.cookies)))?.stations?.length >= 1, 30000);
      if (!ok) throw new Error("station 1 did not pair");
      await new Promise((r) => setTimeout(r, 3000));
    },
  },
  {
    id: "hh-2", base: "hh",
    setup: async ({ sims, ctxState, startSim, waitFor, status, cookieHeader, HH, browser }) => {
      const code = await mintCode(browser, ctxState, HH);
      fs.rmSync(path.join(SIM_DIR, "hh-2.json"), { force: true }); // fresh station key for this household
      sims.set("hh-2", startSim("hh-2", 8011, [`--pair-code=${code}`, "--pair-url", `${HH}/pair`, "--keyfile", path.join(SIM_DIR, "hh-2.json")], 3600));
      const ok = await waitFor(async () => (await status(HH, cookieHeader(ctxState.cookies)))?.stations?.length >= 2, 30000);
      if (!ok) throw new Error("station 2 did not pair");
      await new Promise((r) => setTimeout(r, 3000));
    },
  },
  { id: "hh-members", base: "hh", open: "members", setup: async () => {} },
  { id: "hh-settings", base: "hh", open: "settings", setup: async () => {} },
];

async function mintCode(browser, ctxState, HH) {
  const ctx = await browser.newContext({});
  await ctx.addCookies(ctxState.cookies);
  const r = await ctx.request.post(`${HH}/households/me/pairing`, { data: { kind: "station" } });
  const j = await r.json();
  await ctx.close();
  if (!r.ok() || !j.code) throw new Error(`pairing code failed: ${r.status()}`);
  return j.code;
}

main().catch((err) => {
  console.error(String(err?.stack || err));
  process.exit(2);
});
