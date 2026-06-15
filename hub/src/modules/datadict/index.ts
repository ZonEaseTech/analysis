import { Hono } from 'hono'
import { readFileSync } from 'node:fs'
import { fromAnalysis } from '@/root'

interface Field { name: string, type: string, comment: string }
interface Table { table: string, description: string, fields: Field[] }
interface Metric { name: string, definition: string, formula: string }

function readDoc(rel: string): string {
  try {
    return readFileSync(fromAnalysis(rel), 'utf8')
  }
  catch {
    return ''
  }
}

function parseTables(md: string): Table[] {
  const tables: Table[] = []
  const sections = md.split(/\n###\s+/).slice(1)
  for (const sec of sections) {
    const title = sec.split('\n')[0] ?? ''
    const tm = title.match(/(ttpos_\w+)\s*（?([^）\n]*)）?/)
    if (!tm?.[1])
      continue
    const table = tm[1]
    const description = (tm[2] ?? '').trim()
    const fields: Field[] = []
    for (const line of sec.split('\n')) {
      if (!line.trim().startsWith('|'))
        continue
      const cols = line.split('|').map(s => s.trim())
      const name = cols[1]
      if (!name || name === '字段' || name.startsWith('---'))
        continue
      fields.push({ name, type: cols[2] ?? '', comment: cols[3] ?? '' })
    }
    if (fields.length)
      tables.push({ table, description, fields })
  }
  return tables
}

function parseMetrics(md: string): Metric[] {
  const metrics: Metric[] = []
  const sections = md.split(/\n##\s+/).slice(1)
  for (const sec of sections) {
    const name = (sec.split('\n')[0] ?? '').trim()
    if (!name || name.startsWith('速查'))
      continue
    const def = sec.match(/\*\*业务含义\*\*[：:]\s*(.+)/)?.[1]?.trim() ?? ''
    const formula = sec.match(/\*\*公式\*\*[：:]\s*`?([^`\n]+)`?/)?.[1]?.trim() ?? ''
    if (def || formula)
      metrics.push({ name, definition: def, formula })
  }
  return metrics
}

export const datadict = new Hono()
  .get('/tables', c => c.json({ tables: parseTables(readDoc('docs/bq-schema-reference.md')) }))
  .get('/metrics', c => c.json({ metrics: parseMetrics(readDoc('docs/metrics-catalog.md')) }))
