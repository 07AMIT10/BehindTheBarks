// Print the rendered text of a dashboard page: a replacement for `chrome --dump-dom`, which hangs while
// Print the rendered text of a dashboard page: a replacement for `chrome --dump-dom`, which hangs while
// the dashboard keeps an MJPEG <img> (/video) open for a dropped phone.
//
//   node scripts/dom.mjs <url> [--wait-for "text"] [--cookie "name=value"]
//
// --cookie sets one cookie for the page's origin (households mode: the owner's btb_session).
//
// Navigates with the system Chrome (playwright-core ships no browser; BTB_CHROME, or plain
// google-chrome found on PATH), waits for the `load` event
// (never for network idle, which an MJPEG stream prevents), waits up to 6 s for the dashboard to render
// (past its "Loading WagWatch…" placeholder), then waits up to 6 s for --wait-for text if given, and prints document.body.innerText to stdout. The whole run is capped at 12 s: on timeout
// it exits 124 with a message on stderr and whatever Chrome it started is closed.

import { spawnSync } from "node:child_process";
import { chromium } from "/tmp/pptr/node_modules/playwright-core/index.mjs";

const CHROME = process.env.BTB_CHROME
  || (spawnSync("google-chrome", ["--version"], { stdio: "ignore" }).status === 0 ? "google-chrome" : null);
if (!CHROME) {
  console.error("dom.mjs: BTB_CHROME is not set and no google-chrome on PATH — set BTB_CHROME=/path/to/chrome");
  process.exit(2);
}
const HARD_LIMIT_MS = 12000;
const WAIT_FOR_MS = 6000;

const url = process.argv[2];
const waitIdx = process.argv.indexOf("--wait-for");
const waitFor = waitIdx === -1 ? null : process.argv[waitIdx + 1];
const cookieIdx = process.argv.indexOf("--cookie");
const cookieArg = cookieIdx === -1 ? null : process.argv[cookieIdx + 1];

if (!url) {
  console.error('usage: node scripts/dom.mjs <url> [--wait-for "text"]');
  process.exit(2);
}

let browser = null;
const hardStop = setTimeout(() => {
  console.error(`dom.mjs: no result within ${HARD_LIMIT_MS / 1000} s`);
  const kill = browser ? browser.close().catch(() => {}) : Promise.resolve();
  kill.finally(() => process.exit(124));
}, HARD_LIMIT_MS);

try {
  browser = await chromium.launch({ executablePath: CHROME, args: ["--no-sandbox"] });
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  if (cookieArg) {
    const eq = cookieArg.indexOf("=");
    await context.addCookies([{ name: cookieArg.slice(0, eq), value: cookieArg.slice(eq + 1), url }]);
  }
  const page = await context.newPage();
  await page.goto(url, { waitUntil: "load", timeout: HARD_LIMIT_MS - 2000 });
  // The dashboard renders after `load` (the page shows "Loading WagWatch…" until hydration).
  await page
    .waitForFunction(() => !/^Loading WagWatch/.test(document.body.innerText.trim()), null, { timeout: WAIT_FOR_MS })
    .catch(() => {});
  if (waitFor) {
    await page
      .getByText(waitFor, { exact: false })
      .first()
      .waitFor({ state: "attached", timeout: WAIT_FOR_MS })
      .catch(() => {});
  }
  const text = await page.evaluate(() => document.body.innerText);
  process.stdout.write(text + "\n");
  await browser.close();
  clearTimeout(hardStop);
  process.exit(0);
} catch (err) {
  console.error(`dom.mjs: ${err.message.split("\n")[0]}`);
  if (browser) await browser.close().catch(() => {});
  clearTimeout(hardStop);
  process.exit(1);
}
