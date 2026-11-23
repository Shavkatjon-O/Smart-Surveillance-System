import { readFileSync, rmSync } from "node:fs"
import { basename, dirname, join, normalize } from "node:path"

import { SOURCES, appDir, fromRoot, locate, repoRoot, writeGenerated } from "./sources.ts"

/** Address of a file of the repository on GitHub, for links that leave the site. */
const BLOB = "https://github.com/Shavkatjon-O/Smart-Surveillance-System/blob/main"

/** The architecture page, generated beside the hand-written pages and ignored by git. */
const PAGE = join(appDir, "content/docs/en/architecture.mdx")

/**
 * Build the architecture page from the repository's architecture document, without its own title, with its diagram
 * images pointed at the copies in the site's public files and links to other repository files pointed at GitHub.
 *
 * @returns Where it came from, for the build log.
 */
export function generateArchitecture(): string {
  const source = locate(SOURCES.architecture)
  if (!source) {
    rmSync(PAGE, { force: true })
    return "skipped"
  }
  const folder = dirname(source)
  const diagrams = locate(SOURCES.diagrams, false)
  const text = readFileSync(source, "utf8")
    .replace(/^# .*\n+/, "")
    .replace(/(!?)\[([^\]]*)\]\((?!https?:|#|mailto:)([^)\s]+)\)/g, (_, bang: string, label: string, target: string) => {
      const [path, anchor] = target.split("#")
      const absolute = normalize(join(folder, path))
      if (bang && diagrams && dirname(absolute) === diagrams) return `![${label}](/diagrams/${basename(absolute)})`
      return `${bang}[${label}](${BLOB}/${absolute.slice(repoRoot.length + 1)}${anchor ? `#${anchor}` : ""})`
    })
  writeGenerated("architecture.md", `${text.trimEnd()}\n\nThis page is generated from ${fromRoot(source)}.\n`)
  writeGenerated(
    "../content/docs/en/architecture.mdx",
    [
      "---",
      "title: Architecture",
      "description: The product map, the business domains, the system's context and containers, and the dependency rules.",
      "icon: Network",
      "---",
      "",
      "<include>../../../generated/architecture.md</include>",
    ].join("\n"),
  )
  return fromRoot(source)
}

/**
 * Copy the repository's contributing guide for the contributing page, without its own title.
 *
 * @returns Where it came from, for the build log.
 */
export function generateContributing(): string {
  const source = locate(SOURCES.contributing)
  if (!source) {
    writeGenerated("contributing.md", "")
    return "skipped"
  }
  writeGenerated("contributing.md", `${readFileSync(source, "utf8").replace(/^# .*\n+/, "").trimEnd()}\n\nThis section is generated from ${fromRoot(source)}.\n`)
  return fromRoot(source)
}
