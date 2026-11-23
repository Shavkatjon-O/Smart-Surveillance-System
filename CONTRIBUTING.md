# Contributing

## Commits

Every commit message follows Conventional Commits 1.0.0: a lowercase type, an optional scope in parentheses, a colon, a space and a short description.

| Type | Use |
|---|---|
| feat | A new feature |
| fix | A bug fix |
| build | Build system, dependencies, Dockerfiles, compose |
| chore | Maintenance that changes no behaviour |
| ci | Continuous integration |
| docs | Documentation only |
| style | Formatting only |
| refactor | Code change that neither fixes a bug nor adds a feature |
| perf | Performance improvement |
| test | Tests only |
| revert | Reverts an earlier commit |

Scopes name the affected part: api, console, tv, analytics, connector, adapters, bus, devkit, deploy, docs. Mark a breaking change with an exclamation mark before the colon or a BREAKING CHANGE footer.

Stage and commit explicit paths, never everything at once:

```
git add services/api/app/modules/devices
git commit -m "fix(api): reject a pairing code twice"
```

## Checks

Run the checks of every area you changed from the repository root before committing.

```
docker run --rm -u $(id -u):$(id -g) -e HOME=/tmp -e UV_PROJECT_ENVIRONMENT=/tmp/venv -e UV_CACHE_DIR=/tmp/uv-cache -v $PWD:/repo -w /repo/services/api ghcr.io/astral-sh/uv:python3.13-bookworm-slim sh -c "uv sync --frozen --all-packages && uv run --frozen ruff check . && uv run --frozen ruff format --check . && uv run --frozen pytest -q"

docker run --rm -u $(id -u):$(id -g) -e HOME=/tmp -e UV_PROJECT_ENVIRONMENT=/tmp/venv -e UV_CACHE_DIR=/tmp/uv-cache -v $PWD:/repo -w /repo/packages/camera-adapters ghcr.io/astral-sh/uv:python3.13-bookworm-slim sh -c "uv sync --frozen --all-packages && uv run --frozen ruff check . && uv run --frozen pytest -q"

docker run --rm -u $(id -u):$(id -g) -e HOME=/tmp -e UV_PROJECT_ENVIRONMENT=/tmp/venv -e UV_CACHE_DIR=/tmp/uv-cache -v $PWD:/repo -w /repo/packages/event-bus ghcr.io/astral-sh/uv:python3.13-bookworm-slim sh -c "uv sync --frozen --all-packages && uv run --frozen ruff check . && uv run --frozen ruff format --check . && uv run --frozen pytest -q"

docker build --target test --build-context event-bus=packages/event-bus -t smart-surveillance-system-analytics:test services/analytics
docker run --rm -u $(id -u):$(id -g) -e RUFF_CACHE_DIR=/tmp/ruff-cache -v $PWD/services/analytics:/work:ro -w /work --entrypoint sh smart-surveillance-system-analytics:test -c "ruff check . && ruff format --check . && pytest -q -p no:cacheprovider"

docker run --rm -u $(id -u):$(id -g) -e HOME=/tmp -e COREPACK_HOME=/tmp/corepack -e PATH=/tmp/bin:/usr/local/bin:/usr/bin:/bin -v $PWD/apps/console:/app -w /app node:24 sh -c "mkdir -p /tmp/bin && corepack enable --install-directory /tmp/bin && pnpm install --frozen-lockfile && pnpm lint && pnpm typecheck && pnpm build"

docker compose -f deploy/server/docker-compose.yml config -q

scripts/check-diagrams.sh

cd apps/tv && ./gradlew testDebugUnitTest
```

Check every deploy target you changed with config -q, from its own .env; .github/workflows/compose.yml checks every target and profile from the example env files.

Draw a diagram as a .mmd source in docs/diagrams, render it with scripts/render-diagrams.sh, which needs Chrome, and commit the PNG card beside the source; Markdown and MDX embed the card and never hold a mermaid block.

The API suite includes the import-linter contracts that enforce the dependency rules in docs/architecture.md.

## Documentation comments

Write documentation comments on every public member, as the linters require: docstrings with Args, Returns, Yields, Raises and Attributes sections where they apply in Python, KDoc in Kotlin, JSDoc in TypeScript, a header comment per file and per function in shell, and a comment above every function in C++. Add an ordinary comment only where the code cannot show its reason. Write no emojis and no non-ASCII characters in code, configuration or documentation.

## Privacy

The repository is public. Keep the private data of the sites it watches out of every commit and out of history: no real LAN addresses (examples and tests use 192.168.1.x), device ids, MACs, serials, Wi-Fi names, passwords, tokens, number plates, names or workspace paths, and no images, video or faces from real cameras. Keep .env and apps/tv/cameras.local.properties out of git. Never bypass the pre-push hook.

## Licence

The repository carries no licence file yet.
