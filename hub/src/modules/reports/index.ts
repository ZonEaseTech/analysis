import { existsSync, readdirSync, statSync } from 'node:fs'
import { basename } from 'node:path'
import { Hono } from 'hono'
import { fromAnalysis } from '@/root'
import { runPythonJSON } from '@/shared/python'

const EXPORTS = fromAnalysis('exports')
const HUB_PREVIEW = fromAnalysis('hub', 'scripts', 'xlsx_preview.py')

interface ReportFile { file: string, sizeKb: number, mtime: string, month: string, version: number | null }

function meta(file: string): { name: string, month: string, version: number | null } {
  const month = file.match(/(\d{4})-?(\d{2})/)?.slice(1, 3).join('-') ?? ''
  const version = file.match(/_?v(\d+)/)?.[1]
  const name = file
    .replace(/\.xlsx$/i, '')
    .replace(/_?v\d.*$/i, '')
    .replace(/\d{4}-?\d{2,}/g, '')
    .replace(/[_-]+$/g, '')
    .replace(/[_-]+/g, '_')
    .trim() || file.replace(/\.xlsx$/i, '')
  return { name, month, version: version ? Number(version) : null }
}

function safeExport(file: string): string {
  const b = basename(file)
  return fromAnalysis('exports', b)
}

export const reports = new Hono()
  .get('/', (c) => {
    let files: string[] = []
    try {
      files = readdirSync(EXPORTS).filter(f => f.toLowerCase().endsWith('.xlsx'))
    }
    catch { /* ignore */ }
    const groups = new Map<string, ReportFile[]>()
    for (const f of files) {
      const m = meta(f)
      const st = statSync(fromAnalysis('exports', f))
      const rf: ReportFile = { file: f, sizeKb: Math.round(st.size / 1024), mtime: st.mtime.toISOString(), month: m.month, version: m.version }
      const arr = groups.get(m.name) ?? []
      arr.push(rf)
      groups.set(m.name, arr)
    }
    const out = [...groups.entries()]
      .map(([name, fs]) => ({ name, files: fs.sort((a, b) => b.mtime.localeCompare(a.mtime)) }))
      .sort((a, b) => a.name.localeCompare(b.name))
    return c.json({ groups: out })
  })
  .get('/preview', async (c) => {
    const file = c.req.query('file') ?? ''
    const sheet = c.req.query('sheet') ?? '0'
    const offset = c.req.query('offset') ?? '0'
    const limit = c.req.query('limit') ?? '100'
    const abs = safeExport(file)
    if (!existsSync(abs))
      return c.json({ error: 'file not found' }, 404)
    try {
      const data = await runPythonJSON([HUB_PREVIEW, abs, sheet, offset, limit])
      return c.json(data as object)
    }
    catch (e) {
      return c.json({ error: String(e) }, 500)
    }
  })
  .get('/download', (c) => {
    const file = c.req.query('file') ?? ''
    const abs = safeExport(file)
    if (!existsSync(abs))
      return c.json({ error: 'file not found' }, 404)
    c.header('Content-Disposition', `attachment; filename="${encodeURIComponent(basename(file))}"`)
    return new Response(Bun.file(abs))
  })
