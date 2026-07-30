import { Outlet } from 'react-router'
import { Sidebar } from './Sidebar'
import './DashboardLayout.css'

export function DashboardLayout() {
  return (
    <div className="dashboard-layout">
      <Sidebar />
      <main className="dashboard-main">
        <Outlet />
      </main>
    </div>
  )
}
