import { NavLink } from 'react-router'
import { useDashboards } from './useDashboards'
import './Sidebar.css'

function formatDate(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

export function Sidebar() {
  const { dashboards, loading, error } = useDashboards()

  return (
    <nav className="sidebar" aria-label="Dashboards">
      <div className="sidebar-header">
        <span className="sidebar-title">Dashboards</span>
      </div>

      {loading && <p className="sidebar-status">Loading…</p>}
      {error && <p className="sidebar-status sidebar-status-error">{error}</p>}
      {!loading && !error && dashboards.length === 0 && (
        <p className="sidebar-status">No dashboards yet. Run the generator script to add some.</p>
      )}

      <ul className="sidebar-list">
        {dashboards.map((dashboard) => (
          <li key={dashboard.slug}>
            <NavLink
              to={`/d/${dashboard.slug}`}
              className={({ isActive }) => `sidebar-item${isActive ? ' sidebar-item-active' : ''}`}
            >
              <span className="sidebar-item-title">{dashboard.title}</span>
              <span className="sidebar-item-meta">
                {dashboard.model && <span className="sidebar-item-model">{dashboard.model}</span>}
                <span className="sidebar-item-date">{formatDate(dashboard.updated)}</span>
              </span>
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  )
}
