# Stage 1: Build Next.js static dashboard
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# Stage 2: Python backend + static dashboard
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WEB_PIPELINE=remote \
    PORT=8000

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgomp1 \
    curl \
    # Pin the client to the Supabase/server major (supabase/config.toml major_version = 17):
    # pg_dump/pg_restore must not be older than the server, or nightlies and restores refuse.
    postgresql-client-17 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt

COPY backend/ ./backend/
COPY config.yaml ./config.yaml
COPY data/ ./data/

# Copy static frontend export from stage 1 into frontend/out
COPY --from=frontend-builder /app/frontend/out ./frontend/out

# Set BTB_AUTO_RESTORE=1 to pull state/** from the nightly R2 archive at startup; add
# BTB_RESTORE_DB=1 to also apply the database members (Supabase pg dump / SQLite btb.db).
# scripts/auto_restore.sh keeps restore output visible and, by default, never overwrites the DB.
# scripts/pg_client_version_check.sh guards the nightly pg_dump/pg_restore against an old client.
COPY scripts/auto_restore.sh scripts/pg_client_version_check.sh ./scripts/

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/health || exit 1

CMD ["sh", "-c", "if [ \"$BTB_AUTO_RESTORE\" = \"1\" ]; then sh scripts/auto_restore.sh || echo 'BTB_AUTO_RESTORE: restore failed; starting without restored state (see the lines above)' >&2; fi; exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
