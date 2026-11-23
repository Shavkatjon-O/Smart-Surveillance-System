# Docs

The documentation website of Smart Surveillance System: the product and its apps, analytics features, camera compatibility, pairing, architecture, configuration, the API reference, deployment, privacy, licences, contributing and the changelog. It is a Next.js 16 static export with Tailwind v4 and Fumadocs, styled after the console, in light and dark, and published on GitHub Pages.

## Run

The host needs no Node; run the tools in a node container as your own user, from the repository root:

```
docker run --rm -it -u $(id -u):$(id -g) -e HOME=/tmp -e COREPACK_HOME=/tmp/corepack -e PATH=/tmp/bin:/usr/local/bin:/usr/bin:/bin -p 127.0.0.1:3000:3000 -v $PWD:/repo -w /repo/apps/docs node:24 sh -c "mkdir -p /tmp/bin && corepack enable --install-directory /tmp/bin && pnpm install --frozen-lockfile && pnpm dev --hostname 0.0.0.0"
```

The site opens at http://127.0.0.1:3000/en/. pnpm dev runs the generators once before the dev server; run pnpm generate again after changing a source they read.

## Build and check

```
docker run --rm -u $(id -u):$(id -g) -e HOME=/tmp -e COREPACK_HOME=/tmp/corepack -e PATH=/tmp/bin:/usr/local/bin:/usr/bin:/bin -v $PWD:/repo -w /repo/apps/docs node:24 sh -c "mkdir -p /tmp/bin && corepack enable --install-directory /tmp/bin && pnpm install --frozen-lockfile && pnpm lint && pnpm typecheck && pnpm build && pnpm check:links"
```

pnpm build writes the static site to out/. pnpm check:links crawls every page of out/ and fails on a broken internal link or anchor. The changelog reads the git history, so a checkout needs its full history and a worktree needs its main repository's git folder.

## Content sources

Pages are MDX files under content/docs/en, one folder per section with a meta.json that orders it. Everything that the repository already states in code or data is generated when the site is built, into generated/ and content/docs/en/reference/api/, which git ignores; never edit those files.

| Page | Generated from | Variable |
|---|---|---|
| Camera compatibility | packages/camera-adapters/camera_adapters/catalog/models.yaml and the adapters in packages/camera-adapters/camera_adapters | DOCS_CATALOG, DOCS_ADAPTERS |
| Pairing methods | services/api/app/modules/devices/services/pairing_catalog.py, the catalog and the adapters' label parsers | DOCS_PAIRING_CATALOG |
| Configuration | the .env.example and docker-compose.yml of every deployment target in deploy, the settings classes of the API, the analytics and the connector, and the cv viewer | DOCS_ENV_EXAMPLES, DOCS_COMPOSE, DOCS_SETTINGS, DOCS_ENV_READERS |
| API reference | packages/contracts/openapi.json, split into one small document per operation | DOCS_OPENAPI |
| Connector protocol tables | the same OpenAPI document, by tag | DOCS_OPENAPI |
| Analytics contract | the internal analytics and people routers of the API, and JSON Schemas in packages/contracts/schemas once they exist | DOCS_INTERNAL_ROUTES, DOCS_CONTRACT_SCHEMAS |
| Architecture | docs/architecture.md | DOCS_ARCHITECTURE |
| Contributing | CONTRIBUTING.md | DOCS_CONTRIBUTING |
| Changelog | the git history, read as Conventional Commits | DOCS_GIT_DIR |

Paths are relative to the repository root, DOCS_REPO_ROOT, which defaults to two folders up. A missing source fails the build; DOCS_OPTIONAL, a comma-separated list of source names such as architecture, leaves its page out instead. The product map on the landing and overview pages comes from lib/products.ts.

The rest is written by hand from the code and the repository's README and design records: present tense, each fact once, plain ASCII, and plain text in Markdown table cells. A feature that works but is new or verified only on synthetic input carries the Beta status, and one that exists only in design carries Planned.

## Languages

English pages live in content/docs/en. Russian and Uzbek join by adding ru and uz to lib/i18n.ts and their pages under content/docs/ru and content/docs/uz; a page missing in a language falls back to English, and the site's interface text comes from Fumadocs' translations.

## Deployment

.github/workflows/docs.yml builds the site when the docs or one of their sources change. On main it builds again with the Pages base path, which DOCS_BASE_PATH sets, and deploys out/ to GitHub Pages. The Pages source of the repository must be set to GitHub Actions.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| DOCS_BASE_PATH | empty | Path prefix the site is served under, such as /Smart-Surveillance-System on GitHub Pages |
| DOCS_REPO_ROOT | ../.. | Repository root the generators read |
| DOCS_OPTIONAL | empty | Sources whose pages may be left out when missing |
| DOCS_BUILD_CPUS | empty | Number of build workers, to keep the build's memory down on a small machine; empty lets Next.js choose |

The source path variables are listed under Content sources.
