import { Router } from 'express'
import fs from 'node:fs/promises'
import { listDashboards, readDashboard } from '../lib/scanDashboards.js'
import { resolveSlugPath } from '../lib/resolveSlugPath.js'

export const dashboardsRouter = Router()

dashboardsRouter.get('/dashboards-list.json', async (req, res) => {
  const dashboards = await listDashboards()
  res.json({ dashboards })
})

dashboardsRouter.get('/dashboards/*slug', async (req, res) => {
  const rawSegments = req.params.slug
  const lastSegment = rawSegments[rawSegments.length - 1] ?? ''
  const cleanedLast = lastSegment.endsWith('.json')
    ? lastSegment.slice(0, -'.json'.length)
    : lastSegment
  const slugSegments = [...rawSegments.slice(0, -1), cleanedLast]

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
