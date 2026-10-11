# Hosting: where it runs, and what you can change without redeploying

The app has no host-specific code. Moving between Render, a VM you run yourself, or a free tier
such as Oracle Always Free only changes environment variables and the deployment files under
`deploy/` and `render.yaml`. The Dockerfile is the same everywhere.

## Three layers of configuration

| Layer | Where | Changed by | Needs a redeploy? |
|---|---|---|---|
| Secrets and location | Env vars (`.env`, host dashboard): `BTB_API_TOKEN`, `BTB_DATABASE_URL`, `BTB_DOMAIN`, LLM keys | Operator | Restart only |
| Defaults | `config.yaml` (`web:` section) | Developer | Yes (ships with the image) |
| Live knobs | `PATCH /admin/tuning` (below), stored in the database (`app_settings`) | Operator, at runtime | **No** |

The live knobs override `config.yaml` and survive restarts (as long as the disk does; see below).

## Live bandwidth knobs (admin)

Calls need the operator token, `BTB_API_TOKEN`, as `Authorization: Bearer <token>`. The admin API
returns 403 when no token is set, so it is off by default.

| Key | Default | What it controls | Bandwidth effect |
|---|---|---|---|
| `video_fps` | 4 | Send-rate ceiling for MJPEG `/video`. Only frames that changed are sent. | Main video cost. Lower = less. |
| `ws_frame_fps` | 4 | Max frame messages per second per dashboard WebSocket. | Halves preview traffic at 4 vs 8. |
| `gzip_enabled` | true | gzip for JSON, JS, CSS, HTML. Streams and media are never compressed. | Large saving on JSON and assets. |
| `gzip_min_size` | 1000 | Responses smaller than this (bytes) are not compressed. | Tuning only. |

```bash
# read the current values
curl -H "Authorization: Bearer $BTB_API_TOKEN" https://btb.example.com/admin/tuning

# lower the video rate and turn compression off, no restart
curl -X PATCH -H "Authorization: Bearer $BTB_API_TOKEN" -H "Content-Type: application/json" \
     -d '{"video_fps": 2, "gzip_enabled": false}' https://btb.example.com/admin/tuning
```

Changes are validated as a whole. An unknown key or out-of-range value returns 400 and nothing
changes. `video_fps` and `ws_frame_fps` must be between 0.5 and 30. Open video streams pick up
a new `video_fps` on their next tick. WebSocket frame rate and gzip apply to the next message or
response.

The same knobs have a panel at `/admin` (the static dashboard build serves it). Paste the operator
token there; it is held in memory for that page visit only and never written to browser storage.

## Where it runs

### Render (current)
- `render.yaml` is the Blueprint. Set `BTB_API_TOKEN` and the other secrets in the dashboard, not
  in the repo (the repo is public).
- Render's free disk is ephemeral. Put `BTB_DATABASE_URL` on Supabase (see the runbook): households,
  stations and the admin bandwidth settings then survive a restart or a switch to another host.
- Bandwidth: the workspace includes 5 GB on Hobby and overage is billed per GB. Use the knobs above
  and a card on file so services don't spin down.

### Self-hosted VM (Hetzner, Oracle, or any Docker host)
```bash
cp .env.example .env            # fill in secrets; set BTB_DATABASE_URL if you want Postgres
cd deploy
BTB_DOMAIN=btb.example.com docker compose up -d --build
```
- `deploy/docker-compose.yml` runs the app and Caddy. Caddy issues the TLS certificate and
  proxies `/video` and the WebSockets without buffering.
- Persistent state lives in `deploy/data/out` (SQLite if no `BTB_DATABASE_URL`, logs, profile). The admin
  bandwidth settings live in the database either way.
- Point the domain's DNS A record at the VM and open ports 80 and 443.
- Caddy has no `encode` step on purpose: the app already compresses text, and Caddy compressing
  the MJPEG stream would hold frames back.

### Oracle Always Free (cheapest egress, with a catch)
- Always Free: 2 OCPU / 12 GB Ampere A1 and 10 TB/month outbound (official figures; see
  `docs/ORACLE_RUNBOOK.md` for the sources).
- Oracle may reclaim idle Always Free instances: CPU, network and memory all under 20% over
  7 days. Don't artificially load the machine to avoid it.
- Free A1 capacity is region-dependent. Sign-up can fail for weeks in busy regions.
- Prep: `deploy/oracle/cloud-init.yaml` bootstraps the VM, `backend/ops/state_backup.py` backs up
  state to R2 nightly, and `docs/ORACLE_RUNBOOK.md` covers provisioning and recovery.

## What is portable and what is not
- Portable: the app, the Dockerfile, env-driven config, the live knobs, the persistence paths.
- Host-specific: `render.yaml` (Render only), `deploy/` (any Docker host), and the DNS and TLS
  setup for your domain.
- Demo videos: with `web.demo.video_source: r2` the backend redirects to presigned R2 URLs and
  R2 serves the bytes (free egress). Upload with `scripts/upload_demo_clips.py` first. Default is
  `local`.

## Verified and not verified
- Verified: the backend web suite (477 tests) and the frontend suite (153 tests) pass, and the
  frontend static build produces `/admin`.
- Not verified: `deploy/docker-compose.yml`, `deploy/Caddyfile` and `deploy/oracle/cloud-init.yaml`
  have not been run on real infrastructure. Docker is not installed on the development machine.
  The R2 upload and the Oracle recovery have not been run against real accounts.
- Not verified: `deploy/docker-compose.yml` and `deploy/Caddyfile` have not been run. Docker is not
  installed on the development machine. Run `docker compose config` on a machine with Docker before
  the first deploy.
