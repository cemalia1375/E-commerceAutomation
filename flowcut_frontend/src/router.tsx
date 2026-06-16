import { Routes, Route } from 'react-router-dom'
import GenerateTab from './components/generate/GenerateTab'
import MaterialTab from './components/material/MaterialTab'
import CreativeTab from './components/creative/CreativeTab'
import DashboardTab from './components/dashboard/DashboardTab'
import WorkspaceLayout from './components/workspace/WorkspaceLayout'
import LoginPage from './components/auth/LoginPage'
import RequireAuth from './components/auth/RequireAuth'
import AppShell from './components/layout/AppShell'

export default function AppRouter() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route path="/"                          element={<GenerateTab />} />
        <Route path="/workspace/:scriptId"       element={<WorkspaceLayout />} />
        <Route path="/material"  element={<MaterialTab />} />
        <Route path="/creative"  element={<CreativeTab />} />
        <Route path="/dashboard" element={<DashboardTab />} />
      </Route>
    </Routes>
  )
}
