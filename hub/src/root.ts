import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

// hub/src/root.ts → hub/ is one up, the analysis repo root is two up.
const here = dirname(fileURLToPath(import.meta.url))

/** hub/ project root */
export const HUB_ROOT: string = resolve(here, '..')
/** analysis repo root — where scripts/, exports/, docs/, semantic/ live */
export const ANALYSIS_ROOT: string = resolve(here, '..', '..')

export function fromAnalysis(...parts: string[]): string {
  return resolve(ANALYSIS_ROOT, ...parts)
}
