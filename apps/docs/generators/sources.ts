import { existsSync, mkdirSync, writeFileSync } from "node:fs"
import { dirname, isAbsolute, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"

/** Folder of this app, apps/docs. */
export const appDir = resolve(dirname(fileURLToPath(import.meta.url)), "..")

/** Folder the generated pages and data are written to, which git ignores. */
export const generatedDir = join(appDir, "generated")

/** Root of the repository the site documents; DOCS_REPO_ROOT overrides it. */
export const repoRoot = resolve(appDir, process.env.DOCS_REPO_ROOT || "../..")

/** One input of the generators: a file or folder of the repository, its variable and whether it must exist. */
export interface SourcePath {
  /** Short name used in DOCS_OPTIONAL and in messages. */
  name: string
  /** Environment variable that overrides the path. */
  variable: string
  /** Paths relative to the repository root after the restructure into apps, services and packages; the first that exists wins. */
  fallback: string | string[]
}

/** Every input of the generators, with the locations they have once the repository is laid out by product. */
export const SOURCES = {
  catalog: {
    name: "catalog",
    variable: "DOCS_CATALOG",
    fallback: ["packages/camera-adapters/camera_adapters/catalog/models.yaml", "packages/camera-adapters/catalog/models.yaml"],
  },
  adapters: { name: "adapters", variable: "DOCS_ADAPTERS", fallback: "packages/camera-adapters/camera_adapters" },
  openapi: { name: "openapi", variable: "DOCS_OPENAPI", fallback: "packages/contracts/openapi.json" },
  architecture: { name: "architecture", variable: "DOCS_ARCHITECTURE", fallback: "docs/architecture.md" },
  pairingCatalog: {
    name: "pairing-catalog",
    variable: "DOCS_PAIRING_CATALOG",
    fallback: "services/api/app/modules/devices/services/pairing_catalog.py",
  },
  contributing: { name: "contributing", variable: "DOCS_CONTRIBUTING", fallback: "CONTRIBUTING.md" },
  contractSchemas: { name: "contract-schemas", variable: "DOCS_CONTRACT_SCHEMAS", fallback: "packages/contracts/schemas" },
} satisfies Record<string, SourcePath>

/** Lists of inputs, comma-separated in their variable. */
export const SOURCE_LISTS = {
  settings: {
    name: "settings",
    variable: "DOCS_SETTINGS",
    fallback: [
      "services/api/app/core/config.py",
      "services/analytics/app/settings.py",
      "services/analytics/app/identity/settings.py",
      "services/connector/connector_agent/settings.py",
    ],
  },
  readers: {
    name: "readers",
    variable: "DOCS_ENV_READERS",
    fallback: ["tools/cv-viewer/cv_viewer.py"],
  },
  envExamples: {
    name: "env-examples",
    variable: "DOCS_ENV_EXAMPLES",
    fallback: [
      "deploy/server/.env.example",
      "deploy/central/.env.example",
      "deploy/connector/.env.example",
      "deploy/jetson/.env.example",
      "deploy/devkit/.env.example",
    ],
  },
  compose: {
    name: "compose",
    variable: "DOCS_COMPOSE",
    fallback: [
      "deploy/server/docker-compose.yml",
      "deploy/central/docker-compose.yml",
      "deploy/connector/docker-compose.yml",
      "deploy/jetson/docker-compose.yml",
      "deploy/devkit/docker-compose.yml",
    ],
  },
  internalRoutes: {
    name: "internal-routes",
    variable: "DOCS_INTERNAL_ROUTES",
    fallback: [
      "/internal/analytics=services/api/app/modules/analytics/routers/internal.py",
      "/internal/analytics=services/api/app/modules/analytics/routers/reports.py",
      "/internal/analytics=services/api/app/modules/analytics/routers/follow.py",
      "/internal/people=services/api/app/modules/investigation/routers/internal.py",
    ],
  },
}

const optional = new Set(
  (process.env.DOCS_OPTIONAL || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean),
)

/**
 * Turn a configured path into an absolute one.
 *
 * @param path Absolute, or relative to the repository root.
 * @returns The absolute path.
 */
function absolute(path: string): string {
  return isAbsolute(path) ? path : join(repoRoot, path)
}

/**
 * Find an input, failing the build when it is missing unless DOCS_OPTIONAL names it.
 *
 * @param source The input.
 * @param required Whether the build needs it; an input that only adds a section passes false.
 * @returns Its absolute path, or null when it is missing and allowed to be.
 * @throws Error when it is missing and required.
 */
export function locate(source: SourcePath, required = true): string | null {
  const configured = process.env[source.variable]
  const candidates = (configured ? [configured] : [source.fallback].flat()).map(absolute)
  const path = candidates.find((candidate) => existsSync(candidate)) ?? candidates[0]
  if (existsSync(path)) return path
  if (!required) return null
  if (optional.has(source.name)) {
    console.warn(`[docs] ${source.name}: ${path} is missing; its page is left out`)
    return null
  }
  throw new Error(
    `[docs] ${source.name}: ${path} is missing. Set ${source.variable} to its location, or add ${source.name} to DOCS_OPTIONAL to leave its page out.`,
  )
}

/**
 * Find the inputs of a list that exist; a list whose entries are all missing fails like a missing input.
 *
 * @param list The list.
 * @param list.name Short name used in DOCS_OPTIONAL and in messages.
 * @param list.variable Environment variable with comma-separated paths.
 * @param list.fallback Paths relative to the repository root.
 * @param required Whether the build needs at least one of them.
 * @returns The absolute paths that exist.
 * @throws Error when none exists and the list is required.
 */
export function locateAll(list: { name: string; variable: string; fallback: string[] }, required = true): string[] {
  const configured = process.env[list.variable]
  const paths = (configured ? configured.split(",") : list.fallback).map((item) => absolute(item.trim()))
  const found = paths.filter((path) => existsSync(path))
  if (found.length === 0 && required && !optional.has(list.name)) {
    throw new Error(`[docs] ${list.name}: none of ${paths.join(", ")} exists. Set ${list.variable}.`)
  }
  return found
}

/**
 * Turn a path from the configuration into an absolute one.
 *
 * @param path Absolute, or relative to the repository root.
 * @returns The absolute path.
 */
export function fromConfig(path: string): string {
  return absolute(path)
}

/**
 * Path of a file relative to the repository root, for messages and generated text.
 *
 * @param path Absolute path.
 * @returns The relative path with forward slashes.
 */
export function fromRoot(path: string): string {
  return path.startsWith(repoRoot) ? path.slice(repoRoot.length + 1) : path
}

/**
 * Write a generated file, creating its folder.
 *
 * @param path Path relative to the generated folder.
 * @param content The file's text.
 */
export function writeGenerated(path: string, content: string): void {
  const target = join(generatedDir, path)
  mkdirSync(dirname(target), { recursive: true })
  writeFileSync(target, content.endsWith("\n") ? content : `${content}\n`)
}
