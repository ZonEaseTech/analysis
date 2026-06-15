import { readdirSync, readFileSync } from 'node:fs'
import { fromAnalysis } from '@/root'

export interface ScriptMeta {
  id: string
  path: string
  group: string
  name: string
  whoAsked: string
  what: string
}

const GROUPS: { group: string, dir: string }[] = [
  { group: 'bq_reports', dir: 'bq_reports' },
  { group: 'scripts', dir: 'scripts' },
  { group: 'adhoc', dir: 'scripts/adhoc' },
]

export function encodeId(rel: string): string {
  return Buffer.from(rel).toString('base64url')
}
export function decodeId(id: string): string {
  return Buffer.from(id, 'base64url').toString('utf8')
}

function parseMeta(text: string): { whoAsked: string, what: string } {
  const head = text.split('\n').slice(0, 25)
  let whoAsked = ''
  let what = ''
  for (const line of head) {
    const who = line.match(/#\s*谁问的[:：]\s*(.+)/)
    if (who?.[1])
      whoAsked = who[1].trim()
    const w = line.match(/#\s*问什么[:：]\s*(.+)/)
    if (w?.[1])
      what = w[1].trim()
  }
  if (!what) {
    const doc = text.match(/"""([\s\S]*?)"""/)
    const first = doc?.[1]?.trim().split('\n')[0]?.trim()
    if (first)
      what = first
  }
  return { whoAsked, what }
}

export function scanScripts(): ScriptMeta[] {
  const out: ScriptMeta[] = []
  for (const { group, dir } of GROUPS) {
    let files: string[] = []
    try {
      files = readdirSync(fromAnalysis(dir)).filter(f => f.endsWith('.py') && f !== '__init__.py')
    }
    catch {
      continue
    }
    for (const f of files.sort()) {
      const rel = `${dir}/${f}`
      let text = ''
      try {
        text = readFileSync(fromAnalysis(rel), 'utf8')
      }
      catch {
        continue
      }
      const { whoAsked, what } = parseMeta(text)
      out.push({ id: encodeId(rel), path: rel, group, name: f.replace(/\.py$/, ''), whoAsked, what })
    }
  }
  return out
}
