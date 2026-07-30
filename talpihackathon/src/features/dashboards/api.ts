import type { DashboardDetail, DashboardListResponse, DashboardMeta } from './types'

const API_BASE = `${import.meta.env.BASE_URL}api`

async function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal })
  if (!response.ok) {
    throw new Error(`Request to ${url} failed with status ${response.status}`)
  }
  return response.json() as Promise<T>
}

export async function fetchDashboardList(signal?: AbortSignal): Promise<DashboardMeta[]> {
  const data = await fetchJson<DashboardListResponse>(`${API_BASE}/dashboards-list.json`, signal)
  return data.dashboards
}

export async function fetchDashboardBySlug(
  slug: string,
  signal?: AbortSignal
): Promise<DashboardDetail> {
  return fetchJson<DashboardDetail>(`${API_BASE}/dashboards/${slug}.json`, signal)
}
