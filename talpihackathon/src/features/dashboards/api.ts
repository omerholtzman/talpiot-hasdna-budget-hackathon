import type { DashboardDetail, DashboardListResponse, DashboardMeta } from './types'

async function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal })
  if (!response.ok) {
    throw new Error(`Request to ${url} failed with status ${response.status}`)
  }
  return response.json() as Promise<T>
}

export async function fetchDashboardList(signal?: AbortSignal): Promise<DashboardMeta[]> {
  const data = await fetchJson<DashboardListResponse>('/api/dashboards', signal)
  return data.dashboards
}

export async function fetchDashboardBySlug(
  slug: string,
  signal?: AbortSignal
): Promise<DashboardDetail> {
  return fetchJson<DashboardDetail>(`/api/dashboards/${slug}`, signal)
}
