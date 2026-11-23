import defaultMdxComponents from "fumadocs-ui/mdx"
import { Card, Cards } from "fumadocs-ui/components/card"
import { Callout } from "fumadocs-ui/components/callout"
import { Step, Steps } from "fumadocs-ui/components/steps"
import { Tab, Tabs } from "fumadocs-ui/components/tabs"
import type { MDXComponents } from "mdx/types"

import { ProductMap } from "@/components/product-map"
import { Status } from "@/components/mdx/status"

/**
 * The components MDX pages can use, on top of Fumadocs' defaults.
 *
 * @param components Components that replace or extend them for one page.
 * @returns The component map.
 */
export function getMDXComponents(components?: MDXComponents) {
  return {
    ...defaultMdxComponents,
    Callout,
    Card,
    Cards,
    ProductMap,
    Status,
    Step,
    Steps,
    Tab,
    Tabs,
    ...components,
  } satisfies MDXComponents
}

declare global {
  type MDXProvidedComponents = ReturnType<typeof getMDXComponents>
}
