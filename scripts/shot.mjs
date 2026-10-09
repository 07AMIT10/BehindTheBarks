// Screenshot the dashboard at an exact viewport with the system Chrome (no repo dependency:
// playwright-core ships no browsers, so /home/amit/.local/bin/google-chrome is used headless).
//
//   node scripts/shot.mjs <url> <width> <height> <out.png> [--settle-ms 3000] [--wait-for "text"]
//
// Prints one JSON line: {ok, width, height, scrollWidth, overflowX, waitFor}.
// Unlike `chrome --headless --virtual-time-budget`, an open WebSocket (the live dashboard) does not
// hang this: the page settles for --settle-ms or until --wait-for text appears.

import { chromium } from "/tmp/pptr/node_modules/playwright-core/index.mjs";

const CHROME = process.env.BTB_CHROME || "/home/amit/.local/bin/google-chrome";

async function main() {
  const [url, w, h, out] = process.argv.slice(2).filter((a) => !a.startsWith("--"));
  const arg = (name, dflt) => {
    const i = process.argv.indexOf(`--${name}`);
    return i === -1 ? dflt : process.argv[i + 1];
  };
  const width = Number(arg("width", w ?? 390));
  const height = Number(arg("height", h ?? 844));
  const settleMs = Number(arg("settle-ms", 3000));
  const waitFor = arg("wait-for", null);
  if (!url || !out) {
    console.error("usage: node scripts/shot.mjs <url> <width> <height> <out.png> [--settle-ms N] [--wait-for text]");
    process.exit(2);
  }
  const browser = await chromium.launch({ executablePath: CHROME, args: ["--no-sandbox"] });
  const page = await browser.newPage({ viewport: { width, height } });
  await page.goto(url, { waitUntil: "load", timeout: 20000 });
  if (waitFor) {
    await page.getByText(waitFor, { exact: false }).first().waitFor({ timeout: settleMs + 10000 }).catch(() => {});
  }
  await page.waitForTimeout(settleMs);
  await page.screenshot({ path: out });
  const m = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: window.innerWidth }));
  await browser.close();
  console.log(JSON.stringify({ ok: true, width, height, scrollWidth: m.sw, overflowX: m.sw > m.iw, waitFor, out }));
}

main().catch((err) => {
  console.error(JSON.stringify({ ok: false, error: String(err) }));
  process.exit(1);
});
