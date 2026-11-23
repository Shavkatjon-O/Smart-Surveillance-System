import { readFileSync } from "node:fs"
import { basename, dirname } from "node:path"

import { escape, list, table } from "./markdown.ts"
import { SOURCE_LISTS, fromRoot, locateAll, writeGenerated } from "./sources.ts"

/** A variable the code reads, with what its source says about it. */
interface Setting {
  name: string
  fallback: string | null
  description: string
  reader: string
}

/** A line of an env example file. */
interface ExampleLine {
  name: string
  example: string
  group: number
}

/** A variable compose interpolates. */
interface ComposeUse {
  services: Set<string>
  fallback: string | null
  required: boolean
}

/**
 * Read the Attributes section of a Python class docstring.
 *
 * @param docstring The docstring.
 * @returns Attribute names to their descriptions.
 */
function attributes(docstring: string): Map<string, string> {
  const found = new Map<string, string>()
  const section = /Attributes:\n([\s\S]*)/.exec(docstring)?.[1] ?? ""
  let current = ""
  for (const line of section.split("\n")) {
    const item = /^\s{8}(\w+): (.*)$/.exec(line)
    if (item) {
      current = item[1]
      found.set(current, item[2].trim())
    } else if (current && /^\s{10,}\S/.test(line)) {
      found.set(current, `${found.get(current)} ${line.trim()}`)
    } else if (line.trim() && !/^\s{8}/.test(line)) {
      break
    }
  }
  return found
}

/**
 * Name the component a source file belongs to, such as api for services/api/app/core/config.py.
 *
 * @param path Absolute path.
 * @returns The component's folder relative to the repository root.
 */
function component(path: string): string {
  const relative = fromRoot(path)
  const parts = relative.split("/")
  return parts.length > 2 && ["services", "apps", "tools", "packages"].includes(parts[0])
    ? `${parts[0]}/${parts[1]}`
    : dirname(relative)
}

/**
 * Turn a Python literal default into plain text.
 *
 * @param literal The literal as written.
 * @returns The value, or null when it is empty.
 */
