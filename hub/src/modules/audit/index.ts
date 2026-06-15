import { Hono } from 'hono'
import { basename } from 'node:path'
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { fromAnalysis } from '@/root'

const EXPORTS = fromAnalysis('exports')

interface AuditFile { file: string, kind: 'csv' | 'txt', sizeKb: number }
interface AuditRun { dir: string, files: AuditFile[] }

function listRuns(): AuditRun[] {
  let dirs: string[] = []
  try {
    dirs = readdirSync(EXPORTS).filter((d) => {
      if (!d.startsWith('audit'))
        return false
      try {
        return statSync(fromAnalysis('exports', d)).isDirectory()
      }
      catch {
        return false
      }
    })
  }
  catch {
    return []
  }
  return dirs.sort().map((dir) => {
    let files: AuditFile[] = []
    try {
      files = readdirSync(fromAnalysis('exports', dir))
        .filter(f => f.endsWith('.csv') || f.endsWith('.txt'))
        .sort()
        .map((f) => {
          const st = statSync(fromAnalysis('exports', dir, f))
          return { file: f, kind: f.endsWith('.csv') ? 'csv' as const : 'txt' as const, sizeKb: Math.round(st.size / 1024) }
        })
    }
    catch { /* ignore */ }
    return { dir, files }
  })
}

function parseCsv(text: string, maxRows = 500): { header: string[], rows: string[][] } {
  const lines = text.replace(/^﻿/, '').split(/\r?\n/).filter(l => l.length)
  const split = (l: string): string[] => {
    const out: string[] = []
    let cur = ''
    let q = false
    for (const ch of l) {
      if (ch === '"')
        q = !q
      else if (ch === ',' && !q) {
        out.push(cur)
        cur = ''
      }
      else cur += ch
    }
    out.push(cur)
    return out
  }
  const header = lines.length ? split(lines[0] ?? '') : []
  const rows = lines.slice(1, 1 + maxRows).map(split)
  return { header, rows }
}

export const audit = new Hono()
  .get('/runs', c => c.json({ runs: listRuns() }))
  .get('/file', (c) => {
    const dir = basename(c.req.query('dir') ?? '')
    const file = basename(c.req.query('file') ?? '')
    const abs = fromAnalysis('exports', dir, file)
    if (!existsSync(abs))
      return c.json({ error: 'file not found' }, 404)
    const text = readFileSync(abs, 'utf8')
    if (file.endsWith('.csv'))
      return c.json({ kind: 'csv', ...parseCsv(text) })
    return c.json({ kind: 'txt', text })
  })
