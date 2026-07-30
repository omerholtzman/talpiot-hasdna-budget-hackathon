import { useEffect, useState } from 'react'
import { fetchDashboardList } from './api'
import type { DashboardMeta } from './types'

interface UseDashboardsResult {
  dashboards: DashboardMeta[]
  loading: boolean
  error: string | null
}

export function useDashboards(): UseDashboardsResult {
  const [dashboards, setDashboards] = useState<DashboardMeta[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()

    setLoading(true)
    setError(null)

    fetchDashboardList(controller.signal)
      .then(setDashboards)
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === 'AbortError') return
        setError(err instanceof Error ? err.message : 'Failed to load dashboards')
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })

    return () => controller.abort()
  }, [])

  return { dashboards, loading, error }
}
