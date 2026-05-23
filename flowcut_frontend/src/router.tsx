import { Routes, Route } from 'react-router-dom'
import GenerateTab from './components/generate/GenerateTab'
import MaterialTab from './components/material/MaterialTab'
import CreativeTab from './components/creative/CreativeTab'
import DashboardTab from './components/dashboard/DashboardTab'
import ScriptEditor from './components/generate/ScriptEditor'
import MaterialPreview from './components/generate/MaterialPreview'

export default function AppRouter() {
  return (
    <Routes>
      <Route path="/"                          element={<GenerateTab />} />
      <Route path="/scripts/:scriptId"         element={<ScriptEditor />} />
      <Route path="/scripts/:scriptId/preview" element={<MaterialPreview />} />
      <Route path="/material"  element={<MaterialTab />} />
      <Route path="/creative"  element={<CreativeTab />} />
      <Route path="/dashboard" element={<DashboardTab />} />
    </Routes>
  )
}
