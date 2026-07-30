export interface DashboardMeta {
  slug: string
  title: string
  created: string | null
  updated: string | null
  model: string | null
  path: string | null
}

export interface DashboardDetail extends DashboardMeta {
  content: string
}

export interface DashboardListResponse {
  dashboards: DashboardMeta[]
}
