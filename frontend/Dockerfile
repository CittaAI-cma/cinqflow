# Dev-parity image for the CINQFLOW frontend. Runs `next dev`, matching the
# existing `pnpm dev` workflow (frontend/package.json) rather than a
# production build the rest of the working foundation doesn't use yet.
FROM node:24-slim

WORKDIR /app

# Lockfile first so `pnpm install` is cached while iterating on app code.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN corepack enable && corepack prepare pnpm@11.14.0 --activate \
    && pnpm install --frozen-lockfile

COPY . .

EXPOSE 3000
CMD ["pnpm", "dev"]
