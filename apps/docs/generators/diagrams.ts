import { copyFileSync, mkdirSync, readdirSync, rmSync } from "node:fs"
import { join } from "node:path"

import { SOURCES, appDir, fromRoot, locate } from "./sources.ts"

/** Folder of the site's public files the diagrams are copied into, which git ignores. */
export const diagramsDir = join(appDir, "public/diagrams")

/**
 * Copy the rendered diagrams of the repository into the site's public files, where pages embed them as
 * /diagrams/name.png, so the site keeps no second copy of them in git.
 *
 * @returns Number of diagrams and where they came from, for the build log.
 */
export function generateDiagrams(): string {
  rmSync(diagramsDir, { recursive: true, force: true })
  const source = locate(SOURCES.diagrams)
  if (!source) return "skipped"
  mkdirSync(diagramsDir, { recursive: true })
  const names = readdirSync(source).filter((name) => name.endsWith(".png"))
  for (const name of names) copyFileSync(join(source, name), join(diagramsDir, name))
  return `${names.length} from ${fromRoot(source)}`
}
