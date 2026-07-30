import { BrowserRouter, Route, Routes } from 'react-router'
import { DashboardLayout } from './features/dashboards/DashboardLayout'
import { DashboardDetail } from './features/dashboards/DashboardDetail'
import { EmptyState } from './features/dashboards/EmptyState'

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<DashboardLayout />}>
          <Route index element={<EmptyState />} />
          <Route path="d/*" element={<DashboardDetail />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App
