import { Hono } from 'hono'
import { streamSSE } from 'hono/streaming'
import { readFileSync } from 'node:fs'
import { fromAnalysis } from '@/root'
import { spawnPython } from '@/shared/python'
import { decodeId, scanScripts } from '@/shared/scripts-scan'

interface RunState {
  scriptId: string
  logs: string[]
  done: boolean
  exitCode: number | null
  startedAt: string
}

const runs = new Map<string, RunState>()
const lastRun = new Map<string, string>() // scriptId -> ISO
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

/** Auto-fill the common required CLI args so a web "run" works without a form.
 * --output → timestamped path under exports/; --month → previous month. */
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
    const state: RunState = { scriptId: id, logs: [], done: false, exitCode: null, startedAt: new Date().toISOString() }
    runs.set(runId, state)
    lastRun.set(id, state.startedAt)

    state.logs.push(`[hub] venv/bin/python ${rel}${extra.length ? ` ${extra.join(' ')}` : ''}`)
    const proc = spawnPython([rel, ...extra])
    void (async () => {
      const dec = new TextDecoder()
      const pump = async (stream: ReadableStream<Uint8Array>) => {
        for await (const chunk of stream) {
          for (const line of dec.decode(chunk).split('\n'))
            if (line.length)
              state.logs.push(line)
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
      }
    })()

    return c.json({ runId })
  })

export const runsApi = new Hono()
  .get('/:runId/stream', (c) => {
    const runId = c.req.param('runId')
    const state = runs.get(runId)
    if (!state)
      return c.json({ error: 'no such run' }, 404)
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
