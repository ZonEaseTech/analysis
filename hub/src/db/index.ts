import { mkdirSync } from 'node:fs'
import { dirname } from 'node:path'
import { Database } from 'bun:sqlite'
import { drizzle } from 'drizzle-orm/bun-sqlite'
import { migrate } from 'drizzle-orm/bun-sqlite/migrator'
import { HUB_ROOT } from '@/root'
import * as schema from './schema'

export type DB = ReturnType<typeof createDb>

const DB_PATH = `${HUB_ROOT}/.data/hub.db`
const MIGRATIONS_DIR = `${HUB_ROOT}/drizzle`

/**
 * Single DB bootstrap path: ensure dir, open the bun:sqlite file in WAL mode,
 * bind the schema. Synchronous — the hub is single-instance local state.
 */
export function createDb(dbPath: string = DB_PATH) {
  mkdirSync(dirname(dbPath), { recursive: true })
  const sqlite = new Database(dbPath)
  sqlite.exec('PRAGMA journal_mode = WAL;')
  return drizzle(sqlite, { schema })
}

export function migrateDb(db: DB): void {
  migrate(db, { migrationsFolder: MIGRATIONS_DIR })
}

// Process-wide singleton — the hub is single-instance local state.
let _db: DB | null = null
export function getDb(): DB {
  if (!_db)
    _db = createDb()
  return _db
}
