# Console

The web console of Smart Surveillance System: the wall, camera pages, events, people, counts, coverage, health and the platform administration. It is a Next.js 16 static export with React 19, Tailwind v4 and shadcn/ui, served by nginx, which proxies /api/ to the API and /go2rtc/ to go2rtc. See the root README for the product.

## Run

The compose service web builds the image and serves the export on SSS_WEB_PORT. For development, start the stack and run the dev server, which rewrites /api and /go2rtc to SSS_DEV_ORIGIN:

```
docker compose -f deploy/server/docker-compose.yml up -d --build web
pnpm install --frozen-lockfile && pnpm dev
```

## Test

The console has no unit tests; lint, type check and build are its checks. Run them from the repository root:

```
docker run --rm -u $(id -u):$(id -g) -e HOME=/tmp -e COREPACK_HOME=/tmp/corepack -e PATH=/tmp/bin:/usr/local/bin:/usr/bin:/bin -v $PWD/apps/console:/app -w /app node:24 sh -c "mkdir -p /tmp/bin && corepack enable --install-directory /tmp/bin && pnpm install --frozen-lockfile && pnpm lint && pnpm typecheck && pnpm build"
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| SSS_DEV_ORIGIN | http://127.0.0.1:8750 | Origin the dev server proxies /api and /go2rtc to |
| SSS_WEB_PORT | 8750 | Port the web container publishes (compose) |

## Layout

| Folder | Holds |
|---|---|
| app | Routes and layouts |
| components | Components per area, and the shadcn/ui primitives in ui |
| components/shell | The application shell: the top bar, the sidebar grouped by business domain, and the page frame every page renders its header in; a new page adds its sidebar entry to navigation.ts |
| lib | API client, types and shared logic |
