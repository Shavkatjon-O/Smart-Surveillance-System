import { generateArchitecture, generateContributing } from "./documents.ts"
import { generateCameras } from "./cameras.ts"
import { generateChangelog } from "./changelog.ts"
import { generateConfiguration } from "./configuration.ts"
import { generateDiagrams } from "./diagrams.ts"
import { generateOpenAPI } from "./openapi.ts"
import { generateRoutes } from "./routes.ts"
import { repoRoot } from "./sources.ts"

/**
 * Generate every page and table that comes from the repository instead of being written by hand.
 *
 * @returns Nothing; it throws when a required input is missing.
 */
async function main(): Promise<void> {
  console.log(`[docs] generating from ${repoRoot}`)
  const steps: [string, () => string | Promise<string>][] = [
    ["diagrams", generateDiagrams],
    ["cameras", generateCameras],
    ["configuration", generateConfiguration],
    ["api", generateOpenAPI],
    ["analytics contract", generateRoutes],
    ["architecture", generateArchitecture],
    ["contributing", generateContributing],
    ["changelog", generateChangelog],
  ]
  for (const [name, step] of steps) console.log(`[docs] ${name}: ${await step()}`)
}

await main()
