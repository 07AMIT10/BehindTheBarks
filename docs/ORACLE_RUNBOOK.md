# Oracle Always Free: runbook

Goal: run the backend on Oracle at no cost, with Render as a warm standby and Supabase as the one
database both use. Losing the VM should cost a switch, not data. Facts are from the official docs
checked 2026-10-11 unless marked.

## Where things are

| What | Link | Used for |
|---|---|---|
| Oracle Cloud Free sign-up (the profile you create) | https://www.oracle.com/cloud/free/ | creating the Oracle account |
| Oracle Cloud console | https://cloud.oracle.com | instances, Security Lists, Billing and budgets |
| Supabase dashboard | https://supabase.com/dashboard | project, **Connect** dialog (copy the pooler URL), Billing |
| Render dashboard | https://dashboard.render.com | standby service, custom domain |
| Cloudflare R2 | Cloudflare dashboard → R2 | backups and demo videos (bucket, API token) |
| DNS for your domain | your registrar or Cloudflare DNS | the one name everything uses |

## Architecture

```
            btb.example.com  (one DNS name; the phone app and dashboards use only this)
                    │
        ┌───────────┴────────────┐
        ▼ primary                ▼ standby (cold after 15 min idle)
  Oracle VM (reserved IP)   Render web service (same custom domain, added in Render)
        │                        │
        └───────────┬────────────┘
                    ▼
     Supabase Postgres  (BTB_DATABASE_URL, session pooler on port 5432, IPv4)
                    │
     Cloudflare R2: nightly state backups (out/ + pg_dump), demo MP4s
```

Only one of the two web services should be live at a time. Both can point at Supabase, because the
database is the shared state. Files under `out/` are not shared; see "What does not survive a switch".

## Decisions for you (I did not make these)

1. **Supabase Free or Pro.** Official Supabase docs: a Free project is paused after low database
   activity over a 7-day period, and paid plans are exempt. A paused project stops the app. Options:
   - **Pro** (third-party sites quote about $25/month; check the pricing page). Removes the risk.
   - **Free plus a keepalive.** A daily query from the Oracle VM or an external cron. Supabase does not
     say which activity counts, so this is a guess. Only use it if you accept that.
   I recommend Pro until the Free keepalive has survived a few weeks without a pause.
2. **Oracle Pay As You Go upgrade.** The docs don't say whether PAYG stops idle reclaim. A 2023 blog
   quotes an Oracle notice saying it does. Before upgrading, set a $1 budget alert in Billing and ask
   Oracle support in writing. The upgrade needs a real payment method.
3. **Render standby cost.** The Render Free service spins down after 15 minutes without inbound
   traffic (official docs). The first request after that takes about a minute. Keeping it warm with an
   external monitor uses the Free instance hours (a 24/7 service needs about 744 of the monthly hours;
   verify the current allowance). Or pay for an always-on instance.

## Database connection (Supabase)

- Use the **session pooler** (port **5432**) from the Connect dialog. It works over IPv4, which the
  Oracle VM and Render use. The direct connection is IPv6-only unless you pay for the IPv4 add-on.
- Do **not** use the transaction pooler (port 6543) with this app: it doesn't support the prepared
  statements SQLAlchemy and psycopg use by default.
- The username is `postgres.<project-ref>`, not plain `postgres`. Copy the host from the dialog, don't
  build it from the region.
- Set it as `BTB_DATABASE_URL=postgresql+psycopg://postgres.<ref>:<password>@<pooler-host>:5432/postgres`.
  The `+psycopg` part is required by SQLAlchemy.
- Check the connection limit for your plan in the Supabase dashboard. Each web service keeps a small
  pool, so two instances plus a backup run is fine on the free allowance.

## Oracle VM: setup

1. **Account.** Sign up at https://www.oracle.com/cloud/free/ and log in at https://cloud.oracle.com.
   Choose a home region with A1 capacity. "Out of host capacity" means try another region or time, or
   use the AMD Micro shape.
2. **Instance.** Compute → Create instance. Image: Canonical Ubuntu 24.04. Shape:
   `VM.Standard.A1.Flex`, 2 OCPU / 12 GB. Under Initialization script, paste
   `deploy/oracle/cloud-init.yaml` after replacing `REPLACE_REPO_URL` and `REPLACE_BRANCH`.
3. **Reserved public IP.** Networking → Reserved public IPs → assign it to the instance, so a new VM
   can take over the same address.
4. **Security List.** VCN → Security Lists → ingress TCP 80 and 443 from `0.0.0.0/0`. Cloud-init opened
   the host firewall; Oracle's VCN firewall is separate and must be set here.
5. **Secrets and start.** SSH in, then:
   ```bash
   cd /opt/btb && cp .env.example .env && nano .env   # BTB_DATABASE_URL (Supabase), BTB_API_TOKEN, BTB_R2_*, LLM_*
   cd deploy && BTB_DOMAIN=btb.example.com docker compose up -d --build
   ```
6. **DNS.** Point `btb.example.com` at the reserved IP. Caddy gets the certificate on the first request,
   so DNS must be live first.
