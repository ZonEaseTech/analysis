import process from 'node:process'
import { consola } from 'consola'
import { createApp } from './app'
import { loadConfig } from './config'
import { getDb, migrateDb } from './db'

const config = loadConfig()
migrateDb(getDb())
const app = createApp(config)

const server = Bun.serve({
  port: config.PORT,
  hostname: config.HOST,
  fetch: app.fetch,
  idleTimeout: 255, // long-running script log streams
})

consola.success(`Wallace Report Hub API → http://${config.HOST}:${server.port}`)

for (const sig of ['SIGINT', 'SIGTERM'] as const) {
  process.on(sig, () => {
    consola.info(`${sig} received, shutting down`)
    server.stop()
    process.exit(0)
  })
}
