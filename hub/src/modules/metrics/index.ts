import { Hono } from 'hono'
import { fromAnalysis } from '@/root'
import { runPythonJSON } from '@/shared/python'

const METRICS_DUMP = fromAnalysis('hub', 'scripts', 'metrics_dump.py')

// Cache the registry dump in-process; it only changes when the yaml on disk
// changes, and the page is read-heavy. 60s TTL keeps edits visible during dev.
let cache: { at: number, data: unknown } | null = null
const TTL_MS = 60_000

export const metrics = new Hono()
  .get('/', async (c) => {
    const now = Date.now()
    if (cache && now - cache.at < TTL_MS)
      return c.json(cache.data as object)
    try {
      const data = await runPythonJSON([METRICS_DUMP])
      cache = { at: now, data }
      return c.json(data as object)
    }
    catch (e) {
      return c.json({ error: String(e) }, 500)
    }
  })
