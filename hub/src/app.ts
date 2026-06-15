import { Hono } from 'hono'
import { serveStatic } from 'hono/bun'
import { cors } from 'hono/cors'
import { logger } from 'hono/logger'
import { requestId } from 'hono/request-id'
import { secureHeaders } from 'hono/secure-headers'
import type { Config } from './config'
import { existsSync } from 'node:fs'
import { audit } from './modules/audit'
import { datadict } from './modules/datadict'
import { health } from './modules/health'
import { reports } from './modules/reports'
import { runsApi, scripts } from './modules/scripts'
import { HUB_ROOT } from './root'

export function createApp(_config: Config) {
  const app = new Hono()

  // edge middleware
  app.use('*', requestId())
  app.use('*', secureHeaders())
  app.use('*', cors())
  app.use('*', logger())

  // API routes (mounted under /api)
  const api = new Hono()
  api.route('/health', health)
  api.route('/scripts', scripts)
  api.route('/runs', runsApi)
  api.route('/reports', reports)
  api.route('/datadict', datadict)
  api.route('/audit', audit)
  app.route('/api', api)

  // built SPA (prod): hub/web/dist
  const webDist = `${HUB_ROOT}/web/dist`
  if (existsSync(webDist)) {
    app.use('/*', serveStatic({ root: './web/dist' }))
    app.get('*', serveStatic({ path: './web/dist/index.html' }))
  }
  else {
    app.get('/', c => c.text('Report Hub API up. Frontend not built yet (hub/web/dist missing). Dev: run web on Vite.'))
  }

  return app
}
