import { integer, sqliteTable, text } from 'drizzle-orm/sqlite-core'

/** One execution of a report script (persisted run history). */
export const runs = sqliteTable('runs', {
  id: text('id').primaryKey(), // run-<seq>-<epoch>
  scriptId: text('script_id').notNull(),
  scriptName: text('script_name').notNull(),
  scriptPath: text('script_path').notNull(),
  args: text('args'), // resolved CLI args, space-joined
  startedAt: text('started_at').notNull(), // ISO timestamp
  finishedAt: text('finished_at'), // ISO timestamp, null while running
  exitCode: integer('exit_code'), // null while running
  status: text('status').notNull().$type<'running' | 'done' | 'error'>(),
  log: text('log'), // full captured log, flushed on completion
})

export type RunRow = typeof runs.$inferSelect
export type NewRunRow = typeof runs.$inferInsert
