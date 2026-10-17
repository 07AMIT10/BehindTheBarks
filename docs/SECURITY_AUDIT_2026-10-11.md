# Dependency audit, 2026-10-11

Run on branch `feat/live-audio-and-hardening` (H3d). Commands and real output summaries:

## npm (frontend, production dependencies)

`npm --prefix frontend audit --omit=dev`

Before the fix: exit 1, **3 high** severity findings.

| Package | Installed | Advisory | Fix | Status |
|---|---|---|---|---|
| `next` (pinned `16.3.6`) | 16.3.6 | Six advisories for `16.0.0 - 16.3.7`: GHSA-3w37-wq28-93x7 (Draft Mode content leak through pending `use cache`), GHSA-4jqv-mc3x-m676 (SSG/ISR cache poisoning, self-hosted), GHSA-39w2-rjm5-chcv (dev-server MCP endpoint info disclosure, dev only), GHSA-f87g-xv8r-7p7x (metadata image route `dynamicParams` bypass), GHSA-mcj8-r9mp-w47p (SSG/ISR cross-user substitution and persistent DoS), GHSA-cjq9-62q9-8jv4 (SSRF in Image Optimization) | `next@16.4.0` (npm: "outside the stated dependency range") | **Not applied.** A minor bump, and the exact pin is the owner's decision. Recommended: upgrade `next` and `eslint-config-next` to 16.4.x in a separate commit, then rerun the frontend gate and `scripts/csp_check.mjs`. |
| `sharp` | 0.35.4 | GHSA-wq5f-xc86-pv6w (CVE-2026-96889, librsvg dependency) | 0.35.5 | **Applied** (`npm audit fix`, semver-compatible). Also bumps the `@img/sharp-*` platform binaries 1.3.3 to 1.3.4. |
| `source-map-js` | 1.2.1 | GHSA-68fv-2mgg-jv7q (event-loop DoS via source-map section offsets) | 1.2.2 | **Applied** (`npm audit fix`). |

After the fix: `npm --prefix frontend audit --omit=dev` still exits 1 with **1 high** (`next`, the row above). `npm audit fix --force` was not run. The only lockfile change is `frontend/package-lock.json` (sharp, its platform binaries, source-map-js).

Gate after the fix: `npm --prefix frontend test -- --run` 390 passed; `npm --prefix frontend run build` succeeded.

## Python (`requirements-web.txt`)

`uvx pip-audit -r requirements-web.txt` (network available): exit 0, "No known vulnerabilities found" (28 packages resolved). Nothing to fix.

## Not covered

- Only production npm dependencies were audited (`--omit=dev`), as the task asked. Dev-only findings are not listed here.
- The advisory database is the one pip-audit and npm query at the time of the run; re-run before a release.
