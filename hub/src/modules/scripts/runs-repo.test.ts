import { Database } from 'bun:sqlite'
import { beforeEach, expect, test } from 'bun:test'
import { drizzle } from 'drizzle-orm/bun-sqlite'
import { migrate } from 'drizzle-orm/bun-sqlite/migrator'
import * as schema from '@/db/schema'
import { HUB_ROOT } from '@/root'
import { RunsRepo } from './runs-repo'

function memRepo(): RunsRepo {
  const db = drizzle(new Database(':memory:'), { schema })
  migrate(db, { migrationsFolder: `${HUB_ROOT}/drizzle` })
  return new RunsRepo(db)
}

let repo: RunsRepo

beforeEach(() => {
  repo = memRepo()
})

function seed(id: string, scriptId: string, startedAt: string): void {
  repo.create({
    id,
    scriptId,
    scriptName: scriptId,
    scriptPath: `bq_reports/${scriptId}.py`,
    args: '--month 2026-05',
    startedAt,
    finishedAt: null,
    exitCode: null,
    status: 'running',
    log: null,
  })
}

test('create then get returns the row', () => {
  seed('run-1', 'alpha', '2026-06-15T01:00:00.000Z')
  const row = repo.get('run-1')
  expect(row?.scriptId).toBe('alpha')
  expect(row?.status).toBe('running')
  expect(row?.exitCode).toBeNull()
})

test('finish updates exit code, status and log', () => {
  seed('run-1', 'alpha', '2026-06-15T01:00:00.000Z')
  repo.finish('run-1', {
    finishedAt: '2026-06-15T01:01:00.000Z',
    exitCode: 0,
    status: 'done',
    log: 'line1\nline2',
  })
  const row = repo.get('run-1')
  expect(row?.status).toBe('done')
  expect(row?.exitCode).toBe(0)
  expect(row?.log).toBe('line1\nline2')
})

test('list returns most recent first', () => {
  seed('run-1', 'alpha', '2026-06-15T01:00:00.000Z')
  seed('run-2', 'beta', '2026-06-15T03:00:00.000Z')
  seed('run-3', 'alpha', '2026-06-15T02:00:00.000Z')
  expect(repo.list().map(r => r.id)).toEqual(['run-2', 'run-3', 'run-1'])
})

test('lastRunByScript keeps the max startedAt per script', () => {
  seed('run-1', 'alpha', '2026-06-15T01:00:00.000Z')
  seed('run-2', 'alpha', '2026-06-15T05:00:00.000Z')
  seed('run-3', 'beta', '2026-06-15T02:00:00.000Z')
  const m = repo.lastRunByScript()
  expect(m.get('alpha')).toBe('2026-06-15T05:00:00.000Z')
  expect(m.get('beta')).toBe('2026-06-15T02:00:00.000Z')
})
