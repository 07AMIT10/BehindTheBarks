# Render Cloud Standby Failover Architecture (Worst-Case Fallback)

This guide documents the high-availability failover architecture that guarantees **100% uptime for WagWatch** even if the self-hosted Dell Inspiron server is powered down, reboots, or loses internet connectivity — **without requiring any changes on the Android mobile app**.

---

## 1. Architecture Overview

```
                                [ Android Station Phone ]
                                            │
                                            ▼
                           https://watch.furryfolks.co
                           wss://watch.furryfolks.co/ingest-events
                                            │
                                            ▼
                                   [ Cloudflare Edge ]
                      (Failover Worker / Health Monitor Routing)
                                   /                 \
                     (Healthy)   /                     \  (Fallback on 502/Down)
                               /                         \
                              ▼                           ▼
                 [ Dell Inspiron Server ]       [ Render Cloud Web Service ]
                 - Primary 24/7 origin          - Standby free tier fallback
                 - Unmetered local LAN          - behind-the-barks.onrender.com
                 - Fast 512GB SSD storage       - Auto-deployed from GitHub repo
                 - Cloudflare Tunnel            - Households mode + Auto-restore
```

### Key Properties:
1. **Single Immutable Domain**: Both the Android app and web dashboard connect exclusively to `https://watch.furryfolks.co` / `wss://watch.furryfolks.co`.
2. **Zero Mobile App Impact**: The phone app does not know or care which backend is processing events. It stays paired and streams seamlessly.
3. **Automatic Circuit Breaking**: If Dell becomes unresponsive or returns `502 / 503 / 504 / 530`, Cloudflare switches origin to Render in <3 seconds and enters a 30s cooldown before retrying Dell.

---

## 2. Render Blueprint Configuration (`render.yaml`)

The `render.yaml` specification defines the Standby service:
- **`WEB_PIPELINE=remote`**: Configures remote station event streaming.
- **`BTB_AUTH_MODE=households`**: Ensures Render runs the identical multi-household authentication, pairing codes, and ECDSA P-256 station validation.
- **`BTB_IS_FALLBACK=1`**: Informs the application runtime and `/status` endpoint that Render is operating as fallback.
- **`BTB_AUTO_RESTORE=1`**: Docker entrypoint attempts automated restoration of the newest database archive from R2 on startup.
- **`BTB_FORCE_HTTPS=1`**: Strictly enforces secure cookies and TLS.

---

## 3. State & Database Synchronization

To ensure that station public keys, household IDs, and viewer sessions match between Dell and Render:

### Method A: Shared PostgreSQL Database (Recommended for Real-Time Sync)
Set `DATABASE_URL` or `BTB_DATABASE_URL` in Render environment variables (e.g., free tier Supabase, Neon, or Render PostgreSQL):
- Both Dell and Render point to the same connection URL.
- When Render takes over, all paired stations immediately authenticate.

### Method B: Automated R2 Backup Snapshots
If using SQLite:
- Dell Inspiron runs `backend.ops.state_backup backup` periodically (or after pairing).
- Render container has `BTB_AUTO_RESTORE=1` in `Dockerfile` which runs:
  ```bash
  python -m backend.ops.state_backup restore
  ```
  before starting uvicorn, restoring the latest SQLite database from R2.

---

## 4. Cloudflare Failover Deployment (2 Minutes)

Deploy the failover script located at `scripts/cloudflare_failover_worker.js`:

1. In the **Cloudflare Dashboard**, navigate to **Workers & Pages** -> **Create Application**.
2. Paste the contents of `scripts/cloudflare_failover_worker.js`.
3. Configure the Environment Variables:
   - `PRIMARY_ORIGIN`: `https://primary-watch.furryfolks.co` (points to your Dell Tunnel)
   - `FALLBACK_ORIGIN`: `https://behind-the-barks.onrender.com`
4. Add Route:
   - Route: `watch.furryfolks.co/*`
   - Zone: `furryfolks.co`
5. Test Failover:
   - Stop `btb.service` on Dell: `sudo systemctl stop btb`
   - Navigate to `https://watch.furryfolks.co` -> Render instantly serves the dashboard with the badge `☁️ Render Fallback`.
   - Start `btb.service` on Dell: `sudo systemctl start btb` -> Primary Dell resumes transparently.