7. **Demo videos.** `python scripts/upload_demo_clips.py --dry-run`, then the real run, then
   `BTB_DEMO_VIDEO_SOURCE=r2` in `.env` and restart.
8. **Backups.** `docker compose exec app python -m backend.ops.state_backup backup`, then `list`.
   Check `docker compose exec app pg_dump --version` against your Supabase server version (the dashboard
   shows it). `pg_dump` must be the same major version or newer, or the nightly dump fails.

## Render standby: setup

1. Create a Web Service from the same repo (`render.yaml` is the Blueprint). Set `BTB_DATABASE_URL` to
   the **same Supabase URL** and the same `BTB_API_TOKEN`, `BTB_R2_*` and LLM keys.
2. Add the custom domain `btb.example.com` to the service (the Render Free feature list includes custom
   domains; confirm in the dashboard). Don't point DNS at it yet.
3. Leave it stopped or spun down. It only takes traffic after the switch.
4. Render Free has **no persistent disk**. Anything under `out/` is lost on restart or spin-down.
   Nothing you need should live only there (see the next section).

## What does not survive a switch (and what to do about it)

| Item | Where it lives now | On a switch |
|---|---|---|
| Households, stations, pairing, history | Supabase | survives |
| Admin bandwidth settings | `app_settings` table in Supabase | **survives** (overrides only; unchanged knobs follow `config.yaml`) |
| Session event logs | `out/` on the instance | **not on the other instance**; history in the DB still shows |
| Live monitoring state (current emotion, phone stream) | memory on the instance | **restarts**; the phone reconnects |
| Browser sessions (cookies) | browser, for `btb.example.com` | survive, because the domain is the same |

The admin bandwidth settings are stored in the database, so they survive a switch. Each instance re-reads
them every 30 seconds, so a change made on one host reaches the other.

## The switch: planned (Oracle → Render, or back)

Users see a short "reconnecting" state, not an error page, if you follow this order.

1. **Lower the DNS TTL** for `btb.example.com` to 60 seconds, at least 15 minutes before the switch.
2. **Warm the standby.** Open `https://<render-url>/health` (the Render default URL) and wait for the
   reply (about a minute from cold). Check `/admin/tuning` there with the token, and check that the
   household list loads (it proves the Supabase connection).
3. **Flip DNS.** Point `btb.example.com` at Render. The custom-domain certificate on Render is issued
   after the DNS change, so this may take a few minutes.
4. **Verify.** `https://btb.example.com/health`, the dashboard, and one phone (the on-device app or the
   browser camera) reconnecting.
5. **Check admin settings** on the new instance (`/admin/tuning`). They should match what you set.
6. **Stop the old instance** only after the new one has been stable for a while.

Rollback: point DNS back to the Oracle reserved IP and reverse step 2 if needed.

## Recovery: the Oracle VM is lost

Target: under an hour, practised once on a spare instance. The database is unaffected (Supabase).

1. Create a new instance (same cloud-init and shape) and move the reserved IP to it.
2. Copy `.env` back on (keep a copy outside the VM, e.g. in a password manager).
3. Start the stack, stop the app, restore the state from the newest R2 backup, start again:
   ```bash
   cd /opt/btb/deploy && BTB_DOMAIN=btb.example.com docker compose up -d --build
   docker compose stop app
   docker compose run --rm app python -m backend.ops.state_backup restore --force
   docker compose start app
   ```
   `restore` restores the `out/` files (profile, tuning, logs) and, for SQLite, the database. With
   Supabase it runs `pg_restore` over the database, **which replaces its current contents**. Only do
   that if the database is the thing that went wrong. For a VM-only loss, restore only the files.
4. Check `/health` and `/admin/tuning`. Reinstall the systemd backup timer if it is missing.
   Admin settings come from the database, so they don't need restoring from the archive.

## Keeping the Oracle VM alive

- Traffic: the threshold is 20% CPU, network and memory over 7 days. A demo app may not reach it.
  Don't add dummy load to avoid the check.
- Watch: check the instance state in the console weekly, and add an external uptime check on
  `https://btb.example.com/health` (a free uptime monitor; not verified here).
- `restart: unless-stopped` covers app crashes and reboots, not a reclaimed VM.

## Backups

- **Files:** `backend/ops/state_backup.py` archives `out/` (profile, tuning, logs) to R2 nightly
  (03:15), keeping 14.
- **Database:** with Supabase the archive also holds a `pg_dump` custom-format file. Restore needs the
  `--force` flag. It was checked only against mocked `pg_dump`/`pg_restore` calls; run one real backup
  and restore on a test project before relying on it.
- **Supabase's own backups:** I found no official statement that Free projects get backups; one
  third-party source says they don't. Don't rely on Supabase backups alone; this archive is the copy.

## Costs

- Oracle: stay inside Always Free. Check Cost Analysis monthly, plus the $1 budget alert.
- Supabase: Free, or Pro if you choose it (decision 1).
- Render: Free, unless you choose an always-on instance (decision 3).
- R2: the archives are small; demo clips are about 27 MB in total. Egress is free.
