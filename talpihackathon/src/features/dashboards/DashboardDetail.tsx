import { useParams } from 'react-router'
import { useDashboard } from './useDashboard'
import { MarkdownRenderer } from './markdown/MarkdownRenderer'
import './DashboardDetail.css'

function formatDate(value: string | null): string | null {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric' })
}

export function DashboardDetail() {
  const params = useParams()
  const slug = params['*'] ?? ''
  const { dashboard, loading, error, notFound } = useDashboard(slug)

  if (loading) {
    return (
      <div className="dashboard-detail-status">
        <p>Loading…</p>
      </div>
    )
  }

  if (notFound) {
    return (
      <div className="dashboard-detail-status">
        <h1>Dashboard not found</h1>
        <p>No dashboard exists at "{slug}".</p>
      </div>
    )
  }

  if (error || !dashboard) {
    return (
      <div className="dashboard-detail-status">
        <h1>Something went wrong</h1>
        <p>{error ?? 'Unable to load this dashboard.'}</p>
      </div>
    )
  }

  const created = formatDate(dashboard.created)
  const updated = formatDate(dashboard.updated)

  return (
    <article className="dashboard-detail">
      <header className="dashboard-detail-header">
        <h1>{dashboard.title}</h1>
        <div className="dashboard-detail-meta">
          {dashboard.model && <span className="dashboard-detail-badge">{dashboard.model}</span>}
          {created && <span>Created {created}</span>}
          {updated && <span>Updated {updated}</span>}
          {dashboard.path && <span className="dashboard-detail-path">{dashboard.path}</span>}
        </div>
      </header>

      <MarkdownRenderer content={dashboard.content} currentSlug={dashboard.slug} />
    </article>
  )
}
