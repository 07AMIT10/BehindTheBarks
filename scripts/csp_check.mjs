// Content-Security-Policy regression check for the dashboard and the /camera page (H3a).
//
//   node scripts/csp_check.mjs [--pin http://127.0.0.1:8020] [--hh http://127.0.0.1:8021]
//
// Drives the system Chrome with playwright-core (same setup as scripts/dom.mjs). For each backend given:
//   pin:        GET /        -> the dashboard renders its "WagWatch" text
//   households: POST /households (a fresh owner session), then GET / -> the same
// and GET /camera.html for both. /camera itself answers 404 from the static mount (pre-existing;
// see docs/SECURITY_HARDENING.md). It records every console message and page error, counts the ones that
// mention "Content Security Policy" (a CSP violation), and checks that the document response carries the
// Content-Security-Policy header (so the policy under test is the one that was served).
//
// Exit code 0 only when every backend has zero CSP console messages, zero CSP page errors, the policy
// header present, and the main text rendered. Exit 1 otherwise; 2 on bad usage; 124 on the hard timeout.
//
// Start the backends yourself (see docs/SECURITY_HARDENING.md) and stop them afterwards.

import { spawnSync } from "node:child_process";
import { pathToFileURL } from "node:url";

const PW = process.env.BTB_PLAYWRIGHT || "/tmp/pptr/node_modules/playwright-core/index.mjs";
const { chromium } = await import(pathToFileURL(PW).href);
const CHROME = process.env.BTB_CHROME
  || (spawnSync("google-chrome", ["--version"], { stdio: "ignore" }).status === 0 ? "google-chrome" : null);
if (!CHROME) {
  console.error("csp_check.mjs: BTB_CHROME is not set and no google-chrome on PATH — set BTB_CHROME=/path/to/chrome");
  process.exit(2);
}
const HARD_LIMIT_MS = 120000;
const RENDER_TIMEOUT_MS = 15000;
const CSP_WORDS = /content[- ]security[- ]policy/i;

const arg = (name) => {
  const i = process.argv.indexOf(`--${name}`);
  return i === -1 ? null : process.argv[i + 1];
};
const targets = [];
if (arg("pin")) targets.push({ mode: "pin", base: arg("pin") });
if (arg("hh")) targets.push({ mode: "households", base: arg("hh") });
if (targets.length === 0) {
  console.error("usage: node scripts/csp_check.mjs [--pin <url>] [--hh <url>]");
  process.exit(2);
}

let browser = null;
const hardStop = setTimeout(() => {
  console.error(`csp_check.mjs: no result within ${HARD_LIMIT_MS / 1000} s`);
  const kill = browser ? browser.close().catch(() => {}) : Promise.resolve();
  kill.finally(() => process.exit(124));
}, HARD_LIMIT_MS);

async function checkPage(context, base, route, { wantText }) {
  const page = await context.newPage();
  const messages = [];
  const pageErrors = [];
  page.on("console", (m) => {
    if (m.type() === "error" || m.type() === "warning") messages.push(`${m.type()}: ${m.text()}`);
  });
  page.on("pageerror", (e) => pageErrors.push(String(e.message || e)));
  const response = await page.goto(base + route, { waitUntil: "domcontentloaded", timeout: RENDER_TIMEOUT_MS });
  const cspHeader = response ? (await response.allHeaders())["content-security-policy"] || "" : "";
  let text = "";
  if (wantText) {
    try {
      await page.waitForFunction(
        (word) => document.body.innerText.replace(/Loading WagWatch…/g, "").includes(word),
        wantText,
        { timeout: RENDER_TIMEOUT_MS },
      );
    } catch {
      // reported below through main_text=false
    }
  } else {
    await page.waitForTimeout(1500);
  }
  text = await page.evaluate(() => document.body.innerText);
  await page.waitForTimeout(500); // let late hydration errors arrive
  await page.close();
  const cspMessages = [...messages, ...pageErrors].filter((m) => CSP_WORDS.test(m));
  return {
    route,
    csp_header: Boolean(cspHeader),
    csp_messages: cspMessages,
    console_messages: messages.length,
    page_errors: pageErrors.length,
    main_text: wantText ? text.replace(/Loading WagWatch…/g, "").includes(wantText) : text.trim().length > 0,
  };
}

let failures = 0;
try {
  browser = await chromium.launch({ executablePath: CHROME, args: ["--no-sandbox"] });
  for (const { mode, base } of targets) {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
    if (mode === "households") {
      const r = await context.request.post(`${base}/households`, {
        data: { dog_name: "Rex", location: "Kitchen", name: "CSP check" },
      });
      if (!r.ok()) throw new Error(`POST ${base}/households -> ${r.status()}`);
    }
    const results = [];
    results.push(await checkPage(context, base, "/", { wantText: "WagWatch" }));
    results.push(await checkPage(context, base, "/camera.html", { wantText: null }));
    await context.close();
    for (const r of results) {
      const ok = r.csp_messages.length === 0 && r.csp_header && r.main_text;
      if (!ok) failures += 1;
      console.log(
        `${mode} ${r.route}: csp_violations=${r.csp_messages.length} console_messages=${r.console_messages} ` +
          `page_errors=${r.page_errors} csp_header=${r.csp_header} main_text=${r.main_text} ${ok ? "PASS" : "FAIL"}`,
      );
      for (const m of r.csp_messages) console.log(`  ${m.slice(0, 300)}`);
    }
  }
} catch (err) {
  console.error(`csp_check.mjs: ${err.message}`);
  failures += 1;
} finally {
  clearTimeout(hardStop);
  if (browser) await browser.close().catch(() => {});
}
process.exit(failures === 0 ? 0 : 1);
