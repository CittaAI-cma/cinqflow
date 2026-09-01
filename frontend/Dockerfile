# ─────────────────────────────────────────────────────────────────────────────
# cinqflow / frontend
#
# The UI holds no secrets and no business rules — it renders what the BFF says.
# That is why `CINQFLOW_API` is the only thing this image needs to know about
# its environment, and why it is read at RUNTIME (see the `runtime` stage's
# entrypoint) rather than baked at build time: the same image then runs in dev
# and prod, which is the point of building it once.
# ─────────────────────────────────────────────────────────────────────────────
ARG NODE_VERSION=24

# ── deps ─────────────────────────────────────────────────────────────────────
FROM node:${NODE_VERSION}-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# ── dev ──────────────────────────────────────────────────────────────────────
# `next dev` with the source bind-mounted over it. Selected by the LOCAL
# overlay only; dev and prod build the real thing.
FROM node:${NODE_VERSION}-slim AS dev
WORKDIR /app
ENV NODE_ENV=development \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev"]

# ── builder ──────────────────────────────────────────────────────────────────
FROM node:${NODE_VERSION}-slim AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# next.config.ts inlines `CINQFLOW_API` through its `env` block, so a build
# with the wrong value bakes the wrong value in. The placeholder below is
# REPLACED at container start by the entrypoint — one image, any environment.
ENV CINQFLOW_API=__CINQFLOW_API__
RUN npm run build

# ── runtime ──────────────────────────────────────────────────────────────────
FROM node:${NODE_VERSION}-slim AS runtime
WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    CINQFLOW_API=http://backend:8000

COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/next.config.ts ./next.config.ts

# Swap the build-time placeholder for the real endpoint, then hand PID 1 to
# Next. Done here rather than in the image because the endpoint is an
# environment fact, and environment facts belong in the profile and the .env.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
 && useradd --create-home --uid 10001 cinqflow \
 && chown -R cinqflow:cinqflow /app/.next

USER cinqflow
EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/" >/dev/null || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["npm", "run", "start"]
