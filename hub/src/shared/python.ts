import process from 'node:process'
import { ANALYSIS_ROOT, fromAnalysis } from '@/root'

/** venv python — all report scripts must run through it (project rule). */
export const PYTHON: string = fromAnalysis('venv', 'bin', 'python')

/**
 * Spawn a python process rooted at the analysis repo.
 * PYTHONPATH=<repo root> so absolute imports like `from bq_reports.utils...`
 * resolve when running a script by file path (mirrors `python -m bq_reports.x`).
 */
export function spawnPython(args: string[]): Bun.Subprocess<'ignore', 'pipe', 'pipe'> {
  return Bun.spawn([PYTHON, ...args], {
    cwd: ANALYSIS_ROOT,
    env: { ...process.env, PYTHONPATH: ANALYSIS_ROOT },
    stdout: 'pipe',
    stderr: 'pipe',
  })
}

/** Run python to completion, capture stdout. Throws on non-zero exit. */
export async function runPythonJSON<T>(args: string[]): Promise<T> {
  const proc = spawnPython(args)
  const [out, err, code] = await Promise.all([
    new Response(proc.stdout).text(),
    new Response(proc.stderr).text(),
    proc.exited,
  ])
  if (code !== 0)
    throw new Error(`python exited ${code}: ${err.slice(0, 500)}`)
  return JSON.parse(out) as T
}
