import type { RunValidation } from '@/db/schema'
import { readFileSync } from 'node:fs'
import { Hono } from 'hono'
import { streamSSE } from 'hono/streaming'
import { fromAnalysis } from '@/root'
import { spawnPython } from '@/shared/python'
import { decodeId, scanScripts } from '@/shared/scripts-scan'
import { RunsRepo } from './runs-repo'

const VALIDATION_MARKER = '[[hub:validation]]'

/**
 * Aggregate the 对账 summary the report scripts emit via print_result()'s
 * `[[hub:validation]] {json}` marker lines. null when no validators ran.
 */
export function parseValidation(logs: string[]): RunValidation | null {
  const marks = logs.filter(l => l.startsWith(VALIDATION_MARKER))
  if (marks.length === 0)
    return null
  const acc: RunValidation = { totalRows: 0, mustFix: 0, needsReview: 0 }
  for (const line of marks) {
    try {
      const o = JSON.parse(line.slice(VALIDATION_MARKER.length).trim()) as Record<string, number>
      acc.totalRows += Number(o.total_rows) || 0
      acc.mustFix += Number(o.must_fix) || 0
      acc.needsReview += Number(o.needs_review) || 0
    }
    catch { /* ignore malformed marker */ }
  }
  return acc
}

/**
 * Live, in-memory state for an active run — drives the SSE stream while the
 * process runs. The durable record (incl. final log) lives in SQLite.
 */
interface LiveRun {
  logs: string[]
  done: boolean
  exitCode: number | null
}

const live = new Map<string, LiveRun>()
const repo = new RunsRepo()
let runSeq = 0

const pad = (n: number): string => String(n).padStart(2, '0')
function stamp(): string {
  const d = new Date()
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`
}
function lastMonth(): string {
  const d = new Date()
  d.setDate(1)
  d.setMonth(d.getMonth() - 1)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}`
}

/**
 * Auto-fill the common required CLI args so a web "run" works without a form.
 * --output → timestamped path under exports/; --month → previous month.
 */
function defaultArgs(name: string, source: string): string[] {
  const args: string[] = []
  if (source.includes('--output'))
    args.push('--output', `exports/${name}_${stamp()}.xlsx`)
  if (source.includes('--month'))
    args.push('--month', lastMonth())
  return args
}

export const scripts = new Hono()
  .get('/', (c) => {
    const lastRun = repo.lastRunByScript()
    const list = scanScripts().map(s => ({ ...s, lastRunAt: lastRun.get(s.id) ?? null }))
    return c.json({ scripts: list })
  })
  .get('/:id', (c) => {
    const id = c.req.param('id')
    const meta = scanScripts().find(s => s.id === id)
    if (!meta)
      return c.json({ error: 'script not found' }, 404)
    let source = ''
    try {
      source = readFileSync(fromAnalysis(meta.path), 'utf8')
    }
    catch { /* ignore */ }
    return c.json({ ...meta, source })
  })
  .post('/:id/run', (c) => {
    const id = c.req.param('id')
    const meta = scanScripts().find(s => s.id === id)
    if (!meta)
      return c.json({ error: 'script not found' }, 404)
    const rel = decodeId(id)
    let source = ''
    try {
      source = readFileSync(fromAnalysis(rel), 'utf8')
    }
    catch { /* ignore */ }
    const extra = defaultArgs(meta.name, source)
    const runId = `run-${++runSeq}-${Date.now()}`
    const startedAt = new Date().toISOString()
    const argsStr = extra.join(' ')

    const state: LiveRun = { logs: [], done: false, exitCode: null }
    live.set(runId, state)
    state.logs.push(`[hub] venv/bin/python ${rel}${argsStr ? ` ${argsStr}` : ''}`)
    repo.create({
      id: runId,
      scriptId: id,
      scriptName: meta.name,
      scriptPath: rel,
      args: argsStr || null,
      startedAt,
      finishedAt: null,
      exitCode: null,
      status: 'running',
      log: null,
    })

    const proc = spawnPython([rel, ...extra])
    void (async () => {
      const dec = new TextDecoder()
      const pump = async (stream: ReadableStream<Uint8Array>) => {
        for await (const chunk of stream) {
          for (const line of dec.decode(chunk).split('\n')) {
            if (line.length)
              state.logs.push(line)
          }
        }
      }
      try {
        await Promise.all([pump(proc.stdout), pump(proc.stderr)])
        state.exitCode = await proc.exited
      }
      catch (e) {
        state.logs.push(`[hub] run error: ${String(e)}`)
        state.exitCode = -1
      }
      finally {
        state.done = true
        repo.finish(runId, {
          finishedAt: new Date().toISOString(),
          exitCode: state.exitCode,
          status: state.exitCode === 0 ? 'done' : 'error',
          log: state.logs.join('\n'),
          validation: parseValidation(state.logs),
        })
      }
    })()

    return c.json({ runId })
  })

export const runsApi = new Hono()
  .get('/', (c) => {
    const rows = repo.list().map(({ log: _log, ...r }) => r)
    return c.json({ runs: rows })
  })
  .get('/:runId', (c) => {
    const row = repo.get(c.req.param('runId'))
    if (!row)
      return c.json({ error: 'no such run' }, 404)
    return c.json(row)
  })
  .get('/:runId/stream', (c) => {
    const runId = c.req.param('runId')
    const state = live.get(runId)
    // Finished run (or post-restart): replay the persisted log, then close.
    if (!state) {
      const row = repo.get(runId)
      if (!row)
        return c.json({ error: 'no such run' }, 404)
      return streamSSE(c, async (stream) => {
        for (const line of (row.log ?? '').split('\n'))
          await stream.writeSSE({ data: line })
        await stream.writeSSE({ event: 'done', data: JSON.stringify({ exitCode: row.exitCode }) })
      })
    }
    return streamSSE(c, async (stream) => {
      let i = 0
      while (true) {
        while (i < state.logs.length) {
          await stream.writeSSE({ data: state.logs[i] ?? '' })
          i++
        }
        if (state.done) {
          await stream.writeSSE({ event: 'done', data: JSON.stringify({ exitCode: state.exitCode }) })
          break
        }
        await stream.sleep(300)
      }
    })
  })