function literal(literal: string): string | null {
  const value = literal.trim().replace(/^["']|["']$/g, "")
  if (value === "True" || value === "False") return value.toLowerCase()
  return value === "" ? null : value
}

/**
 * Read the variables a settings class reads: pydantic settings with an env_prefix, or fields that call an env
 * helper with a variable name.
 *
 * @param path The settings file.
 * @returns The settings it defines.
 */
function readSettings(path: string): Setting[] {
  const source = readFileSync(path, "utf8")
  const reader = component(path)
  const settings: Setting[] = []
  const helperPrefix = /environ\.get\(\s*f"([A-Z0-9_]*)\{name\}"/.exec(source)?.[1] ?? ""
  for (const found of source.matchAll(/^class (\w+)(?:\([^)]*\))?:\n\s+"""([\s\S]*?)"""([\s\S]*?)(?=^class |(?![\s\S]))/gm)) {
    const docs = attributes(found[2])
    const body = found[3]
    const prefix = /env_prefix="([^"]*)"/.exec(body)?.[1]
    for (const line of body.split("\n")) {
      const field = /^\s{4}(\w+):\s*[^=\n]+?=\s*(.+)$/.exec(line)
      if (!field) continue
      const [, attribute, value] = field
      const description = docs.get(attribute) ?? ""
      const helper = /\benv\w*\(\s*"([A-Z0-9_]+)"(?:\s*,\s*("[^"]*"|[^)]+))?\)/.exec(value)
      if (helper) {
        const name = helperPrefix && !helper[1].startsWith(helperPrefix) ? `${helperPrefix}${helper[1]}` : helper[1]
        settings.push({ name, fallback: helper[2] ? literal(helper[2]) : null, description, reader })
        continue
      }
      if (prefix === undefined || attribute === "model_config") continue
      const aliases = [...value.matchAll(/"([a-z0-9_]+)"/g)].map((alias) => alias[1].toUpperCase())
      const fieldDefault = /Field\(\s*default=("[^"]*"|[^,)]+)/.exec(value)?.[1]
      const fallback = literal(fieldDefault ?? value)
      const names = value.includes("AliasChoices") ? aliases : [`${prefix}${attribute}`.toUpperCase()]
      for (const name of names) settings.push({ name, fallback, description, reader })
    }
  }
  return settings
}

/**
 * Read the variables other code reads straight from the environment, such as os.environ.get in a tool.
 *
 * @param path The file.
 * @returns The variables with their defaults.
 */
function readEnvironmentReads(path: string): Setting[] {
  const source = readFileSync(path, "utf8")
  return [...source.matchAll(/os\.environ\.get\(\s*"([A-Z0-9_]+)"(?:\s*,\s*"([^"]*)")?\s*\)/g)].map((found) => ({
    name: found[1],
    fallback: found[2] ? found[2] : null,
    description: "",
    reader: component(path),
  }))
}

/**
 * Name the deployment target a file belongs to, such as server for deploy/server/docker-compose.yml.
 *
 * @param path Absolute path of a file in a target's folder.
 * @returns The target's folder name.
 */
function target(path: string): string {
  return basename(dirname(path))
}

/**
 * Read which compose services interpolate each variable, with compose's own defaults and required marks.
 *
 * @param paths The compose files, one per deployment target.
 * @returns Uses by variable name.
 */
function readCompose(paths: string[]): Map<string, ComposeUse> {
  const uses = new Map<string, ComposeUse>()
  for (const path of paths) {
    let service = ""
    let inServices = false
    for (const line of readFileSync(path, "utf8").split("\n")) {
      if (/^\S/.test(line)) inServices = line.startsWith("services:")
      const name = /^ {2}([\w.-]+):\s*$/.exec(line)
      if (inServices && name) service = name[1]
      for (const found of line.matchAll(/\$\{([A-Z0-9_]+)(?:(:?-)([^}]*)|(:?\?)[^}]*)?\}/g)) {
        const use = uses.get(found[1]) ?? { services: new Set<string>(), fallback: null, required: false }
        use.services.add(`${service || "top level"} in ${target(path)}`)
        if (found[2] && found[3] !== undefined && use.fallback === null) use.fallback = found[3] || null
        if (found[4]) use.required = true
        uses.set(found[1], use)
      }
    }
  }
  return uses
}

/**
 * Read an env example file into its variables in order, with the blank-line groups it keeps.
 *
 * @param path The file.
 * @returns Variable names with their example values and group numbers.
 */
function readExample(path: string): ExampleLine[] {
  const variables: ExampleLine[] = []
  let group = 0
  for (const line of readFileSync(path, "utf8").split("\n")) {
    if (!line.trim()) {
      group += 1
      continue
    }
    const found = /^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/.exec(line)
    if (found) variables.push({ name: found[1], example: found[2], group })
  }
  return variables
}

/**
 * Write the generated tables of the configuration reference: the first env example in full, and each other
 * target's example in full when it leaves out more than half of the first one's variables, or else as its
 * differences from the first one.
 *
 * @returns Number of variables, for the build log.
 */
