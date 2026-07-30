import { Router } from 'express'
import fs from 'node:fs/promises'
import { listDashboards, readDashboard } from '../lib/scanDashboards.js'
import { resolveSlugPath } from '../lib/resolveSlugPath.js'

export const dashboardsRouter = Router()

dashboardsRouter.get('/', async (req, res) => {
  const dashboards = await listDashboards()
  res.json({ dashboards })
})

dashboardsRouter.get('/*slug', async (req, res) => {
  const slugSegments = req.params.slug
  const filePath = resolveSlugPath(slugSegments)

  if (!filePath) {
    res.status(404).json({ error: 'Dashboard not found' })
    return
  }

  try {
    await fs.access(filePath)
  } catch {
    res.status(404).json({ error: 'Dashboard not found' })
    return
  }

  try {
    const dashboard = await readDashboard(filePath, slugSegments.join('/'))
    res.json(dashboard)
  } catch (error) {
    console.error(`[dashboards] failed to read ${filePath}:`, error.message)
    res.status(500).json({ error: 'Failed to read dashboard' })
  }
})
