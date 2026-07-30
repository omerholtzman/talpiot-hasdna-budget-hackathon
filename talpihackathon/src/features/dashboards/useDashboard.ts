import { useEffect, useState } from 'react'
import { fetchDashboardBySlug } from './api'
import type { DashboardDetail } from './types'

interface UseDashboardResult {
  dashboard: DashboardDetail | null
  loading: boolean
  error: string | null
  notFound: boolean
}

export function useDashboard(slug: string): UseDashboardResult {
  const [dashboard, setDashboard] = useState<DashboardDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notFound, setNotFound] = useState(false)

  useEffect(() => {
    const controller = new AbortController()

    setLoading(true)
    setError(null)
    setNotFound(false)
    setDashboard(null)

    fetchDashboardBySlug(slug, controller.signal)
      .then(setDashboard)
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === 'AbortError') return
        if (err instanceof Error && err.message.includes('status 404')) {
          setNotFound(true)
          return
        }
        setError(err instanceof Error ? err.message : 'Failed to load dashboard')
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })

    return () => controller.abort()
  }, [slug])

  return { dashboard, loading, error, notFound }
}
