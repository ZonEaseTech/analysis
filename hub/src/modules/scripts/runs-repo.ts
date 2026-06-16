import type { DB } from '@/db'
import type { NewRunRow, RunRow, RunValidation } from '@/db/schema'
import { desc, eq, sql } from 'drizzle-orm'
import { getDb } from '@/db'
import { runs } from '@/db/schema'

/** Persistence for script run history. Owns all DB access for `runs`. */
export class RunsRepo {
  constructor(private db: DB = getDb()) {}

  create(row: NewRunRow): void {
    this.db.insert(runs).values(row).run()
  }

  finish(id: string, patch: { finishedAt: string, exitCode: number | null, status: RunRow['status'], log: string, validation: RunValidation | null }): void {
    this.db.update(runs).set(patch).where(eq(runs.id, id)).run()
  }

  get(id: string): RunRow | undefined {
    return this.db.select().from(runs).where(eq(runs.id, id)).get()
  }

  list(limit = 100): RunRow[] {
    return this.db.select().from(runs).orderBy(desc(runs.startedAt)).limit(limit).all()
  }

  /** scriptId -> most recent startedAt, for the script list "last run" column. */
  lastRunByScript(): Map<string, string> {
    const rows = this.db
      .select({ scriptId: runs.scriptId, last: sql<string>`max(${runs.startedAt})` })
      .from(runs)
      .groupBy(runs.scriptId)
      .all()
    return new Map(rows.map(r => [r.scriptId, r.last]))
  }
}