export function generateConfiguration(): string {
  const examplePaths = locateAll(SOURCE_LISTS.envExamples)
  if (examplePaths.length === 0) return "skipped"
  const settingsPaths = locateAll(SOURCE_LISTS.settings)
  const readerPaths = locateAll(SOURCE_LISTS.readers, false)
  const composePaths = locateAll(SOURCE_LISTS.compose)

  const settings = [...settingsPaths.flatMap(readSettings), ...readerPaths.flatMap(readEnvironmentReads)]
  const compose = readCompose(composePaths)
  const byName = new Map<string, Setting[]>()
  for (const setting of settings) byName.set(setting.name, [...(byName.get(setting.name) ?? []), setting])

  const describe = (name: string) => {
    if (name.startsWith("COMPOSE_")) {
      return { description: "Docker Compose setting, read from the target's .env", readBy: "docker compose", fallback: "empty" }
    }
    const found = byName.get(name) ?? []
    const use = compose.get(name)
    const description = found.find((setting) => setting.description)?.description ?? ""
    const readers = [...new Set(found.map((setting) => setting.reader))]
    const services = use ? [...use.services] : []
    const fallback = found.find((setting) => setting.fallback !== null)?.fallback ?? use?.fallback ?? null
    return {
      description: description || (services.length ? "Interpolated by compose" : "Not read by the code the generator scans"),
      readBy: list([...readers, ...services.map((service) => `compose ${service}`)], "none found"),
      fallback: use?.required ? `required${fallback ? `, ${fallback}` : ""}` : (fallback ?? "empty"),
    }
  }

  const fullTable = (lines: ExampleLine[]) =>
    table(
      ["Name", "Example", "Default", "Meaning", "Read by"],
      lines.map((line) => {
        const info = describe(line.name)
        return [line.name, line.example || "empty", info.fallback, info.description, info.readBy]
      }),
    )

  const [referencePath, ...otherPaths] = examplePaths
  const reference = readExample(referencePath)
  const referenceExamples = new Map(reference.map((line) => [line.name, line.example]))
  const listed = new Set(reference.map((line) => line.name))

  const sections = [
    `Generated from the env example of every deployment target, ${escape(list(examplePaths.map(fromRoot)))}, the settings in ${escape(list(settingsPaths.map(fromRoot)))} and the compose files ${escape(list(composePaths.map(fromRoot)))}. Example is the line in the example file, Default what the code uses when the variable is unset.`,
    "",
    `## ${escape(target(referencePath))}`,
    "",
    `Every variable of ${escape(fromRoot(referencePath))}.`,
    "",
    fullTable(reference),
  ]
  for (const path of otherPaths) {
    const lines = readExample(path)
    const names = new Set(lines.map((line) => line.name))
    for (const name of names) listed.add(name)
    const leftOut = reference.filter((line) => !names.has(line.name)).map((line) => line.name)
    sections.push("", `## ${escape(target(path))}`, "")
    if (leftOut.length * 2 > reference.length) {
      sections.push(`Every variable of ${escape(fromRoot(path))}.`, "", fullTable(lines))
      continue
    }
    const changed = lines.filter((line) => referenceExamples.get(line.name) !== line.example)
    sections.push(
      `${escape(fromRoot(path))} keeps the lines of ${escape(fromRoot(referencePath))} except these${leftOut.length ? ` and leaves out ${escape(list(leftOut))}` : ""}.`,
      "",
      table(
        ["Name", "Example", `Example in ${target(referencePath)}`, "Meaning"],
        changed.map((line) => {
          const before = referenceExamples.get(line.name)
          return [line.name, line.example || "empty", before === undefined ? "not listed" : before || "empty", describe(line.name).description]
        }),
      ),
    )
  }

  const extra = [...byName.entries()]
    .filter(([name]) => !listed.has(name) && name.startsWith("SSS_"))
    .sort(([a], [b]) => a.localeCompare(b))
  sections.push(
    "",
    "## Settings without an example line",
    "",
    "The code reads these as well; they keep their defaults unless set in the environment or the env file.",
  )
  const readers = [...new Set(extra.flatMap(([, found]) => found.map((setting) => setting.reader)))].sort()
  for (const reader of readers) {
    sections.push(
      "",
      `### ${escape(reader)}`,
      "",
      table(
        ["Name", "Default", "Meaning"],
        extra
          .filter(([, found]) => found.some((setting) => setting.reader === reader))
          .map(([name]) => {
            const info = describe(name)
            return [name, info.fallback, info.description]
          }),
      ),
    )
  }
  writeGenerated("configuration.md", sections.join("\n"))
  return `${listed.size} example variables in ${examplePaths.length} targets, ${extra.length} further settings`
}
