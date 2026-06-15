import type { Context } from 'hono'
import { Hono } from 'hono'
import { fromAnalysis } from '@/root'
import { runPythonJSON } from '@/shared/python'

const METRICS_DUMP = fromAnalysis('hub', 'scripts', 'metrics_dump.py')
const BINDINGS_DUMP = fromAnalysis('hub', 'scripts', 'report_bindings_dump.py')

// Cache the python dumps in-process; they only change when yaml on disk
// changes, and the pages are read-heavy. 60s TTL keeps edits visible in dev.
const TTL_MS = 60_000
const cache = new Map<string, { at: number, data: unknown }>()

async function cachedDump(c: Context, key: string, script: string) {
  const hit = cache.get(key)
  const now = Date.now()
  if (hit && now - hit.at < TTL_MS)
    return c.json(hit.data as object)
  try {
    const data = await runPythonJSON([script])
    cache.set(key, { at: now, data })
    return c.json(data as object)
  }
  catch (e) {
    return c.json({ error: String(e) }, 500)
  }
}

export const metrics = new Hono()
  .get('/', c => cachedDump(c, 'catalog', METRICS_DUMP))
  .get('/bindings', c => cachedDump(c, 'bindings', BINDINGS_DUMP))
